/* InfraEngine — procespagina (#proces): procesondersteuning van intake (IV)
   tot en met UO en de overdracht naar realisatie, volgens de tollgate-
   systematiek (TM2/T3/T4/T5). Per stap: status, uitvoering (AI / mens /
   hybride), AI-acties, goedkeuringen en artefacten. Plus het kans- en
   risicoregister en de adminomgeving (stappen/producten aan-uit, uitvoering
   per stap).

   Laadt na app.js en gebruikt de daar gedefinieerde globalen:
   mdNaarHtml, actieveVariant (en het invoerveld #project-naam). */
"use strict";

const prEl = id => document.getElementById(id);
const prEsc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let procesData = null;        // laatste /api/proces/overzicht
let procesTab = null;         // actieve fase-code of "risico"
let procesDocAbort = null;    // lopende AI-stream
let procesDocContext = null;  // {stap, soort} van het open document
let procesDocMarkdown = "";
let procesSjabloon = null;    // /api/proces/sjabloon (admin)

const PR_FOUT_MARK = "[NOTA-FOUT]";

const PR_STATUS = {
  te_doen:        ["te doen", "chip-grijs"],
  bezig:          ["bezig", "chip-blauw"],
  concept_gereed: ["AI-concept — goedkeuren", "chip-amber"],
  gereed:         ["gereed", "chip-groen"],
  nvt:            ["n.v.t.", "chip-grijs door"],
  afgekeurd:      ["afgekeurd", "chip-rood"],
};
const PR_UITVOERING = { ai: "🤖 AI", mens: "👤 Mens", hybride: "🤖+👤 Hybride" };
const PR_FASE_STATUS = {
  actief: ["actief", "chip-blauw"], wachtend: ["wachtend", "chip-grijs"],
  afgerond: ["afgerond", "chip-groen"], uit: ["uitgeschakeld", "chip-grijs door"],
};

function prProject() {
  return document.getElementById("project-naam").value.trim();
}

function prVariant() {
  return (typeof actieveVariant === "number") ? actieveVariant : 0;
}

/* ------------------------------------------------------------ pagina aan/uit */

function openProces() {
  document.body.classList.remove("registers-open");
  document.body.classList.add("proces-open");
  if (!location.hash.startsWith("#proces"))
    history.replaceState(null, "", "#proces");
  ververs();
}

function sluitProces() {
  document.body.classList.remove("proces-open");
  if (location.hash.startsWith("#proces"))
    history.replaceState(null, "", location.pathname + location.search);
}

prEl("btn-proces").addEventListener("click", openProces);
prEl("btn-proces-terug").addEventListener("click", sluitProces);
prEl("btn-proces-verversen").addEventListener("click", () => ververs());
// registers en proces zijn beide "pagina's" over het ontwerp heen; bij het
// openen van de één moet de ander dicht (registers.js kent deze pagina niet)
prEl("btn-registers").addEventListener("click", () =>
  document.body.classList.remove("proces-open"));
window.addEventListener("hashchange", () => {
  if (location.hash.startsWith("#proces")) openProces();
  else document.body.classList.remove("proces-open");
});
if (location.hash.startsWith("#proces")) setTimeout(openProces, 0);

/* ------------------------------------------------------------------ data */

async function prFetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function ververs(behoudTab = true) {
  const naam = prProject();
  prEl("proces-project").textContent = naam || "geen project";
  if (!naam) {
    prEl("proces-inhoud").innerHTML =
      '<p class="leeg">Geef eerst een projectnaam op (kopbalk) of open een project; ' +
      "daarna verschijnt hier het proces van intake (IV) tot en met UO.</p>";
    prEl("proces-tabs").innerHTML = "";
    return;
  }
  prEl("proces-status").textContent = "laden…";
  try {
    procesData = await prFetch(`api/proces/overzicht?project=${encodeURIComponent(naam)}`);
    prEl("proces-status").textContent = "";
    if (!behoudTab || !procesTab) {
      const actief = procesData.fasen.find(f => f.status === "actief");
      procesTab = actief ? actief.code : procesData.fasen[0].code;
    }
    toonProces();
  } catch (e) {
    prEl("proces-status").textContent = `Fout: ${e.message}`;
  }
}

/* ------------------------------------------------------------------ render */

function toonProces() {
  if (!procesData) return;
  // fase-tabs + risicotab
  const tabs = procesData.fasen.map(f => {
    const [lbl, cls] = PR_FASE_STATUS[f.status] || ["", ""];
    return `<button data-tab="${f.code}" class="${procesTab === f.code ? "actief" : ""}"
      title="${prEsc(f.naam)} — ${lbl}">
      ${f.code} <span class="n">${f.voortgang.gereed}/${f.voortgang.totaal}</span>
      ${f.status === "afgerond" ? "✓" : ""}</button>`;
  }).join("") +
    `<button data-tab="risico" class="${procesTab === "risico" ? "actief" : ""}"
      title="Kans- en risicoregister">Risico's <span class="n">${(procesData.risico || []).length}</span></button>` +
    `<button data-tab="planning" class="${procesTab === "planning" ? "actief" : ""}"
      title="Ontwerpplanning (IV t/m UO/NAO)">Planning</button>`;
  prEl("proces-tabs").innerHTML = tabs;
  prEl("proces-tabs").querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => { procesTab = b.dataset.tab; toonProces(); }));

  // takenlijst
  const taken = procesData.taken || [];
  prEl("proces-taken").innerHTML = taken.length
    ? taken.slice(0, 14).map(t =>
        `<button class="taak" data-fase="${t.fase}" data-stap="${t.stap || ""}">
           <span class="taak-fase">${t.fase}</span> ${prEsc(t.naam)}
           <span class="taak-actie">${prEsc(t.actie)}</span></button>`).join("")
    : '<p class="leeg">Geen openstaande taken.</p>';
  prEl("proces-taken").querySelectorAll(".taak").forEach(b =>
    b.addEventListener("click", () => { procesTab = b.dataset.fase; toonProces();
      if (b.dataset.stap) setTimeout(() => {
        const rij = document.querySelector(`tr[data-stap="${b.dataset.stap}"]`);
        if (rij) { rij.scrollIntoView({ block: "center" }); rij.classList.add("focus");
                   setTimeout(() => rij.classList.remove("focus"), 2500); }
      }, 60); }));

  // meldingen
  const m = procesData.meldingen || [];
  prEl("proces-meldingen").innerHTML = m.length
    ? m.slice(0, 12).map(x =>
        `<div class="melding"><span class="mono">${prEsc(x.tijd)}</span> ${prEsc(x.tekst)}</div>`).join("")
    : '<p class="leeg">Nog geen meldingen.</p>';

  prEl("proces-inhoud").innerHTML =
    procesTab === "risico" ? htmlRisico()
    : procesTab === "planning" ? htmlPlanning()
    : htmlFase(procesTab);
  koppelFaseActies();
}

function htmlFase(code) {
  const f = procesData.fasen.find(x => x.code === code);
  if (!f) return '<p class="leeg">Onbekende fase.</p>';
  const [fs, fcls] = PR_FASE_STATUS[f.status] || ["", ""];
  const pct = f.voortgang.totaal
    ? Math.round(100 * f.voortgang.gereed / f.voortgang.totaal) : 0;

  let html = `
  <div class="fase-kaart">
    <div class="fase-kop">
      <h3>${prEsc(f.naam)} <span class="chip ${fcls}">${fs}</span></h3>
      <div class="fase-voortgang" title="${f.voortgang.gereed} van ${f.voortgang.totaal} actieve stappen gereed">
        <div class="balk"><div class="vul" style="width:${pct}%"></div></div>
        <span class="mono">${f.voortgang.gereed}/${f.voortgang.totaal}</span>
      </div>
    </div>
    ${htmlTollgate(f)}
  </div>`;

  if (!procesData.heeft_result && ["IV"].indexOf(code) === -1)
    html += `<p class="hint proces-hint">⚠ Er is nog geen berekend tracé actief — AI-stappen
      hebben projectdata nodig. Open het project in het ontwerpscherm en bereken (of herlaad) het tracé.</p>`;

  const disciplines = [...new Set(f.stappen.map(s => s.discipline))];
  for (const d of disciplines) {
    const stappen = f.stappen.filter(s => s.discipline === d);
    html += `<h4 class="discipline">${prEsc(d)}</h4>
    <table class="register proces-tabel"><thead><tr>
      <th class="smal">Nr</th><th>Activiteit</th><th>Product / beheersdocument</th>
      <th class="smal">Review</th><th class="smal">Uitvoering</th>
      <th>Status</th><th>Acties &amp; producten</th>
    </tr></thead><tbody>`;
    for (const s of stappen) html += htmlStapRij(s, f);
    html += "</tbody></table>";
  }
  return html;
}

function htmlStapRij(s, f) {
  const [lbl, cls] = PR_STATUS[s.status] || [s.status, "chip-grijs"];
  const inactief = !s.actief;
  const artefacten = (s.artefacten || []).map(a =>
    `<a class="artefact" href="${a.url}" target="_blank"
        title="${prEsc(a.soort)} · ${prEsc(a.tijd)}">📄 ${prEsc(a.naam)}</a>`).join(" ");
  const toel = s.toelichting
    ? `<div class="stap-toel">${prEsc(s.toelichting)}</div>` : "";
  return `<tr data-stap="${s.id}" class="${inactief ? "stap-uit" : ""}">
    <td class="mono smal">${prEsc(s.nr || s.id)}</td>
    <td><strong>${prEsc(s.naam)}</strong>${s.opmerking
        ? `<div class="stap-opm">${prEsc(s.opmerking)}</div>` : ""}</td>
    <td>${prEsc(s.product)}${toel}</td>
    <td class="smal">${s.review ? "✔" : ""}</td>
    <td class="smal"><span class="uitvoering u-${s.uitvoering}">${PR_UITVOERING[s.uitvoering] || s.uitvoering}</span></td>
    <td><span class="chip ${cls}">${lbl}</span></td>
    <td class="acties">${inactief ? '<span class="leeg">uitgeschakeld (admin)</span>'
                                  : htmlStapActies(s, f) + " " + artefacten}</td>
  </tr>`;
}

function htmlStapActies(s, f) {
  if (f.status === "wachtend")
    return '<span class="leeg">wacht op vorige tollgate</span>';
  const b = [];
  const klaar = s.status === "gereed" || s.status === "nvt";
  const heeftAi = !!s.cap && s.uitvoering !== "mens";
  if (!klaar) {
    if (s.id === "IV-01")
      b.push(`<button class="klein" data-actie="iv-upload">⇪ IV-document uploaden</button>`);
    if (heeftAi) {
      if (s.cap.startsWith("data:"))
        b.push(`<button class="klein ai" data-actie="ai-data">▶ AI uitvoeren</button>`);
      else if (s.cap === "risico")
        b.push(`<button class="klein ai" data-actie="ai-risico">▶ AI-risicoanalyse</button>`);
      else if (s.cap === "intake")
        b.push(`<button class="klein ai" data-actie="ai-intake">✎ AI-intakeverslag</button>`);
      else
        b.push(`<button class="klein ai" data-actie="ai-doc">✎ AI-concept opstellen</button>`);
    }
    if (s.status === "concept_gereed") {
      b.push(`<button class="klein ok" data-actie="goedkeur">✓ Goedkeuren</button>`);
      b.push(`<button class="klein afkeur" data-actie="afkeur">✗ Afkeuren</button>`);
    } else {
      if (s.status !== "bezig")
        b.push(`<button class="klein" data-actie="start">Start</button>`);
      b.push(`<button class="klein ok" data-actie="gereed">Gereed</button>`);
      b.push(`<button class="klein" data-actie="nvt">n.v.t.</button>`);
    }
  } else {
    b.push(`<button class="klein" data-actie="heropen">↺ Heropenen</button>`);
  }
  return b.join(" ");
}

function htmlTollgate(f) {
  const tg = f.tollgate_besluit || {};
  const genomen = f.tollgate_status === "genomen";
  const review = f.stappen.filter(s => s.review && s.actief);
  const rows = review.map(s => {
    const ok = s.status === "gereed" || s.status === "nvt";
    return `<tr><td class="mono smal">${prEsc(s.nr || s.id)}</td>
      <td>${prEsc(s.product)}</td>
      <td><span class="chip ${ok ? "chip-groen" : "chip-rood"}">${ok ? "voldoet" : "voldoet niet"}</span></td>
      <td>${prEsc(ok ? (s.toelichting || "") : (s.toelichting || "nog niet gereed"))}</td></tr>`;
  }).join("");
  return `
  <details class="tollgate ${genomen ? "genomen" : f.tollgate_gereed ? "gereed" : ""}">
    <summary>
      <span class="tg-badge">${prEsc(f.tollgate)}</span> ${prEsc(f.tollgate_naam)}
      <span class="chip ${genomen ? "chip-groen" : f.tollgate_gereed ? "chip-amber" : "chip-grijs"}">
        ${genomen ? `genomen · ${prEsc(tg.door || "")} · ${prEsc(tg.tijd || "")}`
                  : f.tollgate_gereed ? "gereed voor review" : "nog niet gereed"}</span>
    </summary>
    <p class="hint">Tollgate-review: alle producten met ✔ worden beoordeeld (voldoet /
      voldoet niet). Het besluit is altijd menselijk; bij "voldoet niet"-punten is een
      toelichting (afwijkingsbesluit) verplicht.</p>
    <table class="register proces-tabel"><thead><tr>
      <th class="smal">Nr</th><th>Product</th><th>Oordeel</th><th>Toelichting</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="4" class="leeg">Geen review-producten in deze fase.</td></tr>'}</tbody></table>
    ${genomen ? (tg.toelichting ? `<p class="hint">Toelichting: ${prEsc(tg.toelichting)}</p>` : "") : `
    <div class="tg-besluit">
      <input id="tg-door" type="text" placeholder="naam beslisser (verplicht)">
      <input id="tg-toel" type="text" placeholder="toelichting / afwijkingsbesluit">
      <button class="klein ok" data-tg="genomen">✓ Tollgate nemen</button>
      <button class="klein afkeur" data-tg="afgekeurd">✗ Afkeuren</button>
    </div>`}
  </details>`;
}

/* --------------------------------------------------------------- acties */

function koppelFaseActies() {
  const wrap = prEl("proces-inhoud");
  wrap.querySelectorAll("tr[data-stap] button[data-actie]").forEach(btn => {
    btn.addEventListener("click", () =>
      stapActie(btn.closest("tr").dataset.stap, btn.dataset.actie, btn));
  });
  wrap.querySelectorAll("button[data-tg]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const door = (prEl("tg-door") || {}).value?.trim() || "";
      const toel = (prEl("tg-toel") || {}).value?.trim() || "";
      if (!door) { alert("Vul de naam van de beslisser in."); return; }
      try {
        await prFetch("api/proces/tollgate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: prProject(), fase: procesTab,
                                 besluit: btn.dataset.tg, door, toelichting: toel }),
        });
        ververs();
      } catch (e) { alert(e.message); }
    });
  });
  koppelRisicoActies();
}

async function stapActie(stapId, actie, btn) {
  const project = prProject();
  const stap = vindStap(stapId);
  try {
    if (actie === "ai-data") {
      btn.disabled = true; btn.textContent = "AI bezig…";
      await prFetch("api/proces/ai/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, stap: stapId, variant: prVariant() }),
      });
      ververs();
    } else if (actie === "ai-doc") {
      openDocOverlay(stapId, "doc");
    } else if (actie === "ai-intake") {
      openDocOverlay(stapId, "intake");
    } else if (actie === "ai-risico") {
      btn.disabled = true; btn.textContent = "AI analyseert… (± 1-2 min)";
      await prFetch("api/proces/risico/genereer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, variant: prVariant(), fase: stap?.fase || "" }),
      });
      await prFetch("api/proces/stap", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, stap: stapId,
          actie: stap?.uitvoering === "ai" ? "gereed" : "start",
          toelichting: "Risicoregister door AI geactualiseerd — zie tabblad Risico's.",
          door: "AI" }),
      });
      procesTab = "risico";
      ververs();
    } else if (actie === "iv-upload") {
      kiesIvBestand();
    } else if (actie === "afkeur") {
      const toel = prompt("Toelichting bij de afkeuring (verplicht):", "");
      if (!toel) return;
      await prFetch("api/proces/stap", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, stap: stapId, actie, toelichting: toel }),
      });
      ververs();
    } else {
      await prFetch("api/proces/stap", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, stap: stapId, actie }),
      });
      ververs();
    }
  } catch (e) {
    alert(e.message);
    ververs();
  }
}

function vindStap(stapId) {
  for (const f of (procesData?.fasen || []))
    for (const s of f.stappen) if (s.id === stapId) return s;
  return null;
}

/* ---------------------------------------------------- IV-document upload */

function kiesIvBestand() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".pdf,.docx,.txt,.md";
  input.addEventListener("change", () => {
    const bestand = input.files[0];
    if (!bestand) return;
    const lezer = new FileReader();
    lezer.onload = async () => {
      try {
        await prFetch("api/proces/intake/upload", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: prProject(), bestandsnaam: bestand.name,
                                 data_base64: lezer.result }),
        });
        ververs();
      } catch (e) { alert(e.message); }
    };
    lezer.readAsDataURL(bestand);
  });
  input.click();
}

/* -------------------------------------------- AI-documentoverlay (stream) */

function openDocOverlay(stapId, soort) {
  const stap = vindStap(stapId);
  procesDocContext = { stap: stapId, soort };
  procesDocMarkdown = "";
  prEl("proces-doc-titel").textContent =
    (soort === "intake" ? "Intakeverslag" : (stap?.product || "AI-concept"));
  prEl("proces-doc-status").textContent = "";
  prEl("proces-doc-sheet").innerHTML = "";
  prEl("proces-doc-opslaan").disabled = true;
  prEl("proces-doc-overlay").hidden = false;
  streamDoc(stapId, soort);
}

async function streamDoc(stapId, soort) {
  const status = prEl("proces-doc-status");
  const sheet = prEl("proces-doc-sheet");
  const wrap = prEl("proces-doc-wrap");
  status.textContent = "AI schrijft…";
  sheet.classList.add("bezig");
  procesDocAbort = new AbortController();
  const t0 = Date.now();
  const tik = setInterval(() => {
    status.textContent = `AI schrijft… ${Math.round((Date.now() - t0) / 1000)}s`;
  }, 1000);
  let renderGepland = false;
  const render = () => {
    renderGepland = false;
    sheet.innerHTML = mdNaarHtml(procesDocMarkdown.split(PR_FOUT_MARK)[0]);
    wrap.scrollTop = wrap.scrollHeight;
  };
  try {
    const url = soort === "intake"
      ? `api/proces/intake/stream?project=${encodeURIComponent(prProject())}`
      : `api/proces/ai/stream?project=${encodeURIComponent(prProject())}` +
        `&stap=${encodeURIComponent(stapId)}&variant=${prVariant()}`;
    const r = await fetch(url, { signal: procesDocAbort.signal });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      procesDocMarkdown += decoder.decode(value, { stream: true });
      if (!renderGepland) { renderGepland = true; setTimeout(render, 120); }
    }
    procesDocMarkdown += decoder.decode();
    render();
    if (procesDocMarkdown.includes(PR_FOUT_MARK)) {
      status.textContent = "Fout: " +
        procesDocMarkdown.split(PR_FOUT_MARK)[1].trim();
    } else {
      status.textContent = `Gereed (${Math.round((Date.now() - t0) / 1000)}s) — controleer en sla op.`;
      prEl("proces-doc-opslaan").disabled = false;
    }
  } catch (e) {
    if (e.name !== "AbortError") status.textContent = `Fout: ${e.message}`;
  } finally {
    clearInterval(tik);
    sheet.classList.remove("bezig");
    procesDocAbort = null;
  }
}

prEl("proces-doc-sluiten").addEventListener("click", () => {
  if (procesDocAbort) procesDocAbort.abort();
  prEl("proces-doc-overlay").hidden = true;
});

prEl("proces-doc-opslaan").addEventListener("click", async () => {
  const knop = prEl("proces-doc-opslaan");
  knop.disabled = true;
  prEl("proces-doc-status").textContent = "Word-document opmaken en registreren…";
  try {
    const { stap, soort } = procesDocContext;
    if (soort === "intake") {
      await prFetch("api/proces/intake/document", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: prProject(), markdown: procesDocMarkdown }),
      });
    } else {
      await prFetch("api/proces/ai/document", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: prProject(), stap,
                               markdown: procesDocMarkdown, variant: prVariant() }),
      });
    }
    prEl("proces-doc-overlay").hidden = true;
    ververs();
  } catch (e) {
    prEl("proces-doc-status").textContent = `Fout: ${e.message}`;
    knop.disabled = false;
  }
});

/* -------------------------------------------------------- risicoregister */

const RIS_VELDEN = [
  ["nr", "Nr", false], ["omschrijving", "Risico", true],
  ["oorzaak", "Oorzaak", true], ["gevolg", "Gevolg", true],
  ["aspect", "Aspect", true], ["stadium", "Stadium", true],
  ["allocatie", "Allocatie", true], ["werkpakket", "WP", true],
  ["kans", "K", true], ["geld", "€", true], ["tijd", "T", true],
  ["kwaliteit", "Q", true], ["veiligheid", "V", true], ["omgeving", "O", true],
  ["score", "Score", false], ["status", "Status", true],
];

function htmlRisico() {
  const rijen = procesData.risico || [];
  let html = `
  <div class="fase-kaart">
    <div class="fase-kop">
      <h3>Kans- en risicoregister
        <span class="chip chip-grijs">${rijen.length} risico's</span></h3>
      <div>
        <button class="klein ai" id="btn-risico-genereer">▶ AI: risico's genereren / actualiseren</button>
        <a class="klein knop-a" href="api/proces/risico/xlsx?project=${encodeURIComponent(prProject())}"
           download>⇩ Excel (risico's + procesoverzicht)</a>
      </div>
    </div>
    <p class="hint">Structuur volgens de projectrisico-overzichtstabel: score = kans ×
      som van de gevolgscores (geld, tijd, kwaliteit, veiligheid, omgeving; elk 0-5).
      Dubbelklik op een cel om te bewerken; ▸ toont oorzaak, gevolg en beheersmaatregelen.</p>
  </div>`;
  if (!rijen.length)
    return html + '<p class="leeg">Nog geen risico\'s — laat de AI het initiële register opstellen.</p>';
  html += `<table class="register proces-tabel risico-tabel"><thead><tr>
    <th></th>${RIS_VELDEN.filter(v => !["oorzaak", "gevolg"].includes(v[0]))
      .map(v => `<th class="${v[0].length <= 2 || ["kans","score"].includes(v[0]) ? "smal num" : ""}">${v[1]}</th>`).join("")}
    <th></th></tr></thead><tbody>`;
  for (const r of rijen) {
    const scoreCls = r.score >= 60 ? "chip-rood" : r.score >= 30 ? "chip-amber" : "chip-groen";
    html += `<tr data-nr="${prEsc(r.nr)}">
      <td><button class="klein toggle" title="details">▸</button></td>`;
    for (const [veld,, bewerkbaar] of RIS_VELDEN) {
      if (veld === "oorzaak" || veld === "gevolg") continue;
      const w = r[veld] ?? "";
      html += veld === "score"
        ? `<td class="num"><span class="chip ${scoreCls}">${w}</span></td>`
        : `<td class="${bewerkbaar ? "bewerkbaar" : ""} ${String(w).length < 4 ? "num" : ""}"
              data-veld="${veld}">${prEsc(w)}</td>`;
    }
    html += `<td><button class="klein afkeur" data-del="${prEsc(r.nr)}" title="risico verwijderen">✕</button></td></tr>`;
    const maatregelen = (r.maatregelen || []).map(m =>
      `<li><strong>${prEsc(m.soort)}</strong> — ${prEsc(m.maatregel)}
        ${m.actiehouder ? `<span class="mono">(${prEsc(m.actiehouder)})</span>` : ""}</li>`).join("");
    html += `<tr class="risico-detail" data-detail="${prEsc(r.nr)}" hidden><td></td>
      <td colspan="${RIS_VELDEN.length - 1}">
        <p><strong>Oorzaak:</strong> ${prEsc(r.oorzaak || "—")}</p>
        <p><strong>Gevolg:</strong> ${prEsc(r.gevolg || "—")}</p>
        <p><strong>Beheersmaatregelen:</strong></p>
        <ul>${maatregelen || "<li>nog geen</li>"}</ul>
        ${r.bron === "AI" ? '<p class="hint">Door AI voorgesteld — beoordeel en pas aan.</p>' : ""}
      </td></tr>`;
  }
  return html + "</tbody></table>";
}

const PR_PLAN_STATUS = {
  afgerond: ["afgerond", "chip-groen", "st-afgerond"],
  actief: ["actief", "chip-blauw", "st-actief"],
  wachtend: ["wachtend", "chip-grijs", "st-wachtend"],
  uit: ["uitgeschakeld", "chip-grijs door", "st-uit"],
  gepland: ["gepland", "chip-blauw", "st-gepland"],
};

function htmlPlanning() {
  const rijen = procesData.planning || [];
  const naam = encodeURIComponent(prProject());
  let html = `
  <div class="fase-kaart">
    <div class="fase-kop">
      <h3>Ontwerpplanning <span class="chip chip-grijs">IV t/m NAO</span></h3>
      <div>
        <a class="klein knop-a" href="api/proces/planning/xlsx?project=${naam}"
           download>⇩ Excel</a>
      </div>
    </div>
    <p class="hint">Indicatieve planning per fase (tollgate-mijlpaal) en, waar bekend, per
      discipline — in weken vanaf projectstart. Ingevulde mijlpalen (👥 Projectgegevens)
      worden als datum getoond; ze herrekenen de indicatieve weekplanning niet. De
      uitvoeringsplanning per werkpakket staat in het tracéresultaat (paneel "Uitvoeringsplanning").</p>
  </div>`;
  if (!rijen.length)
    return html + '<p class="leeg">Geen ontwerpplanning beschikbaar.</p>';

  const maxWk = Math.max(1, ...rijen.map(r => r.eind_wk));
  html += `<img class="planning-afbeelding" alt="Ontwerpplanning"
    src="api/kaart/planning-ontwerp.jpg?project=${naam}" loading="lazy">`;
  html += `<table class="register proces-tabel"><thead><tr>
    <th>Fase / discipline</th><th class="smal">Tollgate</th>
    <th class="smal num">Start</th><th class="smal num">Eind</th>
    <th>Status</th><th style="width:210px">Planning (t/m wk ${maxWk})</th>
    <th>Toelichting</th></tr></thead><tbody>`;
  for (const r of rijen) {
    const [lbl, cls, balkCls] = PR_PLAN_STATUS[r.status] || [r.status, "chip-grijs", "st-wachtend"];
    const hoofd = !!r.fase && !r.discipline;
    const mijlpaal = !r.fase;
    const label = mijlpaal ? `◆ ${prEsc(r.toelichting)}`
      : hoofd ? `<strong>${prEsc(r.fase_naam)}</strong>` : `› ${prEsc(r.discipline)}`;
    html += `<tr class="${hoofd ? "" : "sub"}">
      <td>${label}</td>
      <td class="smal mono">${prEsc(r.tollgate || "")}</td>
      <td class="smal num">wk ${r.start_wk}</td>
      <td class="smal num">wk ${r.eind_wk}</td>
      <td><span class="chip ${cls}">${lbl}</span>${r.mijlpaal_waarde
          ? `<div class="stap-toel">${prEsc(r.mijlpaal_waarde)}</div>` : ""}</td>
      <td><span class="balkspoor${hoofd ? "" : " smal"}"><span class="balk ${balkCls}"
          style="left:${((r.start_wk - 1) / maxWk * 100).toFixed(1)}%;
                 width:${(Math.max(r.duur_wk, 0.4) / maxWk * 100).toFixed(1)}%"></span></span></td>
      <td>${mijlpaal ? "" : prEsc(r.toelichting)}</td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

function koppelRisicoActies() {
  const gen = prEl("btn-risico-genereer");
  if (gen) gen.addEventListener("click", async () => {
    gen.disabled = true; gen.textContent = "AI analyseert het project… (± 1-2 min)";
    try {
      await prFetch("api/proces/risico/genereer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: prProject(), variant: prVariant(),
                               fase: "" }),
      });
      ververs();
    } catch (e) { alert(e.message); ververs(); }
  });
  document.querySelectorAll(".risico-tabel .toggle").forEach(b =>
    b.addEventListener("click", () => {
      const nr = b.closest("tr").dataset.nr;
      const det = document.querySelector(`tr[data-detail="${nr}"]`);
      det.hidden = !det.hidden;
      b.textContent = det.hidden ? "▸" : "▾";
    }));
  document.querySelectorAll(".risico-tabel td.bewerkbaar").forEach(td =>
    td.addEventListener("dblclick", async () => {
      const nr = td.closest("tr").dataset.nr;
      const veld = td.dataset.veld;
      const nieuw = prompt(`${veld} voor ${nr}:`, td.textContent.trim());
      if (nieuw === null) return;
      try {
        await prFetch("api/proces/risico/update", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: prProject(), nr, veld, waarde: nieuw }),
        });
        ververs();
      } catch (e) { alert(e.message); }
    }));
  document.querySelectorAll(".risico-tabel button[data-del]").forEach(b =>
    b.addEventListener("click", async () => {
      if (!confirm(`Risico ${b.dataset.del} verwijderen?`)) return;
      try {
        await prFetch(`api/proces/risico?project=${encodeURIComponent(prProject())}` +
                      `&nr=${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
        ververs();
      } catch (e) { alert(e.message); }
    }));
}

/* -------------------------------------------------- projectgegevens (nota's) */

const GEG_VERIFICATIE = [
  ["opdrachtgever", "Opdrachtgever"], ["opgesteld_door", "Opgesteld door"],
  ["verificatie", "Verificatie"], ["autorisatie", "Autorisatie"],
  ["vrijgave", "Vrijgave"],
];

prEl("btn-proces-gegevens").addEventListener("click", openGegevens);
prEl("proces-gegevens-sluiten").addEventListener("click", () =>
  prEl("proces-gegevens-overlay").hidden = true);

async function openGegevens() {
  if (!prProject()) { alert("Geef eerst een projectnaam op."); return; }
  let g;
  try {
    g = await prFetch(`api/proces/gegevens?project=${encodeURIComponent(prProject())}`);
  } catch (e) { alert(e.message); return; }
  const ml = g.mijlpaal_labels || {};
  let html = `<h4 class="discipline">Verificatietabel</h4>
    <table class="register proces-tabel gegevens-tabel"><tbody>` +
    GEG_VERIFICATIE.map(([veld, label]) =>
      `<tr><td class="smal"><strong>${label}</strong></td>
       <td><input data-geg="verificatie.${veld}"
            value="${prEsc(g.verificatie?.[veld] || "")}" placeholder="naam"></td></tr>`)
      .join("") + `</tbody></table>
    <h4 class="discipline">Projectteam</h4>
    <table class="register proces-tabel gegevens-tabel" id="gegevens-team">
      <thead><tr><th>Naam</th><th>Functie</th><th>Organisatie</th></tr></thead><tbody>` +
    (g.team || []).map(r =>
      `<tr><td><input data-team="naam" value="${prEsc(r.naam)}" placeholder="naam"></td>
       <td><input data-team="functie" value="${prEsc(r.functie)}"></td>
       <td><input data-team="organisatie" value="${prEsc(r.organisatie)}" placeholder="organisatie"></td></tr>`)
      .join("") + `</tbody></table>
    <button class="klein" id="gegevens-team-plus">+ teamlid</button>
    <h4 class="discipline">Mijlpalen</h4>
    <table class="register proces-tabel gegevens-tabel"><tbody>` +
    Object.keys(g.mijlpalen || {}).map(k =>
      `<tr><td class="smal"><strong>${prEsc(ml[k] || k)}</strong></td>
       <td><input data-geg="mijlpalen.${k}" value="${prEsc(g.mijlpalen[k] || "")}"
            placeholder="dd-mm-jjjj"></td></tr>`).join("") +
    "</tbody></table>";
  prEl("proces-gegevens-inhoud").innerHTML = html;
  prEl("proces-gegevens-status").textContent = "";
  prEl("gegevens-team-plus").addEventListener("click", () => {
    const tb = document.querySelector("#gegevens-team tbody");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input data-team="naam" placeholder="naam"></td>
      <td><input data-team="functie" placeholder="functie"></td>
      <td><input data-team="organisatie" placeholder="organisatie"></td>`;
    tb.appendChild(tr);
  });
  prEl("proces-gegevens-overlay").hidden = false;
}

prEl("proces-gegevens-opslaan").addEventListener("click", async () => {
  const gegevens = { verificatie: {}, mijlpalen: {}, team: [] };
  document.querySelectorAll("#proces-gegevens-inhoud input[data-geg]").forEach(inp => {
    const [groep, veld] = inp.dataset.geg.split(".");
    gegevens[groep][veld] = inp.value.trim();
  });
  document.querySelectorAll("#gegevens-team tbody tr").forEach(tr => {
    const rij = {};
    tr.querySelectorAll("input[data-team]").forEach(inp =>
      rij[inp.dataset.team] = inp.value.trim());
    if (rij.naam || rij.functie || rij.organisatie) gegevens.team.push(rij);
  });
  try {
    await prFetch("api/proces/gegevens", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: prProject(), gegevens }),
    });
    prEl("proces-gegevens-status").textContent = "opgeslagen ✓";
    setTimeout(() => prEl("proces-gegevens-overlay").hidden = true, 600);
  } catch (e) { alert(e.message); }
});

/* --------------------------------------------------------------- budget */

prEl("btn-proces-budget").addEventListener("click", openBudget);
prEl("proces-budget-sluiten").addEventListener("click", () =>
  prEl("proces-budget-overlay").hidden = true);

const BUDGET_AANLEIDING = [
  ["T1 (intakebesluit)", "T1 — intakebesluit (IV)"],
  ["TM2 (VO vastgesteld)", "TM2 — VO vastgesteld"],
  ["T3 (DO vastgesteld)", "T3 — DO vastgesteld"],
  ["T4 (UO vastgesteld)", "T4 — UO vastgesteld"],
  ["tussentijds", "tussentijds"],
];

async function openBudget() {
  if (!prProject()) { alert("Geef eerst een projectnaam op."); return; }
  let b;
  try {
    b = await prFetch(`api/proces/budget?project=${encodeURIComponent(prProject())}`);
  } catch (e) { alert(e.message); return; }
  prEl("proces-budget-inhoud").innerHTML = htmlBudget(b);
  prEl("proces-budget-status").textContent = "";
  koppelBudgetActies(b);
  prEl("proces-budget-overlay").hidden = false;
}

function htmlBudget(b) {
  const fasen = b.fasen, posten = b.posten;
  const huidigeFasen = (b.taakstellend_huidig || {}).fasen || {};

  let tabel = `<table class="register proces-tabel budget-tabel"><thead><tr>
    <th>Kostenpost</th>${fasen.map(f => `<th>${f}</th>`).join("")}
    <th>Totaal</th></tr></thead><tbody>`;
  for (const [key, label] of posten) {
    tabel += `<tr><td>${prEsc(label)}</td>` +
      fasen.map(f => `<td><input type="number" step="1" min="0"
        data-tsb-fase="${f}" data-tsb-post="${key}"
        value="${(huidigeFasen[f]?.posten?.[key]) ?? 0}"></td>`).join("") +
      `<td class="mono smal tsb-rij-totaal" data-post="${key}"></td></tr>`;
  }
  tabel += `<tr class="tsb-totaalrij"><td><strong>Totaal fase</strong></td>` +
    fasen.map(f => `<td class="mono" id="tsb-totaal-${f}">—</td>`).join("") +
    `<td class="mono" id="tsb-totaal-algemeen">—</td></tr></tbody></table>`;

  const historieTsb = (b.taakstellend_historie || []).slice().reverse().map(r =>
    `<tr><td class="mono smal">${r.tijd}</td><td>${prEsc(r.aanleiding)}</td>
     <td>${prEsc(r.door)}</td><td class="mono">${eur(r.totaal_ontwerpfase)}</td>
     <td>${prEsc(r.toelichting || "")}</td></tr>`).join("");

  const rea = b.realisatie_huidig;
  const historieRea = (b.realisatie_historie || []).slice().reverse().map(r =>
    `<tr><td class="mono smal">${r.tijd}</td><td>${prEsc(r.fase)}</td>
     <td class="mono">${eur(r.bedrag_excl_btw)}</td>
     <td class="mono">${r.bedrag_incl_btw != null ? eur(r.bedrag_incl_btw) : "—"}</td>
     <td>${r.bron === "raw_calculatie" ? "RAW-calculatie" : "handmatig"} — ${prEsc(r.status)}</td>
     <td>${prEsc(r.door)}</td></tr>`).join("");

  return `
    <h4 class="discipline">Taakstellend budget — ontwerpfase (IV t/m UO)</h4>
    <p class="hint">Eén budget voor de gehele ontwerpfase, per projectfase ingedeeld en per
      fase onderverdeeld in kostenposten. Stel na elke tollgate een nieuwe revisie vast;
      fasen die je op 0 laat staan behouden hun laatst vastgestelde waarde — vul dus alleen de
      fase(n) in die je nu bijstelt.</p>
    ${tabel}
    <div class="budget-form">
      <label>Aanleiding<select id="tsb-aanleiding">
        ${BUDGET_AANLEIDING.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
      </select></label>
      <label>Vastgesteld door<input id="tsb-door" placeholder="naam"></label>
      <label>Toelichting<input id="tsb-toelichting" placeholder="toelichting bij deze revisie"></label>
      <button id="tsb-opslaan">Nieuwe revisie vaststellen</button>
    </div>
    <table class="register proces-tabel budget-historie"><thead><tr>
      <th>Tijd</th><th>Aanleiding</th><th>Door</th><th>Totaal t/m UO</th><th>Toelichting</th>
    </tr></thead><tbody>${historieTsb ||
      '<tr><td colspan="5" class="leeg">Nog geen revisie vastgesteld.</td></tr>'}</tbody></table>

    <h4 class="discipline">Begroting realisatiefase — verwachte uitvoeringskosten opdrachtgever</h4>
    <p class="hint">Voor het eerst opgesteld in de IV-fase (TOF-studie, stap IV-TOF) en
      automatisch bijgewerkt zodra het tracé opnieuw wordt doorgerekend (concept, uit de
      RAW-calculatie). Vaststellen legt een vaste snapshot voor de gekozen fase vast.</p>
    <p class="mono">${rea ? `Huidig: ${eur(rea.bedrag_excl_btw)} excl. btw` +
      (rea.bedrag_incl_btw != null ? ` / ${eur(rea.bedrag_incl_btw)} incl. btw` : "") +
      ` — fase ${prEsc(rea.fase)}, ${rea.bron === "raw_calculatie"
        ? "concept uit RAW-calculatie" : "handmatig vastgesteld"}`
      : "Nog geen begroting realisatiefase."}</p>
    <div class="budget-form">
      <label>Fase<select id="rea-fase">
        ${[...fasen, "NAO"].map(f => `<option value="${f}" ${f === (rea?.fase || "IV") ? "selected" : ""}>${f}</option>`).join("")}
      </select></label>
      <label>Bedrag excl. btw<input id="rea-excl" type="number" step="1" min="0"
        value="${rea?.bedrag_excl_btw ?? ""}"></label>
      <label>Bedrag incl. btw<input id="rea-incl" type="number" step="1" min="0"
        value="${rea?.bedrag_incl_btw ?? ""}"></label>
      <label>Door<input id="rea-door" placeholder="naam"></label>
      <label>Toelichting<input id="rea-toelichting" placeholder="toelichting"></label>
      <button id="rea-opslaan">Vaststellen</button>
    </div>
    <table class="register proces-tabel budget-historie"><thead><tr>
      <th>Tijd</th><th>Fase</th><th>Excl. btw</th><th>Incl. btw</th><th>Bron/status</th><th>Door</th>
    </tr></thead><tbody>${historieRea ||
      '<tr><td colspan="6" class="leeg">Nog geen begroting.</td></tr>'}</tbody></table>`;
}

function prBudgetHerbereken() {
  const wrap = prEl("proces-budget-inhoud");
  const fasen = [...new Set([...wrap.querySelectorAll("[data-tsb-fase]")]
    .map(i => i.dataset.tsbFase))];
  let algemeen = 0;
  for (const f of fasen) {
    let tot = 0;
    wrap.querySelectorAll(`[data-tsb-fase="${f}"]`).forEach(inp => tot += Number(inp.value || 0));
    const el = prEl(`tsb-totaal-${f}`);
    if (el) el.textContent = eur(tot);
    algemeen += tot;
  }
  const posten = [...new Set([...wrap.querySelectorAll("[data-tsb-post]")]
    .map(i => i.dataset.tsbPost))];
  for (const p of posten) {
    let tot = 0;
    wrap.querySelectorAll(`[data-tsb-post="${p}"]`).forEach(inp => tot += Number(inp.value || 0));
    const el = wrap.querySelector(`.tsb-rij-totaal[data-post="${p}"]`);
    if (el) el.textContent = eur(tot);
  }
  const alg = prEl("tsb-totaal-algemeen");
  if (alg) alg.innerHTML = `<strong>${eur(algemeen)}</strong>`;
}

function koppelBudgetActies(b) {
  document.querySelectorAll("#proces-budget-inhoud [data-tsb-fase]").forEach(inp =>
    inp.addEventListener("input", prBudgetHerbereken));
  prBudgetHerbereken();

  prEl("tsb-opslaan").addEventListener("click", async () => {
    const door = prEl("tsb-door").value.trim();
    if (!door) { alert("Vul in wie het budget vaststelt."); return; }
    const fasen = {};
    for (const f of b.fasen) {
      const posten = {};
      document.querySelectorAll(`[data-tsb-fase="${f}"]`).forEach(inp =>
        posten[inp.dataset.tsbPost] = Number(inp.value || 0));
      fasen[f] = { posten };
    }
    try {
      await prFetch("api/proces/budget/taakstellend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: prProject(), fasen,
          aanleiding: prEl("tsb-aanleiding").value,
          toelichting: prEl("tsb-toelichting").value.trim(), door,
        }),
      });
      openBudget();
    } catch (e) { alert(e.message); }
  });

  prEl("rea-opslaan").addEventListener("click", async () => {
    const door = prEl("rea-door").value.trim();
    const excl = Number(prEl("rea-excl").value || 0);
    if (!door) { alert("Vul in wie de begroting vaststelt."); return; }
    if (!excl) { alert("Vul een bedrag excl. btw in."); return; }
    const inclRaw = prEl("rea-incl").value;
    try {
      await prFetch("api/proces/budget/realisatie", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: prProject(), fase: prEl("rea-fase").value,
          bedrag_excl_btw: excl,
          bedrag_incl_btw: inclRaw ? Number(inclRaw) : null,
          toelichting: prEl("rea-toelichting").value.trim(), door,
        }),
      });
      openBudget();
    } catch (e) { alert(e.message); }
  });
}

/* ------------------------------------------------------------------ admin */

prEl("btn-proces-admin").addEventListener("click", openAdmin);
prEl("proces-admin-sluiten").addEventListener("click", () =>
  prEl("proces-admin-overlay").hidden = true);

async function openAdmin() {
  try {
    procesSjabloon = await prFetch("api/proces/sjabloon");
  } catch (e) { alert(e.message); return; }
  prEl("admin-ai-auto").checked = !!procesSjabloon.ai_automatisch;
  const wrap = prEl("proces-admin-inhoud");
  let html = "";
  for (const f of procesSjabloon.fasen) {
    const stappen = procesSjabloon.stappen.filter(s => s.fase === f.code);
    const fAan = procesData?.fasen?.find(x => x.code === f.code)?.status !== "uit";
    html += `<details class="admin-fase" open>
      <summary><label><input type="checkbox" class="admin-fase-aan"
        data-fase="${f.code}" ${fAan ? "checked" : ""}>
        <strong>${f.code}</strong> — ${prEsc(f.naam)} (tollgate ${f.tollgate})</label>
        <span class="mono">${stappen.length} stappen</span></summary>
      <table class="register proces-tabel"><thead><tr>
        <th class="smal">Aan</th><th class="smal">Nr</th><th>Activiteit</th>
        <th>Product</th><th class="smal">Review</th><th>Uitvoering</th>
      </tr></thead><tbody>`;
    for (const s of stappen) {
      const kanAi = !!s.cap;
      html += `<tr data-stap="${s.id}">
        <td class="smal"><input type="checkbox" class="admin-stap-aan" ${s.actief ? "checked" : ""}></td>
        <td class="mono smal">${prEsc(s.nr || s.id)}</td>
        <td>${prEsc(s.naam)}</td><td>${prEsc(s.product)}</td>
        <td class="smal">${s.review ? "✔" : ""}</td>
        <td><select class="admin-uitvoering" ${kanAi ? "" : "disabled title='geen automatisering beschikbaar voor deze stap'"}>
          ${["ai", "hybride", "mens"].map(u =>
            `<option value="${u}" ${s.uitvoering === u ? "selected" : ""}
              ${!kanAi && u !== "mens" ? "disabled" : ""}>${PR_UITVOERING[u]}</option>`).join("")}
        </select>${s.uitvoering !== s.standaard_uitvoering
          ? ' <span class="mono" title="wijkt af van standaard">✱</span>' : ""}</td>
      </tr>`;
    }
    html += "</tbody></table></details>";
  }
  wrap.innerHTML = html;
  prEl("proces-admin-overlay").hidden = false;
}

prEl("proces-admin-opslaan").addEventListener("click", async () => {
  const fases = {};
  document.querySelectorAll(".admin-fase-aan").forEach(cb =>
    fases[cb.dataset.fase] = cb.checked);
  const stappen = {};
  document.querySelectorAll("#proces-admin-inhoud tr[data-stap]").forEach(tr => {
    stappen[tr.dataset.stap] = {
      actief: tr.querySelector(".admin-stap-aan").checked,
      uitvoering: tr.querySelector(".admin-uitvoering").value,
    };
  });
  try {
    await prFetch("api/proces/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fases, stappen,
                             ai_automatisch: prEl("admin-ai-auto").checked }),
    });
    prEl("proces-admin-overlay").hidden = true;
    ververs();
  } catch (e) { alert(e.message); }
});
