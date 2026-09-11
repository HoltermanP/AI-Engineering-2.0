/* Kabelbed — fase 1-prototype. OpenLayers-kaart in RD New (EPSG:28992). */
"use strict";

/* ---------------------------------------------------------------- projectie */
proj4.defs("EPSG:28992",
  "+proj=sterea +lat_0=52.15616055555555 +lon_0=5.38763888888889 +k=0.9999079 " +
  "+x_0=155000 +y_0=463000 +ellps=bessel " +
  "+towgs84=565.417,50.3319,465.552,-0.398957,0.343988,-1.8774,4.0725 +units=m +no_defs");
ol.proj.proj4.register(proj4);
const RD = ol.proj.get("EPSG:28992");
RD.setExtent([-285401.92, 22598.08, 595401.92, 903401.92]);

/* ------------------------------------------------------------------- lagen */
const basisLagen = {};   // brt | grijs | lufo → ol.layer.Tile
const zoneLagen = {};    // natura | nnn | gwb | bodem | amk → ol.layer.Tile
const regionaleBodemLagen = [];  // regionale bodembronnen (één toggle)
let bgtLaag = null, dkkLaag = null;

async function wmtsLaag(capUrl, layerId, zIndex, visible) {
  const caps = new ol.format.WMTSCapabilities().read(await (await fetch(capUrl)).text());
  const opts = ol.source.WMTS.optionsFromCapabilities(caps,
    { layer: layerId, matrixSet: "EPSG:28992" });
  opts.crossOrigin = "anonymous";
  return new ol.layer.Tile({ source: new ol.source.WMTS(opts), zIndex, visible });
}

/* ------------------------------------------------------------ tekenbronnen */
const srcArea = new ol.source.Vector();
const srcStations = new ol.source.Vector();
const srcVia = new ol.source.Vector();
const srcForbidden = new ol.source.Vector();
const srcRoutes = new ol.source.Vector();
const srcSegments = new ol.source.Vector();
const srcCrossings = new ol.source.Vector();
const srcMoffen = new ol.source.Vector();
const srcBomen = new ol.source.Vector();
const srcWerkpakketten = new ol.source.Vector();
const srcHighlight = new ol.source.Vector();

const KLEUR_KLASSE = {
  0: "#b9bfba", 1: "#A5C495", 2: "#C4C8CC", 3: "#C9A0C4", 4: "#E8DCB8",
  5: "#4A5158", 6: "#E4B7A0", 7: "#d8d2be", 8: "#8DB6D6", 9: "#8674c6",
  10: "#caa", 11: "#A3302A", 12: "#5E8C61",
};
const KLEUR_SOORT = { water: "#3F6B8A", rijbaan: "#4A5158", spoor: "#6d5bb8" };

function stationStyle(f) {
  const idx = srcStations.getFeatures().indexOf(f) + 1;
  return new ol.style.Style({
    image: new ol.style.Circle({
      radius: 9, fill: new ol.style.Fill({ color: "#C8322B" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 2 }),
    }),
    text: new ol.style.Text({
      text: "MS" + idx, offsetY: -16, font: "600 12px 'IBM Plex Mono',monospace",
      fill: new ol.style.Fill({ color: "#C8322B" }),
      stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
    }),
  });
}

const BOOM_WORTELZONE_M = 2.5; // gelijk aan BOOM_WORTELZONE_M in engine.py

const lagen = {
  bomen: new ol.layer.Vector({
    source: srcBomen, zIndex: 19,
    style: (f, resolution) => [
      new ol.style.Style({
        image: new ol.style.Circle({
          radius: Math.max(3, (f.get("r") || BOOM_WORTELZONE_M) / resolution),
          fill: new ol.style.Fill({ color: "rgba(46,107,47,0.14)" }),
          stroke: new ol.style.Stroke({ color: "rgba(46,107,47,0.55)", width: 1 }),
        }),
      }),
      new ol.style.Style({
        image: new ol.style.Circle({
          radius: 2.5, fill: new ol.style.Fill({ color: "#2E6B2F" }),
        }),
      }),
    ],
  }),
  area: new ol.layer.Vector({
    source: srcArea, zIndex: 20,
    style: new ol.style.Style({
      stroke: new ol.style.Stroke({ color: "#1E2A33", width: 1.6, lineDash: [8, 5] }),
      fill: new ol.style.Fill({ color: "rgba(30,42,51,0.04)" }),
    }),
  }),
  forbidden: new ol.layer.Vector({
    source: srcForbidden, zIndex: 21,
    style: new ol.style.Style({
      stroke: new ol.style.Stroke({ color: "#A3302A", width: 1.5 }),
      fill: new ol.style.Fill({ color: "rgba(163,48,42,0.18)" }),
    }),
  }),
  segments: new ol.layer.Vector({
    source: srcSegments, zIndex: 22,
    style: f => new ol.style.Style({
      stroke: new ol.style.Stroke({
        color: KLEUR_KLASSE[f.get("klasse")] || "#999", width: 9,
        lineCap: "butt",
      }),
    }),
    opacity: 0.75,
  }),
  routes: new ol.layer.Vector({
    source: srcRoutes, zIndex: 23,
    style: f => f.get("actief")
      ? new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#C8322B", width: 3.5 }) })
      : new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#7C8994", width: 2, lineDash: [6, 5] }) }),
  }),
  crossings: new ol.layer.Vector({
    source: srcCrossings, zIndex: 25,
    style: f => {
      const kleur = KLEUR_SOORT[f.get("soort")] || "#333";
      if (f.getGeometry().getType() === "LineString") {
        // boring: exacte lijn van intrede- tot uittredepunt
        return [
          new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#fff", width: 7 }) }),
          new ol.style.Style({
            stroke: new ol.style.Stroke({ color: kleur, width: 4 }),
            text: new ol.style.Text({
              text: f.get("kort"), placement: "line", offsetY: -11,
              font: "600 10px 'IBM Plex Mono',monospace",
              fill: new ol.style.Fill({ color: "#1E2A33" }),
              stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
            }),
          }),
        ];
      }
      if (f.get("punttype")) {
        // intrede-/uittredepunt van een boring
        return new ol.style.Style({
          image: new ol.style.Circle({
            radius: 4.5, fill: new ol.style.Fill({ color: "#fff" }),
            stroke: new ol.style.Stroke({ color: kleur, width: 2.5 }),
          }),
          text: new ol.style.Text({
            text: f.get("punttype") === "intrede" ? "in" : "uit", offsetY: 13,
            font: "500 9px 'IBM Plex Mono',monospace",
            fill: new ol.style.Fill({ color: kleur }),
            stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
          }),
        });
      }
      return new ol.style.Style({
        image: new ol.style.Circle({
          radius: 7, fill: new ol.style.Fill({ color: kleur }),
          stroke: new ol.style.Stroke({ color: "#fff", width: 2 }),
        }),
        text: new ol.style.Text({
          text: f.get("kort"), offsetY: -14, font: "500 10px 'IBM Plex Mono',monospace",
          fill: new ol.style.Fill({ color: "#1E2A33" }),
          stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
        }),
      });
    },
  }),
  moffen: new ol.layer.Vector({
    source: srcMoffen, zIndex: 24,
    style: new ol.style.Style({
      image: new ol.style.RegularShape({
        points: 4, radius: 6, angle: Math.PI / 4,
        fill: new ol.style.Fill({ color: "#1E2A33" }),
        stroke: new ol.style.Stroke({ color: "#fff", width: 1.5 }),
      }),
    }),
  }),
  via: new ol.layer.Vector({
    source: srcVia, zIndex: 26,
    style: new ol.style.Style({
      image: new ol.style.RegularShape({
        points: 4, radius: 7, angle: 0,
        fill: new ol.style.Fill({ color: "#B8771E" }),
        stroke: new ol.style.Stroke({ color: "#fff", width: 2 }),
      }),
    }),
  }),
  stations: new ol.layer.Vector({ source: srcStations, zIndex: 27, style: stationStyle }),
  // werkpakket-labels (WP-01, …) halverwege elk tracédeel van station tot station
  werkpakketten: new ol.layer.Vector({
    source: srcWerkpakketten, zIndex: 28,
    style: f => new ol.style.Style({
      text: new ol.style.Text({
        text: f.get("label"), offsetY: 15,
        font: "600 11px 'IBM Plex Mono',monospace",
        fill: new ol.style.Fill({ color: "#1E5AA8" }),
        stroke: new ol.style.Stroke({ color: "#fff", width: 3 }),
      }),
    }),
  }),
  highlight: new ol.layer.Vector({
    source: srcHighlight, zIndex: 30,
    style: f => new ol.style.Style({
      stroke: new ol.style.Stroke({
        color: "#C8322B",
        width: f.getGeometry().getType().includes("Polygon") ? 3 : 5,
        lineDash: f.getGeometry().getType().includes("Polygon") ? [7, 5] : undefined,
      }),
      fill: new ol.style.Fill({ color: "rgba(200,50,43,.14)" }),
      image: new ol.style.Circle({
        radius: 13, stroke: new ol.style.Stroke({ color: "#C8322B", width: 3 }),
        fill: new ol.style.Fill({ color: "rgba(200,50,43,.15)" }),
      }),
    }),
  }),
};

/* -------------------------------------------------------------------- kaart */
const map = new ol.Map({
  target: "map",
  layers: Object.values(lagen),
  view: new ol.View({
    projection: RD, center: [155000, 463000], zoom: 8,
    minZoom: 3, maxZoom: 16,
  }),
});

(async () => {
  const brtCaps = "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0?request=GetCapabilities&service=WMTS";
  basisLagen.brt = await wmtsLaag(brtCaps, "standaard", 0, true);
  basisLagen.grijs = await wmtsLaag(brtCaps, "grijs", 0, false);
  basisLagen.lufo = await wmtsLaag(
    "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?request=GetCapabilities&service=WMTS",
    "Actueel_orthoHR", 0, false);
  bgtLaag = await wmtsLaag(
    "https://service.pdok.nl/lv/bgt/wmts/v1_0?request=GetCapabilities&service=WMTS",
    "standaardvisualisatie", 1, false);
  dkkLaag = new ol.layer.Tile({
    zIndex: 2, visible: false, opacity: 0.8,
    source: new ol.source.TileWMS({
      url: "https://service.pdok.nl/kadaster/kadastralekaart/wms/v5_0",
      params: { LAYERS: "Kadastralekaart", TILED: true }, crossOrigin: "anonymous",
    }),
  });
  // zonelagen (FO §2) — dezelfde bronnen die de backend in het kostenoppervlak
  // meeneemt, hier als visuele overlay
  const wmsOverlay = (url, layers, opacity = 0.55) => new ol.layer.Tile({
    zIndex: 3, visible: false, opacity,
    source: new ol.source.TileWMS({
      url, params: { LAYERS: layers, TILED: true }, crossOrigin: "anonymous",
    }),
  });
  zoneLagen.natura = wmsOverlay("https://service.pdok.nl/rvo/natura2000/wms/v1_0", "natura2000");
  zoneLagen.nnn = wmsOverlay("https://service.pdok.nl/provincies/natuurnetwerk-nederland/wms/v1_0", "PS.ProtectedSite");
  zoneLagen.gwb = wmsOverlay("https://service.pdok.nl/provincies/grondwaterbeschermingsgebieden/wms/v1_0", "AM.DrinkingWaterProtectionArea");
  zoneLagen.sld = wmsOverlay("https://service.pdok.nl/tno/bro-overheidsbesluit-bodemverontreiniging/wms/v1_0", "sld", 0.8);
  zoneLagen.sad = wmsOverlay("https://service.pdok.nl/tno/bro-milieuhygienisch-bodemonderzoek/wms/v1_0", "sad", 0.75);
  zoneLagen.bodem = wmsOverlay("https://gis.gdngeoservices.nl/standalone/services/blk_gdn/lks_blk_rd_v1/MapServer/WMSServer", "WBB_locaties", 0.8);
  zoneLagen.bodemdek = wmsOverlay("https://gis.gdngeoservices.nl/standalone/services/blk_gdn/lks_blk_rd_v1/MapServer/WMSServer", "Beschikbaarheid_gegevens", 0.5);
  // regionale bodembronnen (zelfde register als backend/pdok.py BODEM_REGIONAAL)
  regionaleBodemLagen.push(
    wmsOverlay("https://geodata.zuid-holland.nl/geoserver/bodem/wms",
               "BS_SPOEDLOCATIES,BS_BSB_TOT_LIJST_GEGEOCODEERD,WM_STORTLOCATIES", 0.8),
    wmsOverlay("https://maps.zaanstad.nl/geoserver/wms",
               "geo:nazca_verontreiniging_geo,geo:bodem_bodeminformatie_activiteiten", 0.7));
  regionaleBodemLagen.forEach(l => map.addLayer(l));
  zoneLagen.amk = wmsOverlay("https://data.geo.cultureelerfgoed.nl/openbaar/wms", "Archeologische_Monumentenkaart_2014", 0.65);
  [basisLagen.brt, basisLagen.grijs, basisLagen.lufo, bgtLaag, dkkLaag,
   ...Object.values(zoneLagen)].forEach(l => map.addLayer(l));
})();

document.querySelectorAll("input[name=basis]").forEach(r =>
  r.addEventListener("change", () => {
    for (const [k, l] of Object.entries(basisLagen)) l.setVisible(r.value === k && r.checked);
  }));
document.getElementById("lg-bgt").addEventListener("change", e => bgtLaag && bgtLaag.setVisible(e.target.checked));
document.getElementById("lg-dkk").addEventListener("change", e => dkkLaag && dkkLaag.setVisible(e.target.checked));
for (const [id, key] of [["lg-natura", "natura"], ["lg-nnn", "nnn"], ["lg-gwb", "gwb"],
                         ["lg-sld", "sld"], ["lg-sad", "sad"],
                         ["lg-bodem", "bodem"], ["lg-bodemdek", "bodemdek"], ["lg-amk", "amk"]])
  document.getElementById(id).addEventListener("change", e =>
    zoneLagen[key] && zoneLagen[key].setVisible(e.target.checked));
document.getElementById("lg-bodemreg").addEventListener("change", e =>
  regionaleBodemLagen.forEach(l => l.setVisible(e.target.checked)));
document.getElementById("lg-bomen").addEventListener("change", e => lagen.bomen.setVisible(e.target.checked));
document.getElementById("lg-seg").addEventListener("change", e => lagen.segments.setVisible(e.target.checked));
document.getElementById("lg-alt").addEventListener("change", e => {
  srcRoutes.getFeatures().forEach(f => { if (!f.get("actief")) f.set("verborgen", e.target.checked); });
  lagen.routes.setStyle(f => {
    if (f.get("actief"))
      return new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#C8322B", width: 3.5 }) });
    if (!e.target.checked) return null;
    return new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#7C8994", width: 2, lineDash: [6, 5] }) });
  });
});

/* ------------------------------------------------------------- tekentools */
let mode = "pan";
let drawInteractie = null;
// stations, via-punten en getekende vlakken zijn versleepbaar
[srcStations, srcVia, srcArea, srcForbidden].forEach(src =>
  map.addInteraction(new ol.interaction.Modify({ source: src })));

function setMode(nieuw) {
  mode = nieuw;
  document.querySelectorAll("button.tool").forEach(b =>
    b.classList.toggle("actief", b.dataset.mode === nieuw));
  if (drawInteractie) { map.removeInteraction(drawInteractie); drawInteractie = null; }
  if (nieuw === "area" || nieuw === "forbidden") {
    drawInteractie = new ol.interaction.Draw({
      source: nieuw === "area" ? srcArea : srcForbidden, type: "Polygon",
    });
    if (nieuw === "area")
      drawInteractie.on("drawstart", () => srcArea.clear());
    drawInteractie.on("drawend", () => { setTimeout(() => setMode("pan"), 50); updateUI(); });
    map.addInteraction(drawInteractie);
  }
  if (nieuw === "street") svOpen(null);
}
document.querySelectorAll("button.tool").forEach(b =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));
setMode("pan");

map.on("click", evt => {
  if (mode === "station" || mode === "via") {
    const f = new ol.Feature(new ol.geom.Point(evt.coordinate));
    (mode === "station" ? srcStations : srcVia).addFeature(f);
    updateUI();
  } else if (mode === "delete") {
    map.forEachFeatureAtPixel(evt.pixel, (f, layer) => {
      for (const src of [srcStations, srcVia, srcForbidden, srcArea])
        if (src.hasFeature(f)) { src.removeFeature(f); updateUI(); return true; }
      return false;
    }, { hitTolerance: 8 });
  } else if (mode === "street") {
    svOpen(evt.coordinate);
  } else if (mode === "pan") {
    let hit = null;
    map.forEachFeatureAtPixel(evt.pixel, f => {
      if (f.get("bor") || f.get("kr")) { hit = f; return true; }
      return false;
    }, { hitTolerance: 8, layerFilter: l => l === lagen.crossings });
    if (hit) toonKaartPopup(hit.get("bor"), hit.get("kr"), evt.coordinate);
    else sluitPopup();
  }
});
map.on("pointermove", evt => {
  if (mode !== "pan" || evt.dragging) return;
  const hit = map.hasFeatureAtPixel(evt.pixel,
    { hitTolerance: 8, layerFilter: l => l === lagen.crossings });
  map.getTargetElement().style.cursor = hit ? "pointer" : "";
});

/* --------------------------------------- popup: boring-/kruisingsgegevens */
const popupEl = document.createElement("div");
popupEl.id = "kaart-popup";
const kaartPopup = new ol.Overlay({
  element: popupEl, positioning: "bottom-center", offset: [0, -14],
  autoPan: { animation: { duration: 150 } },
});
map.addOverlay(kaartPopup);
function sluitPopup() { kaartPopup.setPosition(undefined); }

function popupRij(label, waarde) {
  return waarde == null || waarde === "" ? "" :
    `<div class="rij"><span>${label}</span><span>${waarde}</span></div>`;
}

function toonKaartPopup(b, k, coord) {
  const rd = p => `<span class="mono">${p.map(x => (+x).toFixed(1)).join(", ")}</span>`;
  let kop, inhoud;
  if (b) {
    const kr = resultaat
      ? (resultaat.varianten[actieveVariant].kruisingen || []).find(c => c.nr === b.kruising)
      : null;
    kop = `${b.nr} — ${b.type}`;
    const wtChip = b.werkterrein_oordeel && b.werkterrein_oordeel !== "n.v.t."
      ? `<span class="chip ${WT_CHIP[b.werkterrein_oordeel] || ""}">${b.werkterrein_oordeel}</span>` : "";
    inhoud =
      popupRij("Kruising", `${b.kruising} — ${b.obstakel}`) +
      popupRij("Noodzaak", b.noodzaak) +
      popupRij("Boorlengte", `${b.lengte_m} m` +
        (b.uitloop_m != null ? ` (uitloop ${b.uitloop_m} m)` : "")) +
      popupRij("Intrede (RD)", rd(b.intredepunt_rd)) +
      popupRij("Uittrede (RD)", rd(b.uittredepunt_rd)) +
      popupRij("Dekking-eis", b.dekking_eis) +
      popupRij("Mantelbuis", b.mantelbuis) +
      (b.recht === false ? popupRij("Rechtheid",
        `<span class="chip kritiek">niet recht</span> wijkt tot ${b.afwijking_recht_m} m af`) : "") +
      popupRij("Werkterrein", wtChip && (wtChip +
        (b.werkterrein_intrede_m2 != null
          ? ` in ${b.werkterrein_intrede_m2}/${b.werkterrein_intrede_eis_m2} m² · ` +
            `uit ${b.werkterrein_uittrede_m2}/${b.werkterrein_uittrede_eis_m2} m²` : ""))) +
      popupRij("Richtlijn", kr && kr.richtlijn) +
      popupRij("Bevoegd gezag", kr && kr.bevoegd_gezag) +
      (b.werkterrein_opmerking ? `<p class="opm">${b.werkterrein_opmerking}</p>` : "") +
      (b.type_oorspronkelijk
        ? `<p class="opm">Oorspronkelijk voorstel: ${b.type_oorspronkelijk}.</p>` : "");
  } else {
    kop = `${k.nr} — ${k.techniek}`;
    inhoud =
      popupRij("Soort", `${k.soort}, ${k.breedte_m} m haaks` +
        (k.kruislengte_m && k.kruislengte_m > k.breedte_m + 0.5
          ? ` (${k.kruislengte_m} m langs tracé)` : "")) +
      popupRij("Noodzaak", k.noodzaak) +
      popupRij("Richtlijn", k.richtlijn) +
      popupRij("Bevoegd gezag", k.bevoegd_gezag) +
      (k.detail ? `<p class="opm">${k.detail}</p>` : "");
  }
  popupEl.innerHTML =
    `<button class="sluit" title="Sluiten">×</button><h3>${kop}</h3>${inhoud}`;
  popupEl.querySelector(".sluit").addEventListener("click", sluitPopup);
  kaartPopup.setPosition(coord);
}

/* ------------------------------------------------------------ wegingsprofiel */
const WEIGHT_LABELS = {
  berm_groen: "berm / groenstrook", voetpad: "voetpad / trottoir", fietspad: "fietspad",
  parkeervlak: "parkeervlak", rijbaan: "rijbaan (parallel)", erf_prive: "privaat (erf/agrarisch)",
  overig_onverhard: "overig onverhard", onbekend: "onbekend terrein",
  natuur_groen: "bos / natuurlijk terrein",
  gesloten_verharding: "× gesloten verharding",
  natura2000_weg: "× Natura 2000 (via weg/berm)", nnn: "× Natuurnetwerk NL",
  grondwaterbescherming: "× grondwaterbescherming",
  bodem_verontreinigd: "× verontreinigd/nazorg (SLD)",
  bodem_verdacht: "× onderzoekslocatie (SAD/Wbb)",
  bodem_elders: "× bodemdata elders (signaal)", archeologie: "× archeologie (AMK)",
  boom_wortelzone: "× wortelzone bomen",
  water_kruising: "watergang (kruisprijs/m)",
  spoor_kruising: "spoor (kruisprijs/m)",
};
let defaultWeights = {};
async function laadDefaults() {
  const d = await (await fetch("api/defaults")).json();
  defaultWeights = d.weights;
  svKey = d.google_maps_key || "";
  const tbl = document.getElementById("weights-table");
  tbl.innerHTML = "";
  for (const [k, v] of Object.entries(defaultWeights)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${WEIGHT_LABELS[k] || k}</td>
      <td><input type="number" step="0.1" min="0" data-w="${k}" value="${v}"></td>`;
    tbl.appendChild(tr);
  }
}
document.getElementById("btn-weights-reset").addEventListener("click", () => {
  document.querySelectorAll("#weights-table input").forEach(i =>
    { i.value = defaultWeights[i.dataset.w]; });
});
function leesWeights() {
  const w = {};
  document.querySelectorAll("#weights-table input").forEach(i => {
    const v = parseFloat(i.value);
    if (!isNaN(v)) w[i.dataset.w] = v;
  });
  return w;
}

/* ----------------------------------------------------------------- rekenen */
let resultaat = null;
let actieveVariant = 0;
let actieveTab = "mca";

function coordsVanPolygon(src) {
  const f = src.getFeatures().find(x => !x.get("auto"));
  if (!f) return null;
  const ring = f.getGeometry().getCoordinates()[0];
  return ring.slice(0, ring.length - 1).map(c => [Math.round(c[0] * 100) / 100, Math.round(c[1] * 100) / 100]);
}
function puntenVan(src) {
  return src.getFeatures().map(f => {
    const c = f.getGeometry().getCoordinates();
    return [Math.round(c[0] * 100) / 100, Math.round(c[1] * 100) / 100];
  });
}

const MAX_TRACE_KM = 70;      // gelijk aan MAX_TRACE_KM in main.py
const CORRIDOR_VANAF_M = 2500; // gelijk aan CORRIDOR_VANAF_M in main.py

function hemelsbreedM() {
  const p = puntenVan(srcStations);
  let m = 0;
  for (let i = 1; i < p.length; i++)
    m += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]);
  return m;
}

function updateUI() {
  const n = srcStations.getFeatures().length;
  const m = hemelsbreedM();
  const teLang = m > MAX_TRACE_KM * 1000;
  document.getElementById("btn-compute").disabled = n < 2 || teLang;
  document.getElementById("tekenhint").textContent =
    n < 2 ? `Plaats minimaal twee MS-stations (nu ${n}). Projectgebied tekenen is optioneel.`
    : teLang
      ? `Tracé is hemelsbreed ${(m / 1000).toFixed(1)} km; het maximum is ${MAX_TRACE_KM} km.`
    : m > CORRIDOR_VANAF_M
      ? `${n} stations, hemelsbreed ${(m / 1000).toFixed(1)} km — lang tracé: berekening ` +
        `per deeltraject in een corridor rond de rechte lijn (tot ${MAX_TRACE_KM} km). ` +
        `Stuur het tracé zo nodig met via-punten.`
    : !srcArea.getFeatures().length
      ? `${n} stations; zoekgebied wordt automatisch rond de stations bepaald. Punten zijn versleepbaar.`
      : `${n} stations; volgorde = plaatsingsvolgorde (streng). Punten zijn versleepbaar.`;
  srcStations.changed();
}
[srcArea, srcStations].forEach(s => { s.on("addfeature", updateUI); s.on("removefeature", updateUI); });

const statusEl = document.getElementById("status");

async function bereken() {
  const btn = document.getElementById("btn-compute");
  btn.disabled = true;
  statusEl.textContent = hemelsbreedM() > CORRIDOR_VANAF_M
    ? "Lang tracé: datalagen ophalen en per deeltraject rekenen…\n(dit kan enkele minuten duren)"
    : "Datalagen ophalen bij PDOK en tracé rekenen…\n(eerste keer 30–90 s)";
  // voortgang van de backend tonen zolang de berekening loopt
  const poll = setInterval(async () => {
    try {
      const p = await (await fetch("api/progress")).json();
      if (p.actief && p.stap)
        statusEl.textContent = `Bezig (${Math.round(p.bezig_s)}s): ${p.stap}`;
    } catch (e) { /* voortgang is best effort */ }
  }, 2000);
  try {
    const body = {
      area: coordsVanPolygon(srcArea) || [],
      stations: puntenVan(srcStations),
      via: puntenVan(srcVia),
      forbidden: srcForbidden.getFeatures().map(f =>
        f.getGeometry().getCoordinates()[0].slice(0, -1)),
      weights: leesWeights(),
      variants: document.getElementById("opt-varianten").checked,
      haspel_m: parseFloat(document.getElementById("opt-haspel").value) || 500,
    };
    const r = await fetch("api/compute", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    resultaat = await r.json();
    actieveVariant = 0;
    statusEl.textContent =
      `Klaar. Datalagen ${resultaat.rekentijd_s.datalagen}s, route ${resultaat.rekentijd_s.route}s, ` +
      `cel ${resultaat.celgrootte_m} m` +
      (resultaat.modus === "corridor"
        ? ` · ${resultaat.deeltrajecten} deeltrajecten (corridor)` : "") +
      ((resultaat.corridor_verbreed || []).length
        ? `\nCorridor automatisch verbreed (blokkade omzeild) bij deeltraject ` +
          resultaat.corridor_verbreed.map(v => `${v.deeltraject} (±${v.breedte_m} m)`).join(", ")
        : "") +
      (resultaat.variant_fouten.length ? `\nNiet gelukt: ${resultaat.variant_fouten.map(f => f.variant).join(", ")}` : "") +
      (resultaat.laag_fouten && resultaat.laag_fouten.length
        ? `\nZonelagen niet geladen: ${resultaat.laag_fouten.map(f => f.split(":")[0]).join(", ")}` : "") +
      (resultaat.bodem_bronnen_regionaal && resultaat.bodem_bronnen_regionaal.length
        ? `\nRegionale bodembronnen actief: ${resultaat.bodem_bronnen_regionaal.join("; ")}` : "") +
      ((resultaat.bomen || []).length
        ? `\nBomen: ${resultaat.bomen.length} (${(resultaat.bomen_bronnen || []).join("; ")})`
        : "\nBomen: geen open bomendata in dit gebied (BGT-plustopografie leeg, " +
          "geen gemeentelijk register gekoppeld)");
    // automatisch afgeleid zoekgebied (of de corridor) tonen als er geen
    // gebied is getekend; gemarkeerd als "auto" zodat het niet als getekend
    // projectgebied wordt teruggestuurd bij een herberekening
    if (!srcArea.getFeatures().some(f => !f.get("auto")) && resultaat.gebied) {
      srcArea.getFeatures().filter(f => f.get("auto"))
        .forEach(f => srcArea.removeFeature(f));
      const f = new ol.Feature(geojson.readGeometry(resultaat.gebied));
      f.set("auto", true);
      srcArea.addFeature(f);
    }
    document.getElementById("gemeente-info").textContent =
      resultaat.gemeente ? "· " + resultaat.gemeente : "";
    document.getElementById("btn-exp-geojson").disabled = false;
    document.getElementById("btn-exp-xlsx").disabled = false;
    document.querySelectorAll(".btn-nota").forEach(b => { b.disabled = false; });
    toonResultaat();
    // bij een lang tracé het volledige resultaat in beeld brengen
    if (resultaat.modus === "corridor" && !srcRoutes.isEmpty())
      map.getView().fit(srcRoutes.getExtent(),
        { padding: [70, 70, 70, 70], duration: 500 });
  } catch (e) {
    statusEl.textContent = "Fout: " + e.message;
  } finally {
    clearInterval(poll);
    btn.disabled = false;
    updateUI();
  }
}
document.getElementById("btn-compute").addEventListener("click", bereken);

/* --------------------------------------------------------- resultaat tonen */
const geojson = new ol.format.GeoJSON();

function toonResultaat() {
  sluitPopup();
  srcRoutes.clear(); srcSegments.clear(); srcCrossings.clear(); srcMoffen.clear();
  srcBomen.clear(); srcWerkpakketten.clear(); srcHighlight.clear();
  svRouteGewijzigd();  // Street View-paneel meebewegen met variant/nieuw tracé
  if (!resultaat) return;
  (resultaat.bomen || []).forEach(c => {
    const f = new ol.Feature(new ol.geom.Point([c[0], c[1]]));
    f.set("r", c[2] || BOOM_WORTELZONE_M);
    srcBomen.addFeature(f);
  });
  resultaat.varianten.forEach((v, i) => {
    const f = new ol.Feature(geojson.readGeometry(v.route));
    f.set("actief", i === actieveVariant);
    f.set("variant", i);
    srcRoutes.addFeature(f);
  });
  const v = resultaat.varianten[actieveVariant];
  v.segmenten.forEach(s => {
    const f = new ol.Feature(geojson.readGeometry(s.geometry));
    f.set("klasse", s.klasse);
    srcSegments.addFeature(f);
  });
  const kortTechniek = t => t.includes("HDD") ? "HDD" : t.includes("Persing") ? "PERS"
    : t.includes("Nano") ? "NANO" : t.includes("Raket") ? "RAKET" : "OPEN";
  const metBoring = new Set((v.boringen || []).map(b => b.kruising));
  v.kruisingen.forEach(c => {
    if (metBoring.has(c.nr)) return;  // wordt als boorlijn intrede→uittrede getekend
    const f = new ol.Feature(new ol.geom.Point(c.punt));
    f.set("soort", c.soort);
    f.set("kort", kortTechniek(c.techniek));
    f.set("kr", c);
    srcCrossings.addFeature(f);
  });
  (v.boringen || []).forEach(b => {
    const soort = (b.obstakel || "").split(" ")[0];
    const lijn = new ol.Feature(new ol.geom.LineString(
      b.geometry ? b.geometry.coordinates : [b.intredepunt_rd, b.uittredepunt_rd]));
    lijn.set("soort", soort);
    lijn.set("kort", `${kortTechniek(b.type)} ${b.lengte_m} m`);
    lijn.set("bor", b);
    srcCrossings.addFeature(lijn);
    [["intrede", b.intredepunt_rd], ["uittrede", b.uittredepunt_rd]].forEach(([type, p]) => {
      const f = new ol.Feature(new ol.geom.Point(p));
      f.set("soort", soort);
      f.set("punttype", type);
      f.set("bor", b);
      srcCrossings.addFeature(f);
    });
  });
  v.moffen.forEach(m => srcMoffen.addFeature(new ol.Feature(new ol.geom.Point(m.punt))));
  (v.werkpakketten || []).forEach(w => {
    const lijn = geojson.readGeometry(w.geometry);
    const f = new ol.Feature(new ol.geom.Point(lijn.getCoordinateAt(0.5)));
    f.set("label", w.nr);
    srcWerkpakketten.addFeature(f);
  });

  const sel = document.getElementById("variant-select");
  sel.innerHTML = "";
  resultaat.varianten.forEach((vr, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent = `${vr.naam} — ${(vr.lengte_m / 1000).toFixed(2)} km`;
    if (i === actieveVariant) o.selected = true;
    sel.appendChild(o);
  });
  toonLegenda(v);
  toonTab();
}
document.getElementById("variant-select").addEventListener("change", e => {
  actieveVariant = parseInt(e.target.value, 10);
  toonResultaat();
});

function toonLegenda(v) {
  const el = document.getElementById("legenda");
  const klassen = [...new Set(v.segmenten.map(s => s.klasse))];
  const namen = { 0: "onbekend", 1: "berm/groen", 2: "voetpad", 3: "fietspad", 4: "parkeervlak",
    5: "rijbaan", 6: "privaat (erf/agrarisch)", 7: "onverhard", 8: "watergang", 9: "spoor",
    10: "pand", 11: "verboden", 12: "bos/natuur" };
  el.innerHTML = "<strong>Ligging segmenten</strong>" + klassen.map(k =>
    `<div class="rij"><span class="vlek" style="background:${KLEUR_KLASSE[k]}"></span>${namen[k]}</div>`
  ).join("");
  el.style.display = "block";
}

/* ------------------------------------------------------------------ panelen */
document.querySelectorAll("#tabs button").forEach(b =>
  b.addEventListener("click", () => {
    actieveTab = b.dataset.tab;
    document.querySelectorAll("#tabs button").forEach(x =>
      x.classList.toggle("actief", x === b));
    toonTab();
  }));
document.getElementById("paneel-toggle").addEventListener("click", e => {
  const p = document.getElementById("paneel");
  p.classList.toggle("dicht");
  e.target.textContent = p.classList.contains("dicht") ? "▴" : "▾";
});

function zoomNaar(geomOfPunt) {
  srcHighlight.clear();
  let geom;
  if (Array.isArray(geomOfPunt)) geom = new ol.geom.Point(geomOfPunt);
  else geom = geojson.readGeometry(geomOfPunt);
  srcHighlight.addFeature(new ol.Feature(geom));
  map.getView().fit(geom.getExtent(), { maxZoom: 13, padding: [60, 60, 60, 60], duration: 300 });
}

function tabel(headers, rijen, opRij) {
  const t = document.createElement("table");
  t.className = "register";
  t.innerHTML = "<thead><tr>" + headers.map(h => `<th>${h}</th>`).join("") + "</tr></thead>";
  const tb = document.createElement("tbody");
  rijen.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = r.cells.join("");
    if (r.zoom) {
      tr.className = "klik";
      tr.addEventListener("click", () => {
        tb.querySelectorAll("tr").forEach(x => x.classList.remove("geselecteerd"));
        tr.classList.add("geselecteerd");
        zoomNaar(r.zoom);
        if (opRij) opRij(i);
      });
    } else if (opRij) {
      tr.className = "klik";
      tr.addEventListener("click", () => opRij(i));
    }
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  // koppen van numerieke kolommen rechts uitlijnen, gelijk met de waarden
  const eersteRij = tb.querySelector("tr");
  if (eersteRij) [...eersteRij.children].forEach((cel, i) => {
    if (cel.classList.contains("num"))
      t.tHead.rows[0].cells[i]?.classList.add("num");
  });
  return t;
}
const td = x => `<td>${x ?? ""}</td>`;
const tdn = x => `<td class="num">${x ?? ""}</td>`;
const eur = x => "€ " + Number(x).toLocaleString("nl-NL");
const WT_CHIP = { voldoende: "info", onzeker: "waarschuwing", onvoldoende: "kritiek" };
function wtCel(wt) {
  if (!wt) return "—";
  let s = `<span class="chip ${WT_CHIP[wt.oordeel] || ""}">${wt.oordeel}</span>`;
  if (wt.intrede_m2 != null)
    s += `<br>in ${wt.intrede_m2}/${wt.intrede_eis_m2} m² · uit ${wt.uittrede_m2}/${wt.uittrede_eis_m2} m²`;
  if (wt.opmerking) s += `<br>${wt.opmerking}`;
  return s;
}

function toonTab() {
  const el = document.getElementById("paneel-inhoud");
  el.innerHTML = "";
  if (!resultaat) {
    el.innerHTML = '<p class="leeg">Nog geen berekend tracé.</p>';
    return;
  }
  const v = resultaat.varianten[actieveVariant];
  let t = null;
  if (actieveTab === "mca") {
    const best = Math.min(...resultaat.varianten.map(x => x.mca.kosten_eur));
    t = tabel(
      ["Variant", "Lengte", "Kruisingen (techniek)", "Privaat", "Percelen", "Verg.", "Kosten (ind.)", "Doorloop"],
      resultaat.varianten.map((x, i) => ({
        cells: [
          td((i === actieveVariant ? "▶ " : "") + x.mca.variant),
          tdn(x.mca.lengte_m + " m"),
          td(Object.entries(x.mca.kruisingen).map(([k, n]) => `${n}× ${k}`).join("<br>") || "—"),
          tdn(x.mca.meters_privaat_m + " m"),
          tdn(x.mca.aantal_percelen),
          tdn(x.mca.aantal_vergunningen),
          tdn((x.mca.kosten_eur === best ? "★ " : "") + eur(x.mca.kosten_eur)),
          tdn("≤ " + x.mca.doorlooptijd_wk + " wk"),
        ],
      })),
      i => { actieveVariant = i; toonResultaat(); });
  } else if (actieveTab === "werkpakketten") {
    t = tabel(["Nr", "Naam", "Van (m)", "Tot (m)", "Lengte"],
      (v.werkpakketten || []).map(w => ({
        cells: [td(w.nr), td(w.naam), tdn(w.chainage_van_m), tdn(w.chainage_tot_m),
          tdn(w.lengte_m + " m")],
        zoom: w.geometry,
      })));
    if (!(v.werkpakketten || []).length)
      el.innerHTML = '<p class="leeg">Geen werkpakketten — herbereken het tracé.</p>';
  } else if (actieveTab === "planning") {
    const rijen = v.planning || [];
    const maxWk = Math.max(1, ...rijen.map(r => r.eind_wk));
    t = tabel(["WP", "Fase", "Start", "Eind", "Duur", `Planning (t/m wk ${maxWk})`, "Toelichting"],
      rijen.map(r => ({
        cells: [td(r.werkpakket), td(r.fase), tdn("wk " + r.start_wk),
          tdn("wk " + r.eind_wk), tdn(r.duur_wk + " wk"),
          td(`<span class="balkspoor"><span class="balk${r.fase === "Uitvoering" ? " uitvoering" : ""}"` +
             ` style="left:${((r.start_wk - 1) / maxWk * 100).toFixed(1)}%;` +
             `width:${(r.duur_wk / maxWk * 100).toFixed(1)}%"></span></span>`),
          td(r.toelichting)],
      })));
    if (!rijen.length)
      el.innerHTML = '<p class="leeg">Geen planning — herbereken het tracé.</p>';
  } else if (actieveTab === "segmenten") {
    t = tabel(["Nr", "WP", "Ligging", "Van", "Tot", "Lengte"],
      v.segmenten.map(s => ({
        cells: [td(s.nr), td(s.werkpakket), td(s.ligging), tdn(s.van_m), tdn(s.tot_m), tdn(s.lengte_m + " m")],
        zoom: s.geometry,
      })));
  } else if (actieveTab === "kruisingen") {
    t = tabel(["Nr", "WP", "Soort", "Breedte (haaks)", "Techniekvoorstel", "Werkterrein", "Richtlijn", "Bevoegd gezag"],
      v.kruisingen.map(c => ({
        cells: [td(c.nr), td(c.werkpakket), td(c.soort),
          tdn(c.breedte_m + " m" + (c.kruislengte_m && c.kruislengte_m > c.breedte_m + 0.5
            ? `<br><small>${c.kruislengte_m} m langs tracé</small>` : "")),
          td(`<span class="chip ${c.techniek.includes("HDD") ? "hdd" : ""}">${c.techniek}</span>`
             + (c.techniek_oorspronkelijk ? `<br><s>${c.techniek_oorspronkelijk}</s>` : "")
             + (c.noodzaak ? `<br><em>${c.noodzaak}</em>` : "")
             + `<br>${c.detail}`),
          td(wtCel(c.werkterrein)),
          td(c.richtlijn), td(c.bevoegd_gezag)],
        zoom: c.punt,
      })));
  } else if (actieveTab === "vergunningen") {
    t = tabel(["Nr", "WP", "Item", "Bevoegd gezag", "Trigger", "Doorloop", "Status"],
      v.vergunningen.map(g => ({
        cells: [td(g.nr), td(g.werkpakket), td(g.item), td(g.bevoegd_gezag), td(g.trigger),
          tdn(g.doorlooptijd_wk[0] + "–" + g.doorlooptijd_wk[1] + " wk"),
          td(`<span class="chip">${g.status}</span>`)],
      })));
  } else if (actieveTab === "boringen") {
    t = tabel(["Nr", "WP", "Type", "Obstakel", "Noodzaak", "Lengte", "Intrede (RD)", "Uittrede (RD)", "Dekking-eis", "Mantelbuis", "Werkterrein"],
      v.boringen.map(b => ({
        cells: [td(b.nr), td(b.werkpakket),
          td(b.type + (b.type_oorspronkelijk ? `<br><s>${b.type_oorspronkelijk}</s>` : "")),
          td(b.obstakel), td(b.noodzaak), tdn(b.lengte_m + " m"),
          tdn(b.intredepunt_rd.join(", ")), tdn(b.uittredepunt_rd.join(", ")),
          td(b.dekking_eis), td(b.mantelbuis),
          td(wtCel({ oordeel: b.werkterrein_oordeel, intrede_m2: b.werkterrein_intrede_m2,
            intrede_eis_m2: b.werkterrein_intrede_eis_m2, uittrede_m2: b.werkterrein_uittrede_m2,
            uittrede_eis_m2: b.werkterrein_uittrede_eis_m2, opmerking: b.werkterrein_opmerking }))],
        zoom: b.geometry || b.intredepunt_rd,
      })));
    if (!v.boringen.length)
      el.innerHTML = '<p class="leeg">Geen boringen: alle kruisingen kunnen open of er zijn geen kruisingen.</p>';
  } else if (actieveTab === "onderzoeken") {
    t = tabel(["Nr", "Onderzoek", "Aanleiding", "Conclusie", "Status"],
      (v.onderzoeken || []).map(o => ({
        cells: [td(o.nr), td(o.soort), td(o.aanleiding),
          td(o.conclusie), td(`<span class="chip">${o.status}</span>`)],
      })));
    const zones = Object.entries(v.zones || {});
    if (zones.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Zonelagen op het tracé: " +
        zones.map(([naam, m]) => `${naam} (${Math.round(m)} m)`).join(" · ");
      el.appendChild(p);
    }
  } else if (actieveTab === "zro") {
    el.innerHTML = '<p class="leeg">ZRO-register laden…</p>';
    toonZroTab(el, v);
  } else if (actieveTab === "toetsing") {
    t = tabel(["Ernst", "WP", "Toets", "Grondslag", "Melding"],
      v.toetsing.map(c => ({
        cells: [td(`<span class="chip ${c.ernst}">${c.ernst}</span>`), td(c.werkpakket),
          td(c.toets), td(c.grondslag), td(c.melding)],
        zoom: c.punt || null,
      })));
  } else if (actieveTab === "moffen") {
    t = tabel(["Nr", "WP", "Chainage", "X (RD)", "Y (RD)", "Opmerking"],
      v.moffen.map(m => ({
        cells: [td(m.nr), td(m.werkpakket), tdn(m.chainage_m + " m"), tdn(m.punt[0]), tdn(m.punt[1]), td(m.opmerking)],
        zoom: m.punt,
      })));
    if (!v.moffen.length)
      el.innerHTML = '<p class="leeg">Tracé korter dan één haspellengte: geen moffen nodig.</p>';
  } else if (actieveTab === "kosten") {
    if (v.calculatie) {
      toonCalculatie(el, v.calculatie);
    } else {
      const k = v.kosten;
      t = tabel(["Post", "Bedrag (indicatief)"], [
        { cells: [td("Sleufwerk (naar ligging en verharding)"), tdn(eur(k.sleufwerk))] },
        { cells: [td("Boringen en persingen"), tdn(eur(k.boringen))] },
        { cells: [td("Zakelijk recht (percelen)"), tdn(eur(k.zro))] },
        { cells: [td("Moffen"), tdn(eur(k.moffen))] },
        { cells: [td("Onderzoeken"), tdn(eur(k.onderzoeken || 0))] },
        { cells: [td("<strong>Totaal</strong>"), tdn("<strong>" + eur(k.totaal) + "</strong>")] },
      ]);
    }
  }
  if (t) el.appendChild(t);
}

/* RAW-calculatie (inschrijvingsbegroting): bestekposten per hoofdstuk plus
   de inschrijvingsstaat (staart) en de gehanteerde uitgangspunten. */
function toonCalculatie(el, c) {
  const rijen = [];
  for (const h of c.hoofdstukken) {
    rijen.push({ cells: [
      `<td colspan="5" class="calc-hoofdstuk"><strong>${h.code} ${h.naam.toUpperCase()}</strong></td>`,
      tdn("<strong>" + eur(h.totaal_eur) + "</strong>"),
    ] });
    for (const q of c.posten.filter(q => q.hoofdstuk === h.code)) {
      rijen.push({ cells: [
        td(q.nr),
        td(q.omschrijving + (q.herkomst ? `<br><small class="hint">${q.herkomst}</small>` : "")),
        td(q.eenheid),
        tdn(q.hoeveelheid.toLocaleString("nl-NL")),
        tdn(q.eenheidsprijs_eur.toLocaleString("nl-NL", { minimumFractionDigits: 2 })),
        tdn(eur(q.totaal_eur)),
      ] });
    }
  }
  for (const s of c.staart) {
    const vet = !s.nr; // subtotalen en aannemingssom
    const w = x => vet ? `<strong>${x}</strong>` : x;
    rijen.push({ cells: [
      td(s.nr), `<td colspan="3">${w(s.omschrijving)}</td>`,
      tdn(s.grondslag || ""), tdn(w(eur(s.bedrag_eur))),
    ] });
  }
  const t = tabel(["Bestekpost", "Omschrijving", "Eenheid", "Hoeveelheid",
                   "Prijs/eenheid", "Totaal"], rijen);
  // uitlijning expliciet: de automatische kolomdetectie van tabel() kijkt
  // naar de eerste rij, en dat is hier een hoofdstukrij met colspan
  [...t.tHead.rows[0].cells].forEach((cel, i) => cel.classList.toggle("num", i >= 3));
  el.appendChild(t);
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = c.systematiek + " — prijspeil " + c.prijspeil +
    " · bouwtijdraming " + c.uitvoeringsduur_wk + " weken. Uitgangspunten: " +
    c.uitgangspunten.join(" ");
  el.appendChild(p);
}

/* -------------------------------------------------------------- ZRO-detail */
let zdData = null;          // laatste detail-antwoord van de backend
let zdVergGrondslag = "";   // gevuld zolang de vergoeding het richtlijnvoorstel volgt
let demoZroPending = false; // #demo-zro: eerste perceel openen zodra de tab er is

function zroStatusKlasse(status) {
  if (!status) return "";
  if (status === "gevestigd") return "ok";
  if (status.startsWith("geweigerd")) return "kritiek";
  if (status === "contact te leggen") return "info";
  return "waarschuwing";
}

async function toonZroTab(el, v) {
  let items = null;
  try {
    const r = await fetch("api/zro/register?variant=" + actieveVariant);
    if (r.ok) items = (await r.json()).items;
  } catch (e) { /* backend niet bereikbaar: terugvallen op berekende lijst */ }
  if (actieveTab !== "zro") return; // gebruiker is al naar een andere tab
  el.innerHTML = "";
  if (!items) items = v.zro.map(z => ({ ...z, status: z.workflow }));
  if (!items.length) {
    el.innerHTML = '<p class="leeg">Het tracé kruist geen kadastrale percelen die een ZRO vragen.</p>';
    return;
  }
  const t = tabel(
    ["Nr", "Perceel", "Eigenaar", "Lengte", "Werkstrook", "Aard recht", "Status", "Dossier"],
    items.map(z => ({
      cells: [td(z.nr), td(z.perceel),
        td(z.eigenaar_dossier || z.eigenaar),
        tdn(z.ingenomen_lengte_m + " m"), tdn(z.werkstrook_m2 + " m²"),
        td(z.aard_recht_dossier || z.aard_recht),
        td(`<span class="chip ${zroStatusKlasse(z.status)}">${z.status}</span>`),
        td(z.compleet_totaal
          ? `${z.compleet_ok}/${z.compleet_totaal} ✓ · ${z.n_bijlagen} bijlage${z.n_bijlagen === 1 ? "" : "n"}`
          : "—")],
      zoom: z.geometry || null,
    })),
    i => openZroDetail(items[i].nr));
  el.appendChild(t);
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = "Klik op een perceel om het ZRO-dossier te openen: status, eigenaar, tekening en overeenkomst.";
  el.appendChild(p);
  if (demoZroPending) {
    demoZroPending = false;
    const rij = t.querySelector("tbody tr.klik");
    if (rij) rij.click();
  }
}

const zd = id => document.getElementById(id);

async function openZroDetail(nr) {
  try {
    const r = await fetch(`api/zro/detail?nr=${encodeURIComponent(nr)}&variant=${actieveVariant}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    vulZroDetail(await r.json());
    zd("zro-detail").classList.remove("dicht");
  } catch (e) {
    alert("ZRO-dossier openen mislukt: " + e.message);
  }
}

function vulZroDetail(d) {
  zdData = d;
  const { item, dossier } = d;
  zd("zd-titel").textContent = item.nr;
  zd("zd-perceel").textContent = item.perceel;
  const chip = zd("zd-status-chip");
  chip.textContent = dossier.status;
  chip.className = "chip " + zroStatusKlasse(dossier.status);

  const selSt = zd("zd-status");
  selSt.innerHTML = d.statussen.map(s =>
    `<option${s === dossier.status ? " selected" : ""}>${s}</option>`).join("");

  zd("zd-feiten").innerHTML = [
    ["Gemeente", d.gemeente || "onbekend"],
    ["Ingenomen tracélengte", item.ingenomen_lengte_m + " m"],
    ["Werkstrook", item.werkstrook_m2 + " m²"],
    ["Voorstel register", item.aard_recht],
    ["Kadastrale bron", "DKK (PDOK) — eigendom via BRK niet gekoppeld"],
  ].map(([k, w]) => `<div><dt>${k}</dt><dd>${w}</dd></div>`).join("");

  zd("zd-eigenaar-naam").value = dossier.eigenaar_naam || "";
  zd("zd-eigenaar-adres").value = dossier.eigenaar_adres || "";
  zd("zd-eigenaar-pp").value = dossier.eigenaar_postcode_plaats || "";
  zd("zd-eigenaar-email").value = dossier.eigenaar_email || "";
  zd("zd-eigenaar-tel").value = dossier.eigenaar_telefoon || "";
  zd("zd-netbeheerder").value = dossier.netbeheerder || "";
  zd("zd-aard").innerHTML = '<option value="">— kies —</option>' +
    d.aard_opties.map(a =>
      `<option${a === dossier.aard_recht ? " selected" : ""}>${a}</option>`).join("");
  zd("zd-verg1").value = dossier.vergoeding_eenmalig_eur ?? "";
  zd("zd-verg2").value = dossier.vergoeding_jaarlijks_eur ?? "";
  zd("zd-notaris").value = dossier.notaris || "";
  zd("zd-opm").value = dossier.opmerkingen || "";

  zdVergGrondslag = dossier.vergoeding_grondslag || "";
  const vp = d.vergoeding_voorstel;
  if (vp) {
    const eur = v => "€ " + (v ?? 0).toLocaleString("nl-NL",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    zd("zd-voorstel-tabel").innerHTML =
      vp.regels.map(rg =>
        `<tr><td>${rg.post}<span class="grondslag">${rg.grondslag}</span></td>
         <td class="num">${eur(rg.bedrag_eur)}${rg.periodiek ? " / jr" : ""}</td></tr>`).join("") +
      `<tr class="totaal"><td>Eenmalig totaal</td><td class="num">${eur(vp.eenmalig_eur)}</td></tr>` +
      (vp.jaarlijks_eur != null
        ? `<tr class="totaal"><td>Jaarlijks</td><td class="num">${eur(vp.jaarlijks_eur)} / jr</td></tr>` : "") +
      vp.pm.map(p => `<tr class="pm"><td>${p}</td><td class="num">p.m.</td></tr>`).join("");
    zd("zd-voorstel-toelichting").textContent =
      vp.toelichting.charAt(0).toUpperCase() + vp.toelichting.slice(1) +
      ". Het voorstel volgt de gekozen aard van het recht.";
  }
  zd("zd-voorstel").style.display = vp ? "" : "none";

  zd("zd-checklist").innerHTML = d.checklist.map(c =>
    `<li class="${c.ok ? "ok" : "open"}" title="${c.hint}">${c.ok ? "✓" : "○"} ${c.eis}</li>`).join("");

  zd("zd-bijlagen").innerHTML = d.dossier.bijlagen.length
    ? d.dossier.bijlagen.map(b =>
        `<li><a href="api/zro/bijlage/${encodeURIComponent(d.slug)}/${encodeURIComponent(b.bestand)}"
           target="_blank">${b.bestand}</a>
           <span class="chip">${b.soort}</span> <span class="mono">${b.datum}</span>
           <button class="zd-verwijder" data-bestand="${b.bestand}" title="Bijlage verwijderen">✕</button></li>`).join("")
    : '<li class="leeg">Nog geen bijlagen.</li>';
  zd("zd-bijlagen").querySelectorAll(".zd-verwijder").forEach(b =>
    b.addEventListener("click", async () => {
      if (!confirm(`Bijlage “${b.dataset.bestand}” verwijderen?`)) return;
      const r = await fetch(`api/zro/bijlage?slug=${encodeURIComponent(d.slug)}` +
        `&bestand=${encodeURIComponent(b.dataset.bestand)}` +
        `&nr=${encodeURIComponent(d.item.nr)}&variant=${actieveVariant}`,
        { method: "DELETE" });
      if (r.ok) { vulZroDetail(await r.json()); ververZroRegister(); }
    }));

  zd("zd-overeenkomst").disabled = !d.compleet;
  zd("zd-overeenkomst-hint").textContent = d.compleet
    ? "Alle vestigingsvereisten zijn vervuld."
    : "Beschikbaar zodra alle vestigingsvereisten hierboven ✓ zijn.";
  zd("zd-melding").textContent = "";
}

function leesZdDossier() {
  const num = v => { const n = parseFloat(v); return isNaN(n) ? null : n; };
  return {
    status: zd("zd-status").value,
    eigenaar_naam: zd("zd-eigenaar-naam").value.trim(),
    eigenaar_adres: zd("zd-eigenaar-adres").value.trim(),
    eigenaar_postcode_plaats: zd("zd-eigenaar-pp").value.trim(),
    eigenaar_email: zd("zd-eigenaar-email").value.trim(),
    eigenaar_telefoon: zd("zd-eigenaar-tel").value.trim(),
    netbeheerder: zd("zd-netbeheerder").value.trim(),
    aard_recht: zd("zd-aard").value,
    vergoeding_eenmalig_eur: num(zd("zd-verg1").value),
    vergoeding_jaarlijks_eur: num(zd("zd-verg2").value),
    vergoeding_grondslag: zdVergGrondslag,
    notaris: zd("zd-notaris").value.trim(),
    opmerkingen: zd("zd-opm").value.trim(),
  };
}

function ververZroRegister() {
  if (actieveTab === "zro") toonTab();
}

async function bewaarZroDossier(stil) {
  if (!zdData) return null;
  const r = await fetch("api/zro/detail", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ variant: actieveVariant, nr: zdData.item.nr,
                           dossier: leesZdDossier() }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  const d = await r.json();
  vulZroDetail(d);
  if (!stil) zd("zd-melding").textContent = "Dossier opgeslagen.";
  ververZroRegister();
  return d;
}

zd("zd-sluit").addEventListener("click", () => zd("zro-detail").classList.add("dicht"));
zd("zd-status").addEventListener("change", () =>
  bewaarZroDossier(true).catch(e => alert("Opslaan mislukt: " + e.message)));
// voorstel volgt de aard van het recht: bij wijziging direct herrekenen
zd("zd-aard").addEventListener("change", () =>
  bewaarZroDossier(true).catch(e => alert("Opslaan mislukt: " + e.message)));
zd("zd-voorstel-overnemen").addEventListener("click", async () => {
  const vp = zdData && zdData.vergoeding_voorstel;
  if (!vp) return;
  zd("zd-verg1").value = vp.eenmalig_eur;
  zd("zd-verg2").value = vp.jaarlijks_eur ?? "";
  zdVergGrondslag = vp.grondslag;
  try {
    await bewaarZroDossier(true);
    zd("zd-melding").textContent = "Richtlijnvoorstel overgenomen en opgeslagen.";
  } catch (e) {
    alert("Opslaan mislukt: " + e.message);
  }
});
// handmatig aangepast bedrag is geen richtlijnvoorstel meer
["zd-verg1", "zd-verg2"].forEach(id =>
  zd(id).addEventListener("input", () => { zdVergGrondslag = ""; }));
zd("zd-opslaan").addEventListener("click", () =>
  bewaarZroDossier(false).catch(e => alert("Opslaan mislukt: " + e.message)));

zd("zd-tekening").addEventListener("click", async () => {
  const btn = zd("zd-tekening");
  btn.disabled = true;
  btn.textContent = "Tekening genereren…";
  try {
    await bewaarZroDossier(true);
    const achtergrond = document.querySelector("input[name=zd-ondergrond]:checked").value;
    const r = await fetch("api/zro/tekening", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant: actieveVariant, nr: zdData.item.nr, achtergrond,
                             projectnaam: document.getElementById("project-naam").value.trim() }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    vulZroDetail(await r.json());
    zd("zd-melding").textContent = "ZRO-tekening toegevoegd als bijlage.";
    ververZroRegister();
  } catch (e) {
    alert("Tekening genereren mislukt: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "◫ ZRO-tekening genereren (PDF)";
  }
});

zd("zd-overeenkomst").addEventListener("click", async () => {
  const btn = zd("zd-overeenkomst");
  btn.disabled = true;
  const oud = btn.textContent;
  btn.textContent = "Overeenkomst opstellen…";
  try {
    await bewaarZroDossier(true);
    const r = await fetch("api/zro/overeenkomst", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant: actieveVariant, nr: zdData.item.nr,
                             projectnaam: document.getElementById("project-naam").value.trim() }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    vulZroDetail(await r.json());
    zd("zd-melding").textContent = "Concept-overeenkomst (Word) toegevoegd als bijlage.";
    ververZroRegister();
  } catch (e) {
    alert("Overeenkomst opstellen mislukt: " + e.message);
  } finally {
    btn.textContent = oud;
    btn.disabled = !(zdData && zdData.compleet);
  }
});

zd("zd-upload-knop").addEventListener("click", () => zd("zd-upload").click());
zd("zd-upload").addEventListener("change", () => {
  const file = zd("zd-upload").files[0];
  if (!file || !zdData) return;
  const lezer = new FileReader();
  lezer.onload = async () => {
    try {
      const r = await fetch("api/zro/bijlage", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variant: actieveVariant, nr: zdData.item.nr,
                               bestandsnaam: file.name, data_base64: lezer.result }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
      vulZroDetail(await r.json());
      zd("zd-melding").textContent = `Bijlage “${file.name}” toegevoegd.`;
      ververZroRegister();
    } catch (e) {
      alert("Bijlage toevoegen mislukt: " + e.message);
    } finally {
      zd("zd-upload").value = "";
    }
  };
  lezer.readAsDataURL(file);
});

/* ------------------------------------------------------------------- export */
document.getElementById("btn-exp-geojson").addEventListener("click", () =>
  window.open("api/export/geojson?variant=" + actieveVariant));
document.getElementById("btn-exp-xlsx").addEventListener("click", () =>
  window.open("api/export/xlsx?variant=" + actieveVariant));

/* -------------------------------------------- Street View langs het tracé
   Loop door het berekende tracé: klik met de Street View-tool op (of naast)
   het tracé, of gebruik de knoppen/schuif in het paneel. Zonder sleutel wordt
   het beeld ingebed via Googles sleutelloze embed-iframe (ongedocumenteerd
   pb-formaat, geen API-sleutel nodig); met een GOOGLE_MAPS_API_KEY
   (api/defaults) wordt het interactieve JS-panorama gebruikt — lopen ín het
   beeld beweegt dan ook de kaartmarker mee. "↗ Google Maps" opent hetzelfde
   punt met kijkrichting in een nieuw tabblad. */

const srcStreetview = new ol.source.Vector();
map.addLayer(new ol.layer.Vector({
  source: srcStreetview, zIndex: 31,
  style: f => [
    new ol.style.Style({  // kijkrichting-kegel
      image: new ol.style.RegularShape({
        points: 3, radius: 13, rotateWithView: true,
        rotation: ((f.get("heading") || 0) * Math.PI) / 180,
        fill: new ol.style.Fill({ color: "rgba(26,115,232,.30)" }),
      }),
    }),
    new ol.style.Style({  // positie (pegman-blauw, contrasteert met het rode tracé)
      image: new ol.style.Circle({
        radius: 5.5, fill: new ol.style.Fill({ color: "#1A73E8" }),
        stroke: new ol.style.Stroke({ color: "#fff", width: 2 }),
      }),
    }),
  ],
}));

let svKey = "";                     // uit api/defaults; leeg = fallback zonder inbedding
let svPano = null, svSvc = null, svGoogleBelofte = null;
let svChainage = 0;                 // positie langs het tracé (m)
let svCoords = null, svCum = null;  // routecoördinaten (RD) + cumulatieve lengte
let svTimer = null, svFrameTimer = null;

const sv = id => document.getElementById(id);
const rdNaarWgs = c => proj4("EPSG:28992", "EPSG:4326", [c[0], c[1]]); // → [lon, lat]

function svBearing(a, b) {  // kompasrichting a→b in graden (via WGS84)
  const [l1, f1] = rdNaarWgs(a).map(x => (x * Math.PI) / 180);
  const [l2, f2] = rdNaarWgs(b).map(x => (x * Math.PI) / 180);
  const y = Math.sin(l2 - l1) * Math.cos(f2);
  const x = Math.cos(f1) * Math.sin(f2) - Math.sin(f1) * Math.cos(f2) * Math.cos(l2 - l1);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function svLaadRoute() {
  svCoords = svCum = null;
  if (!resultaat) return false;
  const g = resultaat.varianten[actieveVariant].route;
  const cs = g.type === "LineString" ? g.coordinates
    : g.type === "MultiLineString" ? g.coordinates.flat() : null;
  if (!cs || cs.length < 2) return false;
  svCoords = cs;
  svCum = [0];
  for (let i = 1; i < cs.length; i++)
    svCum.push(svCum[i - 1] + Math.hypot(cs[i][0] - cs[i - 1][0], cs[i][1] - cs[i - 1][1]));
  return true;
}

function svPuntOp(m) {  // RD-coördinaat op chainage m
  const tot = svCum[svCum.length - 1];
  m = Math.max(0, Math.min(m, tot));
  let i = svCum.findIndex(c => c >= m);
  if (i <= 0) i = 1;
  const d = svCum[i] - svCum[i - 1] || 1;
  const t = (m - svCum[i - 1]) / d;
  const a = svCoords[i - 1], b = svCoords[i];
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function svHeadingOp(m) {  // kijkrichting = looprichting (± 8 m vooruit)
  const tot = svCum[svCum.length - 1];
  return m > tot - 1
    ? svBearing(svPuntOp(Math.max(0, tot - 8)), svPuntOp(tot))
    : svBearing(svPuntOp(m), svPuntOp(Math.min(m + 8, tot)));
}

function svChainageBij(coord) {  // dichtstbijzijnde punt op de route → chainage
  let best = Infinity, bestM = 0;
  for (let i = 1; i < svCoords.length; i++) {
    const a = svCoords[i - 1], b = svCoords[i];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const l2 = dx * dx + dy * dy || 1e-9;
    const t = Math.max(0, Math.min(1, ((coord[0] - a[0]) * dx + (coord[1] - a[1]) * dy) / l2));
    const d = Math.hypot(coord[0] - (a[0] + dx * t), coord[1] - (a[1] + dy * t));
    if (d < best) { best = d; bestM = svCum[i - 1] + Math.sqrt(l2) * t; }
  }
  return bestM;
}

function svZetMarker(coord, heading) {
  let f = srcStreetview.getFeatures()[0];
  if (!f) { f = new ol.Feature(new ol.geom.Point(coord)); srcStreetview.addFeature(f); }
  else f.setGeometry(new ol.geom.Point(coord));
  f.set("heading", heading);
}

let svZoekTeller = 0;
function svZoekPano(lat, lon, radius) {  // sleutelloos pano zoeken (JSONP)
  return new Promise((res, rej) => {
    const cb = "__svPano" + (++svZoekTeller);
    const s = document.createElement("script");
    window[cb] = d => {
      delete window[cb]; s.remove();
      // d[1][1] = [type, pano-id]: type 2 = officieel Street View, 10 = losse
      // 360°-foto van een gebruiker (vaak binnen of in een tuin) — overslaan.
      // d[1][5][0][1][0][2..3] = werkelijke camerapositie (lat, lon).
      const p = d && d[1];
      const id = p && p[1] && p[1][1];
      if (!id || p[1][0] !== 2) { res(null); return; }
      const pos = (p[5] && p[5][0] && p[5][0][1] && p[5][0][1][0]) || [];
      res({ id,
            lat: pos[2] != null ? pos[2] : lat,
            lon: pos[3] != null ? pos[3] : lon });
    };
    s.src = "https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch" +
      "?pb=!1m5!1sapiv3!5sUS!11m2!1m1!1b0!2m4!1m2" +
      `!3d${lat.toFixed(7)}!4d${lon.toFixed(7)}` +
      `!2d${radius}!3m10!2m2!1snl!2sNL!9m1!1e2!11m4!1m3!1e2!2b1!3e2` +
      "!4m10!1e1!1e2!1e3!1e4!1e8!1e6!5m1!1e2!6m1!1e2" +
      `&callback=${cb}`;
    s.onerror = () => { delete window[cb]; s.remove();
                        rej(new Error("Street View-zoekdienst niet bereikbaar")); };
    document.head.appendChild(s);
  });
}

let svFrameVolgnr = 0;
function svToonFrame(coord, heading) {  // sleutelloze inbedding (iframe)
  clearTimeout(svFrameTimer);  // schuiven: pas herladen als de hand stilstaat
  svFrameTimer = setTimeout(async () => {
    const mijn = ++svFrameVolgnr;
    let fr = sv("sv-frame");
    if (!fr) {
      fr = document.createElement("iframe");
      fr.id = "sv-frame";
      fr.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0";
      fr.allow = "fullscreen";
      sv("sv-pano").appendChild(fr);
    }
    const [lon, lat] = rdNaarWgs(coord);
    try {
      const pano = await svZoekPano(lat, lon, 60) ||
                   await svZoekPano(lat, lon, 250);
      if (mijn !== svFrameVolgnr) return;  // inmiddels verder gestapt
      if (!pano) {
        fr.style.display = "none";
        svMelding("Geen Street View-beeld binnen 250 m van dit punt (het tracé " +
          "loopt hier niet langs een gefotografeerde weg). Stap verder of " +
          "bekijk het punt via ↗ Google Maps.");
        return;
      }
      // De camera staat op de weg; het tracé ligt vaak in de berm of het veld.
      // Bij meer dan 12 m afstand richten we op het tracépunt in plaats van
      // in de looprichting — anders toont het beeld de verkeerde kant op.
      const panoRd = proj4("EPSG:4326", "EPSG:28992", [pano.lon, pano.lat]);
      const afstand = Math.hypot(panoRd[0] - coord[0], panoRd[1] - coord[1]);
      if (afstand > 12) heading = svBearing(panoRd, coord);
      svMelding(afstand > 40
        ? `Dichtstbijzijnde beeld staat ± ${Math.round(afstand)} m van het ` +
          "tracé; de camera is op het tracépunt gericht."
        : "");
      fr.style.display = "";
      fr.src = "https://www.google.com/maps/embed?pb=" +
        `!4v1!6m8!1m7!1s${encodeURIComponent(pano.id)}` +
        `!2m2!1d${pano.lat.toFixed(6)}!2d${pano.lon.toFixed(6)}` +
        `!3f${Math.round(heading)}!4f0!5f0.7820865974627469`;
    } catch (e) {
      if (mijn === svFrameVolgnr) { fr.style.display = "none"; svMelding(e.message); }
    }
  }, 250);
}

function svLaadGoogle() {
  if (svGoogleBelofte) return svGoogleBelofte;
  svGoogleBelofte = new Promise((res, rej) => {
    window.__svGoogleKlaar = () => res(window.google.maps);
    const s = document.createElement("script");
    s.src = "https://maps.googleapis.com/maps/api/js?key=" +
      encodeURIComponent(svKey) + "&v=weekly&callback=__svGoogleKlaar";
    s.onerror = () => { svGoogleBelofte = null; rej(new Error("Google Maps-script laden mislukt")); };
    document.head.appendChild(s);
  });
  return svGoogleBelofte;
}

async function svInitPano() {
  if (svPano) return;
  const gm = await svLaadGoogle();
  svSvc = new gm.StreetViewService();
  svPano = new gm.StreetViewPanorama(sv("sv-pano"), {
    pov: { heading: 0, pitch: 0 },
    addressControl: false, motionTracking: false, enableCloseButton: false,
  });
  // lopen kan ook ín het beeld (Google-pijlen): de kaartmarker volgt mee
  svPano.addListener("position_changed", () => {
    const p = svPano.getPosition();
    if (!p) return;
    const rd = proj4("EPSG:4326", "EPSG:28992", [p.lng(), p.lat()]);
    const f = srcStreetview.getFeatures()[0];
    if (f) f.setGeometry(new ol.geom.Point(rd));
  });
  svPano.addListener("pov_changed", () => {
    const f = srcStreetview.getFeatures()[0];
    if (f) f.set("heading", svPano.getPov().heading);
  });
}

function svMelding(tekst) {  // html of "" (verbergen)
  const el = sv("sv-melding");
  el.style.display = tekst ? "" : "none";
  el.innerHTML = tekst || "";
}

async function svToon(m) {
  if (!svCum) return;
  const tot = svCum[svCum.length - 1];
  svChainage = Math.max(0, Math.min(m, tot));
  const coord = svPuntOp(svChainage);
  const heading = svHeadingOp(svChainage);
  svZetMarker(coord, heading);
  sv("sv-positie").textContent = `${Math.round(svChainage)} / ${Math.round(tot)} m`;
  sv("sv-slider").max = Math.round(tot);
  sv("sv-slider").value = Math.round(svChainage);
  const [lon, lat] = rdNaarWgs(coord);
  sv("sv-extern").href = "https://www.google.com/maps/@?api=1&map_action=pano" +
    `&viewpoint=${lat.toFixed(6)},${lon.toFixed(6)}&heading=${Math.round(heading)}`;
  if (!svKey) { svToonFrame(coord, heading); return; }  // sleutelloos iframe
  try {
    await svInitPano();
    const vraag = radius => svSvc.getPanorama({
      location: { lat, lng: lon }, radius,
      source: google.maps.StreetViewSource.OUTDOOR,
      preference: google.maps.StreetViewPreference.NEAREST,
    });
    const r = await vraag(60).catch(() => vraag(250));
    // bij afstand tot het tracé: camera op het tracépunt richten (zie svToonFrame)
    const ll = r.data.location.latLng;
    const panoRd = proj4("EPSG:4326", "EPSG:28992", [ll.lng(), ll.lat()]);
    const afstand = Math.hypot(panoRd[0] - coord[0], panoRd[1] - coord[1]);
    if (afstand > 12) heading = svBearing(panoRd, coord);
    svMelding(afstand > 40
      ? `Dichtstbijzijnde beeld staat ± ${Math.round(afstand)} m van het ` +
        "tracé; de camera is op het tracépunt gericht."
      : "");
    svPano.setVisible(true);
    svPano.setPano(r.data.location.pano);
    svPano.setPov({ heading, pitch: 0 });
  } catch (e) {
    if (svPano) svPano.setVisible(false);
    svMelding(e.message && e.message.includes("mislukt")
      ? e.message
      : "Geen Street View-beeld binnen 250 m van dit punt (het tracé loopt hier " +
        "niet langs een gefotografeerde weg). Stap verder of " +
        "bekijk het punt via ↗ Google Maps.");
  }
}

function svOpen(coord) {
  if (!svLaadRoute()) {
    statusEl.textContent = "Street View: bereken eerst een tracé (of open een project met berekend tracé).";
    return;
  }
  sv("sv-paneel").classList.remove("dicht");
  svToon(coord ? svChainageBij(coord) : svChainage);
}

function svSluit() {
  svStopLopen();
  clearTimeout(svFrameTimer);
  sv("sv-paneel").classList.add("dicht");
  srcStreetview.clear();
  if (mode === "street") setMode("pan");
}

function svRouteGewijzigd() {  // variant gewisseld, herberekend of project geleegd
  if (sv("sv-paneel").classList.contains("dicht")) return;
  svStopLopen();
  if (svLaadRoute()) svToon(Math.min(svChainage, svCum[svCum.length - 1]));
  else svSluit();
}

function svStopLopen() {
  if (svTimer) { clearInterval(svTimer); svTimer = null; }
  sv("sv-play").textContent = "▷ Lopen";
}
const svStap = () => parseFloat(sv("sv-stap").value) || 25;

sv("sv-sluit").addEventListener("click", svSluit);
sv("sv-start").addEventListener("click", () => { svStopLopen(); svToon(0); });
sv("sv-eind").addEventListener("click", () => { svStopLopen(); svToon(Infinity); });
sv("sv-terug").addEventListener("click", () => { svStopLopen(); svToon(svChainage - svStap()); });
sv("sv-vooruit").addEventListener("click", () => { svStopLopen(); svToon(svChainage + svStap()); });
sv("sv-slider").addEventListener("input", () => {
  svStopLopen();
  svToon(parseFloat(sv("sv-slider").value));
});
sv("sv-play").addEventListener("click", () => {
  if (svTimer) { svStopLopen(); return; }
  if (!svCum) return;
  sv("sv-play").textContent = "⏸ Stop";
  svTimer = setInterval(() => {
    if (!svCum || svChainage >= svCum[svCum.length - 1]) { svStopLopen(); return; }
    svToon(svChainage + svStap());
  }, 1800);
});

/* ------------------------------------------------- ontwerpnota's (VO/DO/UO)
   AI schrijft de nota als Markdown-stroom; die wordt live als HTML-document
   opgebouwd in het voorbeeldvenster en daarna als opgemaakt Word-document
   gedownload (POST /api/nota/docx met dezelfde Markdown). */

const NOTA_FASEN = { VO: "Voorlopig Ontwerp", DO: "Definitief Ontwerp",
                     UO: "Uitvoeringsgereed Ontwerp" };
const NOTA_FOUT_MARK = "[NOTA-FOUT]";
let notaAbort = null;
let notaMarkdown = "";
let notaFase = "";

/* Begrensde Markdown → HTML (zelfde subset als backend/nota.py) */
function mdNaarHtml(md) {
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = s => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  const lijstRe = /^([-*–]|\d+[.)])\s+/;
  const lines = md.replace(/\r/g, "").split("\n");
  let html = "", i = 0, para = [];
  const flush = () => {
    if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; }
  };
  while (i < lines.length) {
    const s = lines[i].trim();
    if (!s) { flush(); i++; continue; }
    if (/^#{1,3}\s/.test(s)) {
      flush();
      const lvl = s.match(/^#+/)[0].length;
      html += `<h${lvl}>${inline(s.replace(/^#+\s*/, ""))}</h${lvl}>`;
      i++; continue;
    }
    if (lijstRe.test(s)) {
      flush();
      const items = [];
      while (i < lines.length && lijstRe.test(lines[i].trim())) {
        items.push(inline(lines[i].trim().replace(lijstRe, ""))); i++;
      }
      html += `<ul>${items.map(x => `<li>${x}</li>`).join("")}</ul>`;
      continue;
    }
    if (s.startsWith("|")) {
      flush();
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
        if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      if (rows.length) {
        html += "<table><thead><tr>" +
          rows[0].map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
          rows.slice(1).map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
          "</tbody></table>";
      }
      continue;
    }
    para.push(s); i++;
  }
  flush();
  return html;
}

function notaTitelblok(fase) {
  const proj = document.getElementById("project-naam").value.trim() || "zonder naam";
  const v = resultaat.varianten[actieveVariant];
  const datum = new Date().toLocaleDateString("nl-NL",
    { day: "2-digit", month: "2-digit", year: "numeric" });
  return `<div class="nota-titelblok">
    <div class="nota-kicker">Kabelbed · Ontwerpnota middenspanningstracé</div>
    <div class="nota-fase">${NOTA_FASEN[fase]} <span>(${fase})</span></div>
    <table class="nota-meta">
      <tr><td>Project</td><td>${proj}</td></tr>
      <tr><td>Gemeente</td><td>${resultaat.gemeente || "onbekend"}</td></tr>
      <tr><td>Variant</td><td>${v.naam} — ${Math.round(v.lengte_m)} m</td></tr>
      <tr><td>Datum</td><td>${datum}</td></tr>
      <tr><td>Status</td><td>Concept — AI-gegenereerd, toetsing vereist</td></tr>
    </table>
  </div>`;
}

function sluitNota() {
  if (notaAbort) { notaAbort.abort(); notaAbort = null; }
  document.getElementById("nota-overlay").classList.add("dicht");
}
document.getElementById("nota-sluit").addEventListener("click", sluitNota);
document.getElementById("nota-overlay").addEventListener("click", e => {
  if (e.target.id === "nota-overlay") sluitNota();
});

async function startNota(fase) {
  const overlay = document.getElementById("nota-overlay");
  const sheet = document.getElementById("nota-sheet");
  const wrap = document.getElementById("nota-sheetwrap");
  const status = document.getElementById("nota-substatus");
  const dl = document.getElementById("nota-download");

  notaFase = fase;
  notaMarkdown = "";
  overlay.classList.remove("dicht");
  document.getElementById("nota-koptitel").textContent = `${fase}-nota — ${NOTA_FASEN[fase]}`;
  const titelblok = notaTitelblok(fase);
  sheet.innerHTML = titelblok;
  sheet.classList.add("bezig");
  dl.disabled = true;
  status.textContent = "AI schrijft…";

  notaAbort = new AbortController();
  const t0 = Date.now();
  const tik = setInterval(() => {
    status.textContent = `AI schrijft… ${Math.round((Date.now() - t0) / 1000)}s`;
  }, 1000);

  // hertekenen throttlen: maximaal ± 8×/s de hele buffer opnieuw renderen
  let renderGepland = false;
  const render = () => {
    renderGepland = false;
    const md = notaMarkdown.split(NOTA_FOUT_MARK)[0];
    sheet.innerHTML = titelblok + mdNaarHtml(md);
    wrap.scrollTop = wrap.scrollHeight;  // meescrollen met de tekst
  };

  try {
    const naam = document.getElementById("project-naam").value.trim();
    const r = await fetch(`api/nota/stream?fase=${fase}&variant=${actieveVariant}` +
                          `&projectnaam=${encodeURIComponent(naam)}`,
                          { signal: notaAbort.signal });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      notaMarkdown += decoder.decode(value, { stream: true });
      if (!renderGepland) { renderGepland = true; setTimeout(render, 120); }
    }
    notaMarkdown += decoder.decode();
    render();
    if (notaMarkdown.includes(NOTA_FOUT_MARK)) {
      const fout = notaMarkdown.split(NOTA_FOUT_MARK)[1].trim();
      status.textContent = `Fout: ${fout}`;
    } else {
      status.textContent = `Gereed (${Math.round((Date.now() - t0) / 1000)}s).`;
      dl.disabled = false;
    }
  } catch (e) {
    if (e.name !== "AbortError")
      status.textContent = `Fout: ${e.message}`;
  } finally {
    clearInterval(tik);
    sheet.classList.remove("bezig");
    notaAbort = null;
  }
}

document.getElementById("nota-download").addEventListener("click", async () => {
  const dl = document.getElementById("nota-download");
  const status = document.getElementById("nota-substatus");
  dl.disabled = true;
  status.textContent = "Word-document opmaken…";
  try {
    const naam = document.getElementById("project-naam").value.trim();
    const r = await fetch("api/nota/docx", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fase: notaFase, variant: actieveVariant,
                             projectnaam: naam, markdown: notaMarkdown }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="([^"]+)"/);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : `${notaFase}-nota.docx`;
    a.click();
    URL.revokeObjectURL(a.href);
    status.textContent = "Word-document gedownload.";
  } catch (e) {
    status.textContent = `Fout bij downloaden: ${e.message}`;
  } finally {
    dl.disabled = false;
  }
});

document.querySelectorAll(".btn-nota").forEach(btn =>
  btn.addEventListener("click", () => startNota(btn.dataset.fase)));

/* --------------------------------------- projecten: nieuw, opslaan, openen */
async function ververslijst(kies) {
  const namen = await (await fetch("api/project/list")).json();
  const sel = document.getElementById("project-lijst");
  sel.innerHTML = '<option value="">— openen —</option>' +
    namen.map(n => `<option>${n}</option>`).join("");
  if (kies) sel.value = kies;
}

function zetExportKnoppen(aan) {
  document.getElementById("btn-exp-geojson").disabled = !aan;
  document.getElementById("btn-exp-xlsx").disabled = !aan;
  document.querySelectorAll(".btn-nota").forEach(b => { b.disabled = !aan; });
}

function nieuwProject() {
  const ietsAanwezig = srcStations.getFeatures().length
    || srcArea.getFeatures().some(f => !f.get("auto")) || resultaat;
  if (ietsAanwezig &&
      !confirm("Nieuw project starten? Niet-opgeslagen werk gaat verloren."))
    return;
  [srcArea, srcStations, srcVia, srcForbidden].forEach(s => s.clear());
  resultaat = null;
  actieveVariant = 0;
  toonResultaat();  // leegt route-, segment-, kruisings-, mof- en boomlagen
  document.getElementById("variant-select").innerHTML = "";
  toonTab();
  document.getElementById("legenda").innerHTML = "";
  document.getElementById("project-naam").value = "";
  document.getElementById("project-lijst").value = "";
  document.getElementById("gemeente-info").textContent = "";
  document.getElementById("btn-weights-reset").click();
  document.getElementById("opt-varianten").checked = true;
  document.getElementById("opt-haspel").value = 500;
  zetExportKnoppen(false);
  statusEl.textContent = "Nieuw project — plaats stations en bereken een tracé.";
  updateUI();
}
document.getElementById("btn-new").addEventListener("click", nieuwProject);

document.getElementById("btn-save").addEventListener("click", async () => {
  const naam = document.getElementById("project-naam").value.trim();
  if (!naam) { alert("Geef eerst een projectnaam op."); return; }
  const state = {
    area: coordsVanPolygon(srcArea),
    stations: puntenVan(srcStations),
    via: puntenVan(srcVia),
    forbidden: srcForbidden.getFeatures().map(f =>
      f.getGeometry().getCoordinates()[0].slice(0, -1)),
    weights: leesWeights(),
    variants: document.getElementById("opt-varianten").checked,
    haspel_m: parseFloat(document.getElementById("opt-haspel").value) || 500,
    result: resultaat,  // berekend tracé + registers mee opslaan
  };
  const r = await fetch("api/project/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: naam, state }),
  });
  statusEl.textContent = r.ok
    ? `Project “${naam}” opgeslagen` +
      (resultaat ? " (inclusief berekend tracé)." : ".")
    : "Opslaan mislukt.";
  ververslijst(naam);
});

document.getElementById("project-lijst").addEventListener("change", async e => {
  if (!e.target.value) return;
  const r = await fetch("api/project/load?name=" + encodeURIComponent(e.target.value));
  if (!r.ok) { statusEl.textContent = "Project openen mislukt."; return; }
  const p = await r.json();
  const s = p.state;
  document.getElementById("project-naam").value = p.name;
  srcArea.clear(); srcStations.clear(); srcVia.clear(); srcForbidden.clear();
  if (s.area) srcArea.addFeature(new ol.Feature(new ol.geom.Polygon([[...s.area, s.area[0]]])));
  (s.stations || []).forEach(c => srcStations.addFeature(new ol.Feature(new ol.geom.Point(c))));
  (s.via || []).forEach(c => srcVia.addFeature(new ol.Feature(new ol.geom.Point(c))));
  (s.forbidden || []).forEach(ring =>
    srcForbidden.addFeature(new ol.Feature(new ol.geom.Polygon([[...ring, ring[0]]]))));
  if (s.weights) document.querySelectorAll("#weights-table input").forEach(i => {
    if (s.weights[i.dataset.w] !== undefined) i.value = s.weights[i.dataset.w];
  });
  document.getElementById("opt-varianten").checked = !!s.variants;
  document.getElementById("opt-haspel").value = s.haspel_m || 500;

  // meegeslagen rekenresultaat herstellen (kaart, paneel, exports, nota's)
  resultaat = (s.result && s.result.varianten) ? s.result : null;
  actieveVariant = 0;
  document.getElementById("gemeente-info").textContent =
    resultaat && resultaat.gemeente ? "· " + resultaat.gemeente : "";
  zetExportKnoppen(!!resultaat);
  toonResultaat();
  if (!resultaat) {
    document.getElementById("variant-select").innerHTML = "";
    toonTab();
    document.getElementById("legenda").innerHTML = "";
  }

  if (s.area) {
    const ext = new ol.geom.Polygon([[...s.area, s.area[0]]]).getExtent();
    map.getView().fit(ext, { padding: [60, 60, 60, 60], duration: 400 });
  } else if (resultaat && resultaat.bbox) {
    map.getView().fit(resultaat.bbox, { padding: [60, 60, 60, 60], duration: 400 });
  } else if (srcStations.getFeatures().length) {
    map.getView().fit(srcStations.getExtent(),
      { padding: [120, 120, 120, 120], duration: 400 });
  }
  statusEl.textContent = resultaat
    ? `Project “${p.name}” geopend, inclusief berekend tracé.`
    : `Project “${p.name}” geopend — nog geen berekend tracé.`;
  updateUI();
});

/* --------------------------------------------------------------------- init */
async function init() {
  await laadDefaults();
  ververslijst();
  updateUI();
  // #demo: proefgebied Amersfoort-centrum met twee stations, direct rekenen.
  // #demo-zro opent daarna de ZRO-tab en selecteert het eerste perceel.
  if (location.hash.startsWith("#demo")) {
    const area = [[154700, 462700], [155500, 462700], [155500, 463400], [154700, 463400]];
    srcArea.addFeature(new ol.Feature(new ol.geom.Polygon([[...area, area[0]]])));
    srcStations.addFeature(new ol.Feature(new ol.geom.Point([154780, 462780])));
    srcStations.addFeature(new ol.Feature(new ol.geom.Point([155420, 463320])));
    map.getView().fit(srcArea.getFeatures()[0].getGeometry().getExtent(),
      { padding: [60, 60, 60, 60] });
    updateUI();
    await bereken();
    if (location.hash === "#demo-zro") {
      demoZroPending = true; // toonZroTab opent het eerste perceel na het laden
      document.querySelector('#tabs button[data-tab="zro"]').click();
    }
    if (location.hash === "#demo-zones") {
      for (const id of ["lg-amk", "lg-sld", "lg-sad", "lg-bodemdek"]) {
        const cb = document.getElementById(id);
        cb.checked = true;
        cb.dispatchEvent(new Event("change"));
      }
      document.querySelector('#tabs button[data-tab="onderzoeken"]').click();
    }
  }
}
init();
