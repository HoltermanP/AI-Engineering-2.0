"""Maaiveldhoogte (AHN, DTM 0,5 m) via de PDOK-WCS — lengteprofielen.

Gebruikt voor het hoogteprofiel langs boringen (HDD-lengteprofiel: taluds,
dijken en watergangdiepteligging worden zichtbaar) en een indicatief
maaiveldverloop langs het tracé. Zelfde tolerantie als de zonelagen: een
falende dienst betekent geen profiel, geen fout.
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict

import numpy as np
import requests
from PIL import Image

AHN_WCS = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
COVERAGE = "dtm_05m"
NODATA_GRENS = 1e10  # AHN-nodata is ± 3,4e38 (float32-max)
MAX_PX = 700         # per as; grotere vensters worden grover opgevraagd

_session = requests.Session()
_session.headers["User-Agent"] = "InfraEngine-prototype/0.1"

_cache: OrderedDict = OrderedDict()
_lock = threading.Lock()
_CACHE_MAX = 60


def _fetch_grid(bbox: tuple):
    """(hoogtegrid, resolutie_m) voor een bbox; None als de dienst faalt."""
    key = tuple(round(v) for v in bbox)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        breedte = bbox[2] - bbox[0]
        hoogte = bbox[3] - bbox[1]
        res = 0.5
        while breedte / res > MAX_PX or hoogte / res > MAX_PX:
            res *= 2
        params = [
            ("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
            ("COVERAGEID", COVERAGE), ("FORMAT", "image/tiff"),
            ("SUBSET", f"x({bbox[0]},{bbox[2]})"),
            ("SUBSET", f"y({bbox[1]},{bbox[3]})"),
            ("SCALESIZE", f"x({max(2, int(breedte / res))}),"
                          f"y({max(2, int(hoogte / res))})"),
        ]
        r = _session.get(AHN_WCS, params=params, timeout=60)
        r.raise_for_status()
        if "tiff" not in r.headers.get("Content-Type", ""):
            raise ValueError("geen GeoTIFF terug")
        arr = np.array(Image.open(io.BytesIO(r.content)), dtype=np.float32)
        arr[np.abs(arr) > NODATA_GRENS] = np.nan
        waarde = (arr, res)
    except Exception:
        waarde = None
    with _lock:
        _cache[key] = waarde
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return waarde


def profiel_langs_lijn(coords: list, stap_m: float = 5.0) -> list | None:
    """Hoogteprofiel [(chainage_m, hoogte_m_nap), ...] langs een lijn (RD).

    None als de AHN-dienst niet beschikbaar is; punten zonder AHN-dekking
    (water, bebouwing in het DTM) krijgen hoogte None.
    """
    from shapely.geometry import LineString

    lijn = LineString(coords)
    marge = 5.0
    b = lijn.bounds
    grid = _fetch_grid((b[0] - marge, b[1] - marge, b[2] + marge, b[3] + marge))
    if grid is None:
        return None
    arr, res = grid
    xmin, ymin = b[0] - marge, b[1] - marge
    ymax = b[3] + marge
    nrows, ncols = arr.shape
    profiel = []
    n = max(2, int(lijn.length / stap_m) + 1)
    for i in range(n):
        m = min(lijn.length, i * stap_m)
        p = lijn.interpolate(m)
        col = min(ncols - 1, max(0, int((p.x - xmin) / res)))
        row = min(nrows - 1, max(0, int((ymax - p.y) / res)))
        z = float(arr[row, col])
        profiel.append((round(m, 1), None if np.isnan(z) else round(z, 2)))
    return profiel


def profiel_samenvatting(profiel: list | None) -> dict:
    """Min/max/verval van een profiel; leeg dict zonder bruikbare punten."""
    if not profiel:
        return {}
    zs = [z for _, z in profiel if z is not None]
    if not zs:
        return {}
    return {
        "maaiveld_min_nap": round(min(zs), 2),
        "maaiveld_max_nap": round(max(zs), 2),
        "verval_m": round(max(zs) - min(zs), 2),
        "maaiveld_start_nap": next((z for _, z in profiel if z is not None), None),
        "maaiveld_eind_nap": next((z for _, z in reversed(profiel)
                                   if z is not None), None),
    }
