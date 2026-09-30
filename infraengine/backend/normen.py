"""Normenkader nauwkeurigheid en maatvoering — één configureerbaar regelbestand.

Elke regel heeft een waarde, eenheid, bronvermelding (norm of richtlijn),
omschrijving en ernst-niveau. De grenswaarden zijn zonder codewijziging aan
te passen via ``data/normen.json``::

    { "afstand_kabels_m": 0.30, "kabel_diameter_m": 0.055 }

Alle toetslogica (``maatvoering.py``), de routegeneratie-nabewerking en de
calculatie lezen hun grenswaarden uitsluitend hier; er staan geen
grenswaarden in UI-componenten of elders in de code.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERRIDE_PAD = ROOT / "data" / "normen.json"

# ---------------------------------------------------------------------------
# Regelcatalogus. ernst: "kritiek" (blokkerend), "waarschuwing"
# (niet-blokkerende maatvoeringsafwijking) of "info".
# ---------------------------------------------------------------------------

REGELS: dict[str, dict] = {
    # --- 1. nauwkeurigheid geometrie ---------------------------------------
    "crs": dict(
        waarde="EPSG:28992", eenheid="-", ernst="info",
        bron="RD New; Nederlandse geo-standaard (NEN 3610)",
        omschrijving="Coördinatenstelsel van alle tracépunten."),
    "afronding_m": dict(
        waarde=0.01, eenheid="m", ernst="info",
        bron="Opdracht maatvoering; Liander-praktijk",
        omschrijving="Vaste afronding van tracécoördinaten (RD)."),
    "min_segment_m": dict(
        waarde=0.50, eenheid="m", ernst="waarschuwing",
        bron="Opdracht maatvoering; tekenrichtlijn netbeheerder",
        omschrijving="Minimale segmentlengte bij een richtingsverandering; "
                     "kortere knik-segmenten worden verwijderd (generatie) "
                     "of geflagd (handmatig)."),
    "knik_hoek_gr": dict(
        waarde=15.0, eenheid="°", ernst="info",
        bron="Afgeleide parameter bij min_segment_m",
        omschrijving="Richtingsverandering vanaf waar een kort segment als "
                     "knik telt."),
    "snap_tolerantie_m": dict(
        waarde=0.10, eenheid="m", ernst="waarschuwing",
        bron="Opdracht maatvoering; BGT-inwinnauwkeurigheid",
        omschrijving="Punt binnen deze afstand van een referentierand "
                     "(BGT-wegkant, verhardingsrand, erfgrens) wordt exact "
                     "op de rand gesnapt."),
    "snap_zoekafstand_m": dict(
        waarde=0.50, eenheid="m", ernst="waarschuwing",
        bron="Aanname (praktische zoekzone rond referentieranden)",
        omschrijving="Punt dat binnen deze afstand van een referentierand "
                     "ligt maar buiten de snaptolerantie, wordt geflagd als "
                     "niet-gesnapt."),
    "overlengte_pct": dict(
        waarde=2.0, eenheid="%", ernst="info",
        bron="Opdracht maatvoering; snij-/legverlies materiaalstaat",
        omschrijving="Overlengte-toeslag op de werkelijke polylinelengte "
                     "voor de materiaalstaat/calculatie."),
    # --- 2. maatvoering: dekking (geen z-waarden in het datamodel — eis
    #        wordt per liggingstype gerapporteerd, zie maatvoering.py) ------
    "dekking_berm_m": dict(
        waarde=0.70, eenheid="m", ernst="info",
        bron="NEN 7171-1; Liander-praktijk",
        omschrijving="Dekking MS-kabel onder maaiveld in berm/groenstrook."),
    "dekking_trottoir_m": dict(
        waarde=0.70, eenheid="m", ernst="info",
        bron="NEN 7171-1; Liander-praktijk",
        omschrijving="Dekking MS-kabel onder trottoir/voetpad/fietspad/"
                     "parkeervlak."),
    "dekking_rijbaan_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="NEN 7171-1; AVOI wegbeheerder",
        omschrijving="Dekking MS-kabel onder de rijbaan."),
    "dekking_water_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="NEN 7171-1; keur waterschap (bodembreedte watergang)",
        omschrijving="Dekking MS-kabel bij kruising van watergangen."),
    # --- 2. maatvoering: horizontale afstanden -----------------------------
    "afstand_kabels_m": dict(
        waarde=0.25, eenheid="m", ernst="waarschuwing",
        bron="NEN 7171-1 (ligprofiel kabels en leidingen)",
        omschrijving="Minimale horizontale afstand tot parallelle LS/MS-"
                     "kabels en datakabels."),
    "afstand_leidingen_m": dict(
        waarde=0.50, eenheid="m", ernst="waarschuwing",
        bron="NEN 7171-1; VELIN-richtlijn",
        omschrijving="Minimale horizontale afstand tot gas- en "
                     "waterleidingen (lage druk)."),
    "afstand_hd_gas_m": dict(
        waarde=2.00, eenheid="m", ernst="kritiek",
        bron="NEN 3650-serie; Bevb (externe veiligheid buisleidingen)",
        omschrijving="Minimale horizontale afstand tot HD-gasleidingen."),
    "afstand_boom_stam_m": dict(
        waarde=0.50, eenheid="m", ernst="waarschuwing",
        bron="Norminstituut Bomen (Handboek Bomen); CROW",
        omschrijving="Minimale afstand hart tracé tot boomstam."),
    "kroonprojectie_ernst": dict(
        waarde="waarschuwing", eenheid="-", ernst="waarschuwing",
        bron="Norminstituut Bomen; BEA-praktijk",
        omschrijving="Melding wanneer het tracé binnen de kroonprojectie "
                     "van een boom ligt."),
    "gevel_erf_afstand_m": dict(
        waarde=0.50, eenheid="m", ernst="waarschuwing",
        bron="NEN 7171-1 (ligprofiel: strook tegen de gevel/erfgrens "
             "vrijhouden)",
        omschrijving="Minimale afstand van het tracé tot gevel/erfgrens "
                     "(getoetst op DKK-erfgrenzen waar beschikbaar)."),
    # --- 2. maatvoering: buigradius en kruisingshoek -----------------------
    "buigradius_factor": dict(
        waarde=15.0, eenheid="× D", ernst="waarschuwing",
        bron="IEC 60502-2; kabelspecificatie fabrikant",
        omschrijving="Minimale buigradius als veelvoud van de "
                     "kabeldiameter."),
    "kabel_diameter_m": dict(
        waarde=0.05, eenheid="m", ernst="info",
        bron="Liander-praktijk (20kV 3×1×630 Al ≈ Ø 50 mm per fase)",
        omschrijving="Default kabeldiameter voor de buigradiustoets."),
    "min_kruisingshoek_gr": dict(
        waarde=45.0, eenheid="°", ernst="waarschuwing",
        bron="NEN 7171-1 (kruisingen zo haaks mogelijk)",
        omschrijving="Minimale kruisingshoek met andere kabels en "
                     "leidingen; kleinere hoeken worden geflagd."),
    # --- 3. mantelbuizen (sleufloze kruisingen) ----------------------------
    "mantelbuis_circuits_per_buis": dict(
        waarde=1, eenheid="circuits", ernst="waarschuwing",
        bron="IEC 60287 / NEN-HD 60364-5-52 (thermische belasting gebundelde "
             "circuits); Liander-praktijk: één MS-circuit per mantelbuis",
        omschrijving="Maximaal aantal MS-circuits (3 fasen in trefoil) per "
                     "mantelbuis. De drie fasen van één circuit gaan altijd "
                     "samen in één buis; per circuit hoogstens één buis."),
    "mantelbuis_speling_factor": dict(
        waarde=0.85, eenheid="-", ernst="waarschuwing",
        bron="NPR 7171-2 (intrekken kabels in buizen); kabelfabrikant",
        omschrijving="Buitenmaat van de kabelbundel (of van het pakket "
                     "binnenbuizen) mag ten hoogste deze fractie van de "
                     "binnendiameter van de mantelbuis zijn."),
    "mantelbuis_vulgraad_max": dict(
        waarde=0.45, eenheid="-", ernst="waarschuwing",
        bron="NPR 7171-2; praktijkregel intrekken (≤ 40–45 % doorsnede)",
        omschrijving="Maximale vulgraad (kabeldoorsnede / vrije "
                     "buisdoorsnede) van een mantelbuis."),
    "mantelbuis_hdpe_sdr": dict(
        waarde=11, eenheid="-", ernst="info",
        bron="NEN-EN 12201 (PE-buizen); Liander-standaard HDPE SDR11",
        omschrijving="Wanddikteklasse (SDR) van de HDPE-mantelbuis; bepaalt "
                     "de binnendiameter per buitenmaat."),
    "boring_samenvoegafstand_m": dict(
        waarde=0.0, eenheid="m", ernst="info",
        bron="Ontwerpuitgangspunt: zo min mogelijk boringen en mantelbuizen",
        omschrijving="Extra afstand (boven de som van de in-/uitloop van "
                     "beide boringen) waarbinnen twee opeenvolgende "
                     "sleufloze kruisingen in één boring worden gepasseerd."),
    "boring_dubbele_passage_m": dict(
        waarde=5.0, eenheid="m", ernst="info",
        bron="Ontwerpuitgangspunt: zo min mogelijk boringen en mantelbuizen",
        omschrijving="Twee sleufloze kruisingen op verschillende chainage "
                     "maar binnen deze afstand van elkaar (ringtracé dat "
                     "dezelfde weg twee keer passeert) delen één boring met "
                     "één buis per circuit."),
    # --- 4. boringen en persingen: kruisingshoek ---------------------------
    "boring_kruisingshoek_tolerantie_gr": dict(
        waarde=10.0, eenheid="°", ernst="waarschuwing",
        bron="RWS Richtlijn Boortechnieken 2019 §2.3.4 (\"in principe "
             "loodrecht\"); ProRail RLN00427-2 Eis-3.2 (haaks op de "
             "spoorbaan); waterschapsbeleidsregels (loodrecht op de "
             "watergang). Tolerantie in graden: aanname, geen bron",
        omschrijving="Toegestane afwijking van haaks (90°) voor een boring of "
                     "persing. Schuiner wordt de boorlijn haaks op het "
                     "obstakel gelegd; lukt dat niet, dan is het een "
                     "afwijking waarvoor de beheerder toestemming moet geven."),
    # --- 4. boringen en persingen: diepte bij de obstakelrand --------------
    "boring_dekking_rijbaan_m": dict(
        waarde=1.50, eenheid="m", ernst="info",
        bron="RWS Richtlijn Boortechnieken 2019 §2.4.1 (HDD mantelbuis "
             "≤ 160 mm: 1,5 m aan de rand van de weg) en §3.3.1 (persing: "
             "1,0 m + D onder de fundering, 0,5 m verharding)",
        omschrijving="Dekking van de boring/persing onder maaiveld of "
                     "bovenkant verharding bij een rijbaan."),
    "boring_dekking_water_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="Beleidsregels kabels en leidingen WS Rivierenland (5.10) en "
             "WS Rijn en IJssel (2.5): ≥ 1 m onder het leggerprofiel",
        omschrijving="Dekking onder de leggerbodem van een watergang."),
    "boring_dekking_water_primair_m": dict(
        waarde=2.00, eenheid="m", ernst="info",
        bron="Hoogheemraadschap van Rijnland, algemene regel 3 (2,00 m bij "
             "primaire wateren); WS Rivierenland (2 m bij vaarwegen)",
        omschrijving="Dekking onder de leggerbodem van een primaire "
                     "(A-)watergang."),
    "watergang_diepte_m": dict(
        waarde=1.50, eenheid="m", ernst="info",
        bron="Aanname (leggerdiepte B-watergang); legger raadplegen",
        omschrijving="Diepte van de leggerbodem onder maaiveld, zolang de "
                     "legger geen bodemhoogte levert."),
    "persing_dekking_spoor_m": dict(
        waarde=1.50, eenheid="m", ernst="info",
        bron="ProRail RLN00427-2 tabel 2 (OFT ≤ 400 mm: 1,5 m onder "
             "bovenkant spoorstaaf)",
        omschrijving="Dekking van een persing onder het spoor."),
    "hdd_diepte_spoor_m": dict(
        waarde=6.00, eenheid="m", ernst="info",
        bron="ProRail RLN00427-2 tabel 3 (HDD Ø ≤ 250 mm: ≥ 6 m onder "
             "maaiveld, onder de druklijn)",
        omschrijving="Minimale diepte van een gestuurde boring onder het "
                     "spoor."),
    # --- 4. boringen en persingen: afstand van kuip/intredepunt tot rand ----
    "boring_rand_verharding_m": dict(
        waarde=1.00, eenheid="m", ernst="waarschuwing",
        bron="RWS Richtlijn Boortechnieken 2019 §2.4.2, §3.3.9, §4.3.10",
        omschrijving="Beginpunt van de invloedslijn: afstand uit de kant "
                     "verharding van een rijbaan."),
    "boring_talud_verharding": dict(
        waarde=1.50, eenheid="hor/vert", ernst="waarschuwing",
        bron="RWS Richtlijn Boortechnieken 2019 §2.4.2, §3.3.9 (1:1,5; kuip "
             "≥ 1,5 × H uit de rand + 1,0 m)",
        omschrijving="Helling van de invloedslijn onder de rijbaan: kuip of "
                     "intredeput (incl. ontgraving) ligt minimaal "
                     "rand + talud × ontgravingsdiepte uit de kant verharding."),
    "boring_rand_spoor_m": dict(
        waarde=2.75, eenheid="m", ernst="waarschuwing",
        bron="ProRail RLN00427-2 (druklijn 2,75 m uit hart spoor)",
        omschrijving="Beginpunt van de druklijn, gemeten uit hart spoor."),
    "boring_talud_spoor": dict(
        waarde=2.00, eenheid="hor/vert", ernst="waarschuwing",
        bron="ProRail RLN00427-2 tabel 2 (persing: druklijn 1:2; HDD 1:1,5 "
             "— de strengste geldt voor de kuip)",
        omschrijving="Helling van de druklijn van het spoor voor kuipen en "
                     "intredeputten."),
    "boring_afstand_insteek_m": dict(
        waarde=1.00, eenheid="m", ernst="waarschuwing",
        bron="Beschermingszone B-watergang 1 m (WS Rivierenland "
             "beleidsregel 5.10); minimaal 1 m uit de insteek",
        omschrijving="Afstand van kuip of in-/uittredepunt tot de insteek "
                     "van een watergang."),
    "boring_afstand_insteek_primair_m": dict(
        waarde=5.00, eenheid="m", ernst="waarschuwing",
        bron="Beschermingszone A-watergang 4–5 m (WS Rivierenland "
             "beleidsregel 5.10)",
        omschrijving="Afstand van kuip of in-/uittredepunt tot de insteek "
                     "van een primaire (A-)watergang."),
    # --- 4. boringen en persingen: kuipen en boorgeometrie -----------------
    "kuip_werkruimte_m": dict(
        waarde=0.50, eenheid="m", ernst="info",
        bron="Praktijk (werkruimte onder de buis in de kuip)",
        omschrijving="Ontgravingsdiepte van een pers-/ontvangstkuip onder de "
                     "onderkant van de buis."),
    "persing_perskuip_lengte_m": dict(
        waarde=3.00, eenheid="m", ernst="info",
        bron="WarmingUp 2B2 (Deltares, 2022) bijlage 4: perskuip OFT/avegaar "
             "2 × 3 m",
        omschrijving="Lengte van de perskuip in de boorrichting (achter het "
                     "intredepunt)."),
    "persing_ontvangkuip_lengte_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="WarmingUp 2B2 (Deltares, 2022) bijlage 4: ontvangstkuip 1 × 1 m",
        omschrijving="Lengte van de ontvangstkuip in de boorrichting."),
    "raket_perskuip_lengte_m": dict(
        waarde=5.00, eenheid="m", ernst="info",
        bron="WarmingUp 2B2 (Deltares, 2022) bijlage 4: perskuip 5 × 1 m",
        omschrijving="Lengte van de lanceerkuip van een raketboring."),
    "raket_ontvangkuip_lengte_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="WarmingUp 2B2 (Deltares, 2022) bijlage 4: ontvangstkuip 1 × 1 m",
        omschrijving="Lengte van de ontvangstkuip van een raketboring."),
    "boorput_diepte_m": dict(
        waarde=1.00, eenheid="m", ernst="info",
        bron="Aanname (in-/uittredeput gestuurde boring)",
        omschrijving="Ontgravingsdiepte van de in-/uittredeput bij HDD en "
                     "nanodrill; bepaalt de afstand tot de invloedslijn."),
    "hdd_intredehoek_gr": dict(
        waarde=15.0, eenheid="°", ernst="info",
        bron="Praktijk 12–23° (boorplannen; Schrijvers 2023)",
        omschrijving="Intredehoek van een gestuurde boring (HDD)."),
    "hdd_uittredehoek_gr": dict(
        waarde=12.0, eenheid="°", ernst="info",
        bron="Praktijk 12–16° (boorplannen; Schrijvers 2023)",
        omschrijving="Uittredehoek van een gestuurde boring (HDD)."),
    "hdd_boorstang_straal_m": dict(
        waarde=100.0, eenheid="m", ernst="info",
        bron="Praktijk midi-HDD (boorplan Gebr. van Leeuwen: R ≥ 150 m bij "
             "grotere stangen); type boorstelling bepaalt",
        omschrijving="Minimale boogstraal van de boorstreng bij HDD."),
    "nanodrill_intredehoek_gr": dict(
        waarde=20.0, eenheid="°", ernst="info",
        bron="Praktijk nanodrill/boogboring (Schrijvers 2023)",
        omschrijving="Intredehoek van een nanodrill."),
    "nanodrill_uittredehoek_gr": dict(
        waarde=20.0, eenheid="°", ernst="info",
        bron="Praktijk nanodrill/boogboring (Schrijvers 2023)",
        omschrijving="Uittredehoek van een nanodrill."),
    "nanodrill_boorstang_straal_m": dict(
        waarde=40.0, eenheid="m", ernst="info",
        bron="Praktijk nanodrill (korte stangen); aanname",
        omschrijving="Minimale boogstraal van de boorstreng bij nanodrill."),
    "pe_buigstraal_factor": dict(
        waarde=75.0, eenheid="× D", ernst="info",
        bron="Fabrikanttabellen PE (Pipelife/Dyka: 50–75 × D, afhankelijk "
             "van SDR en temperatuur)",
        omschrijving="Minimale buigstraal van de HDPE-mantelbuis in de "
                     "boorboog."),
    # --- geometrische integriteit ------------------------------------------
    "zelf_intersectie_ernst": dict(
        waarde="kritiek", eenheid="-", ernst="kritiek",
        bron="Topologie-eis tracégeometrie (NEN 3610)",
        omschrijving="Een tracé mag zichzelf niet snijden."),
}

_lock = threading.Lock()
_cache: dict = {"stempel": None, "regels": None}


def alle() -> dict[str, dict]:
    """Alle regels, met overrides uit ``data/normen.json`` toegepast."""
    stempel = (OVERRIDE_PAD.stat().st_mtime if OVERRIDE_PAD.exists() else None)
    with _lock:
        if _cache["stempel"] == stempel and _cache["regels"] is not None:
            return _cache["regels"]
    regels = {k: dict(v) for k, v in REGELS.items()}
    if stempel is not None:
        try:
            overrides = json.loads(OVERRIDE_PAD.read_text())
            for k, w in overrides.items():
                if k in regels:
                    if isinstance(w, dict):
                        regels[k].update(w)
                    else:
                        regels[k]["waarde"] = w
                        regels[k]["bron"] += " · override data/normen.json"
        except Exception:
            pass  # kapotte overrides: standaardwaarden blijven gelden
    with _lock:
        _cache.update(stempel=stempel, regels=regels)
    return regels


def regel(rid: str) -> dict:
    return alle()[rid]


def waarde(rid: str):
    return alle()[rid]["waarde"]


def bron(rid: str) -> str:
    return alle()[rid]["bron"]


def ernst(rid: str) -> str:
    return alle()[rid]["ernst"]


def buigradius_eis_m() -> float:
    """Minimale buigradius in meters (factor × kabeldiameter)."""
    return float(waarde("buigradius_factor")) * float(waarde("kabel_diameter_m"))
