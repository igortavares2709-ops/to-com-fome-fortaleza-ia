#!/usr/bin/env python3
"""Extract and normalize Fortaleza restaurants from a Geofabrik OSM PBF.

Requires GDAL/ogr2ogr. The city polygon is the OpenStreetMap municipality
relation 302588. The generated JSON is a derived OSM database under ODbL 1.0.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "restaurants.json"
USER_AGENT = "Mozilla/5.0 ToComFomeFortalezaIAPortfolio/1.0"
HSTORE = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True, help="Path to a Geofabrik Nordeste .osm.pbf")
    parser.add_argument(
        "--source-url",
        default="https://download.geofabrik.de/south-america/brazil/nordeste-latest.osm.pbf",
    )
    parser.add_argument("--snapshot-date", required=True, help="PBF snapshot date (YYYY-MM-DD)")
    parser.add_argument("--boundary", type=Path, help="Optional cached GeoJSON for OSM relation 302588")
    return parser.parse_args()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def atomic_write_json(path: Path, payload: dict) -> None:
    """Replace a JSON snapshot only after the complete file reaches disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    pending = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        pending.chmod(0o644)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def boundary(path: Path) -> None:
    url = (
        "https://nominatim.openstreetmap.org/lookup?format=geojson&osm_ids=R302588"
        "&polygon_geojson=1&extratags=1&email=portfolio%40example.invalid"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if len(payload.get("features", [])) != 1:
        raise RuntimeError("Fortaleza municipality relation was not resolved.")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def parse_hstore(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return {
        key.replace(r'\"', '"').replace(r"\\", "\\"): item.replace(r'\"', '"').replace(r"\\", "\\")
        for key, item in HSTORE.findall(value)
    }


def clean_key(value: str) -> str:
    value = unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def distance_m(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (a["latitude"], a["longitude"], b["latitude"], b["longitude"]),
    )
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(value))


def split_tag(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted(
        {
            item.strip().replace("_", " ")
            for item in value.replace(",", ";").split(";")
            if item.strip()
        }
    )


def status(value: str | None) -> str | None:
    if not value:
        return None
    value = value.lower().strip()
    if value in {"yes", "only"}:
        return "yes"
    if value == "no":
        return "no"
    return value


def http_url(value: str | None) -> str | None:
    """Keep only absolute HTTP(S) links; malformed OSM tags remain unknown."""
    if not value:
        return None
    value = value.strip()
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def source_for(properties: dict) -> dict:
    if properties.get("osm_way_id"):
        osm_type, osm_id = "way", int(properties["osm_way_id"])
    elif properties.get("_layer") == "points":
        osm_type, osm_id = "node", int(properties["osm_id"])
    else:
        osm_type, osm_id = "relation", int(properties["osm_id"])
    return {
        "name": "OpenStreetMap",
        "osm_type": osm_type,
        "osm_id": osm_id,
        "url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "license": "ODbL 1.0",
    }


def feature_to_raw(feature: dict) -> dict | None:
    properties = feature["properties"]
    tags = parse_hstore(properties.get("other_tags"))
    name = properties.get("name") or tags.get("name")
    coordinates = feature.get("geometry", {}).get("coordinates", [])
    if not name or len(coordinates) < 2:
        return None
    lon, lat = coordinates[:2]
    return {
        "name": name.strip(),
        "latitude": float(lat),
        "longitude": float(lon),
        "tags": tags,
        "sources": [source_for(properties)],
    }


def completeness(item: dict) -> int:
    tags = item["tags"]
    return sum(
        bool(tags.get(key))
        for key in (
            "addr:street",
            "addr:housenumber",
            "addr:suburb",
            "cuisine",
            "opening_hours",
            "phone",
            "contact:phone",
            "website",
            "contact:website",
            "delivery",
            "takeaway",
        )
    )


def deduplicate(items: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in sorted(items, key=lambda value: -completeness(value)):
        key = clean_key(item["name"])
        matches = groups.setdefault(key, [])
        duplicate = next((candidate for candidate in matches if distance_m(item, candidate) <= 40), None)
        if not duplicate:
            matches.append(item)
            continue
        for tag, value in item["tags"].items():
            duplicate["tags"].setdefault(tag, value)
        duplicate["sources"].extend(item["sources"])
    return [item for group in groups.values() for item in group]


def normalize(items: list[dict], snapshot_date: str) -> list[dict]:
    restaurants = []
    menu_keys = ("menu", "contact:menu", "website:menu", "menu:url", "url:menu")
    for item in deduplicate(items):
        tags = item["tags"]
        neighborhood = tags.get("addr:suburb") or tags.get("addr:district")
        street, number = tags.get("addr:street"), tags.get("addr:housenumber")
        address = ", ".join(part for part in (street, number, neighborhood) if part)
        source = item["sources"][0]
        menu_url = next((http_url(tags.get(key)) for key in menu_keys if http_url(tags.get(key))), None)
        restaurants.append(
            {
                "id": f"{source['osm_type'][0]}{source['osm_id']}",
                "name": item["name"],
                "latitude": round(item["latitude"], 7),
                "longitude": round(item["longitude"], 7),
                "neighborhood": neighborhood,
                "address": address or None,
                "postcode": tags.get("addr:postcode"),
                "cuisines": split_tag(tags.get("cuisine")),
                "opening_hours": tags.get("opening_hours"),
                "phone": tags.get("contact:phone") or tags.get("phone"),
                "website": http_url(tags.get("contact:website")) or http_url(tags.get("website")),
                "menu_url": menu_url,
                "delivery": status(tags.get("delivery")),
                "takeaway": status(tags.get("takeaway")),
                "wheelchair": status(tags.get("wheelchair")),
                "diet": {
                    "vegetarian": status(tags.get("diet:vegetarian")),
                    "vegan": status(tags.get("diet:vegan")),
                    "halal": status(tags.get("diet:halal")),
                },
                "last_osm_update": snapshot_date,
                "source": source,
                "additional_sources": item["sources"][1:],
            }
        )
    return sorted(restaurants, key=lambda item: (item["neighborhood"] or "zzzz", item["name"].casefold()))


def main() -> None:
    args = parse_args()
    if not args.pbf.is_file():
        raise SystemExit(f"PBF not found: {args.pbf}")
    with tempfile.TemporaryDirectory(prefix="to-com-fome-fortaleza-ia-") as directory:
        temp = Path(directory)
        city = temp / "fortaleza.geojson"
        points = temp / "points.geojson"
        polygons = temp / "polygons.geojson"
        if args.boundary:
            if not args.boundary.is_file():
                raise SystemExit(f"Boundary not found: {args.boundary}")
            shutil.copyfile(args.boundary, city)
        else:
            boundary(city)
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(points),
                str(args.pbf),
                "points",
                "-clipsrc",
                str(city),
                "-where",
                'name IS NOT NULL AND other_tags LIKE \'%"amenity"=>"restaurant"%\'',
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(polygons),
                str(args.pbf),
                "-dialect",
                "SQLite",
                "-sql",
                "SELECT osm_id, osm_way_id, name, amenity, other_tags, "
                "ST_Centroid(geometry) AS geometry FROM multipolygons "
                "WHERE amenity='restaurant' AND name IS NOT NULL",
                "-clipsrc",
                str(city),
            ]
        )
        features = []
        for path, layer in ((points, "points"), (polygons, "multipolygons")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for feature in payload["features"]:
                feature["properties"]["_layer"] = layer
                parsed = feature_to_raw(feature)
                if parsed:
                    features.append(parsed)

    restaurants = normalize(features, args.snapshot_date)
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        "meta": {
            "title": "Restaurantes de Fortaleza",
            "retrieved_at": captured,
            "snapshot_date": args.snapshot_date,
            "coverage": "Elementos com amenity=restaurant e nome, recortados pelo limite municipal de Fortaleza e deduplicados por nome e proximidade de 40 m.",
            "city": "Fortaleza",
            "state": "Ceará",
            "country": "Brasil",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors",
            "source_url": args.source_url,
            "record_count": len(restaurants),
        },
        "restaurants": restaurants,
    }
    atomic_write_json(OUTPUT, payload)
    print(f"Saved {len(restaurants)} deduplicated Fortaleza restaurants to {OUTPUT}")


if __name__ == "__main__":
    main()
