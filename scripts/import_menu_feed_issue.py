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
import unicodedata
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
EXPECTED_SECTIONS = (
    "restaurante",
    "id do restaurante",
    "url pública do cardápio",
    "formato",
    "autorização",
    "observações",
)
AUTHORIZATION_CLAIMS = {
    "confirmo que represento o restaurante ou tenho autorização para disponibilizar e atualizar esta fonte.",
    "confirmo que a fonte não contém dados pessoais, credenciais nem conteúdo copiado do ifood.",
}
CHECKBOX = re.compile(r"^\s*-\s*\[([xX ])\]\s+(.+?)\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--expected-updated-at", required=True)
    return parser.parse_args()


def parse_issue_body(body: str) -> dict[str, str]:
    matches = [
        (heading.strip().casefold(), value.strip())
        for heading, value in SECTION.findall(body or "")
    ]
    headings = tuple(heading for heading, _value in matches)
    if headings != EXPECTED_SECTIONS:
        raise ValueError("Issue sections are missing, duplicated, out of order, or unexpected.")
    sections = dict(matches)
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
    if (
        not isinstance(payload, dict)
        or payload.get("pull_request")
        or payload.get("state") != "open"
        or not str(payload.get("title") or "").startswith("Cardápio:")
    ):
        raise ValueError("The selected number is not a menu submission issue.")
    return payload


def verify_issue_revision(issue: dict, expected_updated_at: str) -> None:
    expected = expected_updated_at.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", expected):
        raise ValueError("The reviewed issue updatedAt value is invalid.")
    if issue.get("updated_at") != expected:
        raise ValueError("The menu submission changed after review; review the current issue again.")


def normalized_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(without_marks.casefold().split())


def validate_submission(
    values: dict[str, str], aliases: dict[str, str], restaurant_names: dict[str, str]
) -> dict:
    restaurant_name = values.get("restaurant_name", "").strip()
    configured_id = values.get("restaurant_id", "").strip()
    source_url = values.get("source_url", "").strip()
    format_name = values.get("format", "").strip()
    authorization = values.get("authorization", "")
    notes = values.get("notes", "").strip()

    if not restaurant_name or len(restaurant_name) > 200:
        raise ValueError("Restaurant name is required and must be at most 200 characters.")
    if not ID_PATTERN.fullmatch(configured_id) or configured_id not in aliases:
        raise ValueError("Restaurant ID is not present in the Fortaleza database.")
    canonical_id = aliases[configured_id]
    canonical_name = restaurant_names.get(canonical_id, "").strip()
    if not canonical_name or normalized_name(restaurant_name) != normalized_name(canonical_name):
        raise ValueError("Restaurant name does not match the selected Fortaleza restaurant ID.")
    if format_name not in ALLOWED_FORMATS:
        raise ValueError("Unsupported menu feed format.")
    authorization_lines = [line.strip() for line in authorization.splitlines() if line.strip()]
    checked_claims = []
    for line in authorization_lines:
        match = CHECKBOX.fullmatch(line)
        if not match or match.group(1).casefold() != "x":
            raise ValueError("Both authorization confirmations are required.")
        checked_claims.append(match.group(2).strip().casefold())
    if len(checked_claims) != 2 or set(checked_claims) != AUTHORIZATION_CLAIMS:
        raise ValueError("Both authorization confirmations are required.")
    if not source_url or len(source_url) > 1_000:
        raise ValueError("Menu feed URL is required and must be at most 1,000 characters.")
    update_menu_feeds.validate_public_https(source_url)

    return {
        "restaurant_id": canonical_id,
        "restaurant_name": canonical_name,
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


def comparable_source_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    hostname = update_menu_feeds.normalized_hostname(parsed.hostname or "")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return urllib.parse.urlunsplit((parsed.scheme.casefold(), hostname, parsed.path or "/", parsed.query, ""))


def merge_feed(
    feeds_payload: dict,
    submission: dict,
    issue_number: int,
    repository: str,
    aliases: dict[str, str],
) -> dict:
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
    submitted_url = comparable_source_url(entry["url"])
    for existing in feeds:
        existing_id = str(existing.get("restaurant_id") or "")
        canonical_existing_id = aliases.get(existing_id, existing_id)
        existing_url = comparable_source_url(str(existing.get("url") or ""))
        if existing_url == submitted_url and canonical_existing_id != submission["restaurant_id"]:
            raise ValueError("This menu source is already assigned to another restaurant.")
        if canonical_existing_id == submission["restaurant_id"]:
            if existing_url == submitted_url and existing.get("format") == entry["format"]:
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
    verify_issue_revision(issue, args.expected_updated_at)
    values = parse_issue_body(str(issue.get("body") or ""))
    restaurants = json.loads(RESTAURANTS.read_text(encoding="utf-8"))["restaurants"]
    aliases = update_menu_feeds.build_restaurant_aliases(restaurants)
    restaurant_names = {
        str(restaurant.get("id") or ""): str(restaurant.get("name") or "")
        for restaurant in restaurants
    }
    submission = validate_submission(values, aliases, restaurant_names)
    item_count = preflight_feed(submission)
    feeds_payload = json.loads(FEEDS.read_text(encoding="utf-8"))
    merged = merge_feed(feeds_payload, submission, args.issue_number, repository, aliases)
    update_menu_feeds.atomic_write_json(FEEDS, merged)
    write_action_output(submission["restaurant_id"], item_count)
    print(f"Approved {submission['restaurant_id']} with {item_count} valid menu items.")


if __name__ == "__main__":
    main()
