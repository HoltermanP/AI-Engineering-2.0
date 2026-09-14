"""Bestaande sonderingen (BRO CPT) langs het tracé — kenmerken, live uit de BRO.

De PDOK-CPT-dienst is alleen WMS (beeld); de kenmerken komen daarom uit de
publieke BRO-uitgifteservice (characteristics search, geen sleutel nodig).
Geleverd per sondering: BRO-ID, locatie (RD), einddiepte, maaiveld (NAP),
kwaliteitsklasse, norm en datum. Zelfde tolerantie als de zonelagen: een
falende dienst betekent geen register, geen fout.
"""
from __future__ import annotations

import math
import re
import threading
from collections import OrderedDict

import requests

CPT_SEARCH = ("https://publiek.broservices.nl/sr/cpt/v1/characteristics/searches"
              "?requestReference=InfraEngine")
BRO_LOKET = "https://www.broloket.nl/ondergrondgegevens?bro-id={bro_id}"

# tegels langs het tracé: klein genoeg voor de uitgifteservice, groot genoeg
# om het aantal requests te beperken (met cache over deeltrajecten heen)
TEGEL_M = 2000.0
MARGE_M = 150.0  # ruimer dan de zoekafstanden in registers.build_sonderingen

_session = requests.Session()
_session.headers["User-Agent"] = "InfraEngine-prototype/0.1"

_cache: OrderedDict = OrderedDict()
_lock = threading.Lock()
_CACHE_MAX = 200


def _rd_naar_wgs(x: float, y: float) -> tuple:
    """RD New → (lat, lon), benadering Schreutelkamp/van Hees (± 1 m)."""
    dx = (x - 155000.0) * 1e-5
    dy = (y - 463000.0) * 1e-5
    lat = 52.15517440 + (
        3235.65389 * dy - 32.58297 * dx * dx - 0.24750 * dy * dy
        - 0.84978 * dx * dx * dy - 0.06550 * dy ** 3 - 0.01709 * dx * dx * dy * dy
        - 0.00738 * dx + 0.00530 * dx ** 4 - 0.00039 * dx * dx * dy ** 3
        + 0.00033 * dx ** 4 * dy - 0.00012 * dx * dy) / 3600.0
    lon = 5.38720621 + (
        5260.52916 * dx + 105.94684 * dx * dy + 2.45656 * dx * dy * dy
        - 0.81885 * dx ** 3 + 0.05594 * dx * dy ** 3 - 0.05607 * dx ** 3 * dy
        + 0.01199 * dy - 0.00256 * dx ** 3 * dy * dy + 0.00128 * dx * dy ** 4
        + 0.00022 * dy * dy - 0.00022 * dx * dx + 0.00026 * dx ** 5) / 3600.0
    return lat, lon


_TAG = {
    "bro_id": re.compile(r"<brocom:broId>([^<]+)</brocom:broId>"),
    "gedereg": re.compile(r"<brocom:deregistered>([^<]+)<"),
    "rd": re.compile(r"deliveredLocation[^>]*EPSG/0/28992[^>]*>\s*"
                     r"<gml:pos>([\d.]+)\s+([\d.]+)</gml:pos>"),
    "maaiveld": re.compile(r'<offset uom="m">([-\d.]+)</offset>'),
    "kwaliteit": re.compile(r"<qualityClass[^>]*>([^<]+)<"),
    "norm": re.compile(r"<cptStandard[^>]*>([^<]+)<"),
    "diepte": re.compile(r'<finalDepth uom="m">([\d.]+)</finalDepth>'),
    "datum": re.compile(r"<startTime>([^<]+)</startTime>"),
    "rapport": re.compile(r"<researchReportDate><brocom:date>([^<]+)<"),
}


def _parse(xml: str) -> list:
    cpts = []
    for blok in re.findall(r"<CPT_C .*?</CPT_C>", xml, re.S):
        def pak(sleutel):
            m = _TAG[sleutel].search(blok)
            return m if m is None else m.groups()
        bro_id = pak("bro_id")
        rd = pak("rd")
        if not bro_id or not rd:
            continue
        gedereg = pak("gedereg")
        if gedereg and gedereg[0].strip() != "nee":
            continue
        def getal(sleutel):
            m = pak(sleutel)
            try:
                return float(m[0])
            except (TypeError, ValueError):
                return None
        datum = pak("datum") or pak("rapport")
        cpts.append({
            "bro_id": bro_id[0],
            "x": float(rd[0]), "y": float(rd[1]),
            "einddiepte_m": getal("diepte"),
            "maaiveld_nap": getal("maaiveld"),
            "kwaliteitsklasse": (pak("kwaliteit") or ("onbekend",))[0],
            "norm": (pak("norm") or ("",))[0],
            "datum": (datum or ("",))[0],
        })
    return cpts


def _kenmerken_bbox(bbox: tuple) -> list:
    """CPT-kenmerken binnen een RD-bbox, met cache; Exception bij dienstfout."""
    key = tuple(round(v / 50) for v in bbox)  # 50 m-raster: cache-hits over naden
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    lat0, lon0 = _rd_naar_wgs(bbox[0], bbox[1])
    lat1, lon1 = _rd_naar_wgs(bbox[2], bbox[3])
    body = {"area": {"boundingBox": {
        "lowerCorner": {"lat": min(lat0, lat1), "lon": min(lon0, lon1)},
        "upperCorner": {"lat": max(lat0, lat1), "lon": max(lon0, lon1)},
    }}}
    r = _session.post(CPT_SEARCH, json=body, timeout=60)
    r.raise_for_status()
    cpts = _parse(r.text)
    with _lock:
        _cache[key] = cpts
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return cpts


def langs_route(coords: list) -> list:
    """Alle bekende sonderingen in tegels rond de route (ontdubbeld op BRO-ID).

    Retourneert een lijst kenmerken-dicts; Exception als de dienst faalt
    (de aanroeper meldt dat in laag_fouten en slaat het register over).
    """
    tegels = []
    vorige = None
    afstand = 0.0
    punten = [coords[0]]
    for a, b in zip(coords[:-1], coords[1:]):
        afstand += math.hypot(b[0] - a[0], b[1] - a[1])
        if afstand >= TEGEL_M:
            punten.append(b)
            afstand = 0.0
    punten.append(coords[-1])
    for a, b in zip(punten[:-1], punten[1:]):
        bbox = (min(a[0], b[0]) - MARGE_M, min(a[1], b[1]) - MARGE_M,
                max(a[0], b[0]) + MARGE_M, max(a[1], b[1]) + MARGE_M)
        if vorige != bbox:
            tegels.append(bbox)
            vorige = bbox
    gezien: dict = {}
    for bbox in tegels:
        for cpt in _kenmerken_bbox(bbox):
            gezien.setdefault(cpt["bro_id"], cpt)
    return list(gezien.values())


def loket_url(bro_id: str) -> str:
    return BRO_LOKET.format(bro_id=bro_id)
