"""Mantelbuizen: het minimale aantal buizen per boring of persing.

Ontwerpuitgangspunt: zo min mogelijk mantelbuizen. Dat wordt op vier
manieren afgedwongen:

1. De drie fasen van één MS-circuit (3×1×630 mm² Al, trefoil) gaan samen in
   één buis — nooit een buis per fase.
2. Per boring is er precies één buis per circuit dat de kruising passeert.
   Passeert een ringtracé dezelfde weg twee keer, dan delen beide circuits
   één boring (``registers.build_boringen`` voegt die samen) en krijgt die
   boring twee buizen in plaats van twee losse boringen met elk één buis.
3. Aangrenzende sleufloze kruisingen (bijvoorbeeld een watergang direct naast
   een rijbaan) worden in één boring gepasseerd: één buis in plaats van twee.
4. Bij een persing of spoorkruising komt er één stalen mantelbuis waarin alle
   HDPE-binnenbuizen liggen — geen stalen buis per circuit.

De buismaat is steeds de kleinste maat uit de standaardreeks waarin de
kabelbundel (of het pakket binnenbuizen) past volgens de spelings- en
vulgraadregels uit het normenkader (``normen.py``, instelbaar via
``data/normen.json``).
"""
from __future__ import annotations

import math

import normen

HDPE = "HDPE"
STAAL = "staal"

# Standaardreeks HDPE-mantelbuizen (buitendiameter mm, NEN-EN 12201).
HDPE_MATEN_MM = (110, 125, 160, 200, 250, 315)

# Standaardreeks stalen mantelbuizen voor persingen: (buitendiameter,
# wanddikte) in mm (NEN-EN 10219 / persingspraktijk).
STAAL_MATEN_MM = ((219.1, 6.3), (273.0, 6.3), (323.9, 7.1), (406.4, 8.8),
                  (508.0, 11.0), (610.0, 12.5), (711.0, 12.5))

# Omhullende-cirkelfactor voor n gelijke cirkels (diameter van de kleinste
# cirkel die n cirkels met diameter 1 omsluit): 1 → 1, 2 → 2, 3 → trefoil …
PAKKING_FACTOR = {1: 1.0, 2: 2.0, 3: 1 + 2 / math.sqrt(3), 4: 1 + math.sqrt(2),
                  5: 1 + 1 / math.sin(math.pi / 5), 6: 3.0, 7: 3.0}
TREFOIL = PAKKING_FACTOR[3]


def _pakking(n: int) -> float:
    if n in PAKKING_FACTOR:
        return PAKKING_FACTOR[n]
    # ruime benadering voor grotere pakketten (hexagonale pakking)
    return 1 + 2 * math.sqrt(n / (math.pi / (2 * math.sqrt(3))) - 0.5)


def kabel_diameter_mm() -> float:
    return float(normen.waarde("kabel_diameter_m")) * 1000.0


def circuit_bundel_mm() -> float:
    """Buitenmaat van drie fasekabels in trefoil."""
    return TREFOIL * kabel_diameter_mm()


def hdpe_binnen_mm(buiten_mm: float) -> float:
    sdr = float(normen.waarde("mantelbuis_hdpe_sdr"))
    return buiten_mm - 2 * buiten_mm / sdr


def _past(inhoud_mm: float, binnen_mm: float, kabel_opp_mm2: float | None) -> bool:
    speling = float(normen.waarde("mantelbuis_speling_factor"))
    if inhoud_mm > speling * binnen_mm:
        return False
    if kabel_opp_mm2 is not None:
        vulgraad = kabel_opp_mm2 / (math.pi / 4 * binnen_mm ** 2)
        if vulgraad > float(normen.waarde("mantelbuis_vulgraad_max")):
            return False
    return True


def kleinste_hdpe(circuits_in_buis: int = 1) -> dict:
    """Kleinste HDPE-buis waarin `circuits_in_buis` circuits passen.

    Standaard 1 (norm ``mantelbuis_circuits_per_buis``); de functie is
    generiek zodat een ander normenkader meer circuits per buis kan toestaan.
    """
    d = kabel_diameter_mm()
    n_kabels = 3 * circuits_in_buis
    bundel = _pakking(n_kabels) * d
    opp = n_kabels * math.pi / 4 * d ** 2
    for buiten in HDPE_MATEN_MM:
        binnen = hdpe_binnen_mm(buiten)
        if _past(bundel, binnen, opp):
            return {"materiaal": HDPE, "diameter_mm": buiten,
                    "binnen_mm": round(binnen, 1),
                    "sdr": int(float(normen.waarde("mantelbuis_hdpe_sdr"))),
                    "vulgraad": round(opp / (math.pi / 4 * binnen ** 2), 2)}
    buiten = HDPE_MATEN_MM[-1]
    return {"materiaal": HDPE, "diameter_mm": buiten,
            "binnen_mm": round(hdpe_binnen_mm(buiten), 1),
            "sdr": int(float(normen.waarde("mantelbuis_hdpe_sdr"))),
            "vulgraad": None, "past_niet": True}


def kleinste_staal(n_binnenbuizen: int, binnenbuis_mm: float) -> dict:
    """Kleinste stalen mantelbuis waarin `n_binnenbuizen` HDPE-buizen passen."""
    pakket = _pakking(n_binnenbuizen) * binnenbuis_mm
    for buiten, wand in STAAL_MATEN_MM:
        binnen = buiten - 2 * wand
        if _past(pakket, binnen, None):
            return {"materiaal": STAAL, "diameter_mm": buiten, "wand_mm": wand,
                    "binnen_mm": round(binnen, 1)}
    buiten, wand = STAAL_MATEN_MM[-1]
    return {"materiaal": STAAL, "diameter_mm": buiten, "wand_mm": wand,
            "binnen_mm": round(buiten - 2 * wand, 1), "past_niet": True}


def _mm(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def omschrijving(b: dict) -> str:
    n = b["aantal"]
    if b["materiaal"] == STAAL:
        return f"{n}× staal Ø{_mm(b['diameter_mm'])}×{_mm(b['wand_mm'])}"
    return f"{n}× HDPE Ø{b['diameter_mm']} SDR{b['sdr']}"


def bepaal(techniek: str, soort: str, circuits: int = 1,
           lengte_m: float | None = None, stalen_mantel: bool | None = None) -> dict:
    """Minimale mantelbuisconfiguratie voor één boring.

    - `techniek`: naam uit engine (HDD/persing/nanodrill/raket).
    - `soort`: obstakelsoort van de (zwaarste) kruising; bij spoor is een
      stalen mantelbuis vereist (ProRail).
    - `circuits`: aantal MS-circuits dat door deze boring gaat.
    - `stalen_mantel`: expliciet forceren/uitzetten; standaard True bij
      persing en spoor.

    Retourneert de registervelden `mantelbuizen` (lijst), `mantelbuis`
    (samenvatting) en `mantelbuis_motivering`.
    """
    circuits = max(1, int(circuits))
    per_buis = max(1, int(normen.waarde("mantelbuis_circuits_per_buis")))
    n_hdpe = math.ceil(circuits / per_buis)
    circuits_in_buis = math.ceil(circuits / n_hdpe)
    hdpe = kleinste_hdpe(circuits_in_buis)
    is_persing = "persing" in (techniek or "").lower()
    if stalen_mantel is None:
        stalen_mantel = is_persing or soort == "spoor"

    buizen = []
    motivering = []
    if circuits == 1:
        motivering.append("1 circuit → 1 buis: de drie fasen gaan samen in "
                          "één buis (trefoil)")
    else:
        motivering.append(f"{circuits} circuits → {n_hdpe} buizen "
                          f"({per_buis} circuit per buis, thermisch); één "
                          "gezamenlijke boring in plaats van "
                          f"{circuits} losse")
    if hdpe.get("past_niet"):
        motivering.append("let op: kabelbundel past in geen standaardmaat — "
                          "kabeltype of buisreeks nakijken")
    else:
        motivering.append(f"kleinste passende maat Ø{hdpe['diameter_mm']} "
                          f"(binnen {_mm(hdpe['binnen_mm'])} mm, vulgraad "
                          f"{hdpe['vulgraad']:.0%})")

    if stalen_mantel:
        staal = kleinste_staal(n_hdpe, hdpe["diameter_mm"])
        buizen.append({**staal, "aantal": 1, "functie": "stalen mantelbuis",
                       "lengte_m": lengte_m})
        buizen.append({**hdpe, "aantal": n_hdpe, "functie": "binnenbuis",
                       "lengte_m": lengte_m})
        reden = ("ProRail: stalen mantelbuis onder het spoor" if soort == "spoor"
                 else "persing: stalen mantelbuis als persbuis")
        motivering.append(f"{reden}; één stalen buis Ø{_mm(staal['diameter_mm'])} "
                          f"met {n_hdpe} HDPE-binnenbuis"
                          + ("zen" if n_hdpe > 1 else "")
                          + " — geen stalen buis per circuit")
        samenvatting = (omschrijving(buizen[0]) + " met "
                        + omschrijving(buizen[1]) + " binnenbuis")
    else:
        buizen.append({**hdpe, "aantal": n_hdpe, "functie": "mantelbuis",
                       "lengte_m": lengte_m})
        samenvatting = omschrijving(buizen[0])

    return {
        "circuits": circuits,
        "mantelbuizen": buizen,
        "mantelbuis": samenvatting,
        "mantelbuis_motivering": "; ".join(motivering),
    }


def totalen(boringen: list) -> list:
    """Materiaalstaat: aantal en meters per buistype over alle boringen."""
    agg: dict = {}
    for b in boringen:
        for mb in b.get("mantelbuizen") or []:
            sleutel = (mb["materiaal"], mb["diameter_mm"], mb.get("sdr"),
                       mb.get("wand_mm"))
            t = agg.setdefault(sleutel, {"materiaal": mb["materiaal"],
                                         "diameter_mm": mb["diameter_mm"],
                                         "sdr": mb.get("sdr"),
                                         "wand_mm": mb.get("wand_mm"),
                                         "aantal": 0, "lengte_m": 0.0,
                                         "boringen": 0})
            lengte = mb.get("lengte_m") or b.get("lengte_m") or 0.0
            t["aantal"] += mb["aantal"]
            t["lengte_m"] += mb["aantal"] * lengte
            t["boringen"] += 1
    uit = []
    for t in agg.values():
        t["lengte_m"] = round(t["lengte_m"], 1)
        t["omschrijving"] = omschrijving({**t, "aantal": t["aantal"]})
        uit.append(t)
    uit.sort(key=lambda t: (t["materiaal"] != HDPE, t["diameter_mm"]))
    return uit


def meters(boringen: list, materiaal: str, techniek: str | None = None) -> float:
    """Buismeters (aantal × lengte) van één materiaal, optioneel per techniek."""
    m = 0.0
    for b in boringen:
        if techniek is not None and b.get("type") != techniek:
            continue
        for mb in b.get("mantelbuizen") or []:
            if mb["materiaal"] == materiaal:
                m += mb["aantal"] * (mb.get("lengte_m") or b.get("lengte_m") or 0.0)
    return m
