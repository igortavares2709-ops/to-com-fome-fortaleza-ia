const state = {
  meta: null,
  results: [],
  selected: 0,
  location: null,
  map: null,
  markerLayer: null,
  mapMarkers: [],
  localBasemapReady: false,
  localBasemapLayers: [],
  searchController: null,
  searchSequence: 0,
  hasSearched: false,
  userInteracted: false,
};
const $ = (id) => document.getElementById(id);
const FORTALEZA_CENTER = [-3.7319, -38.5267];
const DEFAULT_MARKER_STYLE = { radius: 7, color: "#fffdf8", weight: 3, fillColor: "#e94f37", fillOpacity: 1 };
const SELECTED_MARKER_STYLE = { radius: 10, color: "#fffdf8", weight: 4, fillColor: "#174c42", fillOpacity: 1 };

function make(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

async function api(path, body, signal) {
  let response;
  try {
    response = await fetch(path, body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    } : { signal });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new Error("Não foi possível conectar ao serviço. Verifique sua conexão e tente novamente.");
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(response.ok
      ? "O serviço respondeu de forma inesperada. Tente novamente em instantes."
      : "O serviço está temporariamente indisponível. Tente novamente em instantes.");
  }
  if (!response.ok) {
    throw new Error(typeof payload?.error === "string" && payload.error.trim()
      ? payload.error
      : "Não foi possível concluir a busca.");
  }
  return payload;
}

function prefersReducedMotion() {
  return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

function option(value, label = value) {
  const element = make("option", label);
  element.value = value;
  return element;
}

function formatSnapshot(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T12:00:00Z`))
    .replace(" de ", " ")
    .replace(" de ", " ");
}

function populateMeta(meta) {
  state.meta = meta;
  $("stat-restaurants").textContent = meta.record_count.toLocaleString("pt-BR");
  $("stat-neighborhoods").textContent = meta.neighborhoods.length.toLocaleString("pt-BR");
  $("stat-menus").textContent = `${meta.coverage.menus.toLocaleString("pt-BR")} / ${meta.coverage.prices.toLocaleString("pt-BR")}`;
  $("stat-date").textContent = formatSnapshot(meta.snapshot_date);
  for (const neighborhood of meta.neighborhoods) $("neighborhood-filter").append(option(neighborhood));
  for (const cuisine of meta.cuisines) $("cuisine-filter").append(option(cuisine, cuisine.replace(/\b\w/g, (letter) => letter.toUpperCase())));
  for (const restaurant of meta.restaurant_options || []) {
    $("partner-restaurant").append(option(
      restaurant.id,
      `${restaurant.name} · ${restaurant.neighborhood || "bairro não informado"}`,
    ));
  }
  $("google-source-option").hidden = !meta.google_places_available;
}

function intentEntries(intent) {
  const values = [];
  if (intent.neighborhood) values.push(`bairro: ${intent.neighborhood}`);
  if (intent.cuisines?.length) values.push(`culinária: ${intent.cuisines.join(", ")}`);
  if (intent.radiusKm) values.push(`raio: ${intent.radiusKm} km`);
  if (intent.maxPriceCents) values.push(`até ${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(intent.maxPriceCents / 100)}`);
  if (intent.delivery) values.push("delivery confirmado");
  if (intent.takeaway) values.push("retirada confirmada");
  if (intent.wheelchair) values.push("acessibilidade confirmada");
  if (intent.vegetarian) values.push("vegetariano confirmado");
  if (intent.vegan) values.push("vegano confirmado");
  if (intent.halal) values.push("halal confirmado");
  if (intent.openNow) values.push("aberto agora");
  if (intent.menuAvailable) values.push("cardápio publicado");
  return values;
}

function renderIntent(intent) {
  const container = $("intent-chips");
  container.replaceChildren();
  const entries = intentEntries(intent);
  if (!entries.length) container.append(make("span", "busca ampla", "intent-chip empty"));
  for (const value of entries) container.append(make("span", value, "intent-chip"));
  const unsupported = intent.unsupported || [];
  $("unsupported").hidden = !unsupported.length;
  $("unsupported").textContent = unsupported.length
    ? `Este snapshot não possui ${unsupported.join(" ou ")}; esses critérios não foram usados.`
    : "";
}

function evidenceRow(label, value) {
  const row = make("div", undefined, "evidence");
  row.append(make("span", label));
  const text = value === "confirmado" ? "sim" : value === "não oferece" ? "não" : "não informado";
  const className = value === "confirmado" ? "yes" : value === "não informado" ? "unknown" : "";
  row.append(make("b", text, className));
  return row;
}

function actionLink(label, href, className = "") {
  const safe = safeUrl(href);
  if (!safe) return null;
  const link = make("a", label, className);
  link.href = safe;
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

function showMapStatus(message = "") {
  const status = $("map-status");
  status.hidden = !message;
  status.textContent = message;
}

async function loadLocalBasemap() {
  if (!state.map || state.localBasemapReady) return state.localBasemapReady;
  const dataUrl = globalThis.__STATIC_PAGES__
    ? "data/fortaleza-basemap.json?v=20261111-map4"
    : "/data/fortaleza-basemap.json?v=20261111-map4";
  try {
    const response = await fetch(dataUrl);
    if (!response.ok) throw new Error("Base local indisponível");
    const data = await response.json();
    if (!Array.isArray(data.features)) throw new Error("Base local inválida");
    const pane = state.map.getPane("fallbackBasemap") || state.map.createPane("fallbackBasemap");
    pane.style.zIndex = "150";
    pane.style.pointerEvents = "none";
    const renderer = L.canvas({ pane: "fallbackBasemap", padding: 0.25, tolerance: 3 });
    const boundaryLayer = L.geoJSON(data, {
      pane: "fallbackBasemap",
      renderer,
      interactive: false,
      filter: (feature) => feature?.properties?.kind === "boundary",
      style: {
        color: "#8ca79f",
        weight: 1,
        opacity: 0.75,
        fillColor: "#f5f1e8",
        fillOpacity: 1,
      },
    }).addTo(state.map);

    const landcoverStyles = {
      water: { color: "#92c8d2", fillColor: "#b9dfe7" },
      green: { color: "#bed5b5", fillColor: "#dcebd2" },
      sports: { color: "#becaa8", fillColor: "#d7dfbf" },
      beach: { color: "#ddc995", fillColor: "#f3dfb7" },
      cemetery: { color: "#aebfa4", fillColor: "#cdd9c5" },
    };
    const landcoverLayer = L.geoJSON(data, {
      pane: "fallbackBasemap",
      renderer,
      interactive: false,
      filter: (feature) => feature?.properties?.kind === "landcover",
      style: (feature) => ({
        ...(landcoverStyles[feature?.properties?.class] || landcoverStyles.green),
        weight: 0.65,
        opacity: 0.72,
        fillOpacity: 0.88,
      }),
    }).addTo(state.map);

    const roadStyles = {
      motorway: { minZoom: 10, casing: "#c77a40", casingWeight: 5.4, color: "#efb16b", weight: 3.5 },
      trunk: { minZoom: 10, casing: "#c77a40", casingWeight: 5.2, color: "#efb16b", weight: 3.4 },
      primary: { minZoom: 10, casing: "#d2a35f", casingWeight: 4.6, color: "#fff0bd", weight: 2.8 },
      secondary: { minZoom: 10, casing: "#b9a98c", casingWeight: 3.6, color: "#fffdf7", weight: 2 },
      tertiary: { minZoom: 11, casing: "#c5beb1", casingWeight: 3, color: "#fffdf8", weight: 1.6 },
      local: { minZoom: 12, casing: "#d4cfc5", casingWeight: 1.8, color: "#fffdfb", weight: 0.9 },
    };
    const roadStyle = (feature, casing) => {
      const roadClass = feature?.properties?.highway?.replace("_link", "") || "local";
      const style = roadStyles[roadClass] || roadStyles.local;
      const zoom = state.map.getZoom();
      const visible = zoom >= style.minZoom;
      const zoomScale = zoom >= 14 ? 1.15 : zoom <= 11 ? 0.88 : 1;
      const opacity = roadClass === "local" ? (zoom === 12 ? 0.42 : 0.76) : 0.96;
      return {
        color: casing ? style.casing : style.color,
        weight: visible ? (casing ? style.casingWeight : style.weight) * zoomScale : 0,
        opacity: visible ? opacity : 0,
        lineCap: "round",
        lineJoin: "round",
      };
    };
    const roadOptions = {
      pane: "fallbackBasemap",
      renderer,
      interactive: false,
      filter: (feature) => feature?.properties?.kind === "road",
    };
    const roadCasings = L.geoJSON(data, {
      ...roadOptions,
      style: (feature) => roadStyle(feature, true),
    }).addTo(state.map);
    const roadSurfaces = L.geoJSON(data, {
      ...roadOptions,
      style: (feature) => roadStyle(feature, false),
    }).addTo(state.map);
    const refreshRoadStyles = () => {
      roadCasings.setStyle((feature) => roadStyle(feature, true));
      roadSurfaces.setStyle((feature) => roadStyle(feature, false));
    };
    state.map.on("zoomend", refreshRoadStyles);
    state.localBasemapLayers = [boundaryLayer, landcoverLayer, roadCasings, roadSurfaces];
    state.localBasemapReady = true;
    return true;
  } catch {
    return false;
  }
}

function initMap() {
  if (state.map) return true;
  if (!globalThis.L) {
    showMapStatus("O mapa interativo não pôde ser carregado. A lista de restaurantes continua disponível.");
    return false;
  }

  state.map = L.map("result-map", {
    center: FORTALEZA_CENTER,
    zoom: 12,
    minZoom: 10,
    maxZoom: 18,
    maxBounds: [[-4.2, -39.1], [-3.3, -37.8]],
    maxBoundsViscosity: 0.8,
    scrollWheelZoom: false,
    keyboard: true,
    zoomControl: true,
  });
  state.markerLayer = L.layerGroup().addTo(state.map);
  L.control.scale({ imperial: false, metric: true, maxWidth: 110, position: "bottomright" }).addTo(state.map);

  let tileErrors = 0;
  let tileCycleHadError = false;
  const tileUrl = globalThis.__STATIC_PAGES__
    ? "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    : "/api/map-tiles/{z}/{x}/{y}.png";
  const tiles = L.tileLayer(tileUrl, {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
  });
  tiles.on("loading", () => {
    tileErrors = 0;
    tileCycleHadError = false;
  });
  tiles.on("tileerror", () => {
    tileErrors += 1;
    tileCycleHadError = true;
    showMapStatus(state.localBasemapReady
      ? "Mapa local ativo. Alguns nomes e detalhes podem não aparecer."
      : "As ruas não carregaram, mas os pontos e a lista continuam disponíveis.");
  });
  tiles.on("load", () => {
    if (!tileCycleHadError) showMapStatus();
  });
  tiles.addTo(state.map);
  loadLocalBasemap().then((loaded) => {
    if (loaded && tileErrors > 0) {
      showMapStatus("Mapa local ativo. Alguns nomes e detalhes podem não aparecer.");
    }
  });
  return true;
}

function updateMarkerSelection() {
  for (const entry of state.mapMarkers) {
    const selected = entry.index === state.selected;
    entry.marker.setStyle(selected ? SELECTED_MARKER_STYLE : DEFAULT_MARKER_STYLE);
    if (selected) entry.marker.bringToFront();
  }
}

function selectResult(index, scroll = false, centerMap = false) {
  if (!Number.isInteger(index) || index < 0 || index >= state.results.length) return;
  state.selected = index;
  document.querySelectorAll(".result-card").forEach((card, position) => card.classList.toggle("selected", position === index));
  updateMarkerSelection();
  const entry = state.mapMarkers.find((candidate) => candidate.index === index);
  if (centerMap && entry && state.map) {
    const destination = entry.marker.getLatLng();
    const zoom = Math.max(state.map.getZoom(), 15);
    if (prefersReducedMotion()) state.map.setView(destination, zoom, { animate: false });
    else state.map.flyTo(destination, zoom, { duration: 0.45 });
  }
  if (scroll) {
    document.querySelectorAll(".result-card")[index]?.scrollIntoView({
      block: "nearest",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }
}

function renderMap(results) {
  if (!initMap() || !state.markerLayer) return;
  state.markerLayer.clearLayers();
  state.mapMarkers = [];
  const bounds = [];
  $("location-legend").hidden = !state.location;

  if (state.location) {
    const userPoint = [state.location.latitude, state.location.longitude];
    const userMarker = L.circleMarker(userPoint, {
      radius: 7,
      color: "#fffdf8",
      weight: 3,
      fillColor: "#2b6cb0",
      fillOpacity: 1,
    }).addTo(state.markerLayer);
    userMarker.bindTooltip("Sua localização", { direction: "top", offset: [0, -7] });
    bounds.push(userPoint);
  }

  results.forEach((item, index) => {
    if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) return;
    const point = [item.latitude, item.longitude];
    const marker = L.circleMarker(point, index === 0 ? SELECTED_MARKER_STYLE : DEFAULT_MARKER_STYLE).addTo(state.markerLayer);
    const label = make("span", `${item.name} · ${item.neighborhood || "bairro não informado"}`);
    marker.bindTooltip(label, { direction: "top", offset: [0, -8] });
    marker.on("click", () => selectResult(index, true));
    const markerElement = marker.getElement();
    if (markerElement) {
      markerElement.setAttribute("tabindex", "0");
      markerElement.setAttribute("role", "button");
      markerElement.setAttribute("aria-label", `${item.name}, ${item.neighborhood || "bairro não informado"}`);
      markerElement.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectResult(index, true);
        }
      });
    }
    state.mapMarkers.push({ index, marker });
    bounds.push(point);
  });

  requestAnimationFrame(() => {
    state.map.invalidateSize();
    if (!bounds.length) {
      state.map.setView(FORTALEZA_CENTER, 12, { animate: false });
    } else if (bounds.length === 1) {
      state.map.setView(bounds[0], 15, { animate: !prefersReducedMotion() });
    } else {
      state.map.fitBounds(bounds, { padding: [36, 36], maxZoom: 14, animate: !prefersReducedMotion() });
    }
  });
}

function renderCard(item, index) {
  const card = make("article", undefined, `result-card${index === 0 ? " selected" : ""}`);
  const heading = make("div", undefined, "result-name-row");
  heading.append(make("h3", item.name));
  if (item.distanceKm !== null) heading.append(make("span", `${item.distanceKm.toLocaleString("pt-BR")} km`, "distance"));
  card.append(heading, make("p", item.address || item.neighborhood || "Endereço não informado", "address"));

  const tags = make("div", undefined, "tag-row");
  for (const value of item.cuisines.slice(0, 4)) tags.append(make("span", value, "tag"));
  if (!item.cuisines.length) tags.append(make("span", "culinária não informada", "tag"));
  if (item.openNow === true) tags.append(make("span", "aberto agora", "tag"));
  card.append(tags);

  const evidence = make("div", undefined, "evidence-grid");
  evidence.append(
    evidenceRow("Delivery", item.evidence.delivery),
    evidenceRow("Retirada", item.evidence.takeaway),
    evidenceRow("Acessibilidade", item.evidence.wheelchair),
    evidenceRow("Cardápio", item.menu_url || item.officialMenu?.items?.length ? "confirmado" : "não informado"),
  );
  card.append(evidence, make("p", `Por que apareceu: ${item.reasons.join(" · ")}`, "why"));

  if (item.officialMenu?.items?.length) {
    const menuBox = make("div", undefined, `price-preview${item.officialMenu.stale ? " stale" : ""}`);
    const menuHeader = make("div", undefined, "price-preview-head");
    menuHeader.append(
      make("strong", "Preços da fonte oficial"),
      make("span", item.officialMenu.stale ? "última coleta falhou" : "atualizado na coleta diária"),
    );
    menuBox.append(menuHeader);
    const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
    for (const menuItem of item.officialMenu.items.filter((entry) => entry.available).slice(0, 3)) {
      const row = make("div", undefined, "price-row");
      row.append(make("span", menuItem.name), make("b", money.format(menuItem.price_cents / 100)));
      menuBox.append(row);
    }
    card.append(menuBox);
  }

  const actions = make("div", undefined, "result-actions");
  const showOnMap = make("button", "Mostrar no mapa", "map-focus-button");
  showOnMap.type = "button";
  showOnMap.setAttribute("aria-label", `Mostrar ${item.name} no mapa`);
  showOnMap.addEventListener("click", () => selectResult(index, false, true));
  const menuUrl = item.menu_url || item.officialMenu?.source_url;
  const menu = menuUrl ? actionLink("Ver cardápio ↗", menuUrl, "menu") : null;
  const source = actionLink("Ver fonte OSM ↗", item.source.url);
  const website = item.website ? actionLink("Site oficial ↗", item.website) : null;
  actions.append(showOnMap);
  for (const action of [menu, website, source]) if (action) actions.append(action);
  card.append(actions);
  card.addEventListener("click", (event) => {
    if (!event.target.closest("a, button")) selectResult(index, false, true);
  });
  return card;
}

function renderSuggestions(values) {
  const box = $("suggestions");
  box.replaceChildren();
  box.hidden = !values?.length;
  if (!values?.length) return;
  box.append(make("strong", "Nenhuma opção atende a todos os filtros."));
  const list = make("ul");
  for (const value of values) list.append(make("li", value));
  box.append(list);
}

function googleBusinessStatus(value) {
  return {
    OPERATIONAL: "em funcionamento",
    CLOSED_TEMPORARILY: "fechado temporariamente",
    CLOSED_PERMANENTLY: "fechado permanentemente",
  }[value] || null;
}

function renderGooglePlaces(provider) {
  const section = $("google-results-section");
  const list = $("google-results");
  const note = $("google-results-note");
  list.replaceChildren();
  if (!provider || provider.status === "disabled" || provider.status === "not_configured") {
    section.hidden = true;
    return;
  }

  section.hidden = false;
  if (provider.status !== "ok") {
    note.textContent = "A consulta ao Google Maps está temporariamente indisponível. Os resultados da base local continuam válidos.";
    return;
  }

  note.textContent = provider.resultCount
    ? `${provider.resultCount} ${provider.resultCount === 1 ? "resultado consultado" : "resultados consultados"} agora, sem armazenamento local.`
    : "O Google Maps não retornou outra opção para esta busca.";
  for (const place of provider.results || []) {
    const card = make("article", undefined, "google-result-card");
    card.append(make("h4", place.name), make("p", place.address || "Endereço não informado pelo Google Maps"));
    const meta = make("div", undefined, "google-result-meta");
    for (const value of [place.primaryType, googleBusinessStatus(place.businessStatus)].filter(Boolean)) {
      meta.append(make("span", value));
    }
    if (meta.childElementCount) card.append(meta);
    const mapsUrl = safeUrl(place.mapsUrl);
    if (mapsUrl) card.append(actionLink("Abrir no Google Maps ↗", mapsUrl));
    if (place.attributions?.length) {
      const attribution = make("div", "Fonte adicional: ", "google-attributions");
      place.attributions.forEach((source, index) => {
        if (index) attribution.append(document.createTextNode(" · "));
        const sourceUrl = safeUrl(source.url);
        attribution.append(sourceUrl ? actionLink(source.name, sourceUrl) : document.createTextNode(source.name));
      });
      card.append(attribution);
    }
    list.append(card);
  }
}

function renderResponse(payload) {
  state.results = payload.results;
  state.selected = 0;
  $("mode-badge").textContent = payload.mode;
  $("result-count").textContent = `${payload.resultCount} ${payload.resultCount === 1 ? "opção encontrada" : "opções encontradas"}`;
  renderIntent(payload.intent);
  const list = $("results");
  list.replaceChildren();
  for (const [index, item] of payload.results.entries()) list.append(renderCard(item, index));
  if (!payload.results.length) list.append(make("p", "A base não confirmou nenhuma opção com todos esses critérios.", "address"));
  renderMap(payload.results);
  renderSuggestions(payload.suggestions);
  renderGooglePlaces(payload.googlePlaces);
}

async function search() {
  state.hasSearched = true;
  state.searchController?.abort();
  const controller = new AbortController();
  const sequence = ++state.searchSequence;
  state.searchController = controller;
  $("notice").textContent = "";
  $("mode-badge").textContent = "Interpretando…";
  $("results-panel").setAttribute("aria-busy", "true");
  try {
    const payload = await api("/api/search", {
      query: $("query").value,
      neighborhood: $("neighborhood-filter").value,
      cuisine: $("cuisine-filter").value,
      latitude: state.location?.latitude,
      longitude: state.location?.longitude,
      useGooglePlaces: Boolean(
        state.userInteracted && state.meta?.google_places_available && $("google-source").checked,
      ),
    }, controller.signal);
    if (sequence !== state.searchSequence || controller.signal.aborted) return false;
    renderResponse(payload);
    return true;
  } catch (error) {
    if (error?.name === "AbortError" || sequence !== state.searchSequence) return false;
    $("notice").textContent = error.message;
    $("mode-badge").textContent = "Busca indisponível";
    return false;
  } finally {
    if (sequence === state.searchSequence) {
      $("results-panel").setAttribute("aria-busy", "false");
      state.searchController = null;
    }
  }
}

$("query").addEventListener("input", () => { state.userInteracted = true; });
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.userInteracted = true;
  search();
});
for (const button of document.querySelectorAll("[data-query]")) {
  button.addEventListener("click", async () => {
    state.userInteracted = true;
    $("query").value = button.dataset.query;
    const succeeded = await search();
    if (button.classList.contains("craving-card") && succeeded) {
      const explorer = document.querySelector(".explorer");
      $("results-title").focus({ preventScroll: true });
      explorer?.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  });
}
$("neighborhood-filter").addEventListener("change", () => { state.userInteracted = true; search(); });
$("cuisine-filter").addEventListener("change", () => { state.userInteracted = true; search(); });
$("google-source").addEventListener("change", () => { state.userInteracted = true; search(); });
$("menu-source-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const status = $("partner-form-status");
  const restaurantId = $("partner-restaurant").value;
  const restaurant = state.meta?.restaurant_options?.find((item) => item.id === restaurantId);
  const sourceUrl = $("partner-source-url").value.trim();
  const format = $("partner-format").value;
  const notes = $("partner-notes").value.trim();
  const authorized = $("partner-authorization").checked;
  const allowedFormats = new Set(["csv_v1", "json_v1", "jsonld_menu"]);

  if (!restaurant) {
    status.textContent = "Selecione um restaurante cadastrado em Fortaleza.";
    return;
  }
  let parsedUrl;
  try {
    parsedUrl = new URL(sourceUrl);
  } catch {
    status.textContent = "Informe uma URL pública válida.";
    return;
  }
  const hostname = parsedUrl.hostname.toLowerCase();
  if (parsedUrl.protocol !== "https:") {
    status.textContent = "A fonte precisa usar HTTPS.";
    return;
  }
  if (hostname === "ifood.com" || hostname.endsWith(".ifood.com") || hostname === "ifood.com.br" || hostname.endsWith(".ifood.com.br")) {
    status.textContent = "Use uma fonte oficial do restaurante; páginas do iFood não são aceitas.";
    return;
  }
  if (!allowedFormats.has(format) || !authorized) {
    status.textContent = "Confirme o formato e a autorização da fonte.";
    return;
  }

  const body = [
    "### Restaurante",
    "",
    restaurant.name,
    "",
    "### ID do restaurante",
    "",
    restaurant.id,
    "",
    "### URL pública do cardápio",
    "",
    parsedUrl.href,
    "",
    "### Formato",
    "",
    format,
    "",
    "### Autorização",
    "",
    "- [x] Confirmo que represento o restaurante ou tenho autorização para disponibilizar e atualizar esta fonte.",
    "- [x] Confirmo que a fonte não contém dados pessoais, credenciais nem conteúdo copiado do iFood.",
    "",
    "### Observações",
    "",
    notes || "_No response_",
  ].join("\n");
  const issueUrl = new URL("https://github.com/igortavares2709-ops/to-com-fome-fortaleza-ia/issues/new");
  issueUrl.searchParams.set("title", `Cardápio: ${restaurant.name}`);
  issueUrl.searchParams.set("body", body);
  status.textContent = "Abrindo a solicitação no GitHub…";
  window.open(issueUrl.href, "_blank", "noopener,noreferrer");
});
$("location-button").addEventListener("click", () => {
  state.userInteracted = true;
  if (!navigator.geolocation) {
    $("notice").textContent = "Este navegador não oferece geolocalização.";
    return;
  }
  $("location-button").textContent = "Obtendo localização…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      state.location = { latitude: position.coords.latitude, longitude: position.coords.longitude };
      $("location-button").textContent = "✓ Localização usada nesta busca";
      search();
    },
    () => {
      $("notice").textContent = "A localização não foi autorizada. Você ainda pode escolher um bairro.";
      $("location-button").textContent = "⌖ Usar minha localização";
    },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
  );
});

$("query").value = "restaurantes com cardápio disponível";
api("/api/meta")
  .then((meta) => {
    populateMeta(meta);
    if (!state.userInteracted && !state.hasSearched) search();
  })
  .catch((error) => {
    if (!state.userInteracted && !state.hasSearched) search();
    if (!state.hasSearched) $("notice").textContent = error.message;
  });
