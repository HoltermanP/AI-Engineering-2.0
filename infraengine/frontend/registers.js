/* InfraEngine — registerpagina (FO §6): alle registers op een eigen pagina
   (#registers) in tabellen met sorteren, kolomfilters, vrije zoektekst en
   inline bewerken; de kaart schuift mee naar het rechterpaneel en zoomt naar
   de geselecteerde rij.

   Laadt na app.js en gebruikt de daar gedefinieerde globalen:
   map, resultaat, actieveVariant, zoomNaar, toonResultaat, eur, WT_CHIP,
   openZroDetail, ververZroRegister. Bewerkingen gaan naar
   POST /api/register/update; ZRO-dossiervelden via POST /api/zro/detail. */
"use strict";

/* ------------------------------------------------------- opties (spiegels) */
// spiegel van backend/zro.py
const REG_ZRO_STATUSSEN = [
  "contact te leggen", "contact gelegd", "in onderhandeling", "akkoord bereikt",
  "overeenkomst opgesteld", "bij notaris", "gevestigd",
  "geweigerd / gedoogplicht (BP2024)",
];
const REG_AARD_OPTIES = [
  "opstalrecht", "erfdienstbaarheid", "kwalitatieve verplichting",
  "gedoogplicht (Belemmeringenwet / Ow)", "huur / gebruiksovereenkomst",
];
// spiegel van backend/engine.py (TECHNIEK_*)
const REG_TECHNIEKEN = [
  "Open sleuf", "Gestuurde boring (HDD)", "Persing (mantelbuis)",
  "Nanodrill", "Raketboring (ongestuurd)",
];
const REG_STATUS_VERGUNNING = [
  "nog aan te vragen", "in voorbereiding", "aangevraagd", "verleend",
  "geweigerd", "niet nodig",
];
const REG_STATUS_UITVOERING = [
  "open", "in voorbereiding", "ingepland", "uitgevoerd", "vervallen",
];
const REG_STATUS_PLANNING = [
  "gepland", "in voorbereiding", "in uitvoering", "gereed", "vertraagd",
];

const regEsc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* -------------------------------------------------------- registerdefinities
   kolom: k (veld), label, num (rechts + numeriek sorteren), toon(r) (weergave),
   sorteer(r), chip(r) (chipklasse of null voor "—"), edit (true of {opties}),
   dossier (ZRO: dossierveld waarnaar de bewerking geschreven wordt). */
const REG_DEF = {
  werkpakketten: {
    titel: "Werkpakketten",
    data: v => v.werkpakketten || [],
    zoom: r => r.geometry,
    leeg: "Geen werkpakketten — herbereken het tracé.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "naam", label: "Naam", edit: true },
      { k: "van_station", label: "Van station", num: true },
      { k: "tot_station", label: "Tot station", num: true },
      { k: "chainage_van_m", label: "Van (m)", num: true },
      { k: "chainage_tot_m", label: "Tot (m)", num: true },
      { k: "lengte_m", label: "Lengte (m)", num: true },
    ],
  },
  planning: {
    titel: "Uitvoeringsplanning",
    data: v => v.planning || [],
    leeg: "Geen uitvoeringsplanning — herbereken het tracé.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "fase", label: "Fase" },
      { k: "subfase", label: "Subfase" },
      { k: "start_wk", label: "Start (wk)", num: true },
      { k: "eind_wk", label: "Eind (wk)", num: true },
      { k: "duur_wk", label: "Duur (wk)", num: true },
      { k: "status", label: "Status", edit: { opties: REG_STATUS_PLANNING },
        chip: () => "" },
      { k: "toelichting", label: "Toelichting", edit: true },
    ],
  },
  vergunningen: {
    titel: "Vergunningen",
    data: v => v.vergunningen,
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "item", label: "Item", edit: true },
      { k: "bevoegd_gezag", label: "Bevoegd gezag", edit: true },
      { k: "trigger", label: "Trigger", edit: true },
      { k: "doorlooptijd_wk", label: "Doorloop", num: true,
        toon: r => r.doorlooptijd_wk[0] + "–" + r.doorlooptijd_wk[1] + " wk",
        sorteer: r => r.doorlooptijd_wk[1] },
      { k: "status", label: "Status", edit: { opties: REG_STATUS_VERGUNNING },
        chip: () => "" },
      { k: "verantwoordelijke", label: "Verantwoordelijke", edit: true },
    ],
  },
  kruisingen: {
    titel: "Kruisingen",
    // alleen bijzondere punten; standaard open ontgravingen (sloot buiten de
    // legger, erftoegang zonder wegbeheerder) zijn sleufwerk en blijven
    // verborgen tot de schakelaar aanstaat
    data: v => (toonStandaardOpen ? v.kruisingen : v.kruisingen.filter(isBijzonderPunt)),
    schakelaar: v => {
      const n = v.kruisingen.filter(c => !isBijzonderPunt(c)).length;
      return n ? {
        label: `Ook de ${n} standaard open ontgraving(en) tonen (geen bijzonder punt: gewoon sleufwerk)`,
        aan: toonStandaardOpen,
        zet: aan => { toonStandaardOpen = aan; },
      } : null;
    },
    zoom: r => r.punt,
    kolommen: [
      { k: "nr", label: "Nr", num: true },
      { k: "werkpakket", label: "WP" },
      { k: "soort", label: "Soort" },
      { k: "breedte_m", label: "Breedte haaks (m)", num: true },
      { k: "kruislengte_m", label: "Langs tracé (m)", num: true },
      { k: "techniek", label: "Techniek", edit: { opties: REG_TECHNIEKEN },
        chip: r => (r.techniek || "").includes("HDD") ? "hdd" : "" },
      { k: "bijzonder_reden", label: "Bijzonder punt",
        toon: r => (isBijzonderPunt(r) ? "ja" : "nee") + (r.bijzonder_reden ? ` — ${r.bijzonder_reden}` : ""),
        sorteer: r => (isBijzonderPunt(r) ? "ja" : "nee") },
      { k: "noodzaak", label: "Noodzaak", edit: true },
      { k: "detail", label: "Detail", edit: true },
      { k: "richtlijn", label: "Richtlijn" },
      { k: "bevoegd_gezag", label: "Bevoegd gezag", edit: true },
      { k: "werkterrein", label: "Werkterrein",
        toon: r => r.werkterrein ? r.werkterrein.oordeel : "",
        sorteer: r => r.werkterrein ? r.werkterrein.oordeel : "",
        chip: r => r.werkterrein ? (WT_CHIP[r.werkterrein.oordeel] || "") : null },
    ],
  },
  boringen: {
    titel: "Boringen",
    data: v => v.boringen,
    zoom: r => ({ type: "LineString",
                  coordinates: [r.intredepunt_rd, r.uittredepunt_rd] }),
    leeg: "Geen boringen: alle kruisingen kunnen open of er zijn geen kruisingen.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "type", label: "Type", edit: { opties: REG_TECHNIEKEN } },
      { k: "obstakel", label: "Obstakel" },
      { k: "kruisingen", label: "Kruisingen",
        toon: r => (r.kruisingen || [r.kruising]).join(", ") },
      { k: "noodzaak", label: "Noodzaak", edit: true },
      { k: "lengte_m", label: "Lengte (m)", num: true },
      { k: "circuits", label: "Circuits", num: true },
      { k: "intredepunt_rd", label: "Intrede (RD)", num: true,
        toon: r => r.intredepunt_rd.join(", ") },
      { k: "uittredepunt_rd", label: "Uittrede (RD)", num: true,
        toon: r => r.uittredepunt_rd.join(", ") },
      { k: "dekking_eis", label: "Dekking-eis", edit: true },
      { k: "mantelbuis", label: "Mantelbuis", edit: true },
      { k: "mantelbuis_motivering", label: "Waarom dit minimum" },
      { k: "samengevoegd", label: "Samengevoegd" },
      { k: "sonderingen", label: "Sonderingen (BRO)",
        toon: r => (r.sonderingen && r.sonderingen.length)
          ? r.sonderingen.map(s => `${s.nr} (${s.bro_id})`).join(", ")
          : (r.sonderingen_bro || ""),
        sorteer: r => (r.sonderingen || []).length },
      { k: "werkterrein_oordeel", label: "Werkterrein",
        chip: r => WT_CHIP[r.werkterrein_oordeel] ?? "" },
      { k: "status", label: "Status", edit: { opties: REG_STATUS_UITVOERING },
        chip: () => "" },
      { k: "verantwoordelijke", label: "Verantwoordelijke", edit: true },
    ],
  },
  sonderingen: {
    titel: "Sonderingen",
    data: v => v.sonderingen || [],
    zoom: r => r.punt,
    leeg: "Geen bestaande sonderingen (BRO) binnen de zoekafstand van het tracé.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "bro_id", label: "BRO-ID" },
      { k: "chainage_m", label: "Chainage (m)", num: true },
      { k: "afstand_trace_m", label: "Afstand tracé (m)", num: true },
      { k: "einddiepte_m", label: "Einddiepte (m)", num: true },
      { k: "maaiveld_nap", label: "Maaiveld (m NAP)", num: true },
      { k: "kwaliteitsklasse", label: "Klasse" },
      { k: "norm", label: "Norm" },
      { k: "datum", label: "Datum" },
      { k: "relevantie", label: "Relevantie", edit: true,
        toon: r => (r.boringen && r.boringen.length)
          ? `nabij ${r.boringen.join(", ")}` : (r.relevantie || "langs tracé") },
      { k: "bro_loket", label: "BRO-loket", knop: r => ({
          tekst: "loket ↗",
          titel: "Sondering openen in het BRO-loket",
          klik: () => window.open(r.bro_loket, "_blank", "noopener"),
        }) },
      { k: "opmerking", label: "Opmerking", edit: true },
      { k: "status", label: "Status", edit: { opties: REG_STATUS_UITVOERING },
        chip: () => "" },
    ],
  },
  onderzoeken: {
    titel: "Onderzoeken",
    data: v => v.onderzoeken || [],
    leeg: "Geen onderzoeken nodig voor dit tracé.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "soort", label: "Onderzoek", edit: true },
      { k: "aanleiding", label: "Aanleiding", edit: true },
      { k: "conclusie", label: "Conclusie", edit: true },
      { k: "status", label: "Status", edit: { opties: REG_STATUS_UITVOERING },
        chip: () => "" },
      { k: "verantwoordelijke", label: "Verantwoordelijke", edit: true },
    ],
  },
  zro: {
    titel: "ZRO",
    data: () => zroRijen(),
    zoom: r => r.geometry || null,
    leeg: "Het tracé kruist geen kadastrale percelen die een ZRO vragen.",
    kolommen: [
      { k: "nr", label: "Nr" },
      { k: "werkpakket", label: "WP" },
      { k: "perceel", label: "Perceel" },
      { k: "eigenaar_dossier", label: "Eigenaar", edit: true,
        dossier: "eigenaar_naam",
        toon: r => r.eigenaar_dossier || r.eigenaar || "" },
      { k: "eigendom", label: "Eigendom",
        toon: r => r.eigendom || "onbekend",
        chip: r => eigendomKlasse(r.eigendom),
        titel: r => r.eigendom_toelichting || "" },
      { k: "ingenomen_lengte_m", label: "Lengte (m)", num: true },
      { k: "werkstrook_m2", label: "Werkstrook (m²)", num: true },
      { k: "aard_recht_dossier", label: "Aard recht",
        edit: { opties: REG_AARD_OPTIES }, dossier: "aard_recht",
        toon: r => r.aard_recht_dossier || r.aard_recht || "" },
      { k: "vergoeding_eenmalig_eur", label: "Vergoeding (€)", num: true,
        edit: true, dossier: "vergoeding_eenmalig_eur",
        toon: r => r.vergoeding_eenmalig_eur == null ? ""
          : r.vergoeding_eenmalig_eur.toLocaleString("nl-NL")
            + (r.vergoeding_jaarlijks_eur
               ? ` (+${r.vergoeding_jaarlijks_eur.toLocaleString("nl-NL")}/jr)` : "") },
      { k: "status", label: "Status", edit: { opties: REG_ZRO_STATUSSEN },
        dossier: "status", chip: r => zroStatusKlasse(r.status) },
      { k: "dossier", label: "Dossier", knop: r => ({
          tekst: (r.compleet_totaal
            ? `${r.compleet_ok}/${r.compleet_totaal} ✓ · ${r.n_bijlagen} bijl.`
            : "dossier") + " ↗",
          titel: "ZRO-dossier openen (tekening, overeenkomst, bijlagen)",
          klik: () => { if (typeof openZroDetail === "function") openZroDetail(r.nr); },
        }) },
    ],
  },
  segmenten: {
    titel: "Segmenten",
    data: v => v.segmenten,
    zoom: r => r.geometry,
    kolommen: [
      { k: "nr", label: "Nr", num: true },
      { k: "werkpakket", label: "WP" },
      { k: "ligging", label: "Ligging" },
      { k: "van_m", label: "Van (m)", num: true },
      { k: "tot_m", label: "Tot (m)", num: true },
      { k: "lengte_m", label: "Lengte (m)", num: true },
    ],
  },
  toetsing: {
    titel: "Toetsing",
    data: v => v.toetsing,
    zoom: r => r.punt || null,
    kolommen: [
      { k: "ernst", label: "Ernst", chip: r => r.ernst },
      { k: "werkpakket", label: "WP" },
      { k: "toets", label: "Toets" },
      { k: "grondslag", label: "Grondslag" },
      { k: "melding", label: "Melding" },
    ],
  },
  moffen: {
    titel: "Moffen",
    data: v => v.moffen,
    zoom: r => r.punt,
    leeg: "Tracé korter dan één haspellengte: geen moffen nodig.",
    kolommen: [
      { k: "nr", label: "Nr", num: true },
      { k: "werkpakket", label: "WP" },
      { k: "chainage_m", label: "Chainage (m)", num: true },
      { k: "x", label: "X (RD)", num: true, toon: r => r.punt[0],
        sorteer: r => r.punt[0] },
      { k: "y", label: "Y (RD)", num: true, toon: r => r.punt[1],
        sorteer: r => r.punt[1] },
      { k: "opmerking", label: "Opmerking", edit: true },
    ],
  },
  kosten: {
    titel: "Calculatie",
    // RAW-calculatie: bestekposten + inschrijvingsstaat; oudere opgeslagen
    // projecten zonder calculatie vallen terug op de indicatieve raming
    data: v => v.calculatie
      ? [...v.calculatie.posten.map(q => ({ ...q })),
         ...v.calculatie.staart.map(s => ({
           nr: s.nr, omschrijving: s.omschrijving, eenheid: "",
           hoeveelheid: null, eenheidsprijs_eur: null,
           totaal_eur: s.bedrag_eur, herkomst: s.grondslag, staart: true }))]
      : Object.entries(v.kosten).filter(([k]) => k !== "toelichting")
        .map(([post, bedrag]) => ({ nr: "", omschrijving: post,
                                    totaal_eur: bedrag })),
    voettekst: v => v.calculatie
      ? v.calculatie.systematiek + " — prijspeil " + v.calculatie.prijspeil +
        " · bouwtijdraming " + v.calculatie.uitvoeringsduur_wk + " weken. " +
        v.calculatie.uitgangspunten.join(" ")
      : (v.kosten.toelichting || ""),
    kolommen: [
      { k: "nr", label: "Bestekpost" },
      { k: "hoofdstuk", label: "Hoofdstuk",
        toon: r => r.staart ? "staart" : (RAW_HOOFDSTUKKEN[r.hoofdstuk] || r.hoofdstuk || "") },
      { k: "omschrijving", label: "Omschrijving" },
      { k: "eenheid", label: "Eenheid" },
      { k: "hoeveelheid", label: "Hoeveelheid", num: true,
        toon: r => r.hoeveelheid == null ? "" : r.hoeveelheid.toLocaleString("nl-NL") },
      { k: "eenheidsprijs_eur", label: "Prijs/eenheid", num: true,
        toon: r => r.eenheidsprijs_eur == null ? ""
          : r.eenheidsprijs_eur.toLocaleString("nl-NL", { minimumFractionDigits: 2 }) },
      { k: "totaal_eur", label: "Totaal (€)", num: true,
        toon: r => r.totaal_eur == null ? "" : eur(r.totaal_eur) },
      { k: "herkomst", label: "Herkomst" },
    ],
  },
};

// spiegel van backend/calculatie.py (HOOFDSTUKKEN)
const RAW_HOOFDSTUKKEN = {
  "01": "01 Algemeen", "11": "11 Sloopwerk", "21": "21 Bemalingen",
  "22": "22 Grondwerken", "25": "25 Leidingwerk", "26": "26 Kabelwerk",
  "31": "31 Verhardingen", "51": "51 Groen", "62": "62 Verkeersmaatregelen",
};

/* --------------------------------------------------------------- paginastatus */
let registersOpen = false;
let regTab = "vergunningen";
let regSelectie = null;
let regTabelRefs = null;           // { def, tbody }
const regState = {};               // per tab: { sortK, sortDir, filters, zoek }
let regStatusTimer = null;

const regEl = id => document.getElementById(id);
const huidigeVariant = () => resultaat ? resultaat.varianten[actieveVariant] : null;
const stateVan = tab =>
  regState[tab] ??= { sortK: null, sortDir: 1, filters: {}, zoek: "" };
const rijId = r => r.nr ?? r.post ?? "";

function regStatus(tekst, fout) {
  const el = regEl("register-status");
  el.textContent = tekst;
  el.classList.toggle("fout", !!fout);
  clearTimeout(regStatusTimer);
  if (tekst) regStatusTimer = setTimeout(() => { el.textContent = ""; }, 5000);
}

/* -------------------------------------------------------------- ZRO-gegevens
   Het ZRO-register komt van /api/zro/register (registerregel + dossierstatus);
   valt terug op de berekende lijst als de call mislukt. */
let zroCache = null, zroCacheVar = -1, zroLaadt = false;

function zroRijen() {
  if (zroCache && zroCacheVar === actieveVariant) return zroCache;
  if (!zroLaadt) {
    zroLaadt = true;
    const voorVariant = actieveVariant;
    fetch("api/zro/register?variant=" + voorVariant)
      .then(r => r.ok ? r.json() : null)
      .catch(() => null)
      .then(d => {
        zroLaadt = false;
        zroCache = d ? d.items
          : (huidigeVariant()?.zro || []).map(z => ({ ...z, status: z.workflow }));
        zroCacheVar = voorVariant;
        if (registersOpen && regTab === "zro") renderRegisterPagina();
      });
  }
  return null; // nog aan het laden
}

// dossierwijzigingen die via de detailoverlay lopen ook hier verversen
if (typeof ververZroRegister === "function") {
  const regOudVervers = ververZroRegister;
  ververZroRegister = function () {
    regOudVervers();
    zroCache = null;
    if (registersOpen && regTab === "zro") renderRegisterPagina();
  };
}

/* ------------------------------------------------------------------ weergave */
function celTekst(col, r) {
  const w = col.toon ? col.toon(r) : r[col.k];
  return w == null ? "" : String(w);
}

function celHtml(col, r) {
  const tekst = regEsc(celTekst(col, r));
  if (col.chip) {
    const kl = col.chip(r);
    if (kl === null || tekst === "") return "—";
    return `<span class="chip ${kl}">${tekst}</span>`;
  }
  return tekst;
}

function sorteerWaarde(col, r) {
  const w = col.sorteer ? col.sorteer(r) : r[col.k];
  return w == null ? "" : w;
}

function renderRegisterTabs() {
  const nav = regEl("register-tabs");
  nav.innerHTML = "";
  const v = huidigeVariant();
  for (const [key, def] of Object.entries(REG_DEF)) {
    const b = document.createElement("button");
    let n = "";
    if (v && key !== "kosten") {
      const rijen = key === "zro"
        ? (zroCache && zroCacheVar === actieveVariant ? zroCache : v.zro)
        : def.data(v);
      if (Array.isArray(rijen)) n = ` <span class="n">${rijen.length}</span>`;
    }
    b.innerHTML = regEsc(def.titel) + n;
    b.classList.toggle("actief", key === regTab);
    b.addEventListener("click", () => {
      regTab = key;
      regSelectie = null;
      history.replaceState(null, "", "#registers/" + key);
      renderRegisterPagina();
    });
    nav.appendChild(b);
  }
}

function renderRegisterVariant() {
  const sel = regEl("register-variant");
  sel.innerHTML = "";
  if (!resultaat) return;
  resultaat.varianten.forEach((vr, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent = `${vr.naam} — ${(vr.lengte_m / 1000).toFixed(2)} km`;
    o.selected = i === actieveVariant;
    sel.appendChild(o);
  });
}
regEl("register-variant").addEventListener("change", e => {
  actieveVariant = parseInt(e.target.value, 10);
  zroCache = null;
  regSelectie = null;
  toonResultaat(); // kaart en onderpaneel mee laten wisselen
  renderRegisterPagina();
});

function renderRegisterPagina() {
  renderRegisterVariant();
  renderRegisterTabs();
  const inhoud = regEl("register-inhoud");
  inhoud.innerHTML = "";
  regTabelRefs = null;
  const v = huidigeVariant();
  if (!v) {
    inhoud.innerHTML = '<p class="leeg">Nog geen berekend tracé. Ga terug naar ' +
      "het ontwerp en klik <em>Bereken tracé</em>.</p>";
    return;
  }
  const def = REG_DEF[regTab];
  const st = stateVan(regTab);
  regEl("register-zoek").value = st.zoek;

  const sch = def.schakelaar ? def.schakelaar(v) : null;
  if (sch) {
    const p = document.createElement("p");
    p.className = "hint";
    const lab = document.createElement("label");
    const inp = document.createElement("input");
    inp.type = "checkbox";
    inp.checked = !!sch.aan;
    inp.addEventListener("change", () => {
      sch.zet(inp.checked);
      renderRegisterTabs();
      renderRegisterPagina();
    });
    lab.appendChild(inp);
    lab.appendChild(document.createTextNode(" " + sch.label));
    p.appendChild(lab);
    inhoud.appendChild(p);
  }
  const t = document.createElement("table");
  t.className = "register vol";
  const thead = document.createElement("thead");
  const kop = document.createElement("tr");
  def.kolommen.forEach(col => {
    const th = document.createElement("th");
    th.className = "sorteer";
    if (col.num) th.classList.add("num");
    const pijl = st.sortK === col.k ? (st.sortDir > 0 ? " ▲" : " ▼") : "";
    th.innerHTML = regEsc(col.label) + `<span class="pijl">${pijl}</span>`;
    th.title = "Klik om te sorteren";
    th.addEventListener("click", () => {
      if (st.sortK === col.k && st.sortDir > 0) st.sortDir = -1;
      else if (st.sortK === col.k) { st.sortK = null; st.sortDir = 1; }
      else { st.sortK = col.k; st.sortDir = 1; }
      renderRegisterPagina();
    });
    kop.appendChild(th);
  });
  thead.appendChild(kop);
  if (!def.geenFilter) {
    const fr = document.createElement("tr");
    fr.className = "filters";
    def.kolommen.forEach(col => {
      const th = document.createElement("th");
      if (col.num) th.classList.add("num");
      const inp = document.createElement("input");
      inp.type = "search";
      inp.placeholder = "filter";
      inp.value = st.filters[col.k] || "";
      inp.addEventListener("input", () => {
        st.filters[col.k] = inp.value;
        vulTbody();
      });
      inp.addEventListener("click", e => e.stopPropagation());
      th.appendChild(inp);
      fr.appendChild(th);
    });
    thead.appendChild(fr);
  }
  t.appendChild(thead);
  const tbody = document.createElement("tbody");
  t.appendChild(tbody);
  inhoud.appendChild(t);
  regTabelRefs = { def, tbody };
  vulTbody();
  if (def.voettekst) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = def.voettekst(v);
    inhoud.appendChild(p);
  }
}

function vulTbody() {
  if (!regTabelRefs) return;
  const { def, tbody } = regTabelRefs;
  const st = stateVan(regTab);
  const v = huidigeVariant();
  const alle = def.data(v);
  const nKol = def.kolommen.length;
  if (alle == null) {
    tbody.innerHTML = `<tr><td colspan="${nKol}"><span class="leeg">Laden…</span></td></tr>`;
    return;
  }
  let rijen = alle.filter(r => {
    for (const col of def.kolommen) {
      const f = (st.filters[col.k] || "").trim().toLowerCase();
      if (f && !celTekst(col, r).toLowerCase().includes(f)) return false;
    }
    if (st.zoek.trim()) {
      const z = st.zoek.trim().toLowerCase();
      if (!def.kolommen.some(col => celTekst(col, r).toLowerCase().includes(z)))
        return false;
    }
    return true;
  });
  if (st.sortK) {
    const col = def.kolommen.find(c => c.k === st.sortK);
    rijen = [...rijen].sort((a, b) => {
      const x = sorteerWaarde(col, a), y = sorteerWaarde(col, b);
      const c = (typeof x === "number" && typeof y === "number")
        ? x - y
        : String(x).localeCompare(String(y), "nl", { numeric: true });
      return st.sortDir * c;
    });
  }
  tbody.innerHTML = "";
  if (!rijen.length) {
    const melding = alle.length
      ? "Geen rijen voldoen aan het filter."
      : (def.leeg || "Leeg register.");
    tbody.innerHTML =
      `<tr><td colspan="${nKol}"><span class="leeg">${regEsc(melding)}</span></td></tr>`;
    return;
  }
  rijen.forEach(r => {
    const tr = document.createElement("tr");
    tr.className = "klik";
    if (regSelectie !== null && rijId(r) === regSelectie)
      tr.classList.add("geselecteerd");
    def.kolommen.forEach(col => {
      const td = document.createElement("td");
      if (col.num) td.className = "num";
      if (col.knop) {
        const kn = col.knop(r);
        const b = document.createElement("button");
        b.className = "klein";
        b.textContent = kn.tekst;
        b.title = kn.titel || "";
        b.addEventListener("click", e => { e.stopPropagation(); kn.klik(); });
        td.appendChild(b);
      } else {
        td.innerHTML = celHtml(col, r);
        if (col.titel) {
          const t = col.titel(r);
          if (t) td.title = t;
        }
      }
      if (col.edit) {
        td.classList.add("bewerkbaar");
        td.title = "Dubbelklik om te bewerken";
        td.addEventListener("dblclick", e => {
          e.stopPropagation();
          bewerkCel(td, def, col, r);
        });
      }
      tr.appendChild(td);
    });
    tr.addEventListener("click", () => {
      regSelectie = rijId(r);
      tbody.querySelectorAll("tr").forEach(x => x.classList.remove("geselecteerd"));
      tr.classList.add("geselecteerd");
      const z = def.zoom && def.zoom(r);
      if (z) zoomNaar(z);
    });
    tbody.appendChild(tr);
  });
}

/* ------------------------------------------------------------------ bewerken */
function bewerkCel(td, def, col, r) {
  if (td.querySelector("input,select")) return;
  const huidig = celTekst(col, r);
  let klaar = false;

  const commit = async waarde => {
    if (klaar) return;
    klaar = true;
    if (waarde.trim() === huidig.trim()) { vulTbody(); return; }
    try {
      await slaOp(def, col, r, waarde.trim());
      regStatus(`Opgeslagen: ${col.label.toLowerCase()} van ${rijId(r) || "rij"}.`);
    } catch (e) {
      regStatus("Opslaan mislukt: " + e.message, true);
    }
    vulTbody();
    renderRegisterTabs();
  };
  const annuleer = () => { if (!klaar) { klaar = true; vulTbody(); } };

  let veld;
  if (col.edit.opties) {
    veld = document.createElement("select");
    const opties = [...col.edit.opties];
    if (huidig && !opties.includes(huidig)) opties.unshift(huidig);
    for (const o of opties) {
      const opt = document.createElement("option");
      opt.textContent = o;
      opt.selected = o === huidig;
      veld.appendChild(opt);
    }
    veld.addEventListener("change", () => commit(veld.value));
  } else {
    veld = document.createElement("input");
    veld.type = "text";
    veld.value = huidig;
    veld.addEventListener("keydown", e => {
      if (e.key === "Enter") commit(veld.value);
      else if (e.key === "Escape") annuleer();
    });
  }
  veld.addEventListener("blur", () =>
    col.edit.opties ? annuleer() : commit(veld.value));
  veld.addEventListener("click", e => e.stopPropagation());
  td.innerHTML = "";
  td.appendChild(veld);
  veld.focus();
  if (veld.select) veld.select();
}

async function slaOp(def, col, r, waarde) {
  const url = col.dossier ? "api/zro/detail" : "api/register/update";
  const body = col.dossier
    ? { variant: actieveVariant, nr: r.nr, dossier: { [col.dossier]: waarde } }
    : { variant: actieveVariant, register: regTab, nr: r.nr,
        wijzigingen: { [col.k]: waarde } };
  const resp = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
  const d = await resp.json();
  if (col.dossier) {
    // registerregel bijwerken vanuit het opgeslagen dossier
    r.status = d.dossier.status;
    r.eigenaar_dossier = d.dossier.eigenaar_naam;
    r.aard_recht_dossier = d.dossier.aard_recht;
    r.vergoeding_eenmalig_eur = d.dossier.vergoeding_eenmalig_eur;
    r.vergoeding_jaarlijks_eur = d.dossier.vergoeding_jaarlijks_eur;
  } else {
    Object.assign(r, d); // r verwijst het resultaat-object: onderpaneel blijft in sync
  }
}

/* --------------------------------------------------------- openen en sluiten */
function openRegisters(tab) {
  if (tab && REG_DEF[tab]) regTab = tab;
  if (!registersOpen) {
    registersOpen = true;
    zroCache = null; // dossier kan intussen gewijzigd zijn
    document.body.classList.add("registers-open");
    // de ene kaart (en de ZRO-detailoverlay) verhuist mee naar deze pagina
    regEl("register-kaart").appendChild(document.getElementById("map"));
    const zroDetail = document.getElementById("zro-detail");
    if (zroDetail) regEl("register-layout").appendChild(zroDetail);
    map.updateSize();
  }
  if (!location.hash.startsWith("#registers"))
    history.replaceState(null, "", "#registers/" + regTab);
  renderRegisterPagina();
}

function sluitRegisters() {
  if (!registersOpen) return;
  registersOpen = false;
  document.body.classList.remove("registers-open");
  const wrap = document.getElementById("map-wrap");
  wrap.insertBefore(document.getElementById("map"), wrap.firstChild);
  const zroDetail = document.getElementById("zro-detail");
  if (zroDetail) wrap.appendChild(zroDetail);
  map.updateSize();
  if (location.hash.startsWith("#registers"))
    history.replaceState(null, "", location.pathname + location.search);
}

regEl("btn-registers").addEventListener("click", () => openRegisters());
regEl("btn-reg-terug").addEventListener("click", sluitRegisters);
regEl("register-zoek").addEventListener("input", e => {
  stateVan(regTab).zoek = e.target.value;
  vulTbody();
});
window.addEventListener("hashchange", () => {
  if (location.hash.startsWith("#registers"))
    openRegisters(location.hash.split("/")[1]);
  else sluitRegisters();
});

/* ------------------------------------------------------------------- opstart
   Bij een verse paginalading het laatste rekenresultaat terughalen, zodat de
   registerpagina (en de kaart) ook na een herlaad direct gevuld zijn. */
(async () => {
  if (!location.hash.startsWith("#demo") && !resultaat) {
    try {
      const r = await fetch("api/result");
      if (r.ok) {
        const data = await r.json();
        if (!resultaat) {
          resultaat = data;
          actieveVariant = 0;
          document.getElementById("btn-exp-geojson").disabled = false;
          document.getElementById("btn-exp-xlsx").disabled = false;
          document.querySelectorAll(".btn-nota").forEach(b => { b.disabled = false; });
          document.getElementById("gemeente-info").textContent =
            data.gemeente ? "· " + data.gemeente : "";
          (data.stations || []).forEach(c =>
            srcStations.addFeature(new ol.Feature(new ol.geom.Point(c))));
          toonResultaat();
          if (data.bbox)
            map.getView().fit(data.bbox, { padding: [60, 60, 60, 60] });
        }
      }
    } catch (e) { /* geen resultaat beschikbaar: leeg beginnen */ }
  }
  if (location.hash.startsWith("#registers"))
    openRegisters(location.hash.split("/")[1]);
})();
