"""BRK-koppelvlak (voorbereid; licentiebron, FO §2-noot).

Er is nog geen BRK-toegang; dit module maakt de eigenarenkoppeling
plug-and-play: leg ``data/brk/eigenaren.csv`` neer met kolommen
``perceel;eigenaar;adres`` (scheiding ; of ,) — ``perceel`` in dezelfde
aanduiding als het ZRO-register ("GEMEENTE SECTIE NUMMER", bijvoorbeeld
"AMERSFOORT A 1234"). Een BRK-levering (BRK Bevragen / BRK Levering) is met
één export naar dit formaat te vertalen. Zonder bestand blijven eigenaren
op "onbekend — BRK-eigendom niet gekoppeld".
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path

BRK_CSV = Path(__file__).resolve().parent.parent / "data" / "brk" / "eigenaren.csv"

_lock = threading.Lock()
_cache: dict = {"stempel": None, "eigenaren": {}}


def _normaliseer(perceel: str) -> str:
    return " ".join(perceel.upper().split())


def _laad() -> dict:
    if not BRK_CSV.is_file():
        return {}
    stempel = BRK_CSV.stat().st_mtime
    with _lock:
        if _cache["stempel"] == stempel:
            return _cache["eigenaren"]
    eigenaren: dict = {}
    try:
        tekst = BRK_CSV.read_text(encoding="utf-8-sig")
        scheiding = ";" if tekst.count(";") >= tekst.count(",") else ","
        for rij in csv.DictReader(tekst.splitlines(), delimiter=scheiding):
            rij = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in rij.items()}
            perceel = rij.get("perceel")
            if not perceel:
                continue
            eigenaren[_normaliseer(perceel)] = {
                "eigenaar": rij.get("eigenaar", ""),
                "adres": rij.get("adres", ""),
            }
    except Exception:
        eigenaren = {}
    with _lock:
        _cache.update(stempel=stempel, eigenaren=eigenaren)
    return eigenaren


def eigenaar_van(perceel: str) -> dict | None:
    """{eigenaar, adres} voor een perceelaanduiding, of None."""
    return _laad().get(_normaliseer(perceel))


def status() -> dict:
    eigenaren = _laad()
    return {"gekoppeld": bool(eigenaren), "percelen": len(eigenaren),
            "bestand": str(BRK_CSV) if BRK_CSV.is_file() else None}
