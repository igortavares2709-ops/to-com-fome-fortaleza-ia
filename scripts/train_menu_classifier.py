#!/usr/bin/env python3
"""Train and evaluate a dependency-free menu category baseline.

The source category is used only to create a weak label. Model features come
exclusively from the public item name and description.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "menu-items.json"
DEFAULT_MODEL = ROOT / "ml" / "artifacts" / "menu-category-nb.json"
DEFAULT_REPORT = ROOT / "ml" / "results" / "menu-classifier-baseline.json"
MODEL_VERSION = "menu-category-nb-v1"
DEFAULT_SEED = "fortaleza-menu-nb-v1"
DEFAULT_TEST_RATIO = 0.25
ALPHA = 1.0
SERIALIZED_FLOAT_DIGITS = 12

LABELS = (
    "bebida_alcoolica",
    "bebida_sem_alcool",
    "entrada_acompanhamento",
    "prato_principal",
    "sobremesa",
)

# This mapping is the weak-supervision contract. The current promotional
# section is mapped from its observed snapshot contents and its drift risk is
# recorded explicitly in the model card.
CATEGORY_TO_LABEL = {
    "CERVEJAS": "bebida_alcoolica",
    "DESTILADOS": "bebida_alcoolica",
    "DESTILADOS (50ml)": "bebida_alcoolica",
    "DRINKS": "bebida_alcoolica",
    "DRINKS CLÁSSICOS": "bebida_alcoolica",
    "DRINKS ESPECIAIS": "bebida_alcoolica",
    "DRINKS ESPECIAIS NOSSA PRAIA": "bebida_alcoolica",
    "WHISKIES": "bebida_alcoolica",
    "WHISKY (50ml)": "bebida_alcoolica",
    "LONG NECK": "bebida_alcoolica",
    "LICORES": "bebida_alcoolica",
    "LICORES (35ml)": "bebida_alcoolica",
    "CAIPIROSKA": "bebida_alcoolica",
    "CAIPIFRUTAS (50ml)": "bebida_alcoolica",
    "CABARÉ ICE": "bebida_alcoolica",
    "⭐️NOVIDADES E PROMOÇÕES⭐️": "bebida_alcoolica",
    "BEBIDAS": "bebida_sem_alcool",
    "SUCOS": "bebida_sem_alcool",
    "ÁGUAS E REFRI": "bebida_sem_alcool",
    "TÔNICAS E ENERGÉTICOS": "bebida_sem_alcool",
    "CAFÉ": "bebida_sem_alcool",
    "MOCKTAILS — Drinks sem Álcool": "bebida_sem_alcool",
    "SEM ALCOOL": "bebida_sem_alcool",
    "ENTRADA": "entrada_acompanhamento",
    "TAPAS E BOCADILLOS": "entrada_acompanhamento",
    "CRUDOS": "entrada_acompanhamento",
    "GUARNIÇÕES": "entrada_acompanhamento",
    "ADICIONAIS": "entrada_acompanhamento",
    "SALADAS": "entrada_acompanhamento",
    "FESTIVAL DA LAGOSTA": "prato_principal",
    "COCINA EM CASA": "prato_principal",
    "DO MAR": "prato_principal",
    "DA TERRA": "prato_principal",
    "PRATOS INDIVIDUAIS": "prato_principal",
    "Peixes  (para 02 pessoas)": "prato_principal",
    "ESPECIALIDADES DA CASA": "prato_principal",
    "Frutos do mar (para 2 pessoas)": "prato_principal",
    "Carnes e Aves (para 2 pessoas)": "prato_principal",
    "PRATOS KID´S": "prato_principal",
    "VEGETARIANO": "prato_principal",
    "SOBREMESA": "sobremesa",
    "SOBREMESAS DA CASA": "sobremesa",
}

EXCLUDED_CATEGORIES: dict[str, str] = {}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def document_text(item: dict[str, Any]) -> str:
    """Return the only two fields that are allowed to become model features."""
    return " ".join(part for part in (str(item.get("name") or ""), str(item.get("description") or "")) if part)


def tokenize(text: str) -> list[str]:
    words = [word for word in normalize_text(text).split() if len(word) >= 2]
    bigrams = [f"{left}__{right}" for left, right in zip(words, words[1:])]
    return words + bigrams


def fingerprint(item: dict[str, Any]) -> str:
    # Group equal product names even when one source publishes a slightly
    # different description. The description remains available to the model.
    return hashlib.sha256(normalize_text(item.get("name")).encode("utf-8")).hexdigest()


def load_menu_data(path: Path = DEFAULT_DATA) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def corpus_sha256(records: list[dict[str, Any]]) -> str:
    """Fingerprint only the canonical fields that can affect this ML task."""
    rows = [
        {
            "restaurant_id": record["restaurant_id"],
            "name": record["name"],
            "description": record["description"],
            "source_category": record["source_category"],
            "weak_label": record["label"],
        }
        for record in records
    ]
    rows.sort(key=lambda row: (
        row["restaurant_id"],
        normalize_text(row["name"]),
        row["name"],
        row["description"],
        row["source_category"],
        row["weak_label"],
    ))
    canonical = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def weakly_labeled_records(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    unknown_categories: set[str] = set()

    for restaurant_id, menu in payload.get("menus", {}).items():
        for item in menu.get("items", []):
            category = str(item.get("category") or "").strip()
            label = CATEGORY_TO_LABEL.get(category)
            if not label:
                reason = EXCLUDED_CATEGORIES.get(category)
                if reason:
                    excluded.append({
                        "restaurant_id": restaurant_id,
                        "name": str(item.get("name") or ""),
                        "category": category,
                        "reason": reason,
                    })
                else:
                    unknown_categories.add(category or "<vazia>")
                continue
            record = {
                "restaurant_id": restaurant_id,
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "source_category": category,
                "label": label,
            }
            record["fingerprint"] = fingerprint(record)
            records.append(record)

    if unknown_categories:
        raise ValueError(f"Categorias sem decisão de taxonomia: {sorted(unknown_categories)}")
    return records, excluded


def _stable_group_key(seed: str, label: str, group_id: str) -> str:
    return hashlib.sha256(f"{seed}|{label}|{group_id}".encode("utf-8")).hexdigest()


def stratified_group_split(
    records: list[dict[str, Any]],
    *,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: str = DEFAULT_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0 < test_ratio < 1:
        raise ValueError("test_ratio deve estar entre 0 e 1")
    by_label: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_label[record["label"]][record["fingerprint"]].append(record)

    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for label in LABELS:
        groups = list(by_label[label].items())
        if len(groups) < 2:
            raise ValueError(f"A classe {label} não possui grupos suficientes para holdout")
        groups.sort(key=lambda entry: _stable_group_key(seed, label, entry[0]))
        target = max(2, round(sum(len(items) for _, items in groups) * test_ratio))
        chosen: list[tuple[str, list[dict[str, Any]]]] = []
        chosen_count = 0
        while groups and chosen_count < target and len(groups) > 1:
            group = groups.pop(0)
            chosen.append(group)
            chosen_count += len(group[1])
        test.extend(record for _, items in chosen for record in items)
        train.extend(record for _, items in groups for record in items)

    train.sort(key=lambda item: (item["label"], item["fingerprint"], item["restaurant_id"]))
    test.sort(key=lambda item: (item["label"], item["fingerprint"], item["restaurant_id"]))
    return train, test


def feature_counts(record: dict[str, Any]) -> Counter[str]:
    return Counter(tokenize(document_text(record)))


def train_naive_bayes(records: list[dict[str, Any]], *, alpha: float = ALPHA) -> dict[str, Any]:
    if not records:
        raise ValueError("Nenhum registro para treino")
    class_documents = Counter(record["label"] for record in records)
    missing = [label for label in LABELS if not class_documents[label]]
    if missing:
        raise ValueError(f"Classes ausentes no treino: {missing}")

    class_features: dict[str, Counter[str]] = {label: Counter() for label in LABELS}
    vocabulary: set[str] = set()
    for record in records:
        counts = feature_counts(record)
        class_features[record["label"]].update(counts)
        vocabulary.update(counts)

    vocabulary_list = sorted(vocabulary)
    vocabulary_size = len(vocabulary_list)
    total_documents = len(records)
    class_log_prior: dict[str, float] = {}
    default_log_probability: dict[str, float] = {}
    feature_log_probability: dict[str, dict[str, float]] = {}

    for label in LABELS:
        class_log_prior[label] = round(
            math.log(class_documents[label] / total_documents),
            SERIALIZED_FLOAT_DIGITS,
        )
        token_total = sum(class_features[label].values())
        denominator = token_total + alpha * vocabulary_size
        default_log_probability[label] = round(
            math.log(alpha / denominator),
            SERIALIZED_FLOAT_DIGITS,
        )
        feature_log_probability[label] = {
            token: round(
                math.log((class_features[label][token] + alpha) / denominator),
                SERIALIZED_FLOAT_DIGITS,
            )
            for token in vocabulary_list
        }

    return {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "algorithm": "multinomial_naive_bayes",
        "alpha": alpha,
        "serialized_float_digits": SERIALIZED_FLOAT_DIGITS,
        "labels": list(LABELS),
        "feature_contract": ["name", "description"],
        "preprocessing": {
            "unicode": "NFKD, diacríticos removidos",
            "tokens": "palavras alfanuméricas com >=2 caracteres + bigramas adjacentes",
        },
        "training_examples": total_documents,
        "class_document_counts": dict(sorted(class_documents.items())),
        "vocabulary": vocabulary_list,
        "class_log_prior": class_log_prior,
        "default_log_probability": default_log_probability,
        "feature_log_probability": feature_log_probability,
    }


def predict(model: dict[str, Any], text: str) -> dict[str, Any]:
    counts = Counter(token for token in tokenize(text) if token in set(model["vocabulary"]))
    scores: dict[str, float] = {}
    for label in model["labels"]:
        score = float(model["class_log_prior"][label])
        likelihoods = model["feature_log_probability"][label]
        default = float(model["default_log_probability"][label])
        for token, count in counts.items():
            score += count * float(likelihoods.get(token, default))
        scores[label] = score
    maximum = max(scores.values())
    exponentials = {label: math.exp(score - maximum) for label, score in scores.items()}
    total = sum(exponentials.values())
    probabilities = {label: exponentials[label] / total for label in model["labels"]}
    label = max(model["labels"], key=lambda candidate: (probabilities[candidate], candidate))
    return {
        "label": label,
        "confidence": probabilities[label],
        "probabilities": probabilities,
        "recognized_features": sum(counts.values()),
    }


def evaluate(model: dict[str, Any], records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    if not rows:
        raise ValueError("Nenhum registro para avaliação")
    confusion = {label: {candidate: 0 for candidate in LABELS} for label in LABELS}
    for record in rows:
        predicted = predict(model, document_text(record))["label"]
        confusion[record["label"]][predicted] += 1

    correct = sum(confusion[label][label] for label in LABELS)
    per_class: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        true_positive = confusion[label][label]
        false_positive = sum(confusion[other][label] for other in LABELS if other != label)
        false_negative = sum(confusion[label][other] for other in LABELS if other != label)
        support = sum(confusion[label].values())
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
    macro_f1 = sum(value["f1"] for value in per_class.values()) / len(LABELS)
    return {
        "examples": len(rows),
        "accuracy": round(correct / len(rows), 6),
        "macro_f1": round(macro_f1, 6),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def leave_one_restaurant_out(records: list[dict[str, Any]]) -> dict[str, Any]:
    restaurant_ids = sorted({record["restaurant_id"] for record in records})
    predictions: list[tuple[dict[str, Any], str]] = []
    folds: list[dict[str, Any]] = []
    for held_out in restaurant_ids:
        train = [record for record in records if record["restaurant_id"] != held_out]
        test = [record for record in records if record["restaurant_id"] == held_out]
        train_labels = {record["label"] for record in train}
        if train_labels != set(LABELS):
            folds.append({
                "held_out_restaurant_id": held_out,
                "status": "skipped_missing_training_class",
                "missing_labels": sorted(set(LABELS) - train_labels),
            })
            continue
        model = train_naive_bayes(train)
        fold_metrics = evaluate(model, test)
        folds.append({
            "held_out_restaurant_id": held_out,
            "status": "evaluated",
            "train_examples": len(train),
            "test_examples": len(test),
            "metrics": fold_metrics,
        })
        predictions.extend((record, predict(model, document_text(record))["label"]) for record in test)

    if len(predictions) != len(records):
        return {"status": "incomplete", "folds": folds}

    confusion = {label: {candidate: 0 for candidate in LABELS} for label in LABELS}
    for record, predicted in predictions:
        confusion[record["label"]][predicted] += 1
    # Reuse the metric formulas without re-running inference.
    correct = sum(confusion[label][label] for label in LABELS)
    per_class: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in LABELS if other != label)
        fn = sum(confusion[label][other] for other in LABELS if other != label)
        support = sum(confusion[label].values())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
    macro_f1 = sum(value["f1"] for value in per_class.values()) / len(LABELS)
    aggregate = {
        "examples": len(predictions),
        "accuracy": round(correct / len(predictions), 6),
        "macro_f1": round(macro_f1, 6),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }
    return {"status": "evaluated", "aggregate": aggregate, "folds": folds}


def build_artifacts(
    data_path: Path = DEFAULT_DATA,
    *,
    seed: str = DEFAULT_SEED,
    test_ratio: float = DEFAULT_TEST_RATIO,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, _ = load_menu_data(data_path)
    records, excluded = weakly_labeled_records(payload)
    train, test = stratified_group_split(records, test_ratio=test_ratio, seed=seed)
    holdout_model = train_naive_bayes(train)
    holdout_metrics = evaluate(holdout_model, test)
    final_model = train_naive_bayes(records)
    duplicate_groups = len(records) - len({record["fingerprint"] for record in records})
    label_distribution = Counter(record["label"] for record in records)
    source_distribution = {
        restaurant_id: dict(sorted(Counter(
            record["label"] for record in records if record["restaurant_id"] == restaurant_id
        ).items()))
        for restaurant_id in sorted({record["restaurant_id"] for record in records})
    }

    final_model["training_data"] = {
        "path": str(data_path.relative_to(ROOT)) if data_path.is_relative_to(ROOT) else str(data_path),
        "corpus_sha256": corpus_sha256(records),
        "corpus_fields": ["restaurant_id", "name", "description", "source_category", "weak_label"],
        "source_restaurants": len(payload.get("menus", {})),
        "total_items": sum(len(menu.get("items", [])) for menu in payload.get("menus", {}).values()),
        "weakly_labeled_items": len(records),
        "excluded_items": len(excluded),
    }
    final_model["taxonomy"] = {
        "category_to_label": CATEGORY_TO_LABEL,
        "excluded_categories": EXCLUDED_CATEGORIES,
    }

    report = {
        "schema_version": 1,
        "experiment": MODEL_VERSION,
        "data": final_model["training_data"],
        "weak_supervision": {
            "label_source": "campo category dos feeds oficiais",
            "feature_fields": ["name", "description"],
            "label_distribution": dict(sorted(label_distribution.items())),
            "distribution_by_restaurant": source_distribution,
            "excluded_by_category": dict(sorted(Counter(item["category"] for item in excluded).items())),
            "exact_duplicate_records_beyond_first": duplicate_groups,
        },
        "split": {
            "strategy": "stratified_group_holdout_by_normalized_name_fingerprint",
            "seed": seed,
            "requested_test_ratio": test_ratio,
            "train_examples": len(train),
            "test_examples": len(test),
            "train_test_fingerprint_overlap": len(
                {record["fingerprint"] for record in train} & {record["fingerprint"] for record in test}
            ),
        },
        "metrics": {
            "stratified_group_holdout": holdout_metrics,
            "leave_one_restaurant_out": leave_one_restaurant_out(records),
        },
        "artifact": {
            "path": str(DEFAULT_MODEL.relative_to(ROOT)),
            "algorithm": final_model["algorithm"],
            "training_examples": final_model["training_examples"],
            "vocabulary_size": len(final_model["vocabulary"]),
        },
        "intended_use": "baseline experimental para normalização ampla de categorias de cardápio",
        "not_for": ["alergias", "restrições médicas", "decisões automáticas sobre disponibilidade"],
    }
    return final_model, report


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def train_command(args: argparse.Namespace) -> int:
    model, report = build_artifacts(args.data, seed=args.seed, test_ratio=args.test_ratio)
    write_json(args.model, model)
    report["artifact"]["path"] = str(args.model.relative_to(ROOT)) if args.model.is_relative_to(ROOT) else str(args.model)
    write_json(args.report, report)
    print(json.dumps({
        "model": str(args.model),
        "report": str(args.report),
        "holdout": report["metrics"]["stratified_group_holdout"],
        "leave_one_restaurant_out": report["metrics"]["leave_one_restaurant_out"].get("aggregate"),
    }, ensure_ascii=False, indent=2))
    return 0


def evaluate_command(args: argparse.Namespace) -> int:
    _, report = build_artifacts(args.data, seed=args.seed, test_ratio=args.test_ratio)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def predict_command(args: argparse.Namespace) -> int:
    model = json.loads(args.model.read_text(encoding="utf-8"))
    result = predict(model, args.text)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)

    for name, handler in (("train", train_command), ("evaluate", evaluate_command)):
        command = commands.add_parser(name)
        command.add_argument("--data", type=Path, default=DEFAULT_DATA)
        command.add_argument("--seed", default=DEFAULT_SEED)
        command.add_argument("--test-ratio", type=float, default=DEFAULT_TEST_RATIO)
        if name == "train":
            command.add_argument("--model", type=Path, default=DEFAULT_MODEL)
            command.add_argument("--report", type=Path, default=DEFAULT_REPORT)
        command.set_defaults(handler=handler)

    prediction = commands.add_parser("predict")
    prediction.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    prediction.add_argument("--text", required=True)
    prediction.set_defaults(handler=predict_command)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
