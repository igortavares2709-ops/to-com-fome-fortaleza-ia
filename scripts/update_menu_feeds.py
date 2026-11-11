#!/usr/bin/env python3
"""Update prices only from explicitly configured, authorized restaurant feeds."""

from __future__ import annotations

import csv
import copy
import io
import ipaddress
import json
import os
import socket
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDS = ROOT / "data" / "official-menu-feeds.json"
RESTAURANTS = ROOT / "data" / "restaurants.json"
OUTPUT = ROOT / "data" / "menu-items.json"
USER_AGENT = "ToComFomeFortalezaIAMenuUpdater/1.0 (portfolio educacional; contato via repositorio)"
MAX_BYTES = 2_000_000
MAX_ITEMS = 500
ALLOWED_BASIS = {"restaurant_provided", "restaurant_public_feed"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_public_https(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Feed URL must use public HTTPS.")
    for result in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("Feed URL resolves to a non-public address.")
    return url


class PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only when every destination is public HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urllib.parse.urljoin(req.full_url, newurl)
        validate_public_https(destination)
        return super().redirect_request(req, fp, code, msg, headers, destination)


def fetch(url: str, timeout: int = 20) -> bytes:
    validated_url = validate_public_https(url)
    request = urllib.request.Request(
        validated_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/csv;q=0.9,text/html;q=0.8"},
    )
    opener = urllib.request.build_opener(PublicHttpsRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        validate_public_https(response.geturl())
        length = int(response.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            raise ValueError("Feed exceeds 2 MB.")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Feed exceeds 2 MB.")
    return body


def price_cents(value) -> int:
    try:
        amount = Decimal(str(value).strip().replace("R$", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("Invalid price.") from error
    if not amount.is_finite() or amount <= 0 or amount > Decimal("100000"):
        raise ValueError("Price outside accepted range.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "não", "indisponível"}


def normalize_item(item: dict, default_currency: str, source_url: str) -> dict:
    name = str(item.get("name") or item.get("nome") or "").strip()
    if not name or len(name) > 180:
        raise ValueError("Every menu item needs a bounded name.")
    price = item.get("price", item.get("preco"))
    if price is None:
        raise ValueError(f"{name}: missing price.")
    currency = str(item.get("currency") or item.get("moeda") or default_currency).upper()
    if currency != "BRL":
        raise ValueError(f"{name}: only BRL is supported.")
    return {
        "name": name,
        "description": str(item.get("description") or item.get("descricao") or "").strip()[:1000] or None,
        "category": str(item.get("category") or item.get("categoria") or "").strip()[:120] or None,
        "price_cents": price_cents(price),
        "currency": currency,
        "available": truthy(item.get("available", item.get("disponivel", True))),
        "source_url": source_url,
    }


def parse_json_v1(body: bytes, source_url: str) -> tuple[list[dict], str | None]:
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("json_v1 requires an object with an items array.")
    if len(payload["items"]) > MAX_ITEMS:
        raise ValueError("Feed exceeds 500 items.")
    if not payload["items"]:
        raise ValueError("Feed contains no menu items.")
    currency = str(payload.get("currency") or "BRL")
    items = [normalize_item(item, currency, source_url) for item in payload["items"]]
    updated_at = payload.get("updated_at")
    return items, str(updated_at)[:40] if updated_at else None


def parse_csv_v1(body: bytes, source_url: str) -> tuple[list[dict], None]:
    text = body.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) > MAX_ITEMS:
        raise ValueError("Feed exceeds 500 items.")
    if not rows:
        raise ValueError("Feed contains no menu items.")
    return [normalize_item(row, "BRL", source_url) for row in rows], None


class JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.collecting = False
        self.buffer: list[str] = []
        self.documents: list[object] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.collecting = True
            self.buffer = []

    def handle_data(self, data):
        if self.collecting:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.collecting:
            self.collecting = False
            try:
                self.documents.append(json.loads("".join(self.buffer)))
            except json.JSONDecodeError:
                pass


def jsonld_items(value, source_url: str, category: str | None = None):
    found = []
    if isinstance(value, list):
        for child in value:
            found.extend(jsonld_items(child, source_url, category))
        return found
    if not isinstance(value, dict):
        return found
    item_type = value.get("@type")
    types = set(item_type if isinstance(item_type, list) else [item_type])
    if "MenuSection" in types:
        category = str(value.get("name") or category or "").strip() or None
    if "MenuItem" in types:
        offers = value.get("offers")
        if isinstance(offers, list):
            offers = next((offer for offer in offers if isinstance(offer, dict) and offer.get("price") is not None), None)
        if isinstance(offers, dict) and offers.get("price") is not None:
            availability = str(offers.get("availability") or "").lower()
            try:
                item = normalize_item(
                    {
                        "name": value.get("name"),
                        "description": value.get("description"),
                        "category": category,
                        "price": offers.get("price"),
                        "currency": offers.get("priceCurrency") or "BRL",
                        "available": not availability.endswith("outofstock"),
                    },
                    "BRL",
                    source_url,
                )
            except (TypeError, ValueError):
                # Public pages sometimes expose placeholders with a zero or
                # malformed price. They are not real offers and must not make
                # a restaurant appear free in price filters.
                pass
            else:
                found.append(item)
        return found
    for child in value.values():
        found.extend(jsonld_items(child, source_url, category))
    return found


def parse_jsonld_menu(body: bytes, source_url: str) -> tuple[list[dict], None]:
    parser = JsonLdParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    items = []
    for document in parser.documents:
        items.extend(jsonld_items(document, source_url))
    unique = {}
    for item in items:
        key = (item["name"].casefold(), (item["category"] or "").casefold(), item["price_cents"])
        unique[key] = item
    items = list(unique.values())
    if not items:
        raise ValueError("No priced MenuItem entries found in JSON-LD.")
    if len(items) > MAX_ITEMS:
        raise ValueError("Feed exceeds 500 items.")
    return items, None


def parse_feed(format_name: str, body: bytes, source_url: str) -> tuple[list[dict], str | None]:
    if format_name == "json_v1":
        return parse_json_v1(body, source_url)
    if format_name == "csv_v1":
        return parse_csv_v1(body, source_url)
    if format_name == "jsonld_menu":
        return parse_jsonld_menu(body, source_url)
    raise ValueError(f"Unsupported feed format: {format_name}")


def osm_source_id(source: object) -> str | None:
    if not isinstance(source, dict):
        return None
    prefix = {"node": "n", "way": "w", "relation": "r"}.get(str(source.get("osm_type") or "").lower())
    osm_id = source.get("osm_id")
    if not prefix or isinstance(osm_id, bool):
        return None
    try:
        return f"{prefix}{int(osm_id)}"
    except (TypeError, ValueError):
        return None


def build_restaurant_aliases(restaurants: list[dict]) -> dict[str, str]:
    """Map current and historical OSM IDs to the current canonical ID."""

    aliases: dict[str, str] = {}
    for restaurant in restaurants:
        canonical = str(restaurant.get("id") or "")
        if not canonical:
            raise ValueError("Every restaurant needs an id.")
        candidates = [canonical, osm_source_id(restaurant.get("source"))]
        candidates.extend(osm_source_id(source) for source in restaurant.get("additional_sources") or [])
        for alias in filter(None, candidates):
            existing = aliases.get(alias)
            if existing and existing != canonical:
                raise ValueError(f"Restaurant source id {alias} maps to multiple restaurants.")
            aliases[alias] = canonical
    return aliases


def has_usable_prices(menu: object) -> bool:
    if not isinstance(menu, dict):
        return False
    return any(
        isinstance(item, dict)
        and isinstance(item.get("price_cents"), int)
        and not isinstance(item.get("price_cents"), bool)
        and item["price_cents"] > 0
        for item in menu.get("items") or []
    )


def sanitize_menu(menu: object) -> dict | None:
    if not isinstance(menu, dict):
        return None
    sanitized = copy.deepcopy(menu)
    sanitized["items"] = [
        item
        for item in sanitized.get("items") or []
        if isinstance(item, dict)
        and isinstance(item.get("price_cents"), int)
        and not isinstance(item.get("price_cents"), bool)
        and item["price_cents"] > 0
    ]
    return sanitized


def update(feeds_payload: dict, restaurant_aliases: dict[str, str] | set[str], previous: dict) -> dict:
    feeds = feeds_payload.get("feeds")
    if not isinstance(feeds, list):
        raise ValueError("feeds must be an array.")
    if isinstance(restaurant_aliases, set):
        restaurant_aliases = {restaurant_id: restaurant_id for restaurant_id in restaurant_aliases}

    resolved_feeds = []
    seen_restaurants: set[str] = set()
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ValueError("Every feed must be an object.")
        configured_id = str(feed.get("restaurant_id") or "")
        restaurant_id = restaurant_aliases.get(configured_id)
        if not restaurant_id:
            raise ValueError(f"Unknown restaurant_id: {configured_id}")
        if restaurant_id in seen_restaurants:
            raise ValueError(f"Duplicate feed for restaurant_id: {restaurant_id}")
        seen_restaurants.add(restaurant_id)
        resolved_feeds.append((feed, configured_id, restaurant_id))

    active_ids = {restaurant_id for _, _, restaurant_id in resolved_feeds}
    menus: dict[str, dict] = {}
    for previous_id, previous_menu in (previous.get("menus") or {}).items():
        canonical_id = restaurant_aliases.get(previous_id, previous_id)
        if canonical_id in active_ids and canonical_id not in menus:
            sanitized = sanitize_menu(previous_menu)
            if sanitized is not None:
                menus[canonical_id] = sanitized
    successful = failed = 0

    for feed, _configured_id, restaurant_id in resolved_feeds:
        if feed.get("permission_basis") not in ALLOWED_BASIS:
            raise ValueError(f"{restaurant_id}: permission_basis is required.")
        source_url = str(feed.get("url") or "")
        try:
            validate_public_https(source_url)
            items, source_updated_at = parse_feed(
                str(feed.get("format") or ""),
                fetch(source_url),
                source_url,
            )
            menus[restaurant_id] = {
                "source_url": source_url,
                "permission_basis": feed["permission_basis"],
                "source_updated_at": source_updated_at,
                "fetched_at": now_iso(),
                "stale": False,
                "last_error": None,
                "items": items,
            }
            successful += 1
        except Exception as error:  # preserve last known-good prices on transient failures
            failed += 1
            existing = menus.get(restaurant_id)
            if existing:
                existing["stale"] = True
                existing["last_error"] = type(error).__name__
                existing["last_attempt_at"] = now_iso()
            else:
                menus[restaurant_id] = {
                    "source_url": source_url,
                    "permission_basis": feed["permission_basis"],
                    "source_updated_at": None,
                    "fetched_at": None,
                    "stale": True,
                    "last_error": type(error).__name__,
                    "last_attempt_at": now_iso(),
                    "items": [],
                }

    return {
        "meta": {
            "schema_version": 1,
            "generated_at": now_iso() if feeds else None,
            "feed_count": len(feeds),
            "successful_feeds": successful,
            "failed_feeds": failed,
            "restaurants_with_prices": sum(
                1 for menu in menus.values() if has_usable_prices(menu)
            ),
        },
        "menus": menus,
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o644)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    feeds_payload = json.loads(FEEDS.read_text(encoding="utf-8"))
    restaurants = json.loads(RESTAURANTS.read_text(encoding="utf-8"))["restaurants"]
    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"menus": {}}
    result = update(feeds_payload, build_restaurant_aliases(restaurants), previous)
    all_feeds_failed = bool(result["meta"]["feed_count"]) and not result["meta"]["successful_feeds"]
    if all_feeds_failed and not result["meta"]["restaurants_with_prices"]:
        raise SystemExit("All configured menu feeds failed and no usable previous prices exist.")

    atomic_write_json(OUTPUT, result)
    print(
        f"Menu feeds: {result['meta']['successful_feeds']} succeeded, "
        f"{result['meta']['failed_feeds']} failed, "
        f"{result['meta']['restaurants_with_prices']} with prices."
    )
    if all_feeds_failed:
        print("All configured feeds failed; published the last known prices marked as stale.")


if __name__ == "__main__":
    main()
