import importlib.util
import contextlib
import copy
import io
import json
import math
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "menu_classifier", ROOT / "scripts" / "train_menu_classifier.py"
)
menu_classifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(menu_classifier)


class MenuClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload, _ = menu_classifier.load_menu_data()
        cls.records, cls.excluded = menu_classifier.weakly_labeled_records(cls.payload)

    def test_taxonomy_covers_every_source_item(self):
        total = sum(len(menu["items"]) for menu in self.payload["menus"].values())
        self.assertEqual(total, 280)
        self.assertEqual(len(self.records), 280)
        self.assertEqual(self.excluded, [])
        self.assertEqual({record["label"] for record in self.records}, set(menu_classifier.LABELS))

    def test_source_category_never_becomes_a_feature(self):
        first = {
            "name": "Texto idêntico",
            "description": "mesma descrição",
            "source_category": "CERVEJAS",
            "label": "bebida_alcoolica",
        }
        second = {
            **first,
            "source_category": "SOBREMESA",
            "label": "sobremesa",
        }
        self.assertEqual(menu_classifier.document_text(first), menu_classifier.document_text(second))
        self.assertEqual(menu_classifier.feature_counts(first), menu_classifier.feature_counts(second))

    def test_group_split_is_deterministic_and_prevents_exact_text_leakage(self):
        first_train, first_test = menu_classifier.stratified_group_split(self.records)
        second_train, second_test = menu_classifier.stratified_group_split(self.records)
        self.assertEqual(
            [(item["restaurant_id"], item["fingerprint"]) for item in first_train],
            [(item["restaurant_id"], item["fingerprint"]) for item in second_train],
        )
        self.assertEqual(
            [(item["restaurant_id"], item["fingerprint"]) for item in first_test],
            [(item["restaurant_id"], item["fingerprint"]) for item in second_test],
        )
        self.assertFalse(
            {item["fingerprint"] for item in first_train}
            & {item["fingerprint"] for item in first_test}
        )
        self.assertEqual({item["label"] for item in first_test}, set(menu_classifier.LABELS))

    def test_training_and_report_are_reproducible(self):
        first_model, first_report = menu_classifier.build_artifacts()
        second_model, second_report = menu_classifier.build_artifacts()
        self.assertEqual(first_model, second_model)
        self.assertEqual(first_report, second_report)
        self.assertEqual(first_model["serialized_float_digits"], 12)
        self.assertEqual(first_report["split"]["train_test_fingerprint_overlap"], 0)
        self.assertGreaterEqual(first_report["metrics"]["stratified_group_holdout"]["macro_f1"], 0.6)

    def test_committed_artifacts_match_the_reproducible_pipeline(self):
        model, report = menu_classifier.build_artifacts()
        committed_model = json.loads(
            (ROOT / "ml" / "artifacts" / "menu-category-nb.json").read_text(encoding="utf-8")
        )
        committed_report = json.loads(
            (ROOT / "ml" / "results" / "menu-classifier-baseline.json").read_text(encoding="utf-8")
        )
        self.assertEqual(committed_model, model)
        self.assertEqual(committed_report, report)

    def test_operational_feed_changes_do_not_invalidate_the_ml_corpus(self):
        payload = copy.deepcopy(self.payload)
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "menu-items.json"
            data_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            original_model, original_report = menu_classifier.build_artifacts(data_path)

            first_menu = next(iter(payload["menus"].values()))
            first_menu["fetched_at"] = "2099-01-01T00:00:00+00:00"
            first_menu["items"][0]["price_cents"] += 123
            first_menu["items"][0]["available"] = not first_menu["items"][0]["available"]
            payload["meta"]["generated_at"] = "2099-01-01T00:00:00+00:00"
            data_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            changed_model, changed_report = menu_classifier.build_artifacts(data_path)

            self.assertEqual(changed_model, original_model)
            self.assertEqual(changed_report, original_report)

    def test_text_change_updates_the_canonical_corpus_fingerprint(self):
        original_records, _ = menu_classifier.weakly_labeled_records(self.payload)
        original_fingerprint = menu_classifier.corpus_sha256(original_records)
        for field in ("name", "description"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                first_menu = next(iter(payload["menus"].values()))
                first_menu["items"][0][field] += " experimental"
                changed_records, _ = menu_classifier.weakly_labeled_records(payload)
                self.assertNotEqual(
                    menu_classifier.corpus_sha256(changed_records),
                    original_fingerprint,
                )

    def test_model_predicts_and_returns_normalized_probabilities(self):
        model = menu_classifier.train_naive_bayes(self.records)
        cases = {
            "caipirinha de caju com vodka": "bebida_alcoolica",
            "suco natural de laranja": "bebida_sem_alcool",
            "pudim de leite com caramelo": "sobremesa",
            "risoto de camarão com parmesão": "prato_principal",
            "gambas al ajillo acompanha pão": "entrada_acompanhamento",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = menu_classifier.predict(model, text)
                self.assertEqual(result["label"], expected)
                self.assertTrue(math.isclose(sum(result["probabilities"].values()), 1.0))
                self.assertGreater(result["recognized_features"], 0)

    def test_cli_artifacts_are_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.json"
            report_path = Path(directory) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = menu_classifier.main([
                    "train",
                    "--model", str(model_path),
                    "--report", str(report_path),
                ])
            self.assertEqual(code, 0)
            model = json.loads(model_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(model["feature_contract"], ["name", "description"])
            self.assertEqual(report["data"]["weakly_labeled_items"], 280)


if __name__ == "__main__":
    unittest.main()
