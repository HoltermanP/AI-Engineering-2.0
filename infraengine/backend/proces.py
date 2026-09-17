"""Procesondersteuning: intake (IV) → VO → DO → UO → overdracht realisatie.

Het processjabloon (``STAPPEN``) is opgebouwd uit de aangeleverde
procesdocumenten van de netbeheerder:

  - *Checklist VO GW-MS v1.5* — activiteiten en beheersdocumenten van de
    VO-fase (tollgate TM2), per discipline;
  - *WOL Checklist Tollgates* — T3 (DO), T4 (UO) en T5 (overdracht naar
    realisatie/aannemingsovereenkomst), inclusief welke producten onderdeel
    zijn van de tollgate-review;
  - *LEM-APD DO/UO Overzichtstabel Risico's* — structuur van het kans- en
    risicoregister (oorzaak/gevolg, kans × gevolgsom, beheersmaatregelen);
  - *UKZ DO/UO-ontwikkelnota's* — hoofdstukindeling van de beheersdocumenten
    die de AI-conceptgenerator aanhoudt.

Per stap is configureerbaar (adminomgeving, ``data/proces_config.json``):

  - ``actief`` — stap (product) aan/uit;
  - ``uitvoering`` — ``ai`` (AI voert uit, direct gereed), ``mens``
    (handmatig), of ``hybride`` (AI maakt het concept, de mens keurt goed).

De projectstatus per stap staat in ``data/proces/<project>.json``; door AI
gegenereerde producten (Word/markdown) in ``data/proces/<project>/``.
Triggers: na elke tracéberekening worden de data-gedreven stappen van de
actieve fase automatisch bijgewerkt (``event_trace_berekend``); goedkeuringen
en tollgate-besluiten blijven altijd menselijk.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROCES_DIR = DATA_DIR / "proces"
CONFIG_PAD = DATA_DIR / "proces_config.json"

MODEL = "claude-opus-4-8"
MAX_TOKENS_DOC = 24000
MAX_TOKENS_RISICO = 16000


class ProcesError(Exception):
    pass


# ---------------------------------------------------------------------------
# Fasen en tollgates
# ---------------------------------------------------------------------------

FASEN = [
    {"code": "IV", "naam": "Intake (investeringsvoorstel)", "tollgate": "T1",
     "tollgate_naam": "Intakebesluit — start bouwteam/VO-fase"},
    {"code": "VO", "naam": "Voorlopig Ontwerp", "tollgate": "TM2",
     "tollgate_naam": "Tollgate TM2 — VO vastgesteld, start DO-fase"},
    {"code": "DO", "naam": "Definitief Ontwerp", "tollgate": "T3",
     "tollgate_naam": "Tollgate T3 — DO vastgesteld, start UO-fase"},
    {"code": "UO", "naam": "Uitvoeringsgereed Ontwerp", "tollgate": "T4",
     "tollgate_naam": "Tollgate T4 — UO vastgesteld, start overdracht"},
    {"code": "NAO", "naam": "Overdracht naar realisatie", "tollgate": "T5",
     "tollgate_naam": "Tollgate T5 — aannemingsovereenkomst, start realisatie"},
]

FASE_CODES = [f["code"] for f in FASEN]

# Disciplines (kolom "verantwoordelijke discipline" / rubrieken checklists)
D_DOC = "Documentmanagement"
D_SCOPE = "Scopemanagement"
D_TECH = "Technisch management"
D_OMG = "Omgevingsmanagement"
D_FIN = "Financieel management"
D_PLAN = "Planningsmanagement"
D_RISK = "Risicomanagement"
D_CONTR = "Contractmanagement"
D_PLANNEN = "Uitgewerkte plannen voor realisatie"


def _S(id_, fase, discipline, naam, product, *, nr="", review=False,
       uitvoering="mens", cap=None, opm=""):
    return {"id": id_, "nr": nr, "fase": fase, "discipline": discipline,
            "naam": naam, "product": product, "review": review,
            "uitvoering": uitvoering, "cap": cap, "opmerking": opm}


# ---------------------------------------------------------------------------
# Stappencatalogus. cap = automatiseringssleutel:
#   data:<naam>  — direct af te leiden uit het rekenresultaat/registers
#   doc:<naam>   — AI schrijft een conceptdocument (streaming, → Word)
#   nota:<fase>  — bestaande AI-ontwerpnota (VO/DO/UO)
#   risico       — AI genereert/actualiseert het kans- en risicoregister
#   intake       — AI-analyse van het geüploade IV-document
# ---------------------------------------------------------------------------

STAPPEN: list[dict] = [
    # ---------------- IV: intake vanuit het investeringsvoorstel ----------
    _S("IV-01", "IV", D_DOC, "Registreren investeringsvoorstel (IV)",
       "IV-document in projectdossier", uitvoering="mens",
       opm="Upload het IV-document; daarna kan de AI-analyse draaien."),
    _S("IV-02", "IV", D_SCOPE, "Analyseren IV en opstellen intakeverslag",
       "Intakeverslag (scope, stations, randvoorwaarden)", review=True,
       uitvoering="hybride", cap="intake",
       opm="AI leest het IV-document en destilleert scope en aandachtspunten."),
    _S("IV-03", "IV", D_TECH, "Projectgebied en stations intekenen",
       "Projectgebied + stations in kaart", review=True, uitvoering="mens",
       opm="In het kaartscherm: gebied tekenen, MS-stations plaatsen."),
    _S("IV-04", "IV", D_TECH, "Eerste tracéverkenning (varianten) rekenen",
       "Berekend tracé met varianten + MCA", review=True,
       uitvoering="hybride", cap="data:trace",
       opm="De rekenengine bepaalt varianten; de mens beoordeelt het beeld."),
    _S("IV-05", "IV", D_TECH, "Opdelen tracé in werkpakketten (WBS)",
       "Werkpakketindeling (WP-01 …)", uitvoering="ai", cap="data:wbs"),
    _S("IV-06", "IV", D_FIN, "Indicatieve raming en budgetcheck",
       "Indicatieve raming (RAW) t.o.v. IV-budget", review=True,
       uitvoering="hybride", cap="data:raming"),
    _S("IV-07", "IV", D_RISK, "Eerste projectrisico's benoemen",
       "Initieel kans- en risicoregister", uitvoering="hybride", cap="risico"),
    _S("IV-08", "IV", D_PLAN, "Capaciteits- en doorlooptijdcheck",
       "Indicatieve planning per werkpakket", uitvoering="ai",
       cap="data:planning"),

    # ---------------- VO (tollgate TM2) — Checklist VO GW-MS v1.5 ---------
    _S("VO-3.35", "VO", D_DOC, "Onderhouden documentmanagement",
       "Overzicht van documenten (incl. status)", nr="3.35", review=True,
       uitvoering="ai", cap="data:documenten"),
    _S("VO-3.40", "VO", D_SCOPE, "Uitvoeren afwijkingenmanagement (VO-fase)",
       "Afwijkingenregister", nr="3.40", review=True, uitvoering="hybride",
       cap="doc:afwijkingen"),
    _S("VO-3.41", "VO", D_SCOPE,
       "Signaleren en besluiten (scope)wijzigingen (VO-fase)",
       "Wijzigingenregister + akkoord opdrachtgever", nr="3.41", review=True,
       uitvoering="hybride", cap="doc:wijzigingen",
       opm="Bespreken met vertegenwoordiger K&O en APM."),
    _S("VO-3.33", "VO", D_SCOPE, "WBS-structuur vastleggen",
       "Eisenset tegenover objecten (werkpakketten)", nr="3.33",
       uitvoering="ai", cap="data:wbs"),
    _S("VO-3.23", "VO", D_SCOPE, "Analyseren van eisen en raakvlakken",
       "Verificatieplan (techniek en omgeving)", nr="3.23", review=True,
       uitvoering="hybride", cap="doc:verificatieplan"),
    _S("VO-VO", "VO", D_TECH, "Opstellen Voorlopig Ontwerp (VO)",
       "VO-ontwikkelnota (vast format) met variantenafweging", review=True,
       uitvoering="hybride", cap="nota:VO"),
    _S("VO-TRACE", "VO", D_TECH, "Ontwerpen (deel)tracés",
       "Tracétekeningen (GeoJSON/DXF) per werkpakket", uitvoering="ai",
       cap="data:trace"),
    _S("VO-KLIC", "VO", D_TECH, "Aanmelden KLIC-melding",
       "KLIC-melding (oriëntatie)", uitvoering="hybride", cap="data:klic"),
    _S("VO-SCHOUW-ST", "VO", D_TECH, "Schouwen bestaande stations",
       "Schouwrapport bestaande stations (brownfield)", uitvoering="mens"),
    _S("VO-3.10", "VO", D_TECH, "Inventarisatie asbest (stations)",
       "Rapportage asbestinventarisatie station", nr="3.10", uitvoering="mens"),
    _S("VO-3.25a", "VO", D_TECH, "Bestellen stations en opdracht ombouw",
       "Tijdig bestelde stations (stationconfigurator)", nr="3.25",
       uitvoering="mens"),
    _S("VO-PROEF", "VO", D_TECH, "Maken proefsleuven",
       "Proefsleufgegevens (foto's/rapport)", uitvoering="mens"),
    _S("VO-OVERLEG1", "VO", D_TECH, "Voeren Vooroverleg-1 (VO met WV'er/OIV'er)",
       "Gespreksverslag met acties en besluiten", uitvoering="hybride",
       cap="doc:vooroverleg"),
    _S("VO-3.18", "VO", D_TECH, "Beïnvloedings- en belastbaarheidsberekeningen",
       "Beïnvloedings-/belastbaarheidsberekeningen", nr="3.18",
       uitvoering="mens"),
    _S("VO-3.25b", "VO", D_TECH, "Opstellen materiaallijst",
       "Materiaallijst VO", nr="3.25", uitvoering="ai",
       cap="data:materiaal"),
    _S("VO-3.01", "VO", D_TECH, "Opstellen VGM-plan ontwerp (VGM-O)",
       "VGM-O-plan + overdrachtsformulier coördinatie", nr="3.01", review=True,
       uitvoering="hybride", cap="doc:vgm_o",
       opm="Met betrokkenheid van de VMK-adviseur."),
    _S("VO-QUICK", "VO", D_OMG, "Uitvoeren quickscans conditionering",
       "Quickscan flora/fauna, OOO, archeologie, bodem, hydrologie",
       uitvoering="ai", cap="data:onderzoeken",
       opm="Onderzoeksregister + AI-bureauonderzoeken per onderzoek."),
    _S("VO-SCHOUW", "VO", D_OMG, "Uitvoeren schouw omgeving",
       "Schouwrapport omgeving", uitvoering="mens"),
    _S("VO-3.04", "VO", D_OMG, "Opstellen en afstemmen PvE veldonderzoek",
       "Geaccordeerd PvE door bevoegd gezag", nr="3.04", uitvoering="hybride",
       cap="doc:pve_archeologie"),
    _S("VO-3.05", "VO", D_OMG, "Inventariserend veldonderzoek (IVO)",
       "Adviesrapport archeologie", nr="3.05", uitvoering="mens"),
    _S("VO-3.20", "VO", D_OMG, "Stakeholderanalyse en -management",
       "Stakeholderregister", nr="3.20", review=True, uitvoering="ai",
       cap="data:stakeholders"),
    _S("VO-3.28", "VO", D_OMG, "Verkennende gesprekken vergunningen",
       "Vergunningenlijst incl. indieningsvereisten", nr="3.28",
       uitvoering="hybride", cap="data:vergunningen"),
    _S("VO-3.17", "VO", D_OMG, "Opstellen ZRO-/aankoopsituatietekeningen",
       "ZRO-situatietekeningen per perceel", nr="3.17", uitvoering="hybride",
       cap="data:zro",
       opm="Kan bij VO, moet bij DO — afhankelijk van risico-inschatting."),
    _S("VO-3.31", "VO", D_OMG, "Grondaankoop en vestigen ZRO's (optie)",
       "Getekende ZRO / koopovereenkomst / gedoogbesluit", nr="3.31",
       uitvoering="mens", cap="data:zro_status"),
    _S("VO-3.32", "VO", D_OMG, "Verkrijgen betredingstoestemmingen",
       "Overeengekomen betredingstoestemmingen", nr="3.32", uitvoering="mens"),
    _S("VO-3.08", "VO", D_OMG, "Inventarisatie bomenkap",
       "Rapportage impact op bomen langs tracé", nr="3.08", uitvoering="ai",
       cap="data:bomen"),
    _S("VO-3.14", "VO", D_OMG, "G-waardeonderzoek (indien van toepassing)",
       "Onderzoeksrapport g-waarde", nr="3.14", uitvoering="mens"),
    _S("VO-3.44", "VO", D_FIN, "Uitvoeringsraming VO-fase",
       "Concept-uitvoeringsraming (RAW)", nr="3.44", review=True,
       uitvoering="ai", cap="data:raming"),
    _S("VO-TSB", "VO", D_FIN, "Taakstellend budget t.b.v. DO-fase",
       "Taakstellend budget DO-fase", uitvoering="mens"),
    _S("VO-3.27", "VO", D_FIN, "Controleren en verwerken (deel)betalingen",
       "Ingediende en betaalde facturen", nr="3.27", uitvoering="mens"),
    _S("VO-3.42", "VO", D_PLAN, "Afgeven forecast benodigde capaciteit",
       "Resourceplanning", nr="3.42", uitvoering="mens"),
    _S("VO-3.36", "VO", D_PLAN, "Actualiseren projectplanning (VO)",
       "Geactualiseerde projectplanning", nr="3.36", review=True,
       uitvoering="ai", cap="data:planning"),
    _S("VO-3.21", "VO", D_RISK, "Uitvoeren kans- en risicomanagement",
       "Geactualiseerd kans- en risicoregister", nr="3.21", review=True,
       uitvoering="hybride", cap="risico"),
    _S("VO-3.38", "VO", D_CONTR, "Inkopen/inhuren producten en diensten",
       "Getekend inkoop-/inhuurcontract", nr="3.38", uitvoering="mens"),
    _S("VO-3.45", "VO", D_CONTR, "Totstandkoming bouwteamovereenkomst",
       "Getekende bouwteamovereenkomst (BTO)", nr="3.45", uitvoering="mens"),

    # ---------------- DO (tollgate T3) — WOL checklist T3 ------------------
    _S("DO-3.35", "DO", D_DOC, "Onderhouden documentmanagement",
       "Overzicht van documenten (incl. status)", nr="3.35", review=True,
       uitvoering="ai", cap="data:documenten"),
    _S("DO-3.40", "DO", D_SCOPE, "Uitvoeren afwijkingenmanagement (DO-fase)",
       "Afwijkingenregister", nr="3.40", review=True, uitvoering="hybride",
       cap="doc:afwijkingen",
       opm="Randvoorwaardelijk om de tollgate te kunnen uitvoeren."),
    _S("DO-3.41", "DO", D_SCOPE,
       "Signaleren en besluiten (scope)wijzigingen (DO-fase)",
       "Wijzigingenregister + akkoord opdrachtgever", nr="3.41", review=True,
       uitvoering="hybride", cap="doc:wijzigingen"),
    _S("DO-3.33", "DO", D_SCOPE, "WBS-structuur vastleggen",
       "Eisenset tegenover objecten", nr="3.33", uitvoering="ai",
       cap="data:wbs"),
    _S("DO-3.23", "DO", D_SCOPE, "Eisenverificatie DO",
       "Geverifieerde eisenset (V&V)", nr="3.23", review=True,
       uitvoering="hybride", cap="doc:eisenverificatie"),
    _S("DO-3.22", "DO", D_TECH, "Opstellen Definitief Ontwerp (DO)",
       "DO-ontwikkelnota (vast format), geaccordeerd door bouwteam",
       nr="3.22", uitvoering="hybride", cap="nota:DO"),
    _S("DO-3.43", "DO", D_TECH, "Vaststellen DO",
       "Vastgesteld Definitief Ontwerp (ontwerpnota)", nr="3.43", review=True,
       uitvoering="mens",
       opm="Formele vaststelling door de projectleider — altijd menselijk."),
    _S("DO-3.19", "DO", D_TECH, "Proefsleuven (input DO) + KLIC",
       "Rapportage proefsleuven en KLIC-melding", nr="3.19",
       uitvoering="hybride", cap="data:klic"),
    _S("DO-3.24", "DO", D_TECH, "Voeren Vooroverleg-2 (DO met OIV'er)",
       "Gespreksverslag OIV'er met acties en besluiten", nr="3.24",
       review=True, uitvoering="hybride", cap="doc:vooroverleg"),
    _S("DO-VGM", "DO", D_TECH, "Reviewen VGM-O-plan",
       "Risico-inventarisatie VGM-O-plan", review=True, uitvoering="hybride",
       cap="doc:vgm_o"),
    _S("DO-3.18", "DO", D_TECH, "Beïnvloedings- en belastbaarheidsberekeningen",
       "Verwerkt in het ontwerp", nr="3.18", uitvoering="mens"),
    _S("DO-3.34", "DO", D_TECH, "Vastleggen DO in GIS-systeem",
       "GeoJSON/DXF-export van het definitieve tracé", nr="3.34",
       uitvoering="ai", cap="data:trace"),
    _S("DO-3.26", "DO", D_TECH, "Invullen materiaallijst DO",
       "Materiaallijst DO", nr="3.26", review=True, uitvoering="ai",
       cap="data:materiaal"),
    _S("DO-3.25", "DO", D_TECH, "Bestellen stations en materialen",
       "Tijdig bestelde stations en materialen", nr="3.25", uitvoering="mens"),
    _S("DO-3.02", "DO", D_OMG, "Opstellen bouwcommunicatieplan",
       "Vastgesteld bouwcommunicatieplan", nr="3.02", uitvoering="hybride",
       cap="doc:bouwcommunicatie"),
    _S("DO-3.03", "DO", D_OMG, "Opstellen publiekscommunicatieplan",
       "Vastgesteld publiekscommunicatieplan", nr="3.03", uitvoering="hybride",
       cap="doc:publiekscommunicatie"),
    _S("DO-3.04", "DO", D_OMG, "PvE veldonderzoek afstemmen",
       "Geaccordeerd PvE door bevoegd gezag", nr="3.04", uitvoering="hybride",
       cap="doc:pve_archeologie"),
    _S("DO-3.05", "DO", D_OMG, "Inventariserend veldonderzoek (IVO)",
       "Adviesrapport archeologie", nr="3.05", uitvoering="mens"),
    _S("DO-3.20", "DO", D_OMG, "Uitvoeren stakeholdermanagement",
       "Stakeholderregister", nr="3.20", review=True, uitvoering="ai",
       cap="data:stakeholders"),
    _S("DO-3.28", "DO", D_OMG, "Verkennende gesprekken vergunningen",
       "Vergunningenlijst incl. indieningsvereisten", nr="3.28",
       uitvoering="hybride", cap="data:vergunningen"),
    _S("DO-3.30", "DO", D_OMG, "Aanvragen vergunningen (DO-fase)",
       "Lijst van aangevraagde vergunningen", nr="3.30", review=True,
       uitvoering="hybride", cap="data:vergunningen"),
    _S("DO-3.17", "DO", D_OMG, "ZRO-/aankoopsituatietekeningen",
       "ZRO-situatietekeningen per perceel", nr="3.17", uitvoering="hybride",
       cap="data:zro"),
    _S("DO-3.31", "DO", D_OMG, "Grondaankoop en vestigen ZRO's",
       "Getekende ZRO / koopovereenkomst / gedoogbesluit", nr="3.31",
       review=True, uitvoering="mens", cap="data:zro_status"),
    _S("DO-3.32", "DO", D_OMG, "Verkrijgen betredingstoestemmingen",
       "Overeengekomen betredingstoestemmingen", nr="3.32", uitvoering="mens"),
    _S("DO-3.07", "DO", D_OMG, "Aanvullend onderzoek ecologie",
       "Adviesrapport ecologie", nr="3.07", uitvoering="mens"),
    _S("DO-3.08", "DO", D_OMG, "Inventarisatie bomenkap",
       "Rapportage impact op bomen langs tracé", nr="3.08", uitvoering="ai",
       cap="data:bomen"),
    _S("DO-3.09", "DO", D_OMG, "Verkennend bodemonderzoek (NEN 5740)",
       "Definitief rapport verkennend bodemonderzoek", nr="3.09",
       uitvoering="mens"),
    _S("DO-3.10", "DO", D_OMG, "Asbestonderzoek bodem (NEN 5707)",
       "Rapportage asbest in bodem", nr="3.10", uitvoering="mens"),
    _S("DO-3.11", "DO", D_OMG, "Aanvullend bodemonderzoek",
       "Onderzoeksrapport aanvullend bodemonderzoek", nr="3.11",
       uitvoering="mens"),
    _S("DO-3.12", "DO", D_OMG, "Onderzoek bodemziektes",
       "Rapportage aanwezige bodemziektes", nr="3.12", uitvoering="mens"),
    _S("DO-3.14", "DO", D_OMG, "G-waardeonderzoek",
       "Onderzoeksrapport g-waarde", nr="3.14", uitvoering="mens"),
    _S("DO-3.15", "DO", D_OMG, "Bemalingsadvies",
       "Geohydrologisch rapport incl. bemalingsadvies", nr="3.15",
       uitvoering="hybride", cap="doc:bemalingsadvies"),
    _S("DO-3.16", "DO", D_OMG, "Geotechnisch onderzoek",
       "Onderzoeksrapport geotechnisch onderzoek (sonderingen)", nr="3.16",
       uitvoering="hybride", cap="data:sonderingen"),
    _S("DO-3.44", "DO", D_FIN, "Begroten DO",
       "Vastgestelde DO-begroting (RAW)", nr="3.44", review=True,
       uitvoering="ai", cap="data:raming"),
    _S("DO-TSB", "DO", D_FIN, "Bepalen taakstellend budget (DO-fase)",
       "Taakstellend budget DO-fase", uitvoering="mens"),
    _S("DO-3.39", "DO", D_FIN, "Bewaken taakstellend budget (DO-fase)",
       "Kostenoverzicht t.o.v. taakstellend budget", nr="3.39",
       uitvoering="mens"),
    _S("DO-3.27", "DO", D_FIN, "Controleren en verwerken (deel)betalingen",
       "Ingediende en betaalde facturen", nr="3.27", uitvoering="mens"),
    _S("DO-3.38", "DO", D_PLAN, "Inkopen/inhuren producten en diensten",
       "Getekend inkoop-/inhuurcontract", nr="3.38", uitvoering="mens"),
    _S("DO-3.42", "DO", D_PLAN, "Forecast benodigde capaciteit (zacht)",
       "Resourceplanning", nr="3.42", uitvoering="mens"),
    _S("DO-3.36", "DO", D_PLAN, "Actualiseren projectplanning (DO)",
       "Geactualiseerde projectplanning", nr="3.36", review=True,
       uitvoering="ai", cap="data:planning"),
    _S("DO-3.21", "DO", D_RISK, "Uitvoeren kans- en risicomanagement",
       "Geactualiseerd kans- en risicoregister", nr="3.21", review=True,
       uitvoering="hybride", cap="risico"),
    _S("DO-3.45", "DO", D_CONTR, "Bouwteamovereenkomst",
       "Getekende bouwteamovereenkomst", nr="3.45", uitvoering="mens"),

    # ---------------- UO (tollgate T4) — WOL checklist T4 ------------------
    _S("UO-4.28", "UO", D_DOC, "Onderhouden documentmanagement",
       "Overzicht van documenten (incl. status)", nr="4.28", review=True,
       uitvoering="ai", cap="data:documenten"),
    _S("UO-4.32", "UO", D_SCOPE, "Uitvoeren afwijkingenmanagement (UO-fase)",
       "Afwijkingenregister", nr="4.32", review=True, uitvoering="hybride",
       cap="doc:afwijkingen"),
    _S("UO-4.31", "UO", D_SCOPE,
       "Signaleren en besluiten scope-/projectwijzigingen (UO-fase)",
       "Wijzigingenregister + akkoord opdrachtgever (APM)", nr="4.31",
       review=True, uitvoering="hybride", cap="doc:wijzigingen"),
    _S("UO-4.18", "UO", D_TECH, "Bepalen uitvoeringsmethode",
       "Werkplan t.b.v. civiel werk", nr="4.18", uitvoering="hybride",
       cap="doc:werkplan_civiel"),
    _S("UO-4.20", "UO", D_TECH, "Opstellen Uitvoeringsgereed Ontwerp (UO)",
       "UO-ontwikkelnota (vast format)", nr="4.20",
       uitvoering="hybride", cap="nota:UO"),
    _S("UO-4.34", "UO", D_TECH, "Vaststellen UO",
       "Definitief Uitvoeringsontwerp", nr="4.34", review=True,
       uitvoering="mens",
       opm="Formele vaststelling door de projectleider — altijd menselijk."),
    _S("UO-4.19", "UO", D_PLANNEN, "Opstellen boorplannen",
       "Vastgesteld boorplan per boring", nr="4.19", uitvoering="hybride",
       cap="doc:boorplan"),
    _S("UO-4.43", "UO", D_PLANNEN, "Planning en volgordelijkheid warm werk",
       "Overkoepelend werkplan warm werk", nr="4.43", uitvoering="hybride",
       cap="doc:werkplan_warm"),
    _S("UO-4.42", "UO", D_PLANNEN, "Vaststellen civiele werkplannen",
       "Geaccordeerd werkplan civiel werk (ondertekend OIV)", nr="4.42",
       uitvoering="mens"),
    _S("UO-4.44", "UO", D_PLANNEN, "Opstellen bedieningsplan(nen)",
       "Geaccordeerd bedieningsplan", nr="4.44", uitvoering="hybride",
       cap="doc:bedieningsplan"),
    _S("UO-4.22", "UO", D_PLANNEN, "Eisenverificatie UO",
       "Verificatierapport eisenset UO", nr="4.22", review=True,
       uitvoering="hybride", cap="doc:eisenverificatie"),
    _S("UO-4.27", "UO", D_PLANNEN, "Opstellen keuringsplan(nen)",
       "Keuringsplan", nr="4.27", uitvoering="hybride", cap="doc:keuringsplan"),
    _S("UO-4.41", "UO", D_PLANNEN, "Opstellen VGM-plan uitvoering (VGM-U)",
       "VGM-U-plan (i.s.m. VMK-adviseur)", nr="4.41", uitvoering="hybride",
       cap="doc:vgm_u"),
    _S("UO-4.16", "UO", D_PLANNEN, "Controle kabels en leidingen derden",
       "Overzicht te kruisen K&L derden incl. toestemmingen", nr="4.16",
       uitvoering="ai", cap="data:kl_derden"),
    _S("UO-4.01", "UO", D_OMG, "Uitvoeren stakeholdermanagement (UO-fase)",
       "Stakeholderregister", nr="4.01", uitvoering="ai",
       cap="data:stakeholders"),
    _S("UO-4.02", "UO", D_OMG, "Uitvoeren bouwcommunicatieplan",
       "Deliverables conform bouwcommunicatieplan", nr="4.02",
       uitvoering="mens"),
    _S("UO-4.03", "UO", D_OMG, "Uitvoeren publiekscommunicatieplan",
       "Conform publiekscommunicatieplan", nr="4.03", uitvoering="mens"),
    _S("UO-4.04", "UO", D_OMG, "Inventariserend veldonderzoek archeologie",
       "Rapportage veldonderzoek", nr="4.04", uitvoering="mens"),
    _S("UO-4.05", "UO", D_OMG, "Projectgebonden risicoanalyse CS-OOO",
       "Projectgebonden risicoanalyse (ontplofbare oorlogsresten)", nr="4.05",
       uitvoering="hybride", cap="doc:pra_ooo"),
    _S("UO-4.06", "UO", D_OMG, "Opstellen projectplan CS-OOO",
       "Goedgekeurd projectplan + melding CI en EOD", nr="4.06",
       uitvoering="hybride", cap="doc:projectplan_ooo"),
    _S("UO-4.07", "UO", D_OMG, "Begeleiding en uitvoering detectieonderzoek",
       "Rapportage detectieonderzoek", nr="4.07", uitvoering="mens"),
    _S("UO-4.08", "UO", D_OMG, "Opstellen ecologisch werkprotocol",
       "Ecologisch werkprotocol", nr="4.08", uitvoering="hybride",
       cap="doc:eco_protocol"),
    _S("UO-4.09", "UO", D_OMG, "Opstellen verkeersmaatregelenplan",
       "Goedgekeurde verkeersmaatregelen voor het tracé", nr="4.09",
       uitvoering="hybride", cap="doc:verkeersplan"),
    _S("UO-4.10", "UO", D_OMG, "Preventieve mitigerende maatregelen F&F",
       "Uitgevoerde mitigerende maatregelen", nr="4.10", uitvoering="mens"),
    _S("UO-4.13", "UO", D_OMG, "Definitieve AERIUS-berekening",
       "Stikstofberekening met bijbehorende acties", nr="4.13",
       uitvoering="mens"),
    _S("UO-4.11", "UO", D_OMG, "Opstellen bemalingsplan",
       "Bemalingsplan", nr="4.11", uitvoering="hybride",
       cap="doc:bemalingsplan"),
    _S("UO-4.12", "UO", D_OMG, "Opstellen cultuurtechnisch advies",
       "Advies per agrarisch perceel", nr="4.12", uitvoering="hybride",
       cap="doc:cultuurtechnisch"),
    _S("UO-4.14", "UO", D_OMG, "Werkafspraken met grondeigenaren",
       "Werkafspraken per perceel(eigenaar)", nr="4.14", review=True,
       uitvoering="mens", cap="data:zro_status"),
    _S("UO-4.15", "UO", D_OMG, "Gedoogprocedure",
       "Gedoogbeschikking", nr="4.15", review=True, uitvoering="mens"),
    _S("UO-4.21", "UO", D_OMG, "Aanvragen vergunningen (UO-fase)",
       "Overzicht van vergunningen (incl. status)", nr="4.21", review=True,
       uitvoering="hybride", cap="data:vergunningen"),
    _S("UO-ZRO", "UO", D_OMG, "Grondaankoop en vestigen ZRO's",
       "Gevestigde ZRO's (alle percelen)", uitvoering="mens",
       cap="data:zro_status"),
    _S("UO-4.33", "UO", D_FIN, "Bewaken taakstellend budget (bouwteamfase)",
       "UO-raming (RAW)", nr="4.33", review=True, uitvoering="ai",
       cap="data:raming"),
    _S("UO-4.25", "UO", D_PLAN, "Inkopen producten/diensten (UO-fase)",
       "Getekend inkoop-/inhuurcontract", nr="4.25", uitvoering="mens"),
    _S("UO-4.24", "UO", D_PLAN, "Aanvraag capaciteit (harde planning)",
       "Bevestiging uitvoeringscapaciteit", nr="4.24", uitvoering="mens"),
    _S("UO-4.30", "UO", D_PLAN, "Actualiseren projectplanning (UO)",
       "Up-to-date projectplanning", nr="4.30", uitvoering="ai",
       cap="data:planning"),
    _S("UO-4.39", "UO", D_PLAN, "Vaststellen uitvoeringsplanning",
       "Vastgestelde uitvoeringsplanning", nr="4.39", review=True,
       uitvoering="hybride", cap="data:planning"),
    _S("UO-4.17", "UO", D_RISK, "Uitvoeren risicomanagement",
       "Geactualiseerd kans- en risicoregister", nr="4.17", review=True,
       uitvoering="hybride", cap="risico"),
    _S("UO-CAR", "UO", D_CONTR, "Noodzaak aanvullende CAR-verzekering",
       "Aangemelde CAR (indien van toepassing)", uitvoering="mens"),
    _S("UO-4.46", "UO", D_CONTR, "Aanmelden project bij verzekeraar",
       "Bevestiging aanmelding verzekeraar", nr="4.46", uitvoering="mens"),

    # ---------------- NAO (tollgate T5) — overdracht naar realisatie -------
    _S("NAO-4.28", "NAO", D_DOC, "Onderhouden documentmanagement",
       "Overzicht van documenten (incl. status)", nr="4.28", review=True,
       uitvoering="ai", cap="data:documenten"),
    _S("NAO-4.29", "NAO", D_RISK, "Kans- en risicodossier actualiseren",
       "Geactualiseerd kans- en risicoregister", nr="4.29", review=True,
       uitvoering="hybride", cap="risico"),
    _S("NAO-4.39", "NAO", D_PLAN, "Vaststellen uitvoeringsplanning",
       "Vastgestelde uitvoeringsplanning", nr="4.39", review=True,
       uitvoering="hybride", cap="data:planning"),
    _S("NAO-4.34", "NAO", D_TECH, "Vaststellen UO (definitief)",
       "Definitief Uitvoeringsontwerp", nr="4.34", uitvoering="mens"),
    _S("NAO-4.37", "NAO", D_FIN, "Taakstellend budget realisatiefase",
       "Geaccordeerd taakstellend budget (aanneemsom)", nr="4.37", review=True,
       uitvoering="hybride", cap="data:raming"),
    _S("NAO-4.45", "NAO", D_CONTR, "Totstandkoming aannemingsovereenkomst",
       "Getekende aannemingsovereenkomst + oplegnotitie", nr="4.45",
       review=True, uitvoering="hybride", cap="doc:oplegnotitie"),
]

STAP_INDEX = {s["id"]: s for s in STAPPEN}


# ---------------------------------------------------------------------------
# AI-conceptdocumenten: prompts per doc-capability. De AI schrijft een
# concept-beheersdocument op basis van de volledige projectcontext
# (nota.bouw_context); de mens keurt goed (hybride) of het document telt
# direct als gereed (uitvoering=ai).
# ---------------------------------------------------------------------------

AI_DOC: dict[str, dict] = {
    "afwijkingen": {
        "titel": "Afwijkingenregister",
        "doel": "Stel een afwijkingenregister op. Leid kandidaat-afwijkingen "
                "af uit de toetsingsresultaten (knelpunten, kritieke "
                "meldingen), databeperkingen en risico's: per afwijking een "
                "nummer, omschrijving, afwijking van welke eis/richtlijn, "
                "voorstel tot afhandeling (accepteren/herstellen/escaleren) "
                "en status. Tabelvorm.",
    },
    "wijzigingen": {
        "titel": "Wijzigingenregister",
        "doel": "Stel een wijzigingenregister op met de scope zoals die nu "
                "in de data staat als referentie (stations, tracélengte, "
                "werkpakketten). Beschrijf de procedure voor het signaleren, "
                "beoordelen (impact op geld/tijd/kwaliteit) en besluiten van "
                "wijzigingen met akkoord van de opdrachtgever, en een lege "
                "registertabel met voorbeeldregels op basis van actuele "
                "aandachtspunten uit de toetsing.",
    },
    "verificatieplan": {
        "titel": "Verificatieplan eisen en raakvlakken",
        "doel": "Analyseer de eisen en raakvlakken van dit tracéproject en "
                "stel een verificatieplan op (techniek én omgeving): welke "
                "eisen gelden (richtlijnen, dekking, buigradius, "
                "kruisingstechnieken, vergunningsvoorwaarden), hoe en wanneer "
                "elke eis geverifieerd wordt, en welke raakvlakpartijen "
                "(mede-netbeheerders, wegbeheerders, waterschap, ProRail) "
                "betrokken zijn.",
    },
    "eisenverificatie": {
        "titel": "Verificatierapport eisenset",
        "doel": "Verifieer de eisenset tegen het berekende ontwerp: loop de "
                "toetsingsresultaten langs en geef per eis het oordeel "
                "(voldoet/voldoet niet/niet toetsbaar met deze data), het "
                "bewijs uit de registers en de restpunten. Sluit af met een "
                "verificatiematrix per werkpakket.",
    },
    "vgm_o": {
        "titel": "VGM-plan ontwerpfase (VGM-O)",
        "doel": "Stel een VGM-O-plan op (veiligheid, gezondheid, milieu) voor "
                "de ontwerpfase conform Arbobesluit bouwproces: "
                "projectgegevens, betrokken partijen, de uit het ontwerp "
                "volgende risico's (boringen, bemaling, werken langs wegen "
                "en water, verontreinigde bodem, NGE) met beheersmaatregelen "
                "in de ontwerpkeuzes, en de restrisico's die naar de "
                "uitvoeringsfase gaan (overdrachtsformulier coördinatie).",
    },
    "vgm_u": {
        "titel": "VGM-plan uitvoeringsfase (VGM-U)",
        "doel": "Stel een VGM-U-plan op voor de uitvoering: organisatie en "
                "verantwoordelijkheden (V&G-coördinator uitvoering), de "
                "risico-inventarisatie per werkpakket en per boring "
                "(werkterreinen, verkeer, bemaling, kabels en leidingen "
                "derden, verontreinigde bodem, NGE), beheersmaatregelen, "
                "PBM's, en afspraken over toezicht, instructie en melding "
                "van incidenten.",
    },
    "vooroverleg": {
        "titel": "Agenda en gespreksverslag vooroverleg",
        "doel": "Bereid het vooroverleg met de werkvoorbereider en de OIV'er "
                "voor: een agenda plus per agendapunt de relevante feiten uit "
                "het ontwerp (tracékeuze, kruisingen en technieken, boringen "
                "met werkterreinoordeel, knelpunten uit de toetsing, "
                "openstaande onderzoeken) en de te nemen besluiten. Werk uit "
                "als in te vullen gespreksverslag met actie- en besluitenlijst.",
    },
    "pve_archeologie": {
        "titel": "Programma van Eisen archeologisch veldonderzoek",
        "doel": "Stel een concept-PvE op voor inventariserend archeologisch "
                "veldonderzoek langs dit tracé: aanleiding en plangebied, de "
                "archeologische verwachting (AMK-zones uit de data), de "
                "onderzoeksvragen, methode (verkennend booronderzoek langs "
                "het tracé), en de eisen aan rapportage en bevoegd gezag.",
    },
    "bouwcommunicatie": {
        "titel": "Bouwcommunicatieplan",
        "doel": "Stel een bouwcommunicatieplan op: doelgroepen (omwonenden, "
                "bedrijven, weggebruikers, agrarische perceeleigenaren), "
                "communicatiemomenten per werkpakket gekoppeld aan de "
                "planning, middelen (bewonersbrief, inloopavond, borden, "
                "social media), bereikbaarheid tijdens het werk en de "
                "klachtenprocedure.",
    },
    "publiekscommunicatie": {
        "titel": "Publiekscommunicatieplan",
        "doel": "Stel een publiekscommunicatieplan op namens de netbeheerder: "
                "kernboodschap (waarom deze verzwaring), woordvoering, "
                "pers- en omgevingscommunicatie, afstemming met gemeente en "
                "stakeholders, en de planning van publieksmomenten.",
    },
    "werkplan_civiel": {
        "titel": "Werkplan civiel werk",
        "doel": "Stel per werkpakket een werkplan civiel op: "
                "uitvoeringsmethode per tracédeel (open sleuf per ligging, "
                "sleufloze technieken per kruising), sleufprofiel, "
                "grondstromen, bemaling, verkeersmaatregelen, werkterreinen "
                "en opslag, herstel verhardingen en bermen, en de "
                "werkvolgorde met raakvlakken (warm werk, moffen).",
    },
    "werkplan_warm": {
        "titel": "Werkplan warm werk (overkoepelend)",
        "doel": "Stel het overkoepelende werkplan voor het warme werk op: "
                "volgorde van kabelleggen, moffen en aansluiten per "
                "werkpakket (streng-volgorde uit de planning), haspellengtes "
                "en moflocaties uit het moffenplan, raakvlakken met het "
                "civiele werk, beproeving en inbedrijfname.",
    },
    "bedieningsplan": {
        "titel": "Bedieningsplan",
        "doel": "Stel een concept-bedieningsplan op voor de netschakelingen "
                "die nodig zijn om de nieuwe MS-verbinding in bedrijf te "
                "nemen: uitgangssituatie, de schakelvolgorde per station, "
                "veiligstellen en vrijgave, terugvalscenario en de "
                "betrokken functionarissen (WV'er, OIV'er, bedieningscentrum).",
    },
    "keuringsplan": {
        "titel": "Keuringsplan",
        "doel": "Stel een keuringsplan op: de keur- en stoppunten per "
                "werkpakket (sleufdiepte en dekking, zandbed, mantelbuizen, "
                "boringen incl. dieptemeting, moffen, aanvulling en "
                "verdichting, herstel verharding), de bijbehorende "
                "registraties en wie keurt (aannemer/OIV/netbeheerder).",
    },
    "verkeersplan": {
        "titel": "Verkeersmaatregelenplan",
        "doel": "Stel een verkeersmaatregelenplan op voor de bouwlogistiek: "
                "per rijbaankruising en wegwerkvak de maatregel (halve "
                "rijbaanafzetting, omleiding, verkeersregelaars) volgens "
                "CROW 96b, de wegbeheerder uit de vergunningendata, aan- en "
                "afvoerroutes en de afstemming per werkpakket met de planning.",
    },
    "bemalingsplan": {
        "titel": "Bemalingsplan",
        "doel": "Stel een concept-bemalingsplan op: verwachte "
                "grondwaterstanden (uit beschikbare data en bodemtype), "
                "bemalingsmethode per tracédeel en per boring, "
                "lozingsroutes, meldingen/vergunningen (waterschap), "
                "monitoring (peilbuizen) en de grens waarbij een "
                "melding overgaat in een vergunningplicht.",
    },
    "bemalingsadvies": {
        "titel": "Geohydrologisch advies (bemalingsadvies)",
        "doel": "Stel een concept-geohydrologisch advies op: bodemopbouw "
                "langs het tracé (uit bodemkaart en sonderingen), verwachte "
                "grondwaterstanden, benodigde verlaging per tracédeel, "
                "bemalingsmethode en debiet-indicatie, lozing en "
                "vergunningplicht, en aandachtspunten (zettingen, "
                "grondwaterbeschermingsgebied).",
    },
    "pra_ooo": {
        "titel": "Projectgebonden risicoanalyse ontplofbare oorlogsresten",
        "doel": "Stel een projectgebonden risicoanalyse (PRA) ontplofbare "
                "oorlogsresten op: het vooronderzoek (verdachte gebieden uit "
                "de data of 'onbekend — handmatig beoordelen'), de "
                "voorgenomen grondroerende werkzaamheden per werkpakket "
                "(sleuven, boringen, werkterreinen), het risico per "
                "activiteit en de vervolgstappen (detectie, benadering, "
                "protocol toevalsvondst).",
    },
    "projectplan_ooo": {
        "titel": "Projectplan opsporing ontplofbare oorlogsresten (CS-OOO)",
        "doel": "Stel een concept-projectplan CS-OOO op: opsporingsgebied, "
                "detectiemethode, veiligheidsafstanden, taken en "
                "verantwoordelijkheden (senior OCE-deskundige, EOD), "
                "meldingsprocedure bij de certificerende instelling en het "
                "protocol bij aantreffen.",
    },
    "eco_protocol": {
        "titel": "Ecologisch werkprotocol",
        "doel": "Stel een ecologisch werkprotocol op: beschermde natuur "
                "langs het tracé (Natura 2000/NNN-zones uit de data), "
                "kwetsbare perioden (broedseizoen), maatregelen per "
                "werkpakket (vrijgave door ecoloog, loopplanken in de sleuf, "
                "werkrichting, verlichting), en de meldingsprocedure bij "
                "aantreffen van beschermde soorten.",
    },
    "cultuurtechnisch": {
        "titel": "Cultuurtechnisch advies agrarische percelen",
        "doel": "Stel per gekruist agrarisch perceel (ZRO-register) een "
                "cultuurtechnisch advies op: gescheiden ontgraven en "
                "terugplaatsen van teelaarde, rijplaten en insporing, "
                "drainageherstel, verdichting en nazorg, en de afspraken die "
                "met de eigenaar vastgelegd worden (aansluitend op de "
                "vergoedingssystematiek in de dossiers).",
    },
    "boorplan": {
        "titel": "Boorplannen gestuurde boringen",
        "doel": "Stel per boring uit het boorregister een concept-boorplan "
                "op: techniek, in- en uittredepunt (RD-coördinaten), lengte "
                "en uitloop, dekkingseis, mantelbuis, maaiveldprofiel "
                "(hoogtedata), beschikbare sonderingen (BRO-nummers), "
                "werkterrein-oordeel, boorvloeistof en meldingen "
                "(grondwaterbescherming), en de vrijgavecriteria. Eén "
                "hoofdstuk per boring, compact.",
    },
    "oplegnotitie": {
        "titel": "Oplegnotitie realisatieovereenkomst",
        "doel": "Stel de oplegnotitie bij de aannemingsovereenkomst op: "
                "scope en aanneemsom (uit de RAW-calculatie), de "
                "uitvoeringsplanning, de belangrijkste voorwaarden uit "
                "vergunningen en ZRO's, de restrisico's uit het "
                "risicoregister met allocatie OG/ON, en de openstaande "
                "punten bij overdracht naar realisatie.",
    },
}

DOC_SYSTEM = (
    "Je bent een senior werkvoorbereider/adviseur kabelinfrastructuur bij "
    "een Nederlandse netbeheerder. Je stelt concept-beheersdocumenten op "
    "voor middenspanningsprojecten volgens de tollgate-systematiek "
    "(VO/DO/UO). Je schrijft in het Nederlands, zakelijk en concreet, en "
    "baseert je uitsluitend op de aangeleverde projectdata. Noem concrete "
    "aantallen, lengtes, locaties en registratienummers uit de data. "
    "Verzin niets: waar data ontbreekt schrijf je een duidelijk gemarkeerde "
    "invulplek [IN TE VULLEN: …] of benoem je de aanname expliciet.\n\n"
    "OPMAAK — begrensde Markdown, niets anders: begin met `# <titel>`, dan "
    "`## Samenvatting` en genummerde hoofdstukken `## 1. <titel>` met waar "
    "zinvol `### 1.1 <titel>`. Opsommingen met `- `, kernoordelen **vet**, "
    "tabellen met kopregel en `|---|`, maximaal 6 kolommen. Geen "
    "code-blokken, links, afbeeldingen of HTML. Dit is een CONCEPT dat door "
    "de verantwoordelijke wordt getoetst; sluit af met een hoofdstuk "
    "'Openstaande punten en benodigde menselijke toetsing'."
)


# ---------------------------------------------------------------------------
# Configuratie (admin): welke stappen/fasen actief zijn en wie ze uitvoert
# ---------------------------------------------------------------------------

def laad_config() -> dict:
    cfg = {"fases": {}, "stappen": {}, "ai_automatisch": True}
    if CONFIG_PAD.exists():
        try:
            cfg.update(json.loads(CONFIG_PAD.read_text()))
        except Exception:
            pass
    return cfg


def bewaar_config(cfg: dict) -> dict:
    huidig = laad_config()
    for k in ("fases", "stappen"):
        if isinstance(cfg.get(k), dict):
            huidig[k].update(cfg[k])
    if "ai_automatisch" in cfg:
        huidig["ai_automatisch"] = bool(cfg["ai_automatisch"])
    CONFIG_PAD.write_text(json.dumps(huidig, ensure_ascii=False, indent=1))
    return huidig


def _stap_config(cfg: dict, stap: dict) -> dict:
    """Effectieve instellingen voor één stap (sjabloon + admin-overrides)."""
    o = cfg["stappen"].get(stap["id"], {})
    return {"actief": o.get("actief", True),
            "uitvoering": o.get("uitvoering", stap["uitvoering"])}


def fase_actief(cfg: dict, code: str) -> bool:
    return bool(cfg["fases"].get(code, True))


# ---------------------------------------------------------------------------
# Projectstatus
# ---------------------------------------------------------------------------

STATUSSEN = ("te_doen", "bezig", "concept_gereed", "gereed", "nvt",
             "afgekeurd")


def _slug(naam: str) -> str:
    s = re.sub(r"[^\w\-]", "_", (naam or "").strip())[:60]
    if not s:
        raise ProcesError("Geef een projectnaam op.")
    return s


def _state_pad(project: str) -> Path:
    return PROCES_DIR / f"{_slug(project)}.json"


def artefact_dir(project: str) -> Path:
    d = PROCES_DIR / _slug(project)
    d.mkdir(parents=True, exist_ok=True)
    return d


def laad_state(project: str) -> dict:
    pad = _state_pad(project)
    state = {"project": project, "stappen": {}, "tollgates": {},
             "risico": [], "meldingen": []}
    if pad.exists():
        try:
            state.update(json.loads(pad.read_text()))
        except Exception:
            pass
    return state


def bewaar_state(state: dict) -> None:
    PROCES_DIR.mkdir(parents=True, exist_ok=True)
    _state_pad(state["project"]).write_text(
        json.dumps(state, ensure_ascii=False))


def _stap_state(state: dict, stap_id: str) -> dict:
    return state["stappen"].setdefault(stap_id, {
        "status": "te_doen", "toelichting": "", "verantwoordelijke": "",
        "artefacten": [], "log": []})


def _log(st: dict, actie: str, door: str = "") -> None:
    st["log"].append({"tijd": time.strftime("%Y-%m-%d %H:%M"),
                      "actie": actie, "door": door})
    st["log"] = st["log"][-40:]


def _melding(state: dict, tekst: str) -> None:
    state["meldingen"].insert(0, {"tijd": time.strftime("%Y-%m-%d %H:%M"),
                                  "tekst": tekst})
    state["meldingen"] = state["meldingen"][:80]


def _artefact_toevoegen(st: dict, naam: str, soort: str, url: str) -> None:
    st["artefacten"] = [a for a in st["artefacten"] if a["url"] != url]
    st["artefacten"].append({
        "naam": naam, "soort": soort, "url": url,
        "tijd": time.strftime("%Y-%m-%d %H:%M")})


# ---------------------------------------------------------------------------
# Projectgegevens: namen voor de verificatietabel en het projectteam van de
# ontwikkelnota's, plus de mijlpalendata — in te vullen via het
# projectgegevens-scherm (procespagina) en gebruikt door nota.bouw_context.
# ---------------------------------------------------------------------------

TEAM_ROLLEN = ("Ontwerpleider", "Projectbeheerser", "Omgevingsmanager",
               "Projectleider", "Planner", "Technisch manager")
VERIFICATIE_VELDEN = ("opdrachtgever", "opgesteld_door", "verificatie",
                      "autorisatie", "vrijgave")
MIJLPAAL_VELDEN = ("vo_gereed", "do_gereed", "uo_gereed",
                   "contract_getekend", "start_uitvoering", "ibn_datum")
MIJLPAAL_LABELS = {
    "vo_gereed": "VO gereed", "do_gereed": "DO gereed",
    "uo_gereed": "UO gereed", "contract_getekend": "Contract getekend",
    "start_uitvoering": "Geplande start uitvoering (GSU)",
    "ibn_datum": "IBN-datum",
}


def _lege_gegevens() -> dict:
    return {"verificatie": {k: "" for k in VERIFICATIE_VELDEN},
            "team": [{"naam": "", "functie": rol, "organisatie": ""}
                     for rol in TEAM_ROLLEN],
            "mijlpalen": {k: "" for k in MIJLPAAL_VELDEN}}


def laad_gegevens(project: str) -> dict:
    basis = _lege_gegevens()
    g = laad_state(project).get("gegevens") or {}
    basis["verificatie"].update({k: str(v)[:120] for k, v in
                                 (g.get("verificatie") or {}).items()
                                 if k in VERIFICATIE_VELDEN})
    if isinstance(g.get("team"), list) and g["team"]:
        basis["team"] = [{"naam": str(r.get("naam", ""))[:120],
                          "functie": str(r.get("functie", ""))[:80],
                          "organisatie": str(r.get("organisatie", ""))[:80]}
                         for r in g["team"][:16] if isinstance(r, dict)]
    basis["mijlpalen"].update({k: str(v)[:40] for k, v in
                               (g.get("mijlpalen") or {}).items()
                               if k in MIJLPAAL_VELDEN})
    return basis


def bewaar_gegevens(project: str, gegevens: dict) -> dict:
    state = laad_state(project)
    state["gegevens"] = gegevens if isinstance(gegevens, dict) else {}
    bewaar_state(state)
    schoon = laad_gegevens(project)
    state["gegevens"] = schoon
    _melding(state, "Projectgegevens (verificatietabel, projectteam, "
                    "mijlpalen) bijgewerkt.")
    bewaar_state(state)
    return schoon


# ---------------------------------------------------------------------------
# Fase- en tollgate-logica
# ---------------------------------------------------------------------------

def _actieve_fasen(cfg: dict) -> list[dict]:
    return [f for f in FASEN if fase_actief(cfg, f["code"])]


def fase_status(cfg: dict, state: dict, code: str) -> str:
    """'actief' | 'wachtend' | 'afgerond' | 'uit' voor een fase."""
    if not fase_actief(cfg, code):
        return "uit"
    actief = _actieve_fasen(cfg)
    codes = [f["code"] for f in actief]
    if code not in codes:
        return "uit"
    for eerder in codes[:codes.index(code)]:
        tg = state["tollgates"].get(eerder, {})
        if tg.get("status") != "genomen":
            return "wachtend" if eerder != code else "actief"
    tg = state["tollgates"].get(code, {})
    return "afgerond" if tg.get("status") == "genomen" else "actief"


def _fase_stappen(cfg: dict, code: str) -> list[dict]:
    return [s for s in STAPPEN
            if s["fase"] == code and _stap_config(cfg, s)["actief"]]


def tollgate_gereed(cfg: dict, state: dict, code: str) -> bool:
    """Alle actieve review-stappen van de fase zijn gereed of n.v.t."""
    for s in _fase_stappen(cfg, code):
        if not s["review"]:
            continue
        st = state["stappen"].get(s["id"], {})
        if st.get("status") not in ("gereed", "nvt"):
            return False
    return True


def overzicht(project: str, result: dict | None) -> dict:
    cfg = laad_config()
    state = laad_state(project)
    fasen_uit = []
    taken = []
    for f in FASEN:
        code = f["code"]
        fstatus = fase_status(cfg, state, code)
        stappen = []
        gereed = 0
        for s in (x for x in STAPPEN if x["fase"] == code):
            sc = _stap_config(cfg, s)
            st = state["stappen"].get(s["id"], {})
            status = st.get("status", "te_doen")
            if status in ("gereed", "nvt"):
                gereed += 1
            stappen.append({**s, **sc,
                            "status": status,
                            "toelichting": st.get("toelichting", ""),
                            "verantwoordelijke": st.get("verantwoordelijke", ""),
                            "artefacten": st.get("artefacten", []),
                            "log": st.get("log", [])[-8:]})
            if sc["actief"] and fstatus == "actief":
                if status == "concept_gereed":
                    taken.append({"stap": s["id"], "fase": code,
                                  "naam": s["naam"],
                                  "actie": "AI-concept goedkeuren"})
                elif status in ("te_doen", "bezig", "afgekeurd") \
                        and sc["uitvoering"] == "mens":
                    taken.append({"stap": s["id"], "fase": code,
                                  "naam": s["naam"],
                                  "actie": "handmatig uitvoeren"})
        actieve = [x for x in stappen if x["actief"]]
        tg = state["tollgates"].get(code, {})
        tg_gereed = tollgate_gereed(cfg, state, code)
        if fstatus == "actief" and tg_gereed and tg.get("status") != "genomen":
            taken.append({"stap": None, "fase": code,
                          "naam": f["tollgate_naam"],
                          "actie": f"tollgate {f['tollgate']} reviewen"})
        fasen_uit.append({
            **f, "status": fstatus, "stappen": stappen,
            "voortgang": {"gereed": sum(1 for x in actieve
                                        if x["status"] in ("gereed", "nvt")),
                          "totaal": len(actieve)},
            "tollgate_status": tg.get("status", "open"),
            "tollgate_besluit": tg,
            "tollgate_gereed": tg_gereed,
        })
    return {"project": project, "fasen": fasen_uit, "taken": taken,
            "meldingen": state["meldingen"][:30],
            "risico": state.get("risico", []),
            "config": {"ai_automatisch": cfg.get("ai_automatisch", True)},
            "heeft_result": bool(result and result.get("varianten"))}


def stap_actie(project: str, stap_id: str, actie: str, *, toelichting: str = "",
               verantwoordelijke: str = "", door: str = "") -> dict:
    if stap_id not in STAP_INDEX:
        raise ProcesError(f"Onbekende stap '{stap_id}'.")
    state = laad_state(project)
    st = _stap_state(state, stap_id)
    naam = STAP_INDEX[stap_id]["naam"]
    overgangen = {
        "start": ("bezig", f"gestart: {naam}"),
        "gereed": ("gereed", f"gereed gemeld: {naam}"),
        "goedkeur": ("gereed", f"goedgekeurd: {naam}"),
        "afkeur": ("afgekeurd", f"afgekeurd: {naam}"),
        "nvt": ("nvt", f"niet van toepassing verklaard: {naam}"),
        "heropen": ("te_doen", f"heropend: {naam}"),
    }
    if actie not in overgangen:
        raise ProcesError(f"Onbekende actie '{actie}'.")
    if actie == "afkeur" and not toelichting.strip():
        raise ProcesError("Een afkeuring vereist een toelichting.")
    status, melding = overgangen[actie]
    st["status"] = status
    if toelichting:
        st["toelichting"] = toelichting
    if verantwoordelijke:
        st["verantwoordelijke"] = verantwoordelijke
    _log(st, melding, door)
    _melding(state, melding + (f" — {toelichting}" if toelichting else ""))
    bewaar_state(state)
    return {"ok": True, "status": status}


def tollgate_besluit(project: str, fase: str, besluit: str, *, door: str = "",
                     toelichting: str = "") -> dict:
    if fase not in FASE_CODES:
        raise ProcesError(f"Onbekende fase '{fase}'.")
    if besluit not in ("genomen", "afgekeurd"):
        raise ProcesError("Besluit moet 'genomen' of 'afgekeurd' zijn.")
    if not door.strip():
        raise ProcesError("Vul in wie het tollgate-besluit neemt.")
    cfg = laad_config()
    state = laad_state(project)
    if besluit == "genomen" and not tollgate_gereed(cfg, state, fase) \
            and not toelichting.strip():
        raise ProcesError(
            "Niet alle review-producten zijn gereed; een tollgate-besluit "
            "kan dan alleen met een toelichting (afwijkingsbesluit).")
    f = next(x for x in FASEN if x["code"] == fase)
    state["tollgates"][fase] = {
        "status": besluit, "door": door, "toelichting": toelichting,
        "tijd": time.strftime("%Y-%m-%d %H:%M")}
    _melding(state, f"{f['tollgate']} ({f['naam']}): {besluit} door {door}"
                    + (f" — {toelichting}" if toelichting else ""))
    bewaar_state(state)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Automatisering: data-capabilities uit het rekenresultaat
# ---------------------------------------------------------------------------

def _v(result: dict, variant: int) -> dict:
    try:
        return result["varianten"][variant]
    except Exception:
        raise ProcesError("Geen berekend tracé beschikbaar; reken eerst "
                          "een tracé door (kaartscherm).")


def _cap_trace(result, variant, project):
    v = _v(result, variant)
    n_wp = len(v.get("werkpakketten", []))
    return {"samenvatting": f"Tracé {v['naam']}: {v['lengte_m']:.0f} m, "
                            f"{len(v.get('kruisingen', []))} kruisingen, "
                            f"{len(v.get('boringen', []))} boringen, "
                            f"{n_wp} werkpakketten.",
            "artefacten": [
                ("Tracé (GeoJSON, RD)", "geojson",
                 f"/api/export/geojson?variant={variant}"),
                ("Tracé (DXF)", "dxf",
                 f"/api/export/dxf?variant={variant}&projectnaam={project}"),
            ]}


def _cap_wbs(result, variant, project):
    v = _v(result, variant)
    wps = v.get("werkpakketten", [])
    if not wps:
        raise ProcesError("Geen werkpakketten in het resultaat.")
    regels = ", ".join(f"{w['nr']} ({w['lengte_m']:.0f} m)" for w in wps)
    return {"samenvatting": f"{len(wps)} werkpakketten: {regels}. Alle "
                            "registerregels zijn aan werkpakketten toegekend.",
            "artefacten": [("Registers incl. werkpakketten (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_raming(result, variant, project):
    v = _v(result, variant)
    calc = v.get("calculatie") or {}
    som = calc.get("aannemingssom_excl_btw")
    if som is None:
        raise ProcesError("Geen RAW-calculatie in het resultaat.")
    return {"samenvatting": f"RAW-raming: aannemingssom € {som:,.0f} excl. "
                            f"btw ({calc.get('prijspeil', 'prijspeil onbekend')}), "
                            f"uitvoeringsduur ± {calc.get('uitvoeringsduur_wk', '?')} "
                            "weken. Volledige begroting in het Excel-werkblad "
                            "RAW-calculatie.".replace(",", "."),
            "artefacten": [("RAW-calculatie (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_planning(result, variant, project):
    v = _v(result, variant)
    pl = v.get("planning", [])
    if not pl:
        raise ProcesError("Geen planning in het resultaat.")
    eind = max((r.get("eind_wk", 0) for r in pl), default=0)
    return {"samenvatting": f"Indicatieve planning over {len(pl)} regels; "
                            f"laatste werkpakket gereed in week {eind}.",
            "artefacten": [("Planning per werkpakket (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_documenten(result, variant, project):
    state = laad_state(project)
    rijen = []
    for sid, st in state["stappen"].items():
        for a in st.get("artefacten", []):
            rijen.append(f"{sid}: {a['naam']} ({a['tijd']})")
    n = len(rijen)
    return {"samenvatting": f"Documentenregister: {n} producten/artefacten "
                            "geregistreerd in het procesdossier."
                            + (" Laatste: " + "; ".join(rijen[-3:]) if rijen else ""),
            "artefacten": []}


def _cap_klic(result, variant, project):
    import klic as klic_mod
    stat = klic_mod.status()
    if stat.get("features"):
        s = (f"KLIC-levering gekoppeld: {stat['features']} objecten uit "
             f"{len(stat.get('bestanden', []))} bestand(en); netdichtheid "
             "weegt mee en boringen melden bestaande netten.")
    else:
        s = ("Nog geen KLIC-levering gekoppeld (data/klic/). Oriëntatie-"
             "melding aanvragen bij het Kadaster en de GeoJSON-levering in "
             "data/klic/ plaatsen; daarna weegt netdichtheid automatisch mee.")
    return {"samenvatting": s, "artefacten": []}


def _cap_materiaal(result, variant, project):
    v = _v(result, variant)
    lengte = v.get("lengte_m", 0.0)
    boringen = v.get("boringen", [])
    moffen = v.get("moffen", [])
    mantel = sum(b.get("boorlengte_m", b.get("lengte_m", 0)) or 0
                 for b in boringen)
    regels = [
        f"MS-kabel (3 fasen, incl. 3% overlengte): {lengte * 1.03:.0f} m",
        f"mantelbuizen (boringen): {mantel:.0f} m",
        f"moffen: {len(moffen)} stuks",
        f"markeringslint/dekplaten: {lengte:.0f} m",
    ]
    return {"samenvatting": "Materiaallijst uit het ontwerp — " +
                            "; ".join(regels) + ". Details per post in de "
                            "RAW-calculatie (Excel).",
            "artefacten": [("Registers + RAW-calculatie (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_stakeholders(result, variant, project):
    v = _v(result, variant)
    partijen: dict[str, set] = {}
    for r in v.get("vergunningen", []):
        gezag = r.get("bevoegd_gezag") or r.get("gezag")
        if gezag:
            partijen.setdefault(gezag, set()).add(
                r.get("item", "vergunning"))
    eigenaren = len(v.get("zro", []))
    delen = [f"{g} ({', '.join(sorted(s)[:3])})" for g, s in
             sorted(partijen.items())]
    if eigenaren:
        delen.append(f"{eigenaren} perceeleigenaren (ZRO-register)")
    return {"samenvatting": "Stakeholderregister afgeleid uit vergunningen "
                            "en ZRO: " + ("; ".join(delen) if delen else
                            "nog geen stakeholders herleid."),
            "artefacten": [("Vergunningen + ZRO (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_vergunningen(result, variant, project):
    v = _v(result, variant)
    rows = v.get("vergunningen", [])
    if not rows:
        raise ProcesError("Geen vergunningenregister in het resultaat.")
    statussen: dict[str, int] = {}
    for r in rows:
        statussen[r.get("status", "onbekend")] = \
            statussen.get(r.get("status", "onbekend"), 0) + 1
    return {"samenvatting": f"Vergunningenregister: {len(rows)} vergunningen/"
                            "meldingen — " + ", ".join(
                                f"{n}× {s}" for s, n in statussen.items())
                            + ". Status bijhouden kan in de registerpagina.",
            "artefacten": [("Vergunningenregister (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_onderzoeken(result, variant, project):
    v = _v(result, variant)
    rows = v.get("onderzoeken", [])
    if not rows:
        raise ProcesError("Geen onderzoeksregister in het resultaat.")
    return {"samenvatting": f"Onderzoeksregister: {len(rows)} onderzoeken "
                            "(quickscans en veldonderzoeken). Per onderzoek "
                            "kan de AI een bureauonderzoek opstellen "
                            "(Export-paneel → Bureauonderzoek).",
            "artefacten": [("Onderzoeksregister (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_bomen(result, variant, project):
    v = _v(result, variant)
    rows = [o for o in v.get("onderzoeken", [])
            if "boom" in json.dumps(o, ensure_ascii=False).lower()
            or "bea" in json.dumps(o, ensure_ascii=False).lower()]
    zones = v.get("zones", {})
    wortel = zones.get("boom_wortelzone", 0)
    return {"samenvatting": f"Bomen langs het tracé: {wortel:.0f} m door "
                            "wortelzones; BEA/kapvergunning-signalen staan "
                            f"in het onderzoeksregister ({len(rows)} items).",
            "artefacten": [("Onderzoeksregister (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_zro(result, variant, project):
    import zro as zro_mod
    v = _v(result, variant)
    rows = v.get("zro", [])
    if not rows:
        return {"samenvatting": "Geen te kruisen private percelen — geen "
                                "ZRO-tekeningen nodig.", "artefacten": []}
    met_tek = 0
    for z in rows:
        d = zro_mod.laad_dossier(zro_mod.slug_van(z["perceel"]))
        if any("tekening" in (b.get("naam", "") or "").lower()
               for b in d.get("bijlagen", [])):
            met_tek += 1
    return {"samenvatting": f"ZRO-situatietekeningen: {met_tek} van "
                            f"{len(rows)} percelen heeft een tekening in het "
                            "dossier. Genereren kan per perceel in het "
                            "ZRO-dossier (knop ZRO-tekening).",
            "artefacten": [("ZRO-register (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_zro_status(result, variant, project):
    import zro as zro_mod
    v = _v(result, variant)
    rows = v.get("zro", [])
    tel: dict[str, int] = {}
    for z in rows:
        d = zro_mod.laad_dossier(zro_mod.slug_van(z["perceel"]))
        tel[d["status"]] = tel.get(d["status"], 0) + 1
    ok = tel.get("gevestigd", 0)
    return {"samenvatting": f"ZRO-stand: {ok} van {len(rows)} percelen "
                            "gevestigd — " + ", ".join(
                                f"{n}× {s}" for s, n in sorted(tel.items()))
                            + ". Dossiers bijwerken kan in het ZRO-register.",
            "artefacten": []}


def _cap_sonderingen(result, variant, project):
    v = _v(result, variant)
    rows = v.get("sonderingen", [])
    return {"samenvatting": f"Bestaande BRO-sonderingen langs het tracé: "
                            f"{len(rows)}. Per boring staan de gekoppelde "
                            "SON-nummers in het boorregister; waar geen "
                            "sondering nabij is: nieuwe sondering ramen.",
            "artefacten": [("Sonderingenregister (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


def _cap_kl_derden(result, variant, project):
    import klic as klic_mod
    v = _v(result, variant)
    kr = v.get("kruisingen", [])
    stat = klic_mod.status()
    klic_txt = (f"KLIC gekoppeld ({stat['features']} objecten)"
                if stat.get("features") else
                "KLIC niet gekoppeld — overzicht kabels/leidingen derden "
                "blijft onvolledig tot de levering is geïmporteerd")
    return {"samenvatting": f"Kruisingen met infrastructuur: {len(kr)} "
                            f"(watergangen, wegen, spoor). {klic_txt}.",
            "artefacten": [("Kruisingsregister (Excel)", "xlsx",
                            f"/api/export/xlsx?variant={variant}")]}


DATA_CAPS = {
    "trace": _cap_trace, "wbs": _cap_wbs, "raming": _cap_raming,
    "planning": _cap_planning, "documenten": _cap_documenten,
    "klic": _cap_klic, "materiaal": _cap_materiaal,
    "stakeholders": _cap_stakeholders, "vergunningen": _cap_vergunningen,
    "onderzoeken": _cap_onderzoeken, "bomen": _cap_bomen, "zro": _cap_zro,
    "zro_status": _cap_zro_status, "sonderingen": _cap_sonderingen,
    "kl_derden": _cap_kl_derden,
}


def voer_data_cap_uit(project: str, stap_id: str, result: dict | None,
                      variant: int = 0, *, door: str = "AI") -> dict:
    """Voert een data-capability uit en werkt de stapstatus bij."""
    stap = STAP_INDEX.get(stap_id)
    if not stap or not (stap.get("cap") or "").startswith("data:"):
        raise ProcesError(f"Stap '{stap_id}' heeft geen data-automatisering.")
    naam = stap["cap"].split(":", 1)[1]
    if result is None:
        raise ProcesError("Geen berekend tracé beschikbaar; reken eerst een "
                          "tracé door.")
    uitkomst = DATA_CAPS[naam](result, variant, project)
    cfg = laad_config()
    modus = _stap_config(cfg, stap)["uitvoering"]
    state = laad_state(project)
    st = _stap_state(state, stap_id)
    st["toelichting"] = uitkomst["samenvatting"]
    for a_naam, soort, url in uitkomst["artefacten"]:
        _artefact_toevoegen(st, a_naam, soort, url)
    st["status"] = "gereed" if modus == "ai" else "concept_gereed"
    _log(st, f"automatisch bijgewerkt ({stap['cap']})", door)
    _melding(state, f"{stap['naam']}: {'gereed' if modus == 'ai' else 'concept gereed — goedkeuring gevraagd'} (AI)")
    bewaar_state(state)
    return {"ok": True, "status": st["status"],
            "samenvatting": uitkomst["samenvatting"],
            "artefacten": st["artefacten"]}


def event_trace_berekend(project: str, result: dict) -> list[str]:
    """Trigger na elke berekening: data-stappen van actieve fasen bijwerken."""
    if not project:
        return []
    cfg = laad_config()
    if not cfg.get("ai_automatisch", True):
        return []
    state = laad_state(project)
    bijgewerkt = []
    for f in FASEN:
        if fase_status(cfg, state, f["code"]) != "actief":
            continue
        for s in _fase_stappen(cfg, f["code"]):
            cap = s.get("cap") or ""
            if not cap.startswith("data:"):
                continue
            st = state["stappen"].get(s["id"], {})
            if st.get("status") in ("gereed", "nvt"):
                continue  # menselijk afgeronde stappen niet overschrijven
            try:
                voer_data_cap_uit(project, s["id"], result, 0)
                bijgewerkt.append(s["id"])
            except ProcesError:
                pass
    return bijgewerkt


# ---------------------------------------------------------------------------
# AI-conceptdocumenten (doc:*) en ontwerpnota's (nota:*)
# ---------------------------------------------------------------------------

def _anthropic():
    try:
        import anthropic
    except ImportError:
        raise ProcesError("Python-pakket 'anthropic' ontbreekt.")
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise ProcesError("Geen Anthropic API-key (ANTHROPIC_API_KEY in "
                          "omgeving of infraengine/.env).")
    return anthropic


def stream_doc(project: str, stap_id: str, result: dict | None,
               variant: int = 0):
    """Generator van Markdown-delen voor een doc:-capability (SSE)."""
    import nota as nota_mod
    stap = STAP_INDEX.get(stap_id)
    cap = (stap or {}).get("cap") or ""
    if cap.startswith("nota:"):
        if result is None:
            raise ProcesError("Geen berekend tracé; reken eerst door.")
        return nota_mod.stream_nota(result, cap.split(":", 1)[1], variant,
                                    project)
    if not cap.startswith("doc:") or cap.split(":", 1)[1] not in AI_DOC:
        raise ProcesError(f"Stap '{stap_id}' heeft geen AI-documentgenerator.")
    if result is None:
        raise ProcesError("Geen berekend tracé; reken eerst door zodat de "
                          "AI projectdata heeft.")
    spec = AI_DOC[cap.split(":", 1)[1]]
    anthropic = _anthropic()
    context = nota_mod.bouw_context(result, variant, project)
    context["risicoregister"] = laad_state(project).get("risico", [])[:60]
    prompt = (f"Stel het volgende concept-beheersdocument op: "
              f"**{spec['titel']}** voor stap {stap_id} "
              f"({stap['naam']}, fase {stap['fase']}).\n\n"
              f"Opdracht: {spec['doel']}\n\n"
              "Projectdata (JSON, automatisch gegenereerd door het "
              "InfraEngine-ontwerpplatform):\n"
              + json.dumps(context, ensure_ascii=False))

    def _stream():
        client = anthropic.Anthropic()
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS_DOC, system=DOC_SYSTEM,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for tekst in stream.text_stream:
                    yield tekst
                slot = stream.get_final_message()
            if slot.stop_reason == "max_tokens":
                yield ("\n\n[NOTA-FOUT] Document is afgekapt (max_tokens); "
                       "probeer opnieuw.")
        except anthropic.AuthenticationError:
            yield "\n\n[NOTA-FOUT] Anthropic API-key is ongeldig."
        except anthropic.APIConnectionError:
            yield "\n\n[NOTA-FOUT] Verbinding met de Anthropic API verbroken."
        except anthropic.APIStatusError as e:
            yield f"\n\n[NOTA-FOUT] Anthropic API-fout ({e.status_code})."

    return _stream()


def bewaar_doc(project: str, stap_id: str, markdown: str, result: dict | None,
               variant: int = 0, *, door: str = "AI") -> dict:
    """Markdown-concept opslaan als Word-artefact en de stap bijwerken."""
    import nota as nota_mod
    stap = STAP_INDEX.get(stap_id)
    if not stap:
        raise ProcesError(f"Onbekende stap '{stap_id}'.")
    cap = stap.get("cap") or ""
    markdown = (markdown or "").split("[NOTA-FOUT]")[0].strip()
    if len(markdown) < 40:
        raise ProcesError("Geen bruikbare documentinhoud ontvangen.")
    if len(markdown) > 2_000_000:
        raise ProcesError("Documentinhoud onwaarschijnlijk groot; geweigerd.")

    if cap.startswith("nota:"):
        naam, docx = nota_mod.markdown_naar_docx(
            result, cap.split(":", 1)[1], variant, project, markdown)
        titel = nota_mod.NOTA_FASEN[cap.split(":", 1)[1]]["titel"]
    else:
        spec = AI_DOC.get(cap.split(":", 1)[1] if ":" in cap else "", None)
        titel = spec["titel"] if spec else stap["product"]
        naam, docx = _generiek_docx(project, result, variant, titel, markdown)

    map_ = artefact_dir(project)
    (map_ / naam).write_bytes(docx)
    (map_ / (Path(naam).stem + ".md")).write_text(markdown)

    cfg = laad_config()
    modus = _stap_config(cfg, stap)["uitvoering"]
    state = laad_state(project)
    st = _stap_state(state, stap_id)
    _artefact_toevoegen(st, titel + " (concept, Word)", "docx",
                        f"/api/proces/artefact?project={_slug(project)}"
                        f"&bestand={naam}")
    st["status"] = "gereed" if modus == "ai" else "concept_gereed"
    _log(st, f"AI-concept opgesteld: {titel}", door)
    _melding(state, f"{stap['naam']}: AI-concept '{titel}' staat klaar"
                    + ("" if modus == "ai" else " — goedkeuring gevraagd"))
    bewaar_state(state)
    return {"ok": True, "bestand": naam, "status": st["status"],
            "artefacten": st["artefacten"]}


def _generiek_docx(project: str, result: dict | None, variant: int,
                   titel: str, markdown: str) -> tuple[str, bytes]:
    """Generiek beheersdocument → Word, in de stijl van de ontwerpnota's."""
    import nota as nota_mod
    gemeente = (result or {}).get("gemeente") or "onbekend"
    blocks = nota_mod._md_blocks(markdown)
    doc_titel = titel
    inhoud = []
    for soort, data in blocks:
        if soort == "h1" and doc_titel == titel:
            doc_titel = data
        else:
            inhoud.append((soort, data))
    P = [nota_mod._para("InfraEngine · Concept-beheersdocument",
                        style="NotaKicker"),
         nota_mod._para(titel, style="NotaTitel"),
         nota_mod._para(doc_titel if doc_titel != titel else "",
                        style="NotaSubtitel"),
         nota_mod._tabel([
             ["Project", project or "zonder naam"],
             ["Gemeente", gemeente],
             ["Datum", date.today().strftime("%d-%m-%Y")],
             ["Status", "Concept — AI-gegenereerd, toetsing vereist"],
         ], kopregel=False),
         nota_mod._para(
             "Automatisch opgesteld met AI op basis van het berekende tracé "
             f"en de registers van InfraEngine (model {MODEL}). Inhoudelijke "
             "toetsing door de verantwoordelijke discipline is vereist.",
             size=18, color=nota_mod._INK3,
             ppr_extra='<w:spacing w:before="4400"/>'),
         '<w:p><w:r><w:br w:type="page"/></w:r></w:p>']
    for soort, data in inhoud:
        if soort in ("h1", "h2"):
            P.append(nota_mod._para(data, style="Kop1"))
        elif soort == "h3":
            P.append(nota_mod._para(data, style="Kop2"))
        elif soort == "ul":
            for item in data:
                P.append(nota_mod._para("–  " + item, style="Opsomming"))
        elif soort == "table":
            P.append(nota_mod._tabel(data))
        else:
            P.append(nota_mod._para(data))
    footer = (f"{project or 'infraengine'} · {titel} · concept "
              f"{date.today().strftime('%d-%m-%Y')}")
    docx = nota_mod._docx_pakket("".join(P), footer)
    slug = re.sub(r"[^\w\-]", "_", titel)[:50]
    naam = f"{slug}_{date.today().strftime('%Y%m%d')}.docx"
    return naam, docx


# ---------------------------------------------------------------------------
# Kans- en risicoregister (structuur naar de LEM-overzichtstabel):
# score = kans × som(gevolgscores geld/tijd/kwaliteit/veiligheid/omgeving)
# ---------------------------------------------------------------------------

RISICO_SYSTEM = (
    "Je bent risicomanager bij een Nederlandse netbeheerder en stelt een "
    "kans- en risicoregister op voor een middenspanningstracéproject in de "
    "bouwteamfasering VO/DO/UO. Je baseert je uitsluitend op de aangeleverde "
    "projectdata (tracé, kruisingen, boringen, zones, vergunningen, ZRO, "
    "toetsing, planning, kosten) en op generieke ervaringsrisico's van "
    "vergelijkbare kabelprojecten (ZRO's niet tijdig, ontwerpwijziging na "
    "DO, onvoldoende werkstrook, hoge grondwaterstand, archeologische "
    "toevalsvondsten, vervuilde grond, flora & fauna, tegenwerking bevoegd "
    "gezag, wegafsluitingen, capaciteit). Antwoord UITSLUITEND met geldige "
    "JSON (geen Markdown): een array van risico-objecten met de velden "
    "omschrijving (string), oorzaak (string), gevolg (string), "
    "intern_extern ('Intern'|'Extern'), aspect ('Technisch'|'Omgeving'|"
    "'Procesbeheersing'|'Financieel'|'Veiligheid'), stadium ('VO'|'DO'|'UO'|"
    "'Realisatie'), allocatie ('OG'|'ON'|'OG/ON'), kans (1-5), geld (0-5), "
    "tijd (0-5), kwaliteit (0-5), veiligheid (0-5), omgeving (0-5), "
    "werkpakket (string, 'tracébreed' als niet lokaal), maatregelen (array "
    "van {maatregel, soort: 'Preventief'|'Correctief', actiehouder}). "
    "Wees concreet en projectspecifiek: verwijs naar echte kruisingen, "
    "boringen, zones en percelen uit de data."
)


def risico_score(r: dict) -> int:
    gevolgen = sum(int(r.get(k, 0) or 0) for k in
                   ("geld", "tijd", "kwaliteit", "veiligheid", "omgeving"))
    return int(r.get("kans", 0) or 0) * gevolgen


def genereer_risico(project: str, result: dict | None, variant: int = 0,
                    fase: str = "", *, door: str = "AI") -> dict:
    import nota as nota_mod
    if result is None:
        raise ProcesError("Geen berekend tracé; reken eerst door.")
    anthropic = _anthropic()
    state = laad_state(project)
    context = nota_mod.bouw_context(result, variant, project)
    bestaand = state.get("risico", [])
    prompt = (
        f"Projectfase: {fase or 'VO'}. "
        + (f"Er zijn al {len(bestaand)} risico's; actualiseer en vul aan "
           f"(bestaande omschrijvingen: "
           f"{json.dumps([r['omschrijving'] for r in bestaand[:40]], ensure_ascii=False)}). "
           "Lever ALLEEN nieuwe of gewijzigde risico's."
           if bestaand else
           "Stel het initiële register op met de 12-20 belangrijkste "
           "projectspecifieke risico's.")
        + "\n\nProjectdata (JSON):\n" + json.dumps(context, ensure_ascii=False))
    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS_RISICO, system=RISICO_SYSTEM,
            messages=[{"role": "user", "content": prompt}])
    except anthropic.APIError as e:
        raise ProcesError(f"Anthropic API-fout: {e}")
    tekst = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\[.*\]", tekst, re.S)
    if not m:
        raise ProcesError("AI gaf geen bruikbare JSON terug; probeer opnieuw.")
    try:
        nieuw = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise ProcesError("AI-JSON kon niet worden gelezen; probeer opnieuw.")

    bestaande_oms = {r["omschrijving"].strip().lower() for r in bestaand}
    hoogste = max((int(r["nr"].split("-")[1]) for r in bestaand
                   if re.match(r"RIS-\d+$", r.get("nr", ""))), default=0)
    toegevoegd = 0
    for r in nieuw:
        if not isinstance(r, dict) or not r.get("omschrijving"):
            continue
        if r["omschrijving"].strip().lower() in bestaande_oms:
            continue
        hoogste += 1
        rij = {"nr": f"RIS-{hoogste:03d}",
               "omschrijving": str(r.get("omschrijving", ""))[:300],
               "oorzaak": str(r.get("oorzaak", ""))[:400],
               "gevolg": str(r.get("gevolg", ""))[:400],
               "intern_extern": r.get("intern_extern", "Intern"),
               "aspect": r.get("aspect", "Technisch"),
               "stadium": r.get("stadium", fase or "VO"),
               "allocatie": r.get("allocatie", "OG/ON"),
               "eigenaar": r.get("eigenaar", ""),
               "status": "Concept",
               "werkpakket": r.get("werkpakket", "tracébreed"),
               "kans": min(5, max(1, int(r.get("kans", 3) or 3))),
               "maatregelen": [
                   {"maatregel": str(m2.get("maatregel", ""))[:300],
                    "soort": m2.get("soort", "Preventief"),
                    "status": "In overweging",
                    "actiehouder": str(m2.get("actiehouder", ""))[:80]}
                   for m2 in (r.get("maatregelen") or [])[:6]
                   if isinstance(m2, dict)],
               "bron": "AI"}
        for k in ("geld", "tijd", "kwaliteit", "veiligheid", "omgeving"):
            rij[k] = min(5, max(0, int(r.get(k, 0) or 0)))
        rij["score"] = risico_score(rij)
        bestaand.append(rij)
        toegevoegd += 1
    bestaand.sort(key=lambda r: -r.get("score", 0))
    state["risico"] = bestaand
    _melding(state, f"Kans- en risicoregister: {toegevoegd} risico's "
                    f"toegevoegd door AI (totaal {len(bestaand)}).")
    bewaar_state(state)
    return {"ok": True, "toegevoegd": toegevoegd, "risico": bestaand}


def risico_update(project: str, nr: str, veld: str, waarde) -> dict:
    toegestaan = {"omschrijving", "oorzaak", "gevolg", "intern_extern",
                  "aspect", "stadium", "allocatie", "eigenaar", "status",
                  "werkpakket", "kans", "geld", "tijd", "kwaliteit",
                  "veiligheid", "omgeving"}
    if veld not in toegestaan:
        raise ProcesError(f"Veld '{veld}' is niet bewerkbaar.")
    state = laad_state(project)
    for r in state.get("risico", []):
        if r.get("nr") == nr:
            if veld in ("kans", "geld", "tijd", "kwaliteit", "veiligheid",
                        "omgeving"):
                r[veld] = min(5, max(0, int(waarde or 0)))
                r["score"] = risico_score(r)
            else:
                r[veld] = str(waarde)[:400]
            bewaar_state(state)
            return {"ok": True, "risico": r}
    raise ProcesError(f"Risico '{nr}' niet gevonden.")


def risico_verwijder(project: str, nr: str) -> dict:
    state = laad_state(project)
    voor = len(state.get("risico", []))
    state["risico"] = [r for r in state.get("risico", []) if r.get("nr") != nr]
    if len(state["risico"]) == voor:
        raise ProcesError(f"Risico '{nr}' niet gevonden.")
    bewaar_state(state)
    return {"ok": True}


def risico_xlsx(project: str) -> bytes:
    """Kans- en risicoregister als Excel (kolomindeling naar de
    overzichtstabel uit de procesdocumenten)."""
    import io
    from openpyxl import Workbook
    state = laad_state(project)
    wb = Workbook()
    ws = wb.active
    ws.title = "Risicoregister"
    kop = ["ID", "Omschrijving", "Oorzaak", "Gevolg", "Intern/Extern",
           "Risicoaspect", "Projectstadium", "Allocatie", "Eigenaar",
           "Status", "Werkpakket", "Kans", "Geld", "Tijd", "Kwaliteit",
           "Veiligheid", "Omgeving", "Score",
           "Beheersmaatregelen (soort — maatregel, actiehouder)", "Bron"]
    ws.append(kop)
    for r in state.get("risico", []):
        maatregelen = " | ".join(
            f"{m.get('soort', '')} — {m.get('maatregel', '')}"
            + (f" ({m['actiehouder']})" if m.get("actiehouder") else "")
            for m in r.get("maatregelen", []))
        ws.append([r.get("nr"), r.get("omschrijving"), r.get("oorzaak"),
                   r.get("gevolg"), r.get("intern_extern"), r.get("aspect"),
                   r.get("stadium"), r.get("allocatie"), r.get("eigenaar"),
                   r.get("status"), r.get("werkpakket"), r.get("kans"),
                   r.get("geld"), r.get("tijd"), r.get("kwaliteit"),
                   r.get("veiligheid"), r.get("omgeving"), r.get("score"),
                   maatregelen, r.get("bron", "")])
    ws2 = wb.create_sheet("Procesoverzicht")
    ws2.append(["Fase", "Stap", "Nr", "Activiteit", "Product", "Review",
                "Status", "Toelichting", "Verantwoordelijke", "Artefacten"])
    cfg = laad_config()
    for s in STAPPEN:
        sc = _stap_config(cfg, s)
        if not sc["actief"]:
            continue
        st = state["stappen"].get(s["id"], {})
        ws2.append([s["fase"], s["id"], s["nr"], s["naam"], s["product"],
                    "ja" if s["review"] else "",
                    st.get("status", "te_doen"), st.get("toelichting", ""),
                    st.get("verantwoordelijke", ""),
                    "; ".join(a["naam"] for a in st.get("artefacten", []))])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Intake: AI-analyse van het geüploade investeringsvoorstel (IV)
# ---------------------------------------------------------------------------

IV_EXTENSIES = {".pdf", ".docx", ".txt", ".md"}
IV_MAX_MB = 25

INTAKE_SYSTEM = (
    "Je bent intakecoördinator bij een Nederlandse netbeheerder. Je "
    "analyseert een investeringsvoorstel (IV) voor een nieuw "
    "middenspanningstracé en stelt het intakeverslag op waarmee het "
    "bouwteam start. Schrijf in het Nederlands, zakelijk, in begrensde "
    "Markdown (zoals: `# titel`, `## kop`, lijsten met `- `, tabellen met "
    "`|---|`). Behandel: (1) samenvatting van de opdracht, (2) scope en "
    "beoogde verbinding(en) — stations/locaties, lengtes, aantallen "
    "circuits voor zover genoemd, (3) budget en planning uit het IV, "
    "(4) randvoorwaarden en uitgangspunten, (5) ontbrekende informatie en "
    "vragen aan de opdrachtgever, (6) advies voor de eerste "
    "tracéverkenning in InfraEngine (welk gebied intekenen, welke "
    "stations plaatsen, aandachtspunten). Verzin niets dat niet in het "
    "document staat; markeer aannames expliciet."
)


def intake_upload(project: str, bestandsnaam: str, data: bytes) -> dict:
    ext = Path(bestandsnaam).suffix.lower()
    if ext not in IV_EXTENSIES:
        raise ProcesError("Alleen .pdf, .docx, .txt of .md wordt ondersteund.")
    if len(data) > IV_MAX_MB * 1024 * 1024:
        raise ProcesError(f"Bestand groter dan {IV_MAX_MB} MB.")
    map_ = artefact_dir(project)
    pad = map_ / f"IV-document{ext}"
    for oud in map_.glob("IV-document.*"):
        oud.unlink()
    pad.write_bytes(data)
    state = laad_state(project)
    st = _stap_state(state, "IV-01")
    _artefact_toevoegen(st, f"IV-document ({bestandsnaam})", ext.lstrip("."),
                        f"/api/proces/artefact?project={_slug(project)}"
                        f"&bestand={pad.name}")
    st["status"] = "gereed"
    _log(st, f"IV-document geüpload: {bestandsnaam}")
    _melding(state, f"IV-document geregistreerd: {bestandsnaam}. "
                    "De AI-intakeanalyse (IV-02) kan nu draaien.")
    bewaar_state(state)
    return {"ok": True, "bestand": pad.name}


def _iv_blokken(project: str) -> list[dict]:
    import base64
    import zipfile as zf
    map_ = artefact_dir(project)
    bestand = next(iter(map_.glob("IV-document.*")), None)
    if bestand is None:
        raise ProcesError("Geen IV-document geüpload (stap IV-01).")
    if bestand.suffix == ".pdf":
        return [{"type": "document",
                 "source": {"type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(
                                bestand.read_bytes()).decode()}}]
    if bestand.suffix == ".docx":
        with zf.ZipFile(bestand) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        tekst = re.sub(r"<[^>]+>", " ", xml)
        tekst = re.sub(r"\s+", " ", tekst)[:150_000]
    else:
        tekst = bestand.read_text(errors="ignore")[:150_000]
    if not tekst.strip():
        raise ProcesError("Geen leesbare tekst in het IV-document.")
    return [{"type": "text", "text": "IV-document (tekst):\n\n" + tekst}]


def stream_intake(project: str):
    anthropic = _anthropic()
    blokken = _iv_blokken(project)

    def _stream():
        client = anthropic.Anthropic()
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS_DOC, system=INTAKE_SYSTEM,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": blokken + [
                    {"type": "text",
                     "text": f"Projectnaam: {project}. Stel het "
                             "intakeverslag op."}]}],
            ) as stream:
                for tekst in stream.text_stream:
                    yield tekst
        except anthropic.AuthenticationError:
            yield "\n\n[NOTA-FOUT] Anthropic API-key is ongeldig."
        except anthropic.APIConnectionError:
            yield "\n\n[NOTA-FOUT] Verbinding met de Anthropic API verbroken."
        except anthropic.APIStatusError as e:
            yield f"\n\n[NOTA-FOUT] Anthropic API-fout ({e.status_code})."

    return _stream()


def bewaar_intake(project: str, markdown: str, *, door: str = "AI") -> dict:
    markdown = (markdown or "").split("[NOTA-FOUT]")[0].strip()
    if len(markdown) < 40:
        raise ProcesError("Geen bruikbare verslaginhoud ontvangen.")
    naam, docx = _generiek_docx(project, None, 0, "Intakeverslag", markdown)
    map_ = artefact_dir(project)
    (map_ / naam).write_bytes(docx)
    (map_ / (Path(naam).stem + ".md")).write_text(markdown)
    cfg = laad_config()
    stap = STAP_INDEX["IV-02"]
    modus = _stap_config(cfg, stap)["uitvoering"]
    state = laad_state(project)
    st = _stap_state(state, "IV-02")
    _artefact_toevoegen(st, "Intakeverslag (concept, Word)", "docx",
                        f"/api/proces/artefact?project={_slug(project)}"
                        f"&bestand={naam}")
    st["status"] = "gereed" if modus == "ai" else "concept_gereed"
    _log(st, "AI-intakeverslag opgesteld", door)
    _melding(state, "Intakeverslag staat klaar"
                    + ("" if modus == "ai" else " — goedkeuring gevraagd"))
    bewaar_state(state)
    return {"ok": True, "bestand": naam, "status": st["status"]}
