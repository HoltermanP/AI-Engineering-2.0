"""Publiek/privaat-inschatting per gekruist perceel (ZRO-voorbereiding).

Zonder BRK-koppeling (licentie) is eigendom niet vast te stellen; wel is uit
open data goed in te schatten of een perceel publiek dan wel privaat is:

- BRK-import (``data/brk/eigenaren.csv``, indien aanwezig): een eigenaarnaam
  die op een overheid duidt (gemeente, provincie, Staat, waterschap,
  ProRail, …) is beslissend.
- BGT-grondgebruik binnen het perceel: wegdelen, bermen, groenvoorziening,
  water en spoor duiden op openbaar gebied; erf, agrarisch terrein en
  bebouwing (panden) duiden op particulier bezit.

De uitkomst is een inschatting (``publiek`` / ``privaat`` / ``onbekend``) met
toelichting, geen kadastraal feit: publieke gebouwen staan ook op "erf",
tertiaire sloten horen vaak bij het aanliggende (private) perceel. Bij
twijfel of tegenstrijdige signalen valt de uitkomst op "onbekend". De
inschatting helpt ramen hoeveel ZRO's er op private percelen te vestigen
zijn; op publieke percelen loopt de ligging doorgaans via de AVOI-vergunning.
"""
from __future__ import annotations

import re

from shapely.strtree import STRtree

# eigenaarnamen (BRK-import) die op publiek eigendom duiden
_OVERHEID_RE = re.compile(
    r"gemeente|provincie|waterschap|hoogheemraadschap|de staat|"
    r"staat der nederlanden|rijkswaterstaat|rijksvastgoed|domeinen|"
    r"prorail|railinfratrust|staatsbosbeheer", re.IGNORECASE)

# BGT-categorieën, zelfde indeling als het kostenoppervlak (engine.build_painter)
_AGRARISCH = {"bouwland", "grasland agrarisch", "fruitteelt", "boomteelt"}
_URBAAN_GROEN = {"groenvoorziening"}

# beslisgrenzen (fracties van het perceeloppervlak); publiek vraagt sterkere
# dominantie dan privaat — een perceel ten onrechte publiek noemen zou het
# aantal te vestigen ZRO's onderschatten
_PRIVAAT_MIN = 0.40    # zoveel erf/agrarisch/bebouwing → privaat ...
_PRIVAAT_DOM = 2.0     # ... mits minstens 2× het publieke signaal
_PUBLIEK_MIN = 0.50    # zoveel openbaar gebied → publiek ...
_PUBLIEK_DOM = 3.0     # ... mits minstens 3× het private signaal
_PAND_MIN_M2 = 25.0    # bebouwing vanaf schuurformaat telt als privaat-signaal


def verzamel_signalen(bgt: dict) -> list:
    """Compacte eigendomssignalen uit een BGT-databundel.

    Retourneert ``[(sleutel, geometrie, categorie)]`` met categorie
    ``publiek`` / ``privaat`` / ``pand``; de sleutel ontdubbelt features die
    in overlappende (corridor-)bboxen dubbel zijn opgehaald.
    """
    signalen = []

    def voeg(coll, cat, pred=None, buffer=None):
        for g, p in bgt.get(coll, []):
            if pred is not None and not pred(p):
                continue
            sleutel = (coll, p.get("lokaal_id")
                       or (round(g.centroid.x, 1), round(g.centroid.y, 1)))
            signalen.append((sleutel, g.buffer(buffer) if buffer else g, cat))

    voeg("wegdeel", "publiek")
    voeg("ondersteunendwegdeel", "publiek")
    voeg("waterdeel", "publiek")
    voeg("ondersteunendwaterdeel", "publiek")
    voeg("spoor", "publiek", buffer=2.5)  # lijnen → vlak
    voeg("begroeidterreindeel", "publiek",
         lambda p: (p.get("fysiek_voorkomen") or "") in _URBAAN_GROEN)
    voeg("begroeidterreindeel", "privaat",
         lambda p: (p.get("fysiek_voorkomen") or "") in _AGRARISCH)
    voeg("onbegroeidterreindeel", "privaat",
         lambda p: p.get("fysiek_voorkomen") == "erf")
    voeg("pand", "pand")
    voeg("overigbouwwerk", "pand")
    return signalen


class Signalen:
    """Ruimtelijke index over de eigendomssignalen, voor snelle toetsing."""

    def __init__(self, feats):
        feats = [(g, cat) for g, cat in feats
                 if g is not None and not g.is_empty]
        self.geoms = [g for g, _ in feats]
        self.cats = [cat for _, cat in feats]
        self.tree = STRtree(self.geoms) if feats else None


def maak_signalen(feats) -> Signalen:
    """Signalen-index uit ``[(geometrie, categorie)]`` (corridor: samengevoegd)."""
    return Signalen(feats)


def signalen_uit_bgt(bgt: dict) -> Signalen:
    return Signalen((g, cat) for _, g, cat in verzamel_signalen(bgt))


def schat(perceel_geom, brk_info: dict | None, signalen: Signalen | None) -> dict:
    """Inschatting publiek/privaat voor één perceel.

    Retourneert ``{"eigendom", "eigendom_toelichting"}``. De BRK-eigenaar is
    beslissend als hij gekoppeld is; anders beslist het BGT-grondgebruik
    binnen het perceel.
    """
    naam = (brk_info or {}).get("eigenaar", "").strip()
    if naam:
        publiek = bool(_OVERHEID_RE.search(naam))
        return {"eigendom": "publiek" if publiek else "privaat",
                "eigendom_toelichting": f"BRK-eigenaar: {naam}"}

    if (signalen is None or signalen.tree is None or perceel_geom is None
            or perceel_geom.is_empty or perceel_geom.area <= 0):
        return {"eigendom": "onbekend",
                "eigendom_toelichting": "geen BRK-koppeling en geen "
                                        "BGT-grondgebruik beschikbaar"}

    opp = perceel_geom.area
    m2 = {"publiek": 0.0, "privaat": 0.0, "pand": 0.0}
    for i in signalen.tree.query(perceel_geom):
        g = signalen.geoms[i]
        try:
            snede = g.intersection(perceel_geom).area
        except Exception:  # zelden: ongeldige BGT-geometrie
            snede = g.buffer(0).intersection(perceel_geom.buffer(0)).area
        m2[signalen.cats[i]] += snede

    frac_pub = min(1.0, m2["publiek"] / opp)
    frac_priv = min(1.0, (m2["privaat"] + m2["pand"]) / opp)
    pct = f"{frac_pub * 100:.0f}% openbaar gebruik, {frac_priv * 100:.0f}% erf/agrarisch/bebouwing (BGT)"

    if frac_priv >= _PRIVAAT_MIN and frac_priv >= _PRIVAAT_DOM * frac_pub:
        toel = f"inschatting uit grondgebruik: {pct}"
        if m2["pand"] >= _PAND_MIN_M2:
            toel += f"; bebouwing aanwezig ({m2['pand']:.0f} m²)"
        return {"eigendom": "privaat", "eigendom_toelichting": toel}
    if frac_pub >= _PUBLIEK_MIN and frac_pub >= _PUBLIEK_DOM * frac_priv:
        return {"eigendom": "publiek",
                "eigendom_toelichting": f"inschatting uit grondgebruik: {pct}"}
    if m2["pand"] >= _PAND_MIN_M2 and frac_priv >= frac_pub:
        return {"eigendom": "privaat",
                "eigendom_toelichting": f"inschatting uit bebouwing "
                                        f"({m2['pand']:.0f} m² pand/bouwwerk "
                                        f"op het perceel); {pct}"}
    return {"eigendom": "onbekend",
            "eigendom_toelichting": f"grondgebruik niet doorslaggevend: {pct}; "
                                    f"BRK-koppeling nodig voor uitsluitsel"}
