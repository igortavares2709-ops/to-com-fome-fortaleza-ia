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
    def test_issue_revision_must_match_the_reviewed_timestamp(self):
        issue = {"updated_at": "2026-11-11T22:15:00Z"}
        import_menu_feed_issue.verify_issue_revision(issue, "2026-11-11T22:15:00Z")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            import_menu_feed_issue.verify_issue_revision(issue, "2026-11-11T22:16:00Z")
        with self.assertRaisesRegex(ValueError, "invalid"):
            import_menu_feed_issue.verify_issue_revision(issue, "ontem")

    def test_parses_and_validates_authorized_submission(self):
        values = import_menu_feed_issue.parse_issue_body(VALID_BODY)
        with mock.patch.object(
            import_menu_feed_issue.update_menu_feeds,
            "validate_public_https",
            return_value="https://example.com/menu.csv",
        ):
            submission = import_menu_feed_issue.validate_submission(
                values, {"n123": "n456"}, {"n456": "Restaurante Exemplo"}
            )

        self.assertEqual(submission["restaurant_id"], "n456")
        self.assertEqual(submission["format"], "csv_v1")
        self.assertEqual(submission["permission_basis"], "restaurant_provided")

    def test_rejects_missing_authorization(self):
        values = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("- [x]", "- [ ]")
        )
        with self.assertRaisesRegex(ValueError, "authorization"):
            import_menu_feed_issue.validate_submission(
                values, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
            )

    def test_rejects_negated_or_unchecked_authorization_claims(self):
        negated = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace(
                "Confirmo que represento o restaurante ou tenho autorização para disponibilizar e atualizar esta fonte.",
                "Não represento o restaurante e não tenho autorização para disponibilizar esta fonte.",
            )
        )
        unchecked = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace(
                "- [x] Confirmo que a fonte não contém", "- [ ] Confirmo que a fonte não contém"
            )
        )
        for values in (negated, unchecked):
            with self.assertRaisesRegex(ValueError, "authorization"):
                import_menu_feed_issue.validate_submission(
                    values, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
                )

    def test_rejects_hidden_or_fenced_authorization_claims(self):
        first_claim = "- [x] Confirmo que represento o restaurante ou tenho autorização para disponibilizar e atualizar esta fonte."
        for replacement in (
            f"<!--\n{first_claim}\n-->",
            f"```\n{first_claim}\n```",
        ):
            values = import_menu_feed_issue.parse_issue_body(VALID_BODY.replace(first_claim, replacement))
            with self.assertRaisesRegex(ValueError, "authorization"):
                import_menu_feed_issue.validate_submission(
                    values, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
                )

    def test_rejects_duplicate_or_unexpected_sections(self):
        injected = VALID_BODY.replace(
            "Contato confirmado pela gerência.",
            "Contato confirmado.\n\n### ID do restaurante\n\nn999",
        )
        with self.assertRaisesRegex(ValueError, "duplicated"):
            import_menu_feed_issue.parse_issue_body(injected)

    def test_rejects_ifood_pages_even_when_authorized(self):
        values = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("https://example.com/menu.csv", "https://www.ifood.com.br/delivery/menu")
        )
        with self.assertRaisesRegex(ValueError, "iFood"):
            import_menu_feed_issue.validate_submission(
                values, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
            )

        trailing_dot = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("https://example.com/menu.csv", "https://www.ifood.com.br./delivery/menu")
        )
        with self.assertRaisesRegex(ValueError, "iFood"):
            import_menu_feed_issue.validate_submission(
                trailing_dot, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
            )

    def test_rejects_credentials_and_restaurant_name_mismatch(self):
        credentials = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("https://example.com/menu.csv", "https://alice:secret@example.com/menu.csv")
        )
        with self.assertRaisesRegex(ValueError, "credentials"):
            import_menu_feed_issue.validate_submission(
                credentials, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
            )

        mismatched = import_menu_feed_issue.parse_issue_body(
            VALID_BODY.replace("Restaurante Exemplo", "Outro Restaurante", 1)
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            import_menu_feed_issue.validate_submission(
                mismatched, {"n123": "n123"}, {"n123": "Restaurante Exemplo"}
            )

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
        aliases = {"n123": "n123"}
        merged = import_menu_feed_issue.merge_feed(original, submission, 12, "owner/repo", aliases)
        self.assertEqual(len(merged["feeds"]), 1)
        self.assertIs(
            import_menu_feed_issue.merge_feed(merged, submission, 12, "owner/repo", aliases),
            merged,
        )

        replacement = dict(submission, url="https://example.com/new.csv")
        with self.assertRaisesRegex(ValueError, "already has a different feed"):
            import_menu_feed_issue.merge_feed(merged, replacement, 13, "owner/repo", aliases)

    def test_merge_resolves_aliases_and_rejects_reused_source(self):
        base = {
            "version": 1,
            "feeds": [
                {
                    "restaurant_id": "n10",
                    "url": "https://example.com/menu.csv",
                    "format": "csv_v1",
                }
            ],
        }
        alias_submission = {
            "restaurant_id": "w20",
            "restaurant_name": "Restaurante Exemplo",
            "url": "https://example.com/new.csv",
            "format": "csv_v1",
            "permission_basis": "restaurant_provided",
            "notes": "",
        }
        with self.assertRaisesRegex(ValueError, "already has a different feed"):
            import_menu_feed_issue.merge_feed(
                base, alias_submission, 14, "owner/repo", {"n10": "w20", "w20": "w20"}
            )

        reused = dict(alias_submission, restaurant_id="n30", url="https://example.com/menu.csv")
        with self.assertRaisesRegex(ValueError, "another restaurant"):
            import_menu_feed_issue.merge_feed(
                base, reused, 15, "owner/repo", {"n10": "w20", "n30": "n30"}
            )


if __name__ == "__main__":
    unittest.main()
