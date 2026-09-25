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
