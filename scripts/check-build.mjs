import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const resolve = (relative) => path.join(root, relative);
const read = (relative) => fs.readFileSync(resolve(relative), "utf8");

for (const relative of [
  "dist/server/index.js",
  "pages/index.html",
  "pages/terms.html",
  "pages/privacy.html",
  "pages/templates/cardapio.csv",
  "pages/favicon.svg",
  "pages/data/fortaleza-basemap.json",
  "pages/vendor/leaflet/leaflet.js",
  "pages/vendor/leaflet/leaflet.css",
  "pages/vendor/leaflet/LICENSE",
]) {
  const stat = fs.statSync(resolve(relative));
  assert.ok(stat.isFile() && stat.size > 0, `${relative} is missing or empty`);
}

const workerSource = read("dist/server/index.js");
assert.doesNotMatch(workerSource, /\/\*__ASSETS__\*\/|import dataset|import menuData/);
assert.match(workerSource, /vendor\/leaflet\/LICENSE/);

const { default: worker } = await import(`${pathToFileURL(resolve("dist/server/index.js")).href}?smoke=${Date.now()}`);
const homeResponse = await worker.fetch(new Request("https://build.local/"), {});
assert.equal(homeResponse.status, 200);
assert.match(homeResponse.headers.get("content-type") ?? "", /text\/html/);

for (const policyPath of ["/terms.html", "/privacy.html"]) {
  const policyResponse = await worker.fetch(new Request(`https://build.local${policyPath}`), {});
  assert.equal(policyResponse.status, 200);
  assert.match(policyResponse.headers.get("content-type") ?? "", /text\/html/);
}

const templateResponse = await worker.fetch(new Request("https://build.local/templates/cardapio.csv"), {});
assert.equal(templateResponse.status, 200);
assert.match(templateResponse.headers.get("content-type") ?? "", /text\/csv/);
assert.match(await templateResponse.text(), /^name,price,description,category,available,currency/m);

const fallbackFavicon = await worker.fetch(new Request("https://build.local/favicon.ico"), {});
assert.equal(fallbackFavicon.status, 200);
assert.match(fallbackFavicon.headers.get("content-type") ?? "", /image\/svg\+xml/);

const metaResponse = await worker.fetch(new Request("https://build.local/api/meta"), {});
assert.equal(metaResponse.status, 200);
const meta = await metaResponse.json();
assert.ok(Number.isInteger(meta.record_count) && meta.record_count > 0);

const licenseResponse = await worker.fetch(
  new Request("https://build.local/vendor/leaflet/LICENSE"),
  {},
);
assert.equal(licenseResponse.status, 200);
assert.match(await licenseResponse.text(), /Redistribution and use in source and binary forms/);

const pagesHtml = read("pages/index.html");
assert.match(pagesHtml, /globalThis\.__STATIC_PAGES__ = true/);
assert.match(pagesHtml, /id="menu-source-form"/);
assert.match(pagesHtml, /templates\/cardapio\.csv/);
assert.doesNotMatch(
  pagesHtml,
  /(?:href|src)="\/(?:favicon\.svg|styles\.css|app\.js|vendor\/leaflet\/)/,
);
assert.doesNotMatch(read("pages/vendor/leaflet/leaflet.js"), /sourceMappingURL=leaflet\.js\.map/);
assert.match(read("pages/vendor/leaflet/LICENSE"), /Redistribution and use in source and binary forms/);

console.log("Build artifacts passed Worker and GitHub Pages smoke checks.");
