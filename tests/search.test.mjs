import test from "node:test";
import assert from "node:assert/strict";
import dataset from "../data/restaurants.json" with { type: "json" };
import menuData from "../data/menu-items.json" with { type: "json" };
import worker, {
  haversineKm,
  isOpenAt,
  parseIntent,
  searchGooglePlaces,
  searchRestaurants,
  validateAiIntent,
} from "../src/worker.mjs";

test("interpreta bairro, culinária, serviço, menu e raio", () => {
  const intent = parseIntent("Pizza no Meireles com delivery, cardápio e raio de 3 km");
  assert.equal(intent.neighborhood, "Meireles");
  assert.deepEqual(intent.cuisines, ["pizza"]);
  assert.equal(intent.delivery, true);
  assert.equal(intent.menuAvailable, true);
  assert.equal(intent.radiusKm, 3);
});

test("usa preços oficiais e ainda rejeita avaliações ausentes", () => {
  const intent = parseIntent("Quero jantar por até R$ 40 com nota 5");
  assert.equal(intent.maxPriceCents, 4000);
  assert.deepEqual(intent.unsupported, ["avaliações"]);
});

test("não confunde distância, quantidade de pessoas ou palavras parciais com filtros", () => {
  const radius = parseIntent("restaurantes até 5 km");
  assert.equal(radius.radiusKm, 5);
  assert.equal(radius.maxPriceCents, null);
  assert.equal(parseIntent("pizza por 2 pessoas").maxPriceCents, null);
  assert.deepEqual(parseIntent("um lugar com massagem").cuisines, []);
  assert.equal(parseIntent("restaurantes em 1,5 km").radiusKm, 1.5);
  assert.deepEqual(parseIntent("quero pizzas artesanais").cuisines, ["pizza"]);
});

test("reconhece preço apenas com contexto monetário", () => {
  assert.equal(parseIntent("orçamento até 35,50").maxPriceCents, 3550);
  assert.equal(parseIntent("preço de até 35,50").maxPriceCents, 3550);
  assert.equal(parseIntent("quero gastar 28 reais").maxPriceCents, 2800);
  assert.equal(parseIntent("jantar menos de 3 km").maxPriceCents, null);
});

test("respeita negações e não permite que a IA reative esses filtros", () => {
  const cases = [
    ["sem delivery", "delivery"],
    ["não quero retirada", "takeaway"],
    ["não preciso de acessibilidade", "wheelchair"],
    ["não quero opção vegetariana", "vegetarian"],
    ["sem opção vegana", "vegan"],
    ["não precisa ser halal", "halal"],
    ["não precisa estar aberto agora", "openNow"],
    ["sem cardápio", "menuAvailable"],
  ];
  for (const [query, field] of cases) {
    const fallback = parseIntent(query);
    assert.equal(fallback[field], false, `${query} não deve ativar ${field}`);
    assert.ok(fallback.negated.includes(field), `${query} deve registrar a negação`);
    assert.equal(validateAiIntent({ [field]: true }, fallback)[field], false);
  }
  const combined = parseIntent("sem delivery com retirada");
  assert.equal(combined.delivery, false);
  assert.equal(combined.takeaway, true);
  const nested = parseIntent("não quero restaurante com delivery, mas quero retirada");
  assert.equal(nested.delivery, false);
  assert.ok(nested.negated.includes("delivery"));
  assert.equal(nested.takeaway, true);
  assert.equal(parseIntent("sem delivery, mas com delivery").delivery, true);
});

test("respeita culinárias negadas e reconhece dietas no plural", () => {
  for (const query of ["sem pizza", "não quero pizza"]) {
    const fallback = parseIntent(query);
    assert.deepEqual(fallback.cuisines, []);
    assert.ok(fallback.negatedCuisines.includes("pizza"));
    assert.deepEqual(validateAiIntent({ cuisines: ["pizza"] }, fallback).cuisines, []);
  }
  assert.equal(parseIntent("quero opções veganas").vegan, true);
  assert.equal(parseIntent("restaurantes veganos").vegan, true);
  assert.equal(parseIntent("quero opções vegetarianas").vegetarian, true);
  assert.equal(parseIntent("restaurantes vegetarianos").vegetarian, true);
});

test("busca por menu só retorna links publicados", () => {
  const intent = parseIntent("restaurantes com cardápio disponível");
  const results = searchRestaurants(intent);
  assert.ok(results.length > 0);
  assert.ok(results.every(
    (item) => /^https?:\/\//.test(item.menu_url) ||
      (/^https?:\/\//.test(item.officialMenu?.source_url) && item.officialMenu.items.length > 0),
  ));
});

test("filtro de delivery exige confirmação explícita", () => {
  const results = searchRestaurants(parseIntent("pizza no Meireles com delivery"));
  assert.ok(results.length > 0);
  assert.ok(results.every((item) => item.delivery === "yes"));
});

test("distância geográfica é calculada em quilômetros", () => {
  const distance = haversineKm(-3.731, -38.526, -3.731, -38.516);
  assert.ok(distance > 1 && distance < 1.2);
});

test("interpreta um horário simples sem alegar suporte universal", () => {
  assert.equal(isOpenAt("Mo-Fr 11:00-15:00", new Date("2026-11-09T15:00:00Z")), true);
  assert.equal(isOpenAt("evento especial", new Date("2026-11-09T15:00:00Z")), null);
});

test("interpreta horários compostos e virada da meia-noite", () => {
  assert.equal(isOpenAt("Mo-Fr 11:00-15:00,18:00-22:00", new Date("2026-11-09T23:00:00Z")), true);
  assert.equal(isOpenAt("Fr 18:00-02:00", new Date("2026-11-07T04:00:00Z")), true);
  assert.equal(isOpenAt("Fr 18:00-02:00", new Date("2026-11-07T06:00:00Z")), false);
  assert.equal(isOpenAt("Sa 12:00-00:00, Su 12:00-22:00", new Date("2026-11-08T18:00:00Z")), true);
  assert.equal(isOpenAt("Mo-Fr 11:00-15:00; feriados consultar", new Date("2026-11-09T20:00:00Z")), null);
  assert.equal(isOpenAt("Mo-Fr 11:00-15:00", new Date("2026-11-09T18:00:00Z")), false);
  assert.equal(isOpenAt("Fr 18:00-02:00", new Date("2026-11-07T05:00:00Z")), false);
});

test("IA parcial complementa sem apagar os filtros determinísticos", () => {
  const fallback = parseIntent("pizza no Meireles com delivery em 3 km por até R$ 40 com nota 5");
  const merged = validateAiIntent({
    neighborhood: "Aldeota",
    radiusKm: 10,
    maxPriceBRL: 20,
    unsupported: [],
  }, fallback);
  assert.ok(merged.cuisines.includes("pizza"));
  assert.equal(merged.neighborhood, "Meireles");
  assert.equal(merged.radiusKm, 3);
  assert.equal(merged.delivery, true);
  assert.equal(merged.maxPriceCents, 4000);
  assert.deepEqual(merged.unsupported, ["avaliações"]);
});

test("IA rejeita booleanos usados como raio ou preço", () => {
  const merged = validateAiIntent({ radiusKm: true, maxPriceBRL: true }, parseIntent("pizza"));
  assert.equal(merged.radiusKm, null);
  assert.equal(merged.maxPriceCents, null);
});

test("raio sem bairro ou localização não é ignorado", () => {
  const intent = parseIntent("restaurantes em um raio de 1 km");
  assert.equal(intent.radiusKm, 1);
  assert.deepEqual(searchRestaurants(intent), []);
});

test("snapshot possui fonte e cardápios rastreáveis", () => {
  assert.ok(dataset.restaurants.length >= 700);
  assert.ok(dataset.restaurants.every((item) => item.source?.url));
  assert.ok(dataset.restaurants.filter((item) => item.menu_url).length >= 15);
});

test("preços têm fonte oficial e cobertura declarada", () => {
  assert.equal(menuData.meta.restaurants_with_prices, 2);
  const items = Object.values(menuData.menus).flatMap((menu) => menu.items);
  assert.ok(items.length >= 250);
  assert.ok(items.every((item) => Number.isInteger(item.price_cents) && item.source_url.startsWith("https://")));
});

test("API expõe metadados e busca estruturada", async () => {
  const metaResponse = await worker.fetch(new Request("https://example.test/api/meta"));
  const meta = await metaResponse.json();
  assert.equal(meta.record_count, dataset.restaurants.length);
  assert.ok(meta.coverage.menus >= 15);
  assert.equal(meta.restaurant_options.length, dataset.restaurants.length);
  assert.ok(meta.restaurant_options.every((item) => item.id && item.name));

  const searchResponse = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://example.test" },
    body: JSON.stringify({ query: "restaurantes com cardápio" }),
  }));
  const payload = await searchResponse.json();
  assert.equal(searchResponse.status, 200);
  assert.ok(payload.results.every((item) => item.menu_url || item.officialMenu?.items?.length));
  assert.equal(payload.mode, "interpretação local");
  assert.equal(payload.googlePlaces.status, "disabled");
});

test("consulta Google Places somente quando solicitada e não expõe a chave", async () => {
  const originalFetch = globalThis.fetch;
  let upstream = null;
  globalThis.fetch = async (url, init) => {
    upstream = { url: String(url), init };
    return new Response(JSON.stringify({
      places: [
        {
          id: "ChIJ-fortaleza-1",
          displayName: { text: "Restaurante Teste" },
          formattedAddress: "Avenida Beira Mar, Fortaleza - CE, Brasil",
          googleMapsUri: "https://maps.google.com/?cid=123",
          primaryTypeDisplayName: { text: "Restaurante" },
          businessStatus: "OPERATIONAL",
          attributions: [{ displayName: "Fonte parceira", uri: "https://example.org/fonte" }],
        },
        {
          id: "ChIJ-fechado-2",
          displayName: { text: "Restaurante Encerrado" },
          businessStatus: "CLOSED_PERMANENTLY",
        },
        { id: "sem-nome" },
      ],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  try {
    const result = await searchGooglePlaces("pizza no Meireles", {
      GOOGLE_PLACES_API_KEY: "server-secret",
    }, { enabled: true });
    assert.equal(result.status, "ok");
    assert.equal(result.resultCount, 1);
    assert.equal(result.results[0].name, "Restaurante Teste");
    assert.equal(result.results[0].mapsUrl, "https://maps.google.com/?cid=123");
    assert.equal("latitude" in result.results[0], false);
    assert.equal(JSON.stringify(result).includes("server-secret"), false);
    assert.equal(upstream.url, "https://places.googleapis.com/v1/places:searchText");
    assert.equal(upstream.init.headers["X-Goog-Api-Key"], "server-secret");
    assert.match(upstream.init.headers["X-Goog-FieldMask"], /places\.displayName/);
    const requestBody = JSON.parse(upstream.init.body);
    assert.equal(requestBody.includedType, "restaurant");
    assert.equal(requestBody.strictTypeFiltering, true);
    assert.match(requestBody.textQuery, /Fortaleza, Ceará, Brasil$/);
    assert.deepEqual(requestBody.locationRestriction.rectangle.low, {
      latitude: -3.91,
      longitude: -38.66,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Google Places permanece desativado sem opção ou sem chave", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("não deveria consultar a rede"); };
  try {
    assert.equal((await searchGooglePlaces("pizza", {}, { enabled: true })).status, "not_configured");
    assert.equal((await searchGooglePlaces("pizza", { GOOGLE_PLACES_API_KEY: "secret" })).status, "disabled");

    const response = await worker.fetch(new Request("https://example.test/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://example.test" },
      body: JSON.stringify({ query: "pizza", useGooglePlaces: false }),
    }), { GOOGLE_PLACES_API_KEY: "secret" });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.googlePlaces.status, "disabled");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("consulta externa exige origem do próprio aplicativo", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: "pizza", useGooglePlaces: true }),
  }), { GOOGLE_PLACES_API_KEY: "secret" });
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "Origem não permitida para a consulta externa.");
});

test("falha do Google Places não interrompe a busca local", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("quota exceeded", { status: 429 });
  try {
    const response = await worker.fetch(new Request("https://example.test/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://example.test" },
      body: JSON.stringify({ query: "pizza", useGooglePlaces: true }),
    }), { GOOGLE_PLACES_API_KEY: "secret" });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.ok(payload.results.length > 0);
    assert.equal(payload.googlePlaces.status, "unavailable");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("API bloqueia POST de outra origem", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://malicious.test" },
    body: "{}",
  }));
  assert.equal(response.status, 403);
});

test("API mede o corpo real e rejeita JSON acima de 32 KB", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://example.test" },
    body: JSON.stringify({ query: "pizza", padding: "x".repeat(33_000) }),
  }));
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error, "O pedido excede 32 KB.");
});

test("API exige que o JSON seja um objeto", async () => {
  for (const body of ["null", "[]", '"texto"']) {
    const response = await worker.fetch(new Request("https://example.test/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://example.test" },
      body,
    }));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error, "Envie um objeto JSON válido.");
  }
});

test("API explica quando o raio não possui uma referência", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://example.test" },
    body: JSON.stringify({ query: "restaurantes em um raio de 1 km" }),
  }));
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.resultCount, 0);
  assert.ok(payload.suggestions.includes("Ative sua localização ou informe um bairro para usar o raio de busca."));
});

test("API não transforma coordenadas nulas em zero", async () => {
  const response = await worker.fetch(new Request("https://example.test/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://example.test" },
    body: JSON.stringify({ query: "restaurante", latitude: null, longitude: null }),
  }));
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.ok(payload.results.length > 0);
  assert.ok(payload.results.every((item) => item.distanceKm === null));
});

test("resposta vazia da IA preserva a interpretação local", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    choices: [{ message: { content: "{}" } }],
  }), { status: 200, headers: { "Content-Type": "application/json" } });
  try {
    const response = await worker.fetch(new Request("https://example.test/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://example.test" },
      body: JSON.stringify({ query: "pizza com delivery" }),
    }), { OPENAI_API_KEY: "test-key" });
    const payload = await response.json();
    assert.equal(payload.mode, "IA generativa");
    assert.ok(payload.intent.cuisines.includes("pizza"));
    assert.equal(payload.intent.delivery, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("proxy do mapa limita a área e conserva o cache da fonte", async () => {
  const originalFetch = globalThis.fetch;
  let upstreamUrl = null;
  globalThis.fetch = async (url) => {
    upstreamUrl = String(url);
    return new Response(new Uint8Array([137, 80, 78, 71]), {
      status: 200,
      headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=604800", ETag: '"tile-v1"' },
    });
  };

  try {
    const tile = await worker.fetch(new Request("https://example.test/api/map-tiles/12/1609/2090.png"));
    assert.equal(tile.status, 200);
    assert.equal(tile.headers.get("Content-Type"), "image/png");
    assert.equal(tile.headers.get("Cache-Control"), "public, max-age=604800");
    assert.equal(tile.headers.get("ETag"), '"tile-v1"');
    assert.equal(upstreamUrl, "https://tile.openstreetmap.org/12/1609/2090.png");

    upstreamUrl = null;
    const outside = await worker.fetch(new Request("https://example.test/api/map-tiles/12/1/1.png"));
    assert.equal(outside.status, 404);
    assert.equal(upstreamUrl, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
