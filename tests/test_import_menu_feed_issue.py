import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE = SCRIPTS / "import_menu_feed_issue.py"
SPEC = importlib.util.spec_from_file_location("import_menu_feed_issue", MODULE)
import_menu_feed_issue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(import_menu_feed_issue)


VALID_BODY = """### Restaurante

Restaurante Exemplo

### ID do restaurante

n123

### URL pública do cardápio

https://example.com/menu.csv

### Formato

csv_v1

### Autorização

- [x] Confirmo que represento o restaurante ou tenho autorização para disponibilizar e atualizar esta fonte.
- [x] Confirmo que a fonte não contém dados pessoais, credenciais nem conteúdo copiado do iFood.

### Observações

Contato confirmado pela gerência.
"""


class ImportMenuFeedIssueTests(unittest.TestCase):
    def test_parses_and_validates_authorized_submission(self):
        values = import_menu_feed_issue.parse_issue_body(VALID_BODY)
        with mock.patch.object(
            import_menu_feed_issue.update_menu_feeds,
            "validate_public_https",
            return_value="https://example.com/menu.csv",
        ):
            submission = import_menu_feed_issue.validate_submission(values, {"n123": "n456"})

        self.assertEqual(submission["restaurant_id"], "n456")
        self.assertEqual(submission["format"], "csv_v1")
        self.assertEqual(submission["permission_basis"], "restaurant_provided")

    def test_rejects_missing_authorization(self):
        values = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("- [x]", "- [ ]")
        )
        with self.assertRaisesRegex(ValueError, "authorization"):
            import_menu_feed_issue.validate_submission(values, {"n123": "n123"})

    def test_rejects_ifood_pages_even_when_authorized(self):
        values = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("https://example.com/menu.csv", "https://www.ifood.com.br/delivery/menu")
        )
        with self.assertRaisesRegex(ValueError, "iFood"):
            import_menu_feed_issue.validate_submission(values, {"n123": "n123"})

    def test_merge_is_idempotent_and_rejects_silent_replacement(self):
        submission = {
            "restaurant_id": "n123",
            "restaurant_name": "Restaurante Exemplo",
            "url": "https://example.com/menu.csv",
            "format": "csv_v1",
            "permission_basis": "restaurant_provided",
            "notes": "",
        }
        original = {"version": 1, "feeds": []}
        merged = import_menu_feed_issue.merge_feed(original, submission, 12, "owner/repo")
        self.assertEqual(len(merged["feeds"]), 1)
        self.assertIs(import_menu_feed_issue.merge_feed(merged, submission, 12, "owner/repo"), merged)

        replacement = dict(submission, url="https://example.com/new.csv")
        with self.assertRaisesRegex(ValueError, "already has a different feed"):
            import_menu_feed_issue.merge_feed(merged, replacement, 13, "owner/repo")


if __name__ == "__main__":
    unittest.main()
