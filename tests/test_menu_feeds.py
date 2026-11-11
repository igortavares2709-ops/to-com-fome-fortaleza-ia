import importlib.util
import json
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).parents[1] / "scripts" / "update_menu_feeds.py"
SPEC = importlib.util.spec_from_file_location("menu_feeds", MODULE)
menu_feeds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(menu_feeds)


class MenuFeedTests(unittest.TestCase):
    def test_price_rounding(self):
        self.assertEqual(menu_feeds.price_cents("19,995"), 2000)

    def test_rejects_zero_price(self):
        with self.assertRaises(ValueError):
            menu_feeds.price_cents("0")

    def test_json_contract(self):
        body = b'{"currency":"BRL","items":[{"name":"Prato do dia","price":"24.90"}]}'
        items, updated = menu_feeds.parse_json_v1(body, "https://example.com/menu.json")
        self.assertIsNone(updated)
        self.assertEqual(items[0]["price_cents"], 2490)
        self.assertTrue(items[0]["available"])

    def test_rejects_missing_price(self):
        with self.assertRaises(ValueError):
            menu_feeds.normalize_item({"name": "Sem preço"}, "BRL", "https://example.com/menu.json")

    def test_empty_structured_feeds_are_rejected(self):
        with self.assertRaises(ValueError):
            menu_feeds.parse_json_v1(b'{"currency":"BRL","items":[]}', "https://example.com/menu.json")
        with self.assertRaises(ValueError):
            menu_feeds.parse_csv_v1(b"name,price\n", "https://example.com/menu.csv")

    def test_jsonld_menu(self):
        body = b'''<script type="application/ld+json">{"@type":"Menu","hasMenuSection":[{"@type":"MenuSection","name":"Pratos","hasMenuItem":[{"@type":"MenuItem","name":"Peixe","offers":{"@type":"Offer","price":"39.90","priceCurrency":"BRL","availability":"https://schema.org/InStock"}}]}]}</script>'''
        items, updated = menu_feeds.parse_jsonld_menu(body, "https://example.com/menu")
        self.assertIsNone(updated)
        self.assertEqual(items[0]["category"], "Pratos")
        self.assertEqual(items[0]["price_cents"], 3990)

    def test_jsonld_skips_zero_priced_placeholders(self):
        body = b'''<script type="application/ld+json">[{"@type":"MenuItem","name":"Indisponivel","offers":{"@type":"Offer","price":"0","priceCurrency":"BRL"}},{"@type":"MenuItem","name":"Peixe","offers":{"@type":"Offer","price":"39.90","priceCurrency":"BRL"}}]</script>'''
        items, _ = menu_feeds.parse_jsonld_menu(body, "https://example.com/menu")
        self.assertEqual([item["name"] for item in items], ["Peixe"])

    def test_empty_configuration_is_stable(self):
        result = menu_feeds.update({"feeds": []}, {"n1"}, {"menus": {}})
        self.assertEqual(result["meta"]["feed_count"], 0)
        self.assertIsNone(result["meta"]["generated_at"])

    def test_dns_failure_is_isolated_and_preserves_previous_prices(self):
        feeds = {
            "feeds": [
                {
                    "restaurant_id": "n1",
                    "url": "https://unavailable.example/menu.json",
                    "format": "json_v1",
                    "permission_basis": "restaurant_public_feed",
                },
                {
                    "restaurant_id": "n2",
                    "url": "https://working.example/menu.json",
                    "format": "json_v1",
                    "permission_basis": "restaurant_public_feed",
                },
            ]
        }
        previous = {
            "menus": {
                "n1": {
                    "source_url": "https://unavailable.example/menu.json",
                    "items": [{"name": "Prato anterior", "price_cents": 2500}],
                }
            }
        }

        def validate(url):
            if "unavailable" in url:
                raise socket.gaierror("temporary DNS failure")
            return url

        body = b'{"items":[{"name":"Prato atual","price":"31.00"}]}'
        with mock.patch.object(menu_feeds, "validate_public_https", side_effect=validate), mock.patch.object(
            menu_feeds, "fetch", return_value=body
        ):
            result = menu_feeds.update(feeds, {"n1", "n2"}, previous)

        self.assertEqual(result["meta"]["successful_feeds"], 1)
        self.assertEqual(result["meta"]["failed_feeds"], 1)
        self.assertTrue(result["menus"]["n1"]["stale"])
        self.assertEqual(result["menus"]["n1"]["items"][0]["price_cents"], 2500)
        self.assertFalse(result["menus"]["n2"]["stale"])

    def test_all_failures_keep_only_usable_previous_prices(self):
        feeds = {
            "feeds": [
                {
                    "restaurant_id": "n1",
                    "url": "https://example.com/menu.json",
                    "format": "json_v1",
                    "permission_basis": "restaurant_public_feed",
                }
            ]
        }
        previous = {
            "menus": {
                "n1": {
                    "source_url": "https://example.com/menu.json",
                    "items": [
                        {"name": "Prato", "price_cents": 2500},
                        {"name": "Placeholder", "price_cents": 0},
                    ],
                }
            }
        }
        with mock.patch.object(menu_feeds, "validate_public_https", side_effect=socket.gaierror("offline")):
            result = menu_feeds.update(feeds, {"n1"}, previous)

        self.assertEqual(result["meta"]["successful_feeds"], 0)
        self.assertEqual(result["meta"]["restaurants_with_prices"], 1)
        self.assertTrue(result["menus"]["n1"]["stale"])
        self.assertEqual([item["price_cents"] for item in result["menus"]["n1"]["items"]], [2500])

    def test_historical_osm_id_resolves_to_current_restaurant(self):
        restaurants = [
            {
                "id": "w20",
                "source": {"osm_type": "way", "osm_id": 20},
                "additional_sources": [{"osm_type": "node", "osm_id": 10}],
            }
        ]
        aliases = menu_feeds.build_restaurant_aliases(restaurants)
        feed = {
            "feeds": [
                {
                    "restaurant_id": "n10",
                    "url": "https://example.com/menu.json",
                    "format": "json_v1",
                    "permission_basis": "restaurant_public_feed",
                }
            ]
        }
        with mock.patch.object(menu_feeds, "validate_public_https", side_effect=lambda url: url), mock.patch.object(
            menu_feeds,
            "fetch",
            return_value=b'{"items":[{"name":"Prato","price":"20"}]}',
        ):
            result = menu_feeds.update(feed, aliases, {"menus": {}})
        self.assertIn("w20", result["menus"])
        self.assertNotIn("n10", result["menus"])

    def test_duplicate_feeds_for_aliases_are_rejected(self):
        feeds = {
            "feeds": [
                {"restaurant_id": "n10"},
                {"restaurant_id": "w20"},
            ]
        }
        with self.assertRaisesRegex(ValueError, "Duplicate feed"):
            menu_feeds.update(feeds, {"n10": "w20", "w20": "w20"}, {"menus": {}})

    def test_redirect_to_non_https_destination_is_rejected(self):
        request = mock.Mock(full_url="https://example.com/menu")
        with self.assertRaises(ValueError):
            menu_feeds.PublicHttpsRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://example.com/private",
            )

    def test_atomic_json_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "menu-items.json"
            menu_feeds.atomic_write_json(path, {"menus": {"n1": {"items": []}}})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "menus": {\n    "n1": {\n      "items": []\n    }\n  }\n}\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(list(path.parent.glob(".menu-items.json.*.tmp")), [])

    def test_main_does_not_replace_output_when_all_feeds_fail_without_prices(self):
        failed_result = {
            "meta": {
                "feed_count": 1,
                "successful_feeds": 0,
                "failed_feeds": 1,
                "restaurants_with_prices": 0,
            },
            "menus": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds.json"
            restaurants = root / "restaurants.json"
            output = root / "menu-items.json"
            feeds.write_text(json.dumps({"feeds": []}), encoding="utf-8")
            restaurants.write_text(json.dumps({"restaurants": []}), encoding="utf-8")
            original = '{"menus":{"n1":{"items":[{"price_cents":2500}]}}}\n'
            output.write_text(original, encoding="utf-8")

            with mock.patch.multiple(
                menu_feeds,
                FEEDS=feeds,
                RESTAURANTS=restaurants,
                OUTPUT=output,
            ), mock.patch.object(menu_feeds, "update", return_value=failed_result):
                with self.assertRaises(SystemExit):
                    menu_feeds.main()

            self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
