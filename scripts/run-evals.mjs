import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import menuData from "../data/menu-items.json" with { type: "json" };
import * as engine from "../src/worker.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const datasetPath = path.join(root, "evals/intent-cases.jsonl");
const datasetSource = fs.readFileSync(datasetPath, "utf8");
const cases = datasetSource
  .split(/\r?\n/)
  .filter((line) => line.trim())
  .map((line, index) => {
    try {
      return JSON.parse(line);
    } catch (error) {
      throw new Error(`Caso inválido na linha ${index + 1}: ${error.message}`);
    }
  });

const collectionFields = new Set(["cuisines", "negatedCuisines", "negated", "unsupported"]);
const normalizeToken = (value) => engine.normalize(String(value));
const normalizedSet = (values) => new Set(values.map(normalizeToken));
const round = (value, digits = 6) => Number(value.toFixed(digits));

function assertDatasetContract() {
  if (cases.length < 40 || cases.length > 60) {
    throw new Error(`O conjunto dourado deve ter entre 40 e 60 casos; recebeu ${cases.length}.`);
  }
  const ids = new Set();
  for (const item of cases) {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Cada caso deve ser um objeto.");
    if (typeof item.id !== "string" || !item.id) throw new Error("Todo caso precisa de id.");
    if (ids.has(item.id)) throw new Error(`ID duplicado: ${item.id}`);
    ids.add(item.id);
    if (typeof item.category !== "string" || !item.category) throw new Error(`${item.id}: category ausente.`);
    if (typeof item.query !== "string") throw new Error(`${item.id}: query deve ser string.`);
    if (!item.expected || typeof item.expected !== "object" || Array.isArray(item.expected)) {
      throw new Error(`${item.id}: expected deve ser objeto.`);
    }
  }
}

function valuesEqual(expected, actual) {
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) return false;
    const wanted = normalizedSet(expected);
    const received = normalizedSet(actual);
    return wanted.size === received.size && [...wanted].every((value) => received.has(value));
  }
  if (typeof expected === "number") return typeof actual === "number" && Math.abs(expected - actual) < 1e-9;
  return Object.is(expected, actual);
}

function requestedCuisineMatches(result, cuisines) {
  if (!cuisines.length) return true;
  const available = normalizedSet(result.cuisines || []);
  return cuisines.some((value) => available.has(normalizeToken(value)));
}

function constraintViolations(intent, result) {
  const violations = [];
  const resultCuisines = normalizedSet(result.cuisines || []);
  if (
    intent.neighborhood &&
    !intent.radiusKm &&
    normalizeToken(result.neighborhood) !== normalizeToken(intent.neighborhood)
  ) violations.push("neighborhood");
  if (!requestedCuisineMatches(result, intent.cuisines || [])) violations.push("cuisines");
  if ((intent.negatedCuisines || []).some((value) => resultCuisines.has(normalizeToken(value)))) {
    violations.push("negatedCuisines");
  }
  if (intent.delivery && !["yes", "only"].includes(result.delivery)) violations.push("delivery");
  if (intent.takeaway && !["yes", "only"].includes(result.takeaway)) violations.push("takeaway");
  if (intent.wheelchair && result.wheelchair !== "yes") violations.push("wheelchair");
  if (intent.vegetarian && !["yes", "only"].includes(result.diet?.vegetarian)) violations.push("vegetarian");
  if (intent.vegan && !["yes", "only"].includes(result.diet?.vegan)) violations.push("vegan");
  if (intent.halal && !["yes", "only"].includes(result.diet?.halal)) violations.push("halal");
  if (intent.openNow && result.openNow !== true) violations.push("openNow");
  if (
    intent.menuAvailable &&
    !result.menu_url &&
    !(Array.isArray(result.officialMenu?.items) && result.officialMenu.items.length)
  ) violations.push("menuAvailable");
  if (
    intent.maxPriceCents &&
    !menuData.menus?.[result.id]?.items?.some(
      (item) => item.available && item.price_cents <= intent.maxPriceCents,
    )
  ) violations.push("maxPriceCents");
  if (
    intent.radiusKm &&
    (!(typeof result.distanceKm === "number") || result.distanceKm > intent.radiusKm)
  ) violations.push("radiusKm");
  return violations;
}

function hasRetrievalConstraint(intent) {
  return Boolean(
    intent.neighborhood ||
    intent.cuisines?.length ||
    intent.negatedCuisines?.length ||
    intent.radiusKm ||
    intent.maxPriceCents ||
    intent.delivery ||
    intent.takeaway ||
    intent.wheelchair ||
    intent.vegetarian ||
    intent.vegan ||
    intent.halal ||
    intent.openNow ||
    intent.menuAvailable
  );
}

function percentile(values, quantile) {
  if (!values.length) return 0;
  const ordered = [...values].sort((a, b) => a - b);
  const index = Math.min(ordered.length - 1, Math.ceil(quantile * ordered.length) - 1);
  return ordered[index];
}

function evaluate() {
  assertDatasetContract();
  const slots = { total: 0, correct: 0 };
  const collections = { truePositive: 0, falsePositive: 0, falseNegative: 0 };
  const abstention = { total: 0, correct: 0 };
  const retrievalExpectations = { total: 0, correct: 0 };
  const constraints = { results: 0, valid: 0, violations: 0 };
  const evidence = { results: 0, grounded: 0 };
  let negativeCuisineViolations = 0;
  let exactCases = 0;
  const durationsMs = [];
  const failures = [];
  const categoryTotals = new Map();

  for (const item of cases) {
    const startedAt = performance.now();
    const intent = engine.parseIntent(item.query);
    const results = engine.searchRestaurants(intent, { now: new Date("2026-11-09T15:00:00.000Z") });
    durationsMs.push(performance.now() - startedAt);
    let caseCorrect = true;

    for (const [field, expected] of Object.entries(item.expected)) {
      slots.total += 1;
      const actual = intent[field];
      const correct = valuesEqual(expected, actual);
      if (correct) slots.correct += 1;
      else {
        caseCorrect = false;
        failures.push({ id: item.id, check: `intent.${field}`, expected, actual });
      }
      if (collectionFields.has(field) && Array.isArray(expected)) {
        const wanted = normalizedSet(expected);
        const received = normalizedSet(Array.isArray(actual) ? actual : []);
        for (const value of received) {
          if (wanted.has(value)) collections.truePositive += 1;
          else collections.falsePositive += 1;
        }
        for (const value of wanted) if (!received.has(value)) collections.falseNegative += 1;
      }
    }

    if (item.requiresAbstention) {
      abstention.total += 1;
      const expected = normalizedSet(item.expected.unsupported || []);
      const actual = normalizedSet(intent.unsupported || []);
      if (expected.size > 0 && [...expected].every((value) => actual.has(value))) abstention.correct += 1;
      else caseCorrect = false;
    }

    if (item.retrieval?.expectResults || item.retrieval?.expectNoResults) {
      retrievalExpectations.total += 1;
      const correct = item.retrieval.expectResults ? results.length > 0 : results.length === 0;
      if (correct) retrievalExpectations.correct += 1;
      else {
        caseCorrect = false;
        failures.push({
          id: item.id,
          check: item.retrieval.expectResults ? "retrieval.expectResults" : "retrieval.expectNoResults",
          expected: item.retrieval.expectResults ? "> 0" : 0,
          actual: results.length,
        });
      }
    }

    if (hasRetrievalConstraint(intent)) {
      for (const result of results) {
        const violations = constraintViolations(intent, result);
        constraints.results += 1;
        if (!violations.length) constraints.valid += 1;
        else {
          constraints.violations += 1;
          caseCorrect = false;
          failures.push({ id: item.id, check: "retrieval.constraints", resultId: result.id, actual: violations });
        }
        if (violations.includes("negatedCuisines")) negativeCuisineViolations += 1;
      }
    }

    for (const result of results) {
      evidence.results += 1;
      if (
        Array.isArray(result.reasons) &&
        result.reasons.length > 0 &&
        typeof result.source?.url === "string" &&
        /^https:\/\//.test(result.source.url)
      ) evidence.grounded += 1;
      else {
        caseCorrect = false;
        failures.push({ id: item.id, check: "retrieval.evidence", resultId: result.id });
      }
    }

    if (caseCorrect) exactCases += 1;
    const category = categoryTotals.get(item.category) || { total: 0, exact: 0 };
    category.total += 1;
    if (caseCorrect) category.exact += 1;
    categoryTotals.set(item.category, category);
  }

  const collectionPrecision = collections.truePositive /
    Math.max(1, collections.truePositive + collections.falsePositive);
  const collectionRecall = collections.truePositive /
    Math.max(1, collections.truePositive + collections.falseNegative);
  const collectionF1 = collectionPrecision + collectionRecall
    ? (2 * collectionPrecision * collectionRecall) / (collectionPrecision + collectionRecall)
    : 1;
  const metrics = {
    case_exact_rate: round(exactCases / cases.length),
    slot_exact_accuracy: round(slots.correct / Math.max(1, slots.total)),
    collection_micro_precision: round(collectionPrecision),
    collection_micro_recall: round(collectionRecall),
    collection_micro_f1: round(collectionF1),
    abstention_accuracy: round(abstention.correct / Math.max(1, abstention.total)),
    retrieval_expectation_accuracy: round(
      retrievalExpectations.correct / Math.max(1, retrievalExpectations.total),
    ),
    constraint_precision: round(constraints.valid / Math.max(1, constraints.results)),
    evidence_coverage: round(evidence.grounded / Math.max(1, evidence.results)),
    negative_cuisine_violations: negativeCuisineViolations,
  };
  const thresholds = {
    minimum_cases: 40,
    case_exact_rate: 0.92,
    slot_exact_accuracy: 0.96,
    collection_micro_f1: 0.96,
    abstention_accuracy: 1,
    retrieval_expectation_accuracy: 1,
    constraint_precision: 1,
    evidence_coverage: 1,
    negative_cuisine_violations: 0,
  };
  const gates = {
    minimum_cases: cases.length >= thresholds.minimum_cases,
    case_exact_rate: metrics.case_exact_rate >= thresholds.case_exact_rate,
    slot_exact_accuracy: metrics.slot_exact_accuracy >= thresholds.slot_exact_accuracy,
    collection_micro_f1: metrics.collection_micro_f1 >= thresholds.collection_micro_f1,
    abstention_accuracy: metrics.abstention_accuracy >= thresholds.abstention_accuracy,
    retrieval_expectation_accuracy:
      metrics.retrieval_expectation_accuracy >= thresholds.retrieval_expectation_accuracy,
    constraint_precision: metrics.constraint_precision >= thresholds.constraint_precision,
    evidence_coverage: metrics.evidence_coverage >= thresholds.evidence_coverage,
    negative_cuisine_violations:
      metrics.negative_cuisine_violations === thresholds.negative_cuisine_violations,
  };
  const passed = Object.values(gates).every(Boolean);

  return {
    report_version: 1,
    evaluation_mode: "local_deterministic",
    scope:
      "Conjunto de aceitação curado para o interpretador local e a recuperação estruturada; não mede qualidade geral de um LLM.",
    provider: "none",
    model: null,
    prompt_version:
      engine.AI_PROMPT_VERSION || engine.PROMPT_VERSION || "not_applicable_local_parser",
    schema_version:
      engine.AI_SCHEMA_VERSION || engine.INTENT_SCHEMA_VERSION || "not_applicable_local_parser",
    dataset: {
      path: "evals/intent-cases.jsonl",
      sha256: crypto.createHash("sha256").update(datasetSource).digest("hex"),
      cases: cases.length,
      categories: Object.fromEntries(
        [...categoryTotals.entries()]
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([name, value]) => [name, {
            cases: value.total,
            exact_rate: round(value.exact / value.total),
          }]),
      ),
    },
    counts: {
      exact_cases: exactCases,
      evaluated_slots: slots.total,
      correct_slots: slots.correct,
      abstention_cases: abstention.total,
      constrained_results: constraints.results,
      evidence_results: evidence.results,
    },
    metrics,
    latency_ms: {
      note: "Informativa; não participa dos gates porque varia conforme a máquina.",
      p50: round(percentile(durationsMs, 0.5), 3),
      p95: round(percentile(durationsMs, 0.95), 3),
    },
    thresholds,
    gates,
    passed,
    failures: failures.slice(0, 100),
  };
}

function summary(report) {
  const percent = (value) => `${(value * 100).toFixed(2)}%`;
  const lines = [
    `AI eval local: ${report.dataset.cases} casos / ${report.counts.evaluated_slots} slots`,
    `Exact cases: ${percent(report.metrics.case_exact_rate)}`,
    `Slot accuracy: ${percent(report.metrics.slot_exact_accuracy)}`,
    `Collection micro-F1: ${percent(report.metrics.collection_micro_f1)}`,
    `Abstention: ${percent(report.metrics.abstention_accuracy)}`,
    `Constraint precision: ${percent(report.metrics.constraint_precision)}`,
    `Evidence coverage: ${percent(report.metrics.evidence_coverage)}`,
    `Negation violations: ${report.metrics.negative_cuisine_violations}`,
    `Latency p50/p95 (informative): ${report.latency_ms.p50} ms / ${report.latency_ms.p95} ms`,
    report.passed ? "Gates: PASS" : "Gates: FAIL",
  ];
  if (!report.passed) {
    const failedGates = Object.entries(report.gates).filter(([, passed]) => !passed).map(([name]) => name);
    lines.push(`Failed gates: ${failedGates.join(", ")}`);
    for (const failure of report.failures.slice(0, 12)) lines.push(`- ${JSON.stringify(failure)}`);
  }
  return lines.join("\n");
}

const report = evaluate();
const args = process.argv.slice(2);
const jsonOnly = args.includes("--json");
const writeIndex = args.indexOf("--write-baseline");
if (writeIndex >= 0) {
  const relative = args[writeIndex + 1] || "evals/results/local-baseline.json";
  const outputPath = path.resolve(root, relative);
  if (!outputPath.startsWith(`${root}${path.sep}`)) throw new Error("O relatório deve ficar dentro do projeto.");
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify({ recorded_at: new Date().toISOString(), ...report }, null, 2)}\n`);
  if (!jsonOnly) console.log(`Baseline gravado em ${path.relative(root, outputPath)}.`);
}
if (jsonOnly) console.log(JSON.stringify(report, null, 2));
else console.log(summary(report));
if (!report.passed) process.exitCode = 1;
