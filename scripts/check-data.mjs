import assert from "node:assert/strict";
import dataset from "../data/restaurants.json" with { type: "json" };
import menuData from "../data/menu-items.json" with { type: "json" };
import feedData from "../data/official-menu-feeds.json" with { type: "json" };
import basemap from "../data/fortaleza-basemap.json" with { type: "json" };

const ALLOWED_FEED_FORMATS = new Set(["json_v1", "csv_v1", "jsonld_menu"]);
const ALLOWED_PERMISSION_BASES = new Set(["restaurant_provided", "restaurant_public_feed"]);

function isHttpUrl(value, { httpsOnly = false } = {}) {
  try {
    const parsed = new URL(value);
    return Boolean(parsed.hostname) && (httpsOnly ? parsed.protocol === "https:" : ["http:", "https:"].includes(parsed.protocol));
  } catch {
    return false;
  }
}

function assertIsoDate(value, label) {
  assert.equal(typeof value, "string", `${label} must be an ISO date`);
  assert.ok(Number.isFinite(Date.parse(value)), `${label} must be an ISO date`);
}

function sourceId(source) {
  const prefix = { node: "n", way: "w", relation: "r" }[source?.osm_type];
  return prefix && Number.isInteger(source?.osm_id) ? `${prefix}${source.osm_id}` : null;
}

const records = dataset.restaurants;
assert.equal(dataset.meta.record_count, records.length, "metadata count differs from records");
assert.ok(records.length >= 700, "expected a municipality-wide snapshot");
assert.equal(new Set(records.map((item) => item.id)).size, records.length, "IDs must be unique");
assert.match(dataset.meta.attribution, /OpenStreetMap/);

assert.equal(basemap.type, "FeatureCollection", "basemap must be GeoJSON");
assert.equal(basemap.meta.snapshot_date, dataset.meta.snapshot_date, "basemap and restaurants must use the same OSM snapshot");
assert.equal(basemap.meta.source_url, dataset.meta.source_url, "basemap and restaurants must use the same OSM source");
assert.equal(basemap.meta.license, "ODbL 1.0");
assert.match(basemap.meta.attribution, /OpenStreetMap/);
assert.equal(basemap.meta.feature_count, basemap.features.length, "basemap feature count is inconsistent");
assert.equal(basemap.features.filter((feature) => feature.properties?.kind === "boundary").length, 1);
const roadFeatures = basemap.features.filter((feature) => feature.properties?.kind === "road");
assert.ok(roadFeatures.length >= 4, "basemap must contain the main road classes");
const allowedRoads = new Set([
  "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
  "secondary", "secondary_link", "tertiary", "tertiary_link", "local",
]);
assert.ok(roadFeatures.every((feature) => allowedRoads.has(feature.properties.highway)), "unexpected basemap road class");
assert.ok(basemap.meta.local_street_groups >= 900, "basemap local-street coverage is unexpectedly small");
const landcoverFeatures = basemap.features.filter((feature) => feature.properties?.kind === "landcover");
assert.deepEqual(
  new Set(landcoverFeatures.map((feature) => feature.properties.class)),
  new Set(["water", "green", "sports", "beach", "cemetery"]),
  "basemap land-cover classes are incomplete",
);
assert.ok(Buffer.byteLength(JSON.stringify(basemap)) <= 900_000, "basemap exceeds the size budget");

for (const item of records) {
  assert.ok(item.name && item.name.length <= 200, "every record needs a bounded name");
  assert.ok(item.latitude >= -3.91 && item.latitude <= -3.68, `${item.name}: latitude outside Fortaleza`);
  assert.ok(item.longitude >= -38.66 && item.longitude <= -38.38, `${item.name}: longitude outside Fortaleza`);
  assert.ok(item.source?.url?.startsWith("https://www.openstreetmap.org/"), `${item.name}: missing OSM source`);
  assert.equal(item.source.license, "ODbL 1.0");
  assert.equal(sourceId(item.source), item.id, `${item.name}: ID does not match its primary OSM source`);
  assert.ok((item.additional_sources || []).every((source) => sourceId(source)), `${item.name}: invalid additional OSM source`);
  if (item.website) assert.ok(isHttpUrl(item.website), `${item.name}: website must be an HTTP URL`);
  if (item.menu_url) assert.ok(isHttpUrl(item.menu_url), `${item.name}: menu must be an HTTP URL`);
}

assert.ok(records.filter((item) => item.menu_url).length >= 15, "expected sourced menu links");
const restaurantIds = new Set(records.map((item) => item.id));
const aliases = new Map();
for (const restaurant of records) {
  for (const id of [restaurant.id, sourceId(restaurant.source), ...(restaurant.additional_sources || []).map(sourceId)]) {
    if (!id) continue;
    assert.ok(!aliases.has(id) || aliases.get(id) === restaurant.id, `OSM source ${id} belongs to multiple restaurants`);
    aliases.set(id, restaurant.id);
  }
}

assert.equal(feedData.version, 1, "unsupported official feed configuration version");
assert.ok(Array.isArray(feedData.feeds), "official feeds must be an array");
const configuredFeeds = new Map();
for (const feed of feedData.feeds) {
  const canonicalId = aliases.get(feed.restaurant_id);
  assert.ok(canonicalId, `feed references unknown restaurant ${feed.restaurant_id}`);
  assert.ok(!configuredFeeds.has(canonicalId), `duplicate feed for restaurant ${canonicalId}`);
  assert.ok(isHttpUrl(feed.url, { httpsOnly: true }), `${canonicalId}: feed must use public HTTPS`);
  assert.ok(ALLOWED_FEED_FORMATS.has(feed.format), `${canonicalId}: unsupported feed format`);
  assert.ok(ALLOWED_PERMISSION_BASES.has(feed.permission_basis), `${canonicalId}: invalid permission basis`);
  configuredFeeds.set(canonicalId, feed);
}

assert.equal(menuData.meta.schema_version, 1, "unsupported menu schema version");
assert.equal(menuData.meta.feed_count, feedData.feeds.length, "feed count metadata is inconsistent");
assert.equal(
  menuData.meta.successful_feeds + menuData.meta.failed_feeds,
  menuData.meta.feed_count,
  "successful and failed feed counts must cover every configured feed",
);
if (menuData.meta.feed_count) assertIsoDate(menuData.meta.generated_at, "menu generated_at");
assert.equal(Object.keys(menuData.menus).length, configuredFeeds.size, "menus must match the configured feeds");

for (const [restaurantId, menu] of Object.entries(menuData.menus)) {
  assert.ok(restaurantIds.has(restaurantId), `menu references unknown restaurant ${restaurantId}`);
  const configured = configuredFeeds.get(restaurantId);
  assert.ok(configured, `menu ${restaurantId} has no configured official feed`);
  assert.equal(typeof menu.stale, "boolean", `${restaurantId}: stale must be boolean`);
  assert.ok(isHttpUrl(menu.source_url, { httpsOnly: true }), `${restaurantId}: official feed must use HTTPS`);
  assert.ok(ALLOWED_PERMISSION_BASES.has(menu.permission_basis), `${restaurantId}: invalid menu permission basis`);
  if (!menu.stale) {
    assert.equal(menu.source_url, configured.url, `${restaurantId}: menu source differs from its configured feed`);
    assert.equal(menu.permission_basis, configured.permission_basis, `${restaurantId}: permission basis differs from configuration`);
  }
  assert.ok(Array.isArray(menu.items), `${restaurantId}: items must be an array`);
  if (menu.fetched_at !== null) assertIsoDate(menu.fetched_at, `${restaurantId}: fetched_at`);
  if (menu.stale) {
    assert.ok(typeof menu.last_error === "string" && menu.last_error, `${restaurantId}: stale feed needs last_error`);
    assertIsoDate(menu.last_attempt_at, `${restaurantId}: last_attempt_at`);
  } else {
    assert.equal(menu.last_error, null, `${restaurantId}: successful feed cannot have last_error`);
    assertIsoDate(menu.fetched_at, `${restaurantId}: fetched_at`);
    assert.ok(menu.items.length > 0, `${restaurantId}: successful feed cannot be empty`);
  }
  for (const item of menu.items) {
    assert.ok(item.name && item.name.length <= 180, `${restaurantId}: invalid menu item name`);
    assert.ok(Number.isInteger(item.price_cents) && item.price_cents > 0, `${restaurantId}: invalid price`);
    assert.equal(item.currency, "BRL");
    assert.equal(typeof item.available, "boolean", `${restaurantId}: availability must be boolean`);
    assert.equal(item.source_url, menu.source_url, `${restaurantId}: item source must match menu source`);
  }
}
assert.equal(
  menuData.meta.successful_feeds,
  Object.values(menuData.menus).filter((menu) => !menu.stale).length,
  "successful feed metadata is inconsistent",
);
assert.equal(
  menuData.meta.failed_feeds,
  Object.values(menuData.menus).filter((menu) => menu.stale).length,
  "failed feed metadata is inconsistent",
);
assert.equal(
  menuData.meta.restaurants_with_prices,
  Object.values(menuData.menus).filter((menu) => menu.items.some((item) => item.price_cents > 0)).length,
  "price coverage metadata is inconsistent",
);
console.log(
  `Data OK: ${records.length} restaurants, ${records.filter((item) => item.menu_url).length} menu links, ` +
    `${menuData.meta.restaurants_with_prices} official price feeds.`,
);
