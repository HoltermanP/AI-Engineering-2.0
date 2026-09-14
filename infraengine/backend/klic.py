"""KLIC-koppelvlak (voorbereid; licentiebron, FO §2-noot).

Er is nog geen KLIC-toegang; dit module maakt de koppeling plug-and-play:
leg GeoJSON-bestanden uit een KLIC-levering (oriëntatie- of graafmelding,
per thema geëxporteerd naar EPSG:28992) in ``data/klic/`` en de netten tellen
mee als weging (``klic_netdichtheid``) en in de kruisingsinformatie. Zonder
bestanden blijft alles op "onbekend — KLIC niet gekoppeld".
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from shapely.geometry import shape

KLIC_DIR = Path(__file__).resolve().parent.parent / "data" / "klic"

_lock = threading.Lock()
_cache: dict = {"stempel": None, "geoms": []}


def _bestanden() -> list:
    if not KLIC_DIR.is_dir():
        return []
    return sorted(p for p in KLIC_DIR.iterdir()
                  if p.suffix.lower() in (".json", ".geojson"))


def _laad() -> list:
    """Alle KLIC-features (geom, props) uit data/klic/, gecachet op mtime."""
    paden = _bestanden()
    stempel = tuple((p.name, p.stat().st_mtime) for p in paden)
    with _lock:
        if _cache["stempel"] == stempel:
            return _cache["geoms"]
    geoms = []
    for pad in paden:
        try:
            data = json.loads(pad.read_text())
        except Exception:
            continue
        for f in data.get("features", []):
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = shape(geom)
            except Exception:
                continue
            if g.is_empty:
                continue
            props = f.get("properties", {}) or {}
            props.setdefault("bron", pad.name)
            geoms.append((g, props))
    with _lock:
        _cache.update(stempel=stempel, geoms=geoms)
    return geoms


def fetch_geoms(bbox: tuple) -> list:
    """KLIC-netten (geom, props) die de bbox raken; leeg zonder import."""
    xmin, ymin, xmax, ymax = bbox
    out = []
    for g, p in _laad():
        b = g.bounds
        if b[2] < xmin or b[0] > xmax or b[3] < ymin or b[1] > ymax:
            continue
        out.append((g, p))
    return out


def status() -> dict:
    geoms = _laad()
    return {
        "gekoppeld": bool(geoms),
        "bestanden": [p.name for p in _bestanden()],
        "features": len(geoms),
    }
