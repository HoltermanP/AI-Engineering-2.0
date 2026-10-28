/* InfraEngine — uitvoeringsontwerp: exact intekenen, inmetingen en
   werktekeningen. Gebruikt de globals uit app.js (map, resultaat,
   actieveVariant, srcKlic, statusEl, setMode, toonResultaat, geojson). */
"use strict";

const uoEl = id => document.getElementById(id);
const AFRONDING = 0.01;   // normenkader afronding_m
const rondAf = v => Math.round(v / AFRONDING) * AFRONDING;
const fmtNL = (v, d = 2) => Number(v).toLocaleString("nl-NL",
  { minimumFractionDigits: d, maximumFractionDigits: d });
const kmTekst = m => `${Math.floor(m / 1000)}+${String(Math.round(m % 1000)).padStart(3, "0")}`;

async function uoJson(url, opties) {
  const r = await fetch(url, opties);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
  return d;
}
const uoPost = (url, body) => uoJson(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
function uoVoortgang(el) {  // backend-voortgang tonen zolang een verzoek loopt
  return setInterval(async () => {
    try {
      const p = await (await fetch("api/progress")).json();
      if (p.actief && p.stap) el.textContent = `Bezig (${Math.round(p.bezig_s)}s): ${p.stap}`;
    } catch (e) { /* best effort */ }
  }, 1500);
}

/* ---------------------------------------------------------------- lagen */
const srcVang = new ol.source.Vector();     // BGT- en kadasterranden om op te vangen
const srcInmeting = new ol.source.Vector();
const srcHulp = new ol.source.Vector();     // hulplijnen
const srcExact = new ol.source.Vector();    // het te bewerken tracé
const srcExactSel = new ol.source.Vector(); // geselecteerd punt, gezochte coördinaat
const srcBladen = new ol.source.Vector();
const srcWolken = new ol.source.Vector();

const lagenUo = {
  bladen: new ol.layer.Vector({
    source: srcBladen, zIndex: 9, visible: false,
    style: f => new ol.style.Style({
      stroke: new ol.style.Stroke({ color: "rgba(60,70,90,.75)", width: 1.2, lineDash: [8, 5] }),
      fill: new ol.style.Fill({ color: f.get("geraakt") ? "rgba(216,27,96,.06)" : "rgba(0,0,0,0)" }),
      text: new ol.style.Text({
        text: `blad ${f.get("nr")}`, font: "600 12px Inter,sans-serif", overflow: true,
        fill: new ol.style.Fill({ color: "#3C465A" }),
        backgroundFill: new ol.style.Fill({ color: "rgba(255,255,255,.8)" }), padding: [1, 4, 1, 4],
      }),
    }),
  }),
  wolken: new ol.layer.Vector({
    source: srcWolken, zIndex: 44, visible: false,
    style: f => new ol.style.Style({
      stroke: new ol.style.Stroke({ color: "#D81B60", width: 2.2 }),
      text: new ol.style.Text({ text: "△ " + f.get("rev"), font: "700 12px Inter,sans-serif",
        fill: new ol.style.Fill({ color: "#D81B60" }), offsetY: -12, placement: "point" }),
    }),
  }),
  inmeting: new ol.layer.Vector({
    source: srcInmeting, zIndex: 12, declutter: true,
    style: (f, res) => {
      const t = f.getGeometry().getType();
      if (t === "Point") {
        if (f.get("t") && !f.get("b"))
          return res > 0.35 ? null : new ol.style.Style({ text: new ol.style.Text({
            text: f.get("t"), font: "10px 'IBM Plex Mono',monospace",
            rotation: -(f.get("r") || 0) * Math.PI / 180,
            fill: new ol.style.Fill({ color: "#2B2B2B" }) }) });
        return res > 0.6 ? null : new ol.style.Style({ image: new ol.style.RegularShape({
          points: 4, radius: 3.5, radius2: 0, angle: Math.PI / 4,
          stroke: new ol.style.Stroke({ color: "#2B2B2B", width: 1 }) }) });
      }
      return new ol.style.Style({ stroke: new ol.style.Stroke({ color: "#2B2B2B", width: 1 }) });
    },
  }),
  vang: new ol.layer.Vector({
    source: srcVang, zIndex: 35, visible: false,
    style: f => new ol.style.Style({ stroke: new ol.style.Stroke({
      color: f.get("c") === "perceel" ? "rgba(160,97,154,.55)" : "rgba(13,148,136,.45)",
      width: 1, lineDash: f.get("c") === "perceel" ? [4, 3] : undefined }) }),
  }),
  hulp: new ol.layer.Vector({
    source: srcHulp, zIndex: 41,
    style: f => new ol.style.Style({ stroke: new ol.style.Stroke({
      color: f === actieveHulp ? "#E65100" : "rgba(230,81,0,.55)",
      width: f === actieveHulp ? 2.2 : 1.4, lineDash: [10, 4, 2, 4] }) }),
  }),
  exact: new ol.layer.Vector({
    source: srcExact, zIndex: 45,
    style: (f, res) => {
      const stijlen = [new ol.style.Style({
        stroke: new ol.style.Stroke({ color: "#C8322B", width: 3 }) })];
      const coords = f.getGeometry().getCoordinates();
      coords.forEach((c, i) => stijlen.push(new ol.style.Style({
        geometry: new ol.geom.Point(c),
        image: new ol.style.Circle({ radius: i === (exact && exact.sel) ? 6.5 : 4.5,
          fill: new ol.style.Fill({ color: i === (exact && exact.sel) ? "#C8322B" : "#fff" }),
          stroke: new ol.style.Stroke({ color: "#C8322B", width: 2 }) }),
        text: res < 1.2 ? new ol.style.Text({ text: `P${i + 1}`, offsetX: 10, offsetY: -10,
          font: "600 11px 'IBM Plex Mono',monospace", fill: new ol.style.Fill({ color: "#C8322B" }),
          backgroundFill: new ol.style.Fill({ color: "rgba(255,255,255,.8)" }) }) : undefined,
      })));
      return stijlen;
    },
  }),
  exactSel: new ol.layer.Vector({
    source: srcExactSel, zIndex: 46,
    style: new ol.style.Style({ image: new ol.style.RegularShape({ points: 4, radius: 10,
      radius2: 0, angle: 0, stroke: new ol.style.Stroke({ color: "#3E6FDE", width: 2 }) }) }),
  }),
};
Object.values(lagenUo).forEach(l => map.addLayer(l));

/* ---------------------------------------------------------- cursorcoördinaat */
map.on("pointermove", evt => {
  const el = uoEl("cursor-xy");
  if (mode !== "exact" && mode !== "maat") { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `X ${fmtNL(evt.coordinate[0])} · Y ${fmtNL(evt.coordinate[1])}`;
});

/* ================================================================ inmeting */
let inmetingen = [];
async function inmLaadLijst() {
  try {
    const d = await uoJson("api/inmeting/lijst");
    inmetingen = d.inmetingen;
    uoEl("inm-upload").title = uoEl("inm-upload").title.split(" DWG:")[0] +
      (d.dwg ? " DWG: ODA-converter gevonden." : " DWG: niet beschikbaar (geen ODA File Converter op de server) — lever DXF aan.");
    inmToonLijst();
    inmLaadFeatures();
  } catch (e) { uoEl("inm-status").textContent = "Inmetingen: " + e.message; }
}
function inmToonLijst() {
  uoEl("inm-status").textContent = inmetingen.length
    ? `${inmetingen.length} inmeting(en) ingelezen.` : "Nog geen inmeting ingelezen.";
  const lijst = uoEl("inm-lijst");
  lijst.innerHTML = "";
  inmetingen.forEach(m => {
    const rij = document.createElement("div");
    rij.className = "inm-item";
    rij.innerHTML = `<input type="checkbox" ${m.zichtbaar !== false ? "checked" : ""}
      title="Tonen, vangen en meenemen in de werktekeningen">
      <span title="${m.bestand} — ${m.aantal} objecten, ${m.met_hoogte} met NAP-hoogte, ingelezen ${m.datum}${m.etrs89_omgerekend ? " — ETRS89 omgerekend met RDNAPTRANS™2018" : ""}">${m.naam}</span>
      <button title="Inzoomen">⌖</button><button title="Naar de prullenbak (niet definitief gewist)">✕</button>`;
    const [cb, , zoom, weg] = rij.children;
    cb.addEventListener("change", async () => {
      await uoPost("api/inmeting/zichtbaar", { id: m.id, zichtbaar: cb.checked });
      m.zichtbaar = cb.checked;
      inmLaadFeatures();
    });
    zoom.addEventListener("click", () => map.getView().fit(m.bbox, { padding: [40, 40, 40, 40], duration: 300 }));
    weg.addEventListener("click", async () => {
      if (!confirm(`Inmeting “${m.naam}” naar de prullenbak verplaatsen?`)) return;
      await uoJson("api/inmeting?id=" + m.id, { method: "DELETE" });
      inmLaadLijst();
    });
    lijst.appendChild(rij);
  });
}
let inmVolgnr = 0;
async function inmLaadFeatures() {
  const nr = ++inmVolgnr;
  const res = map.getView().getResolution();
  if (!uoEl("lg-inmeting").checked || !inmetingen.length || res > 2) {
    srcInmeting.clear();
    return;
  }
  const bbox = map.getView().calculateExtent(map.getSize());
  try {
    const d = await uoJson("api/inmeting/features?bbox=" + bbox.map(v => v.toFixed(1)).join(","));
    if (nr !== inmVolgnr) return;
    srcInmeting.clear();
    srcInmeting.addFeatures(d.features.map(f => {
      const g = f.g.type === "Point" ? new ol.geom.Point(f.g.coordinates.slice(0, 2))
        : new ol.geom.LineString(f.g.coordinates.map(c => c.slice(0, 2)));
      const feat = new ol.Feature(g);
      feat.setProperties({ l: f.l, t: f.t, r: f.r, b: f.b, z: f.z });
      return feat;
    }));
  } catch (e) { /* offline */ }
}
uoEl("lg-inmeting").addEventListener("change", e => {
  lagenUo.inmeting.setVisible(e.target.checked);
  inmLaadFeatures();
});
uoEl("inm-upload").addEventListener("click", () => uoEl("inm-input").click());
uoEl("inm-input").addEventListener("change", async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  uoEl("inm-status").textContent = `Bezig: ${file.name} inlezen…`;
  try {
    const b64 = await new Promise((ok, nok) => {
      const r = new FileReader();
      r.onload = () => ok(r.result);
      r.onerror = nok;
      r.readAsDataURL(file);
    });
    const m = await uoPost("api/inmeting/upload", { bestandsnaam: file.name, data_base64: b64 });
    await inmLaadLijst();
    map.getView().fit(m.bbox, { padding: [40, 40, 40, 40], duration: 300 });
    uoEl("inm-status").textContent = `“${m.naam}” ingelezen: ${m.aantal} objecten in ` +
      `${m.lagen.length} lagen, ${m.met_hoogte} met NAP-hoogte.`;
  } catch (err) {
    uoEl("inm-status").textContent = "Inlezen mislukt: " + err.message;
  }
});

/* ======================================================== exact intekenen */
let exact = null;          // { coords, sel, undo, redo, origineel }
let actieveHulp = null;    // ol.Feature in srcHulp
let exKlikActie = null;    // "offset" | "hulpkies" | null
let exZoekPunt = null;     // laatst gezochte RD-coördinaat
const exactF = new ol.Feature(new ol.geom.LineString([]));
const exModify = new ol.interaction.Modify({
  source: srcExact,
  deleteCondition: e => ol.events.condition.altKeyOnly(e) && ol.events.condition.singleClick(e),
});
let exSnaps = [];
let exHulpDraw = null;
let exVoorSleep = null;
exModify.on("modifystart", () => { exVoorSleep = exact && exact.coords.map(c => c.slice()); });
exModify.on("modifyend", () => {
  if (!exact) return;
  const nieuw = exactF.getGeometry().getCoordinates().map(c => [rondAf(c[0]), rondAf(c[1])]);
  if (exVoorSleep) exact.undo.push(exVoorSleep);
  exact.redo = [];
  // het versleepte punt selecteren
  const i = nieuw.findIndex((c, k) => !exVoorSleep || !exVoorSleep[k] ||
    c[0] !== exVoorSleep[k][0] || c[1] !== exVoorSleep[k][1]);
  exact.coords = nieuw;
  if (i >= 0) exact.sel = i;
  exRender();
});

function exActief() { return !!exact; }

function exStart() {
  if (!resultaat) {
    statusEl.textContent = "Exact intekenen kan zodra er een tracé is (bereken of importeer er een).";
    setTimeout(() => setMode("pan"), 0);
    return;
  }
  const v = resultaat.varianten[actieveVariant];
  const g = geojson.readGeometry(v.route);
  const coords = (g.getType() === "MultiLineString" ? g.getCoordinates().flat() : g.getCoordinates())
    .map(c => [rondAf(c[0]), rondAf(c[1])]);
  exact = { coords, sel: 0, undo: [], redo: [], origineel: coords.map(c => c.slice()) };
  exactF.getGeometry().setCoordinates(coords);
  srcExact.clear();
  srcExact.addFeature(exactF);
  lagen.routes.setVisible(false);
  lagen.segments.setVisible(false);
  lagenUo.vang.setVisible(true);
  map.addInteraction(exModify);
  exZetSnaps();
  uoEl("exact-paneel").classList.remove("dicht");
  uoEl("uo-paneel").classList.add("dicht");
  uoEl("ex-info").textContent = `${v.naam}${v.vastgelegd ? " · vastgelegd " + v.vastgelegd.datum : " · berekend tracé"}`;
  exLaadVang();
  exRender();
  statusEl.textContent = "Exact intekenen: sleep hoekpunten (ze vangen op BGT, kadaster, " +
    "inmeting, KLIC en hulplijnen) of typ coördinaten in het paneel rechts.";
}

function exStop() {
  if (!exact) return;
  exact = null;
  exKlikActie = null;
  map.removeInteraction(exModify);
  exSnaps.forEach(s => map.removeInteraction(s));
  exSnaps = [];
  if (exHulpDraw) { map.removeInteraction(exHulpDraw); exHulpDraw = null; }
  srcExact.clear();
  srcExactSel.clear();
  lagen.routes.setVisible(true);
  lagen.segments.setVisible(uoEl("lg-seg").checked);
  lagenUo.vang.setVisible(false);
  uoEl("exact-paneel").classList.add("dicht");
}

function exZetSnaps() {
  exSnaps.forEach(s => map.removeInteraction(s));
  exSnaps = [];
  if (!exact) return;
  const bronnen = [];
  if (uoEl("ex-v-hulp").checked) bronnen.push(srcHulp);
  if (uoEl("ex-v-inm").checked) bronnen.push(srcInmeting);
  if (uoEl("ex-v-klic").checked) bronnen.push(srcKlic);
  if (uoEl("ex-v-bgt").checked || uoEl("ex-v-perceel").checked) bronnen.push(srcVang);
  bronnen.forEach(src => {
    const s = new ol.interaction.Snap({ source: src, pixelTolerance: 12 });
    map.addInteraction(s);
    exSnaps.push(s);
  });
}
["ex-v-bgt", "ex-v-perceel", "ex-v-inm", "ex-v-klic", "ex-v-hulp"].forEach(id =>
  uoEl(id).addEventListener("change", () => { exZetSnaps(); exLaadVang(true); }));

let vangVolgnr = 0;
let vangBbox = null;
async function exLaadVang(forceer) {
  if (!exact) return;
  const nr = ++vangVolgnr;
  const ext = map.getView().calculateExtent(map.getSize());
  const hint = uoEl("ex-vang-hint");
  const bgt = uoEl("ex-v-bgt").checked, perc = uoEl("ex-v-perceel").checked;
  if (!bgt && !perc) { srcVang.clear(); hint.textContent = ""; return; }
  if (ext[2] - ext[0] > 700 || ext[3] - ext[1] > 700) {
    hint.textContent = "Zoom in tot straatniveau (beeld ≤ 700 m) om op BGT-randen en kadastrale grenzen te vangen.";
    return;
  }
  // ruimer ophalen dan het beeld, zodat kleine verschuivingen niets nieuws vragen
  if (!forceer && vangBbox && ext[0] >= vangBbox[0] && ext[1] >= vangBbox[1]
      && ext[2] <= vangBbox[2] && ext[3] <= vangBbox[3]) return;
  const m = 80;
  const bb = [ext[0] - m, ext[1] - m, ext[2] + m, ext[3] + m];
  hint.textContent = "BGT-randen ophalen…";
  try {
    const d = await uoJson(`api/bgt/lijnen?bbox=${bb.map(v => v.toFixed(1)).join(",")}&percelen=${perc}`);
    if (nr !== vangVolgnr) return;
    vangBbox = bb;
    srcVang.clear();
    srcVang.addFeatures(d.features.filter(f => f.c === "perceel" ? perc : bgt).map(f => {
      const feat = new ol.Feature(new ol.geom.LineString(f.g));
      feat.set("c", f.c);
      feat.set("f", f.f);
      return feat;
    }));
    hint.textContent = `${srcVang.getFeatures().length} randen vangbaar (BGT${perc ? " + kadaster" : ""}).`;
  } catch (e) {
    hint.textContent = "BGT-randen niet beschikbaar: " + e.message;
  }
}
map.on("moveend", () => { exLaadVang(); inmLaadFeatures(); });

function exZet(coords, sel) {
  exact.undo.push(exact.coords.map(c => c.slice()));
  exact.redo = [];
  exact.coords = coords.map(c => [rondAf(c[0]), rondAf(c[1])]);
  if (sel !== undefined) exact.sel = sel;
  exactF.getGeometry().setCoordinates(exact.coords);
  exRender();
}

function exLengtes(coords) {
  const s = [0];
  for (let i = 1; i < coords.length; i++)
    s.push(s[i - 1] + Math.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1]));
  return s;
}
const exRichting = (a, b) => ((Math.atan2(b[0] - a[0], b[1] - a[1]) * 180 / Math.PI) + 360) % 360;

function exRender() {
  if (!exact) return;
  exactF.getGeometry().setCoordinates(exact.coords);
  exactF.changed();
  const s = exLengtes(exact.coords);
  uoEl("ex-lengte").textContent = `${exact.coords.length} punten · ${fmtNL(s[s.length - 1])} m`;
  const tabel = uoEl("ex-tabel");
  const orig = exact.origineel;
  tabel.innerHTML = "<tr><th>punt</th><th>X</th><th>Y</th><th>metrering</th><th>knik</th></tr>";
  exact.coords.forEach((c, i) => {
    const tr = document.createElement("tr");
    const veranderd = !orig.some(o => Math.abs(o[0] - c[0]) < 0.005 && Math.abs(o[1] - c[1]) < 0.005);
    tr.className = (i === exact.sel ? "sel " : "") + (veranderd ? "gewijzigd" : "");
    let knik = "";
    if (i > 0 && i < exact.coords.length - 1) {
      let d = exRichting(c, exact.coords[i + 1]) - exRichting(exact.coords[i - 1], c);
      d = ((d + 540) % 360) - 180;
      knik = fmtNL(Math.abs(d), 1) + "°";
    }
    tr.innerHTML = `<td>P${i + 1}</td><td><input type="number" step="0.01" value="${c[0].toFixed(2)}"></td>` +
      `<td><input type="number" step="0.01" value="${c[1].toFixed(2)}"></td>` +
      `<td>${kmTekst(s[i])}</td><td>${knik}</td>`;
    tr.addEventListener("click", () => { if (exact.sel !== i) exSelecteer(i); });
    tr.addEventListener("dblclick", () => map.getView().animate({ center: c, duration: 250 }));
    tr.querySelectorAll("input").forEach((inp, k) => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v)) return;
      const nieuw = exact.coords.map(q => q.slice());
      nieuw[i][k] = v;
      exZet(nieuw, i);
    }));
    tabel.appendChild(tr);
  });
  exSelecteer(exact.sel);
  uoEl("ex-undo").disabled = !exact.undo.length;
  uoEl("ex-redo").disabled = !exact.redo.length;
  uoEl("ex-verwijder").disabled = exact.coords.length <= 2;
  uoEl("ex-hulp-info").textContent = actieveHulp
    ? `Actieve hulplijn: ${actieveHulp.get("oms") || "getekend"}` : "Geen actieve hulplijn.";
}

// selectie bijwerken zonder de tabel opnieuw op te bouwen (invoervelden houden focus)
function exSelecteer(i) {
  exact.sel = i;
  exactF.changed();
  const tabel = uoEl("ex-tabel");
  [...tabel.rows].forEach((r, k) => r.classList.toggle("sel", k === i + 1));
  const rij = tabel.rows[i + 1];
  if (rij) {
    const wrap = uoEl("ex-tabel-wrap");
    if (rij.offsetTop < wrap.scrollTop + 24 || rij.offsetTop > wrap.scrollTop + wrap.clientHeight - 24)
      wrap.scrollTop = rij.offsetTop - wrap.clientHeight / 2;
  }
  const c = exact.coords[i];
  uoEl("ex-sel-naam").textContent = c ? `P${i + 1}` : "";
  if (c) {
    uoEl("ex-x").value = c[0].toFixed(2);
    uoEl("ex-y").value = c[1].toFixed(2);
    if (i > 0) {
      const p = exact.coords[i - 1];
      uoEl("ex-d").value = Math.hypot(c[0] - p[0], c[1] - p[1]).toFixed(2);
      uoEl("ex-r").value = exRichting(p, c).toFixed(2);
    } else { uoEl("ex-d").value = ""; uoEl("ex-r").value = ""; }
  }
}

/* ---- puntbewerkingen */
uoEl("ex-xy-ok").addEventListener("click", () => {
  const x = parseFloat(uoEl("ex-x").value), y = parseFloat(uoEl("ex-y").value);
  if (!isFinite(x) || !isFinite(y)) return;
  const n = exact.coords.map(c => c.slice());
  n[exact.sel] = [x, y];
  exZet(n);
});
uoEl("ex-pol-ok").addEventListener("click", () => {
  const i = exact.sel;
  if (i === 0) { statusEl.textContent = "P1 heeft geen vorig punt."; return; }
  const d = parseFloat(uoEl("ex-d").value), r = parseFloat(uoEl("ex-r").value) * Math.PI / 180;
  if (!isFinite(d) || !isFinite(r)) return;
  const p = exact.coords[i - 1];
  const n = exact.coords.map(c => c.slice());
  n[i] = [p[0] + d * Math.sin(r), p[1] + d * Math.cos(r)];
  exZet(n);
});
uoEl("ex-rel-ok").addEventListener("click", () => {
  const dx = parseFloat(uoEl("ex-dx").value) || 0, dy = parseFloat(uoEl("ex-dy").value) || 0;
  const n = exact.coords.map(c => c.slice());
  n[exact.sel] = [n[exact.sel][0] + dx, n[exact.sel][1] + dy];
  exZet(n);
});
uoEl("ex-invoegen").addEventListener("click", () => {
  const i = exact.sel;
  const n = exact.coords.map(c => c.slice());
  const j = i < n.length - 1 ? i + 1 : i - 1;
  const m = [(n[i][0] + n[j][0]) / 2, (n[i][1] + n[j][1]) / 2];
  const pos = Math.max(i, j);
  n.splice(pos, 0, m);
  exZet(n, pos);
});
uoEl("ex-verwijder").addEventListener("click", () => {
  if (exact.coords.length <= 2) return;
  const n = exact.coords.map(c => c.slice());
  n.splice(exact.sel, 1);
  exZet(n, Math.min(exact.sel, n.length - 1));
});
uoEl("ex-km-ok").addEventListener("click", () => {
  const m = parseFloat(uoEl("ex-km").value);
  const s = exLengtes(exact.coords);
  if (!isFinite(m) || m <= 0 || m >= s[s.length - 1]) {
    statusEl.textContent = `Metrering moet tussen 0 en ${fmtNL(s[s.length - 1])} m liggen.`;
    return;
  }
  const i = s.findIndex(v => v > m);
  const a = exact.coords[i - 1], b = exact.coords[i];
  const t = (m - s[i - 1]) / (s[i] - s[i - 1]);
  const n = exact.coords.map(c => c.slice());
  n.splice(i, 0, [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
  exZet(n, i);
});
function exUndo() {
  if (!exact || !exact.undo.length) return;
  exact.redo.push(exact.coords);
  exact.coords = exact.undo.pop();
  exact.sel = Math.min(exact.sel, exact.coords.length - 1);
  exRender();
}
function exRedo() {
  if (!exact || !exact.redo.length) return;
  exact.undo.push(exact.coords);
  exact.coords = exact.redo.pop();
  exact.sel = Math.min(exact.sel, exact.coords.length - 1);
  exRender();
}
uoEl("ex-undo").addEventListener("click", exUndo);
uoEl("ex-redo").addEventListener("click", exRedo);
document.addEventListener("keydown", e => {
  if (!exact || !(e.ctrlKey || e.metaKey) || e.target.tagName === "INPUT") return;
  if (e.key === "z" && !e.shiftKey) { e.preventDefault(); exUndo(); }
  if (e.key === "y" || (e.key === "z" && e.shiftKey)) { e.preventDefault(); exRedo(); }
});

/* ---- hulplijnen */
function lijnSegmentBij(coords, p) {  // index van het dichtstbijzijnde lijnstuk
  let beste = { d: Infinity, i: 0 };
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1], b = coords[i];
    const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
    const d = Math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
    if (d < beste.d) beste = { d, i };
  }
  return beste.i;
}
function lijnenVan(f) {
  const g = f.getGeometry();
  const t = g.getType();
  if (t === "LineString") return [g.getCoordinates()];
  if (t === "MultiLineString") return g.getCoordinates();
  if (t === "Polygon") return g.getCoordinates();
  if (t === "MultiPolygon") return g.getCoordinates().flat();
  return [];
}
// evenwijdige polylijn (miter-hoeken) op afstand d; d > 0 = links van de looprichting
function offsetLijn(coords, d) {
  const n = [];
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1], b = coords[i];
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    n.push([-(b[1] - a[1]) / L, (b[0] - a[0]) / L]);
  }
  return coords.map((c, i) => {
    const n0 = n[Math.max(0, i - 1)], n1 = n[Math.min(n.length - 1, i)];
    let mx = n0[0] + n1[0], my = n0[1] + n1[1];
    const ml = Math.hypot(mx, my) || 1;
    mx /= ml; my /= ml;
    const cos = mx * n1[0] + my * n1[1];
    const k = d / Math.max(0.25, cos);
    return [c[0] + mx * k, c[1] + my * k];
  });
}
function maakHulp(coords, oms) {
  const f = new ol.Feature(new ol.geom.LineString(coords));
  f.set("oms", oms);
  srcHulp.addFeature(f);
  actieveHulp = f;
  lagenUo.hulp.changed();
  exRender();
}
function exKiesRand(evt) {
  let gekozen = null;
  map.forEachFeatureAtPixel(evt.pixel, f => { gekozen = f; return true; }, {
    hitTolerance: 8,
    layerFilter: l => [lagenUo.vang, lagenUo.inmeting, lagenUo.hulp, lagen.klic].includes(l),
  });
  return gekozen;
}
uoEl("ex-off-kies").addEventListener("click", () => {
  exKlikActie = "offset";
  statusEl.textContent = "Klik op de rand (gevel, verhardingsrand, perceelgrens, inmetingslijn) " +
    "aan de kant waar de hulplijn moet komen.";
});
uoEl("ex-hulp-kies").addEventListener("click", () => {
  exKlikActie = "hulpkies";
  statusEl.textContent = "Klik op een hulplijn om die actief te maken.";
});
uoEl("ex-hulp-wis").addEventListener("click", () => {
  srcHulp.clear();
  actieveHulp = null;
  exRender();
});
uoEl("ex-hulp-teken").addEventListener("click", () => {
  if (exHulpDraw) map.removeInteraction(exHulpDraw);
  exHulpDraw = new ol.interaction.Draw({ source: srcHulp, type: "LineString", maxPoints: 2 });
  exHulpDraw.on("drawend", e => {
    e.feature.set("oms", "getekend");
    actieveHulp = e.feature;
    setTimeout(() => { map.removeInteraction(exHulpDraw); exHulpDraw = null; exRender(); }, 0);
  });
  map.addInteraction(exHulpDraw);
  exZetSnaps();  // snap ná de draw-interactie toevoegen
  statusEl.textContent = "Hulplijn: klik begin- en eindpunt (vangt op de aangevinkte lagen).";
});

function hulpLijnstuk(p) {  // dichtstbijzijnde lijnstuk van de actieve hulplijn, als oneindige lijn
  const c = actieveHulp.getGeometry().getCoordinates();
  const i = lijnSegmentBij(c, p);
  return [c[i - 1], c[i]];
}
function projecteer(p, [a, b]) {
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1e-12;
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2;
  return [a[0] + t * dx, a[1] + t * dy];
}
function snijpunt([p1, p2], [p3, p4]) {
  const d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0]);
  if (Math.abs(d) < 1e-9) return null;
  const t = ((p1[0] - p3[0]) * (p3[1] - p4[1]) - (p1[1] - p3[1]) * (p3[0] - p4[0])) / d;
  return [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])];
}
function vraagHulp() {
  if (actieveHulp) return true;
  statusEl.textContent = "Maak of kies eerst een hulplijn (∥ Evenwijdig aan rand, ╱ Hulplijn tekenen).";
  return false;
}
uoEl("ex-proj").addEventListener("click", () => {
  if (!vraagHulp()) return;
  const i = exact.sel, n = exact.coords.map(c => c.slice());
  n[i] = actieveHulp.getGeometry().getClosestPoint(n[i]);
  exZet(n);
});
uoEl("ex-verleng").addEventListener("click", () => {
  if (!vraagHulp()) return;
  const i = exact.sel, n = exact.coords.map(c => c.slice());
  const j = i > 0 ? i - 1 : 1;
  const q = snijpunt([n[j], n[i]], hulpLijnstuk(n[i]));
  if (!q) { statusEl.textContent = "Lijnstuk loopt evenwijdig aan de hulplijn."; return; }
  n[i] = q;
  exZet(n);
});
uoEl("ex-seg").addEventListener("click", () => {
  if (!vraagHulp()) return;
  const i = exact.sel, n = exact.coords.map(c => c.slice());
  if (i >= n.length - 1) { statusEl.textContent = "Kies het beginpunt van een lijnstuk."; return; }
  const ls = hulpLijnstuk([(n[i][0] + n[i + 1][0]) / 2, (n[i][1] + n[i + 1][1]) / 2]);
  n[i] = projecteer(n[i], ls);
  n[i + 1] = projecteer(n[i + 1], ls);
  exZet(n);
});

/* ---- kaartklik in exacte modus */
map.on("click", evt => {
  if (mode !== "exact" || !exact) return;
  if (exKlikActie === "offset") {
    const f = exKiesRand(evt);
    exKlikActie = null;
    if (!f) { statusEl.textContent = "Geen rand geraakt — probeer opnieuw (∥ Evenwijdig aan rand)."; return; }
    const d = parseFloat(uoEl("ex-off").value) || 0;
    const lijn = lijnenVan(f).reduce((best, l) => {
      const i = lijnSegmentBij(l, evt.coordinate);
      const p = projecteer(evt.coordinate, [l[i - 1], l[i]]);
      const afst = Math.hypot(p[0] - evt.coordinate[0], p[1] - evt.coordinate[1]);
      return !best || afst < best.afst ? { l, i, afst } : best;
    }, null);
    if (!lijn || lijn.l.length < 2) return;
    // stuk van ± 60 m rond de klik, zodat een lange rand (perceelring) niet helemaal meegaat
    const l = lijn.l, i = lijn.i;
    let a = i - 1, b = i, s = 0;
    while (a > 0 && s < 60) { s += Math.hypot(l[a][0] - l[a - 1][0], l[a][1] - l[a - 1][1]); a--; }
    s = 0;
    while (b < l.length - 1 && s < 60) { s += Math.hypot(l[b + 1][0] - l[b][0], l[b + 1][1] - l[b][1]); b++; }
    const stuk = l.slice(a, b + 1).map(c => c.slice(0, 2));
    const seg = [l[i - 1], l[i]];
    const kant = Math.sign((seg[1][0] - seg[0][0]) * (evt.coordinate[1] - seg[0][1]) -
                           (seg[1][1] - seg[0][1]) * (evt.coordinate[0] - seg[0][0])) || 1;
    let off = offsetLijn(stuk, kant * d);
    if (off.length === 2) {  // los lijnstuk: 30 m verlengen naar beide kanten
      const [p, q] = off, L = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
      const ux = (q[0] - p[0]) / L, uy = (q[1] - p[1]) / L;
      off = [[p[0] - ux * 30, p[1] - uy * 30], [q[0] + ux * 30, q[1] + uy * 30]];
    }
    const bron = f.get("c") ? (f.get("c") === "perceel" ? "perceelgrens" : `BGT ${f.get("c")}`)
      : f.get("l") ? `inmeting ${f.get("l")}` : srcHulp.hasFeature(f) ? "hulplijn" : "KLIC";
    maakHulp(off, `${fmtNL(d)} m uit ${bron}`);
    statusEl.textContent = `Hulplijn op ${fmtNL(d)} m uit ${bron}. Leg punten erop met ⊥, ⇥ of ═.`;
    return;
  }
  if (exKlikActie === "hulpkies") {
    exKlikActie = null;
    let f = null;
    map.forEachFeatureAtPixel(evt.pixel, x => { f = x; return true; },
      { hitTolerance: 8, layerFilter: l => l === lagenUo.hulp });
    if (f) {
      actieveHulp = f;
      lagenUo.hulp.changed();
      uoEl("ex-hulp-info").textContent = `Actieve hulplijn: ${f.get("oms") || "getekend"}`;
    }
    return;
  }
  // hoekpunt selecteren
  let beste = null;
  exact.coords.forEach((c, i) => {
    const px = map.getPixelFromCoordinate(c);
    const d = Math.hypot(px[0] - evt.pixel[0], px[1] - evt.pixel[1]);
    if (d <= 12 && (!beste || d < beste.d)) beste = { d, i };
  });
  if (beste) exSelecteer(beste.i);
});

/* ---- coördinaat zoeken (RD of ETRS89) */
uoEl("ex-zoek-ok").addEventListener("click", async () => {
  const t = uoEl("ex-zoek").value.trim().replace(/;/g, " ");
  const getallen = (t.match(/-?\d+(?:[.,]\d+)?/g) || []).map(s => parseFloat(s.replace(",", ".")));
  const info = uoEl("ex-zoek-info");
  if (getallen.length < 2) { info.textContent = "Geef twee getallen: X Y (RD) of lat, lon."; return; }
  let [a, b] = getallen;
  try {
    if (Math.abs(a) <= 90 && Math.abs(b) <= 180) {
      const lat = Math.max(a, b) > 45 ? Math.max(a, b) : a, lon = lat === a ? b : a;
      const r = await uoJson(`api/rdnap/naar-rd?lat=${lat}&lon=${lon}`);
      exZoekPunt = [rondAf(r.x), rondAf(r.y)];
      info.textContent = `ETRS89 ${lat}, ${lon} → RD X ${fmtNL(r.x)} Y ${fmtNL(r.y)} ` +
        (r.nauwkeurig ? "(RDNAPTRANS™2018)" : "(buiten het correctiegrid: ± 0,25 m)");
    } else {
      exZoekPunt = [rondAf(a), rondAf(b)];
      info.textContent = `RD X ${fmtNL(a)} Y ${fmtNL(b)}`;
    }
  } catch (e) { info.textContent = e.message; return; }
  srcExactSel.clear();
  srcExactSel.addFeature(new ol.Feature(new ol.geom.Point(exZoekPunt)));
  map.getView().animate({ center: exZoekPunt, duration: 300 });
});
uoEl("ex-zoek-punt").addEventListener("click", () => {
  if (!exZoekPunt || !exact) return;
  const n = exact.coords.map(c => c.slice());
  n.splice(exact.sel + 1, 0, exZoekPunt.slice());
  exZet(n, exact.sel + 1);
});

/* ---- vastleggen / annuleren */
uoEl("ex-vastleggen").addEventListener("click", async () => {
  if (!exact) return;
  const knop = uoEl("ex-vastleggen");
  knop.disabled = true;
  const poll = uoVoortgang(statusEl);
  statusEl.textContent = "Bezig: tracé vastleggen en registers opnieuw bepalen…";
  try {
    const r = await uoPost("api/trace/vastleggen", {
      variant: actieveVariant, coords: exact.coords,
      projectnaam: uoEl("project-naam").value.trim(),
      haspel_m: parseFloat(uoEl("opt-haspel").value) || 500,
    });
    resultaat = r;
    setMode("pan");
    toonResultaat();
    const v = resultaat.varianten[actieveVariant];
    statusEl.textContent = `Tracé vastgelegd: ${v.route.coordinates.length} punten, ` +
      `${fmtNL(v.lengte_m, 1)} m; registers, toetsing en maatvoering bijgewerkt. ` +
      "Sla het project op om dit te bewaren.";
  } catch (e) {
    statusEl.textContent = "Vastleggen mislukt: " + e.message;
  } finally {
    clearInterval(poll);
    knop.disabled = false;
  }
});
uoEl("ex-annuleer").addEventListener("click", () => {
  if (exact && exact.undo.length && !confirm("Wijzigingen in de ligging vervallen. Doorgaan?")) return;
  setMode("pan");
  statusEl.textContent = "Exact intekenen geannuleerd — de ligging is ongewijzigd.";
});
uoEl("ex-sluit").addEventListener("click", () => uoEl("ex-annuleer").click());

/* haak vanuit setMode (app.js) */
function uoModusGewijzigd(nieuw) {
  if (nieuw === "exact") { if (!exact) exStart(); }
  else if (exact) exStop();
  uoEl("cursor-xy").hidden = nieuw !== "exact" && nieuw !== "maat";
}

/* ============================================================ werktekeningen */
let uoSet = null;   // laatste /api/tekening/set
const STEMPEL_VELDEN = [
  ["opdrachtgever", "Opdrachtgever"], ["ontwerpbureau", "Ontwerpbureau"],
  ["projectnummer", "Projectnummer"], ["werkordernummer", "Werkordernummer"],
  ["ontwerper", "Ontwerper"], ["tekenaar", "Getekend"], ["controleur", "Gecontroleerd"],
  ["fase", "Fase"], ["tekeningnummer_prefix", "Tekeningnr.-prefix"],
];
const BLADSOORTEN = [["overzicht", "overzicht"], ["situatie", "situatiebladen"],
  ["boorprofielen", "boorprofielen"], ["dwarsprofielen", "dwarsprofielen"],
  ["uitzetstaat", "uitzetstaat"]];
const ONDERGROND = [["bgt", "BGT"], ["kadaster", "kadaster"], ["klic", "KLIC"],
  ["inmeting", "inmeting"], ["bomen", "bomen"]];

(function bouwFormulier() {
  uoEl("uo-stempel").innerHTML = STEMPEL_VELDEN.map(([k, t]) =>
    `<label>${t}<input data-stempel="${k}"></label>`).join("");
  uoEl("uo-bladsoorten").innerHTML = "<span class='hint'>Bladen:</span>" + BLADSOORTEN.map(([k, t]) =>
    `<label><input type="checkbox" data-blad="${k}"> ${t}</label>`).join("");
  uoEl("uo-ondergrond").innerHTML = "<span class='hint'>Ondergrond:</span>" + ONDERGROND.map(([k, t]) =>
    `<label><input type="checkbox" data-og="${k}"> ${t}</label>`).join("");
})();

function uoLeesInstellingen() {
  const stempel = {};
  document.querySelectorAll("[data-stempel]").forEach(i => { stempel[i.dataset.stempel] = i.value.trim(); });
  const bladen = {}, ondergrond = {};
  document.querySelectorAll("[data-blad]").forEach(i => { bladen[i.dataset.blad] = i.checked; });
  document.querySelectorAll("[data-og]").forEach(i => { ondergrond[i.dataset.og] = i.checked; });
  return {
    papier: uoEl("uo-papier").value, schaal: parseInt(uoEl("uo-schaal").value, 10),
    overlap_m: parseFloat(uoEl("uo-overlap").value) || 0,
    km_interval_m: parseFloat(uoEl("uo-km").value) || 25,
    stempel, bladen, ondergrond,
  };
}

function uoVulFormulier(inst) {
  uoEl("uo-papier").value = inst.papier;
  uoEl("uo-schaal").value = String(inst.schaal);
  uoEl("uo-overlap").value = inst.overlap_m;
  uoEl("uo-km").value = inst.km_interval_m;
  document.querySelectorAll("[data-stempel]").forEach(i => { i.value = inst.stempel[i.dataset.stempel] || ""; });
  document.querySelectorAll("[data-blad]").forEach(i => { i.checked = inst.bladen[i.dataset.blad] !== false; });
  document.querySelectorAll("[data-og]").forEach(i => { i.checked = inst.ondergrond[i.dataset.og] !== false; });
  uoEl("uo-voorschrift-bron").textContent = "Tekenvoorschrift: " + (inst.bron || "standaard");
}

function uoToon(d) {
  uoSet = d;
  if (resultaat) resultaat.varianten[actieveVariant].tekeningen = d.tekeningen;
  uoVulFormulier(d.instellingen);
  const v = resultaat.varianten[actieveVariant];
  uoEl("uo-info").textContent = `${v.naam} · ${fmtNL(v.lengte_m / 1000, 2)} km`;
  const meld = uoEl("uo-melding");
  const revs = d.revisies.filter(r => r.status !== "vervallen");
  const laatste = revs[revs.length - 1];
  if (!d.vastgelegd) {
    meld.hidden = false;
    meld.className = "uo-melding";
    meld.textContent = "Dit is een berekend tracé. Leg de ligging voor het UO eerst exact vast " +
      "(✎ Exact intekenen → ✓ Tracé vastleggen), bij voorkeur op een ingemeten ondergrond.";
  } else if (d.gewijzigd_na_revisie && laatste) {
    meld.hidden = false;
    meld.className = "uo-melding";
    meld.textContent = `De ligging is gewijzigd na revisie ${laatste.rev}. Maak een nieuwe ` +
      "revisie: de wijzigingen krijgen dan een wolk op de bladen.";
  } else {
    meld.hidden = false;
    meld.className = "uo-melding ok";
    meld.textContent = `Tracé vastgelegd op ${d.vastgelegd.datum}` +
      (laatste ? ` · revisie ${laatste.rev} (${laatste.status})` : " · nog geen revisie uitgegeven");
  }
  uoEl("uo-bladen-info").textContent = `${d.bladen.length} situatiebladen op 1:${d.instellingen.schaal} ` +
    `(${d.instellingen.papier}), ${d.boringen} boring(en) met lengteprofiel.`;
  uoEl("uo-dwg").disabled = !d.dwg;
  uoEl("uo-dwg").title = d.dwg ? "DWG (AutoCAD 2018) via de ODA File Converter"
    : "DWG vraagt de gratis ODA File Converter op de server; de DXF opent direct in AutoCAD/BricsCAD/Civil 3D.";
  // bladindeling en wolken op de kaart
  srcBladen.clear();
  const geraakt = new Set(laatste ? laatste.bladen_gewijzigd || [] : []);
  d.bladen.forEach(b => {
    const f = new ol.Feature(new ol.geom.Polygon([[...b.polygoon, b.polygoon[0]]]));
    f.set("nr", b.nr);
    f.set("geraakt", revs.length > 1 && geraakt.has(b.nr));
    srcBladen.addFeature(f);
  });
  srcWolken.clear();
  (laatste ? laatste.wolken || [] : []).forEach(w => {
    const f = new ol.Feature(new ol.geom.Polygon([[...w, w[0]]]));
    f.set("rev", laatste.rev);
    srcWolken.addFeature(f);
  });
  lagenUo.bladen.setVisible(uoEl("uo-toon-bladen").checked && !uoEl("uo-paneel").classList.contains("dicht"));
  lagenUo.wolken.setVisible(lagenUo.bladen.getVisible());
  // revisietabel
  const wrap = uoEl("uo-revisies");
  if (!d.revisies.length) {
    wrap.innerHTML = "<p class='hint'>Nog geen revisie. De eerste revisie (A) legt de uitgangssituatie vast; " +
      "volgende revisies krijgen wijzigingswolken.</p>";
  } else {
    wrap.innerHTML = "<table><tr><th>rev</th><th>datum</th><th>omschrijving</th><th>status</th><th>akkoord</th></tr>" +
      d.revisies.map(r => `<tr><td><b>${r.rev}</b></td><td>${r.datum}</td><td>${r.omschrijving}` +
        `${r.door ? " <span class='hint'>(" + r.door + ")</span>" : ""}` +
        `${(r.wolken || []).length ? ` <span class='hint'>${r.wolken.length} wolk(en), blad ${(r.bladen_gewijzigd || []).join(", ")}</span>` : ""}</td>` +
        `<td><select data-rev="${r.rev}">${["concept", "ter controle", "akkoord", "vervallen"].map(s =>
          `<option ${s === r.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>` +
        `<td>${r.akkoord_door ? r.akkoord_door + "<br><span class='hint'>" + r.akkoord_datum + "</span>" : ""}</td></tr>`).join("") +
      "</table>";
    wrap.querySelectorAll("select[data-rev]").forEach(sel => sel.addEventListener("change", async () => {
      let naam = "";
      if (sel.value === "akkoord") {
        naam = prompt("Akkoord gegeven door (naam):", "") || "";
        if (!naam) { sel.value = d.revisies.find(r => r.rev === sel.dataset.rev).status; return; }
      }
      try {
        uoToon(await uoPost("api/tekening/status",
          { variant: actieveVariant, rev: sel.dataset.rev, status: sel.value, naam }));
      } catch (e) { uoEl("uo-dl-info").textContent = e.message; }
    }));
  }
}

async function uoOpen() {
  if (!resultaat) return;
  if (exact) setMode("pan");
  uoEl("zro-detail").classList.add("dicht");
  uoEl("uo-paneel").classList.remove("dicht");
  try {
    uoToon(await uoJson("api/tekening/set?variant=" + actieveVariant));
  } catch (e) {
    uoEl("uo-melding").hidden = false;
    uoEl("uo-melding").textContent = e.message;
  }
}
function uoSluit() {
  uoEl("uo-paneel").classList.add("dicht");
  lagenUo.bladen.setVisible(false);
  lagenUo.wolken.setVisible(false);
}
uoEl("btn-uo").addEventListener("click", uoOpen);
uoEl("uo-sluit").addEventListener("click", uoSluit);
uoEl("uo-toon-bladen").addEventListener("change", e => {
  lagenUo.bladen.setVisible(e.target.checked);
  lagenUo.wolken.setVisible(e.target.checked);
});
["uo-papier", "uo-schaal", "uo-overlap"].forEach(id => uoEl(id).addEventListener("change", async () => {
  // bladindeling direct herzien (projectinstellingen)
  try {
    uoToon(await uoPost("api/tekening/instellingen",
      { variant: actieveVariant, instellingen: uoLeesInstellingen() }));
  } catch (e) { uoEl("uo-dl-info").textContent = e.message; }
}));
uoEl("uo-inst-opslaan").addEventListener("click", async () => {
  try {
    uoToon(await uoPost("api/tekening/instellingen",
      { variant: actieveVariant, instellingen: uoLeesInstellingen() }));
    uoEl("uo-dl-info").textContent = "Instellingen bij dit project bewaard (sla het project op).";
  } catch (e) { uoEl("uo-dl-info").textContent = e.message; }
});
uoEl("uo-inst-standaard").addEventListener("click", async () => {
  if (!confirm("Deze instellingen als tekenvoorschrift voor alle projecten opslaan?")) return;
  try {
    const inst = uoLeesInstellingen();
    const huidig = await uoJson("api/tekening/voorschrift");
    await uoPost("api/tekening/voorschrift", { ...inst, laagnamen: huidig.laagnamen,
      bron: "Tekenvoorschrift aangepast op " + new Date().toLocaleDateString("nl-NL") });
    uoEl("uo-dl-info").textContent = "Opgeslagen als standaard tekenvoorschrift.";
    uoToon(await uoJson("api/tekening/set?variant=" + actieveVariant));
  } catch (e) { uoEl("uo-dl-info").textContent = e.message; }
});

async function uoDownload(url, naam) {
  const info = uoEl("uo-dl-info");
  const knoppen = ["uo-pdf", "uo-dxf", "uo-dwg", "uo-csv"].map(uoEl);
  knoppen.forEach(k => { k.dataset.was = k.disabled; k.disabled = true; });
  const poll = uoVoortgang(info);
  info.textContent = "Bezig: tekeningen opbouwen…";
  try {
    // instellingen eerst bewaren, zodat de download klopt met het formulier
    await uoPost("api/tekening/instellingen",
      { variant: actieveVariant, instellingen: uoLeesInstellingen() });
    const r = await fetch(url);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = naam;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    info.textContent = `${naam} gedownload (${fmtNL(blob.size / 1e6, 1)} MB).`;
  } catch (e) {
    info.textContent = "Mislukt: " + e.message;
  } finally {
    clearInterval(poll);
    knoppen.forEach(k => { k.disabled = k.dataset.was === "true"; });
  }
}
function uoQuery() {
  const p = new URLSearchParams({ variant: actieveVariant,
    projectnaam: uoEl("project-naam").value.trim() });
  const sel = uoEl("uo-selectie").value.trim();
  if (sel) p.set("bladen", sel);
  return p;
}
const uoBestand = ext => (uoEl("project-naam").value.trim() || "infraengine")
  .replace(/[^\w-]+/g, "_") + "_werktekeningen." + ext;
uoEl("uo-pdf").addEventListener("click", () =>
  uoDownload("api/tekening/pdf?" + uoQuery(), uoBestand("pdf")));
uoEl("uo-dxf").addEventListener("click", () =>
  uoDownload("api/tekening/dxf?" + uoQuery(), uoBestand("dxf")));
uoEl("uo-dwg").addEventListener("click", () => {
  const p = uoQuery();
  p.set("formaat", "dwg");
  uoDownload("api/tekening/dxf?" + p, uoBestand("dwg"));
});
uoEl("uo-csv").addEventListener("click", () =>
  uoDownload("api/tekening/uitzetstaat.csv?" + uoQuery(), uoBestand("csv").replace("werktekeningen", "uitzetstaat")));

uoEl("uo-rev-nieuw").addEventListener("click", async () => {
  const oms = uoEl("uo-rev-oms").value.trim();
  if (uoSet && uoSet.revisies.length && !oms) {
    uoEl("uo-dl-info").textContent = "Geef een omschrijving van de wijziging.";
    return;
  }
  try {
    const d = await uoPost("api/tekening/revisie", { variant: actieveVariant, omschrijving: oms,
      door: uoEl("uo-rev-door").value.trim(), definitief: uoEl("uo-rev-def").checked });
    uoEl("uo-rev-oms").value = "";
    uoToon(d);
    const r = d.revisie;
    uoEl("uo-dl-info").textContent = `Revisie ${r.rev} aangemaakt` + (r.wolken.length
      ? `: ${r.wolken.length} gewijzigd(e) gebied(en) op blad ${r.bladen_gewijzigd.join(", ")}.`
      : ".") + " Sla het project op om de revisie te bewaren.";
  } catch (e) { uoEl("uo-dl-info").textContent = e.message; }
});

/* haak vanuit toonResultaat (app.js) */
function uoNaResultaat() {
  if (exact) setMode("pan");  // variantwissel of nieuw resultaat: bewerking vervalt
  uoEl("btn-uo").disabled = !resultaat;
  if (!resultaat) { uoSluit(); srcBladen.clear(); srcWolken.clear(); return; }
  if (!uoEl("uo-paneel").classList.contains("dicht")) uoOpen();
}

inmLaadLijst();
