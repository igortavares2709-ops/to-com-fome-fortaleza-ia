import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const dataset = JSON.parse(read("data/restaurants.json"));
const menuData = JSON.parse(read("data/menu-items.json"));

function replaceExactlyOnce(source, pattern, replacement, label) {
  if (pattern.global) throw new Error(`The ${label} pattern must not be global.`);
  const matches = source.match(new RegExp(pattern.source, `${pattern.flags}g`)) ?? [];
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one ${label} reference, found ${matches.length}.`);
  }
  return source.replace(pattern, typeof replacement === "function" ? replacement : () => replacement);
}

let worker = read("src/worker.mjs")
  .replace(
    'import dataset from "../data/restaurants.json" with { type: "json" };',
    `const dataset = ${JSON.stringify(dataset)};`,
  )
  .replace(
    'import menuData from "../data/menu-items.json" with { type: "json" };',
    `const menuData = ${JSON.stringify(menuData)};`,
  )
  .replace("const ASSETS = /*__ASSETS__*/ null;", "const ASSETS = null;")
  .replaceAll("export async function ", "async function ")
  .replaceAll("export function ", "function ")
  .replace("export default {", "const worker = {");

if (
  worker.includes("/*__ASSETS__*/") ||
  worker.includes('import dataset from "../data/restaurants.json"') ||
  worker.includes('import menuData from "../data/menu-items.json"') ||
  /(^|\n)export\s/m.test(worker)
) {
  throw new Error("Pages Worker sentinels were not replaced.");
}

const bridge = `
globalThis.__STATIC_PAGES__ = true;
const nativeFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = async (input, init) => {
  const target = typeof input === "string" ? input : input.url;
  if (typeof target === "string" && target.startsWith("/api/")) {
    return worker.fetch(new Request("https://pages.local" + target, init), {});
  }
  return nativeFetch(input, init);
};`;

let script = `${worker}\n${bridge}\n${read("src/app.js")}`.replace(/<\/script/gi, "<\\/script");
const css = read("src/styles.css").replace(/<\/style/gi, "<\\/style");
let html = read("src/index.html");
html = replaceExactlyOnce(
  html,
  /href="\/favicon\.svg(\?[^\"]*)?"/,
  (_, query = "") => `href="favicon.svg${query}"`,
  "favicon",
);
html = replaceExactlyOnce(
  html,
  /href="\/vendor\/leaflet\/leaflet\.css(\?[^\"]*)?"/,
  (_, query = "") => `href="vendor/leaflet/leaflet.css${query}"`,
  "Leaflet stylesheet",
);
html = replaceExactlyOnce(
  html,
  /src="\/vendor\/leaflet\/leaflet\.js(\?[^\"]*)?"/,
  (_, query = "") => `src="vendor/leaflet/leaflet.js${query}"`,
  "Leaflet script",
);
html = replaceExactlyOnce(
  html,
  /<link\s+rel="stylesheet"\s+href="\/styles\.css(?:\?[^\"]*)?"\s*\/?>/,
  `<style>${css}</style>`,
  "application stylesheet",
);
html = replaceExactlyOnce(
  html,
  /<script\s+src="\/app\.js(?:\?[^\"]*)?"\s*><\/script>/,
  `<script>${script}</script>`,
  "application script",
);
html = replaceExactlyOnce(
  html,
  /class="brand"\s+href="\/"/,
  'class="brand" href="#"',
  "home link",
);

if (/(?:href|src)="\/(?:favicon\.svg|styles\.css|app\.js|vendor\/leaflet\/)/.test(html)) {
  throw new Error("Pages artifact still contains an absolute application asset URL.");
}

const output = path.join(root, "pages");
fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, "index.html"), html);
fs.writeFileSync(path.join(output, ".nojekyll"), "");
fs.copyFileSync(path.join(root, "src/terms.html"), path.join(output, "terms.html"));
fs.copyFileSync(path.join(root, "src/privacy.html"), path.join(output, "privacy.html"));
fs.mkdirSync(path.join(output, "templates"), { recursive: true });
fs.copyFileSync(path.join(root, "src/templates/cardapio.csv"), path.join(output, "templates/cardapio.csv"));
fs.copyFileSync(path.join(root, "src/favicon.svg"), path.join(output, "favicon.svg"));
fs.mkdirSync(path.join(output, "data"), { recursive: true });
fs.copyFileSync(
  path.join(root, "data/fortaleza-basemap.json"),
  path.join(output, "data/fortaleza-basemap.json"),
);
fs.cpSync(path.join(root, "src/assets"), path.join(output, "assets"), { recursive: true });
fs.cpSync(path.join(root, "src/vendor"), path.join(output, "vendor"), { recursive: true });
console.log(`Built Pages artifact with ${dataset.restaurants.length} restaurants and optimized food photography.`);
