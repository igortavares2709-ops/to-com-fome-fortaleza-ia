import fs from "node:fs";
import assert from "node:assert/strict";

const [previousPath, currentPath] = process.argv.slice(2);
assert.ok(previousPath && currentPath, "Usage: node scripts/check-drift.mjs previous.json current.json");
const previous = JSON.parse(fs.readFileSync(previousPath, "utf8"));
const current = JSON.parse(fs.readFileSync(currentPath, "utf8"));
assert.ok(Array.isArray(previous.restaurants) && previous.restaurants.length, "previous snapshot has no restaurants");
assert.ok(Array.isArray(current.restaurants) && current.restaurants.length, "current snapshot has no restaurants");
const previousIds = previous.restaurants.map((restaurant) => restaurant.id);
const currentIds = current.restaurants.map((restaurant) => restaurant.id);
assert.equal(new Set(previousIds).size, previousIds.length, "previous snapshot contains duplicate restaurant IDs");
assert.equal(new Set(currentIds).size, currentIds.length, "current snapshot contains duplicate restaurant IDs");
const before = previous.restaurants.length;
const after = current.restaurants.length;
const ratio = after / before;
assert.ok(ratio >= 0.85, `Restaurant count dropped more than 15% (${before} → ${after}).`);
assert.ok(ratio <= 1.30, `Restaurant count grew more than 30% (${before} → ${after}).`);
const currentIdSet = new Set(currentIds);
const retained = new Set(previousIds.filter((id) => currentIdSet.has(id))).size;
const retention = retained / before;
assert.ok(
  retention >= 0.70,
  `Restaurant identity retention fell below 70% (${retained}/${before}, ${(retention * 100).toFixed(1)}%).`,
);
console.log(
  `Drift OK: ${before} → ${after} restaurants (${((ratio - 1) * 100).toFixed(1)}%); ` +
    `${(retention * 100).toFixed(1)}% of IDs retained.`,
);
