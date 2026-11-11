import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const binaryAsset = (relative, mime) => ({
  base64: fs.readFileSync(path.join(root, relative)).toString("base64"),
  mime,
});
const dataset = JSON.parse(read("data/restaurants.json"));
const menuData = JSON.parse(read("data/menu-items.json"));
const assets = {
  "index.html": read("src/index.html"),
  "terms.html": read("src/terms.html"),
  "privacy.html": read("src/privacy.html"),
  "templates/cardapio.csv": read("src/templates/cardapio.csv"),
  "styles.css": read("src/styles.css"),
  "app.js": read("src/app.js"),
  "data/fortaleza-basemap.json": read("data/fortaleza-basemap.json"),
  "favicon.svg": read("src/favicon.svg"),
  "assets/hero-fortaleza.webp": binaryAsset("src/assets/hero-fortaleza.webp", "image/webp"),
  "assets/comida-regional.webp": binaryAsset("src/assets/comida-regional.webp", "image/webp"),
  "assets/pizza-artesanal.webp": binaryAsset("src/assets/pizza-artesanal.webp", "image/webp"),
  "assets/prato-vegano.webp": binaryAsset("src/assets/prato-vegano.webp", "image/webp"),
  "assets/moqueca-fortaleza.webp": binaryAsset("src/assets/moqueca-fortaleza.webp", "image/webp"),
  "assets/sobremesas-tropicais.webp": binaryAsset("src/assets/sobremesas-tropicais.webp", "image/webp"),
  "assets/google-maps-attribution.png": binaryAsset("src/assets/google-maps-attribution.png", "image/png"),
  "vendor/leaflet/leaflet.js": read("src/vendor/leaflet/leaflet.js"),
  "vendor/leaflet/leaflet.css": read("src/vendor/leaflet/leaflet.css"),
  "vendor/leaflet/LICENSE": read("src/vendor/leaflet/LICENSE"),
  "vendor/leaflet/images/layers.png": binaryAsset("src/vendor/leaflet/images/layers.png", "image/png"),
  "vendor/leaflet/images/layers-2x.png": binaryAsset("src/vendor/leaflet/images/layers-2x.png", "image/png"),
  "vendor/leaflet/images/marker-icon.png": binaryAsset("src/vendor/leaflet/images/marker-icon.png", "image/png"),
  "vendor/leaflet/images/marker-icon-2x.png": binaryAsset("src/vendor/leaflet/images/marker-icon-2x.png", "image/png"),
  "vendor/leaflet/images/marker-shadow.png": binaryAsset("src/vendor/leaflet/images/marker-shadow.png", "image/png"),
};
let worker = read("src/worker.mjs");
worker = worker.replace(
  'import dataset from "../data/restaurants.json" with { type: "json" };',
  `const dataset = ${JSON.stringify(dataset)};`,
);
worker = worker.replace(
  'import menuData from "../data/menu-items.json" with { type: "json" };',
  `const menuData = ${JSON.stringify(menuData)};`,
);
worker = worker.replace(
  "const ASSETS = /*__ASSETS__*/ null;",
  `const ASSETS = ${JSON.stringify(assets)};`,
);
if (
  worker.includes("/*__ASSETS__*/") ||
  worker.includes("import dataset") ||
  worker.includes("import menuData")
) {
  throw new Error("Build sentinels were not replaced.");
}
const output = path.join(root, "dist/server");
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, "index.js"), worker);
console.log(`Built Worker with ${dataset.restaurants.length} restaurants and ${dataset.restaurants.filter((item) => item.menu_url).length} menu links.`);
