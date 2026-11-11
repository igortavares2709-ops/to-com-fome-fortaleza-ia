#!/usr/bin/env python3
"""Import one manually approved GitHub issue as an authorized menu feed.

The workflow remains deliberately manual: a repository maintainer chooses the
issue number, while this script validates the restaurant, authorization claim,
URL, format and feed contents before changing the allowlist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import update_menu_feeds

ROOT = Path(__file__).resolve().parents[1]
FEEDS = ROOT / "data" / "official-menu-feeds.json"
RESTAURANTS = ROOT / "data" / "restaurants.json"
ALLOWED_FORMATS = {"csv_v1", "json_v1", "jsonld_menu"}
SECTION = re.compile(r"^###\s+(.+?)\s*\n+(.*?)(?=^###\s+|\Z)", re.MULTILINE | re.DOTALL)
ID_PATTERN = re.compile(r"^[nwr]\d{1,20}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-number", type=int, required=True)
    return parser.parse_args()


def parse_issue_body(body: str) -> dict[str, str]:
    sections = {
        heading.strip().casefold(): value.strip()
        for heading, value in SECTION.findall(body or "")
    }
    aliases = {
        "restaurant_name": "restaurante",
        "restaurant_id": "id do restaurante",
        "source_url": "url pública do cardápio",
        "format": "formato",
        "authorization": "autorização",
        "notes": "observações",
    }
    parsed = {key: sections.get(label, "") for key, label in aliases.items()}
    if parsed["notes"] == "_No response_":
        parsed["notes"] = ""
    return parsed


def fetch_issue(repository: str, issue_number: int, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GITHUB_REPOSITORY is invalid.")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues/{issue_number}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ToComFomeMenuApproval/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read(1_000_001))
    if not isinstance(payload, dict) or payload.get("pull_request"):
        raise ValueError("The selected number is not a menu submission issue.")
    return payload


def validate_submission(values: dict[str, str], aliases: dict[str, str]) -> dict:
    restaurant_name = values.get("restaurant_name", "").strip()
    configured_id = values.get("restaurant_id", "").strip()
    source_url = values.get("source_url", "").strip()
    format_name = values.get("format", "").strip()
    authorization = values.get("authorization", "").casefold()
    notes = values.get("notes", "").strip()

    if not restaurant_name or len(restaurant_name) > 200:
        raise ValueError("Restaurant name is required and must be at most 200 characters.")
    if not ID_PATTERN.fullmatch(configured_id) or configured_id not in aliases:
        raise ValueError("Restaurant ID is not present in the Fortaleza database.")
    if format_name not in ALLOWED_FORMATS:
        raise ValueError("Unsupported menu feed format.")
    if "[x]" not in authorization:
        raise ValueError("The authorization confirmations are required.")
    required_claims = ("represento o restaurante", "conteúdo copiado do ifood")
    if not all(claim in authorization for claim in required_claims):
        raise ValueError("Both authorization confirmations are required.")

    parsed_url = urllib.parse.urlparse(source_url)
    hostname = (parsed_url.hostname or "").casefold()
    if hostname == "ifood.com" or hostname.endswith(".ifood.com") or hostname == "ifood.com.br" or hostname.endswith(".ifood.com.br"):
        raise ValueError("iFood pages are not accepted as menu feeds.")
    update_menu_feeds.validate_public_https(source_url)

    return {
        "restaurant_id": aliases[configured_id],
        "restaurant_name": restaurant_name,
        "url": source_url,
        "format": format_name,
        "permission_basis": "restaurant_provided",
        "notes": notes[:500],
    }


def preflight_feed(submission: dict) -> int:
    body = update_menu_feeds.fetch(submission["url"])
    items, _source_updated_at = update_menu_feeds.parse_feed(
        submission["format"], body, submission["url"]
    )
    if not items:
        raise ValueError("The approved source contains no valid priced items.")
    return len(items)


def merge_feed(feeds_payload: dict, submission: dict, issue_number: int, repository: str) -> dict:
    feeds = feeds_payload.get("feeds")
    if not isinstance(feeds, list):
        raise ValueError("Official feed configuration is invalid.")
    entry = {
        "restaurant_id": submission["restaurant_id"],
        "url": submission["url"],
        "format": submission["format"],
        "permission_basis": submission["permission_basis"],
        "notes": (
            f"Autorização enviada por {submission['restaurant_name']} e aprovada no issue "
            f"https://github.com/{repository}/issues/{issue_number}."
            + (f" {submission['notes']}" if submission["notes"] else "")
        )[:1000],
    }
    for existing in feeds:
        if existing.get("restaurant_id") != submission["restaurant_id"]:
            continue
        if existing.get("url") == entry["url"] and existing.get("format") == entry["format"]:
            return feeds_payload
        raise ValueError("This restaurant already has a different feed; review it manually.")
    merged = dict(feeds_payload)
    merged["feeds"] = sorted([*feeds, entry], key=lambda feed: feed["restaurant_id"])
    return merged


def write_action_output(restaurant_id: str, item_count: int) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        output.write(f"restaurant_id={restaurant_id}\nitem_count={item_count}\n")


def main() -> None:
    args = parse_args()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not repository or not token:
        raise SystemExit("GITHUB_REPOSITORY and GH_TOKEN are required.")

    issue = fetch_issue(repository, args.issue_number, token)
    values = parse_issue_body(str(issue.get("body") or ""))
    restaurants = json.loads(RESTAURANTS.read_text(encoding="utf-8"))["restaurants"]
    aliases = update_menu_feeds.build_restaurant_aliases(restaurants)
    submission = validate_submission(values, aliases)
    item_count = preflight_feed(submission)
    feeds_payload = json.loads(FEEDS.read_text(encoding="utf-8"))
    merged = merge_feed(feeds_payload, submission, args.issue_number, repository)
    update_menu_feeds.atomic_write_json(FEEDS, merged)
    write_action_output(submission["restaurant_id"], item_count)
    print(f"Approved {submission['restaurant_id']} with {item_count} valid menu items.")


if __name__ == "__main__":
    main()
