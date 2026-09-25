"""Grondwaterstanden (BRO GLD) — informatieve live datalaag.

Putlocaties met grondwaterstandonderzoek komen uit de PDOK OGC API
"BRO Grondwatermonitoring (GM) in samenhang — karakteristieken"
(collectie gm_gld, rechtstreeks in RD). De meetreeks zelf wordt per put
pas bij een klik opgehaald bij de BRO-uitgifteservice
(publiek.broservices.nl, seriesAsCsv) — altijd de actuele stand van de
registratie. Alleen informatief: weegt niet mee in het kostenoppervlak.
"""
from __future__ import annotations

import csv
import io
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import requests
from skimage import measure

OGC_GLD_ITEMS = ("https://api.pdok.nl/bzk/bro-gminsamenhang-karakteristieken"
                 "/ogc/v1/collections/gm_gld/items")
CRS_RD = "http://www.opengis.net/def/crs/EPSG/0/28992"
SERIES_CSV = "https://publiek.broservices.nl/gm/gld/v1/seriesAsCsv/{bro_id}"

MAX_BBOX_KM2 = 400.0  # putten zijn dicht gezaaid; de frontend zoomt eerst in
MAX_PUTTEN = 2000     # paginering afkappen (afgekapt=True → melding in legenda)
_PAGINA = 1000
PUT_TTL_S = 600.0     # putlocaties wijzigen zelden
STAND_TTL_S = 1800.0  # meetreeks per put: BRO vraagt max ± 3 req/s
REEKS_MAX_PUNTEN = 150

_BRO_ID = re.compile(r"^GLD\d{12}$")

_session = requests.Session()
_session.headers["User-Agent"] = "InfraEngine-prototype/0.1"

_put_cache: dict = {}    # bbox (afgerond op 100 m) → (tijdstip, resultaat)
_stand_cache: dict = {}  # bro_id → (tijdstip, resultaat)


class GrondwaterError(Exception):
    """Ongeldige aanvraag (bbox te groot, onbekend BRO-ID, geen metingen)."""


class DienstError(GrondwaterError):
    """PDOK of BRO-uitgifteservice niet bereikbaar."""


def _uit_cache(cache: dict, sleutel, ttl: float):
    hit = cache.get(sleutel)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _in_cache(cache: dict, sleutel, waarde, maximum: int = 200):
    if len(cache) >= maximum:  # simpele begrenzing voor het prototype
        cache.clear()
    cache[sleutel] = (time.time(), waarde)
    return waarde


def putten(bbox: tuple) -> dict:
    """GLD-putten (punten, RD) in de bbox met eerste/laatste meetdatum."""
    km2 = max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / 1e6
    if km2 > MAX_BBOX_KM2:
        raise GrondwaterError(
            f"Kaartbeeld is {km2:.0f} km²; grondwaterputten worden tot "
            f"{MAX_BBOX_KM2:.0f} km² geladen. Zoom verder in.")
    sleutel = tuple(round(x / 100) for x in bbox)
    cached = _uit_cache(_put_cache, sleutel, PUT_TTL_S)
    if cached is not None:
        return cached

    url = OGC_GLD_ITEMS
    params = {"f": "json", "limit": _PAGINA,
              "bbox": ",".join(f"{x:.1f}" for x in bbox),
              "bbox-crs": CRS_RD, "crs": CRS_RD}
    feats: list = []
    afgekapt = False
    while True:
        try:
            r = _session.get(url, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            raise DienstError(f"PDOK (BRO GM in samenhang) niet bereikbaar: {e}")
        for f in d.get("features", []):
            p = f.get("properties", {})
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point" or not p.get("bro_id"):
                continue
            x, y = geom["coordinates"][:2]
            feats.append({
                "bro_id": p["bro_id"],
                "x": round(x, 1), "y": round(y, 1),
                "eerste": p.get("research_first_date"),
                "laatste": p.get("research_last_date"),
                "observaties": p.get("number_of_observations"),
            })
        volgende = next((l.get("href") for l in d.get("links", [])
                         if l.get("rel") == "next"), None)
        if not volgende:
            break
        if len(feats) >= MAX_PUTTEN:
            afgekapt = True
            break
        url, params = volgende, None  # next-link bevat alle parameters al

    return _in_cache(_put_cache, sleutel,
                     {"putten": feats, "afgekapt": afgekapt})


# seriesAsCsv-kolommen: Tijdstip (unix epoch ms), daarna per observatietype
# een waarde- [m t.o.v. NAP] en opmerkingskolom; beoordeeld gaat voor.
_WAARDE_KOLOMMEN = (("beoordeeld", 3), ("voorlopig", 1),
                    ("controle", 5), ("onbekend", 7))


def stand(bro_id: str) -> dict:
    """Actuele stand + compacte meetreeks van één GLD, live uit de BRO."""
    if not _BRO_ID.match(bro_id):
        raise GrondwaterError("Ongeldig GLD BRO-ID.")
    cached = _uit_cache(_stand_cache, bro_id, STAND_TTL_S)
    if cached is not None:
        return cached

    try:
        r = _session.get(SERIES_CSV.format(bro_id=bro_id), timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise DienstError(f"BRO-uitgifteservice niet bereikbaar: {e}")

    metingen: list = []  # (tijd_ms, stand_m_nap, type)
    for rij in csv.reader(io.StringIO(r.text)):
        if not rij or not rij[0]:
            continue
        try:
            t = int(rij[0])
        except ValueError:  # kopregel
            continue
        for naam, i in _WAARDE_KOLOMMEN:
            if i < len(rij) and rij[i]:
                try:
                    metingen.append((t, float(rij[i]), naam))
                except ValueError:
                    pass
                break
    if not metingen:
        raise GrondwaterError("Geen metingen in dit grondwaterstandendossier.")
    metingen.sort(key=lambda m: m[0])
    laatste = metingen[-1]
    standen = [m[1] for m in metingen]
    # gelijkmatig uitgedunde reeks voor de sparkline in de kaartpopup
    stap = max(1, len(metingen) // REEKS_MAX_PUNTEN)
    reeks = [[m[0], m[1]] for m in metingen[::stap]]
    if reeks[-1][0] != laatste[0]:
        reeks.append([laatste[0], laatste[1]])

    return _in_cache(_stand_cache, bro_id, {
        "bro_id": bro_id,
        "laatste": {"tijd_ms": laatste[0], "stand_m_nap": laatste[1],
                    "type": laatste[2]},
        "aantal": len(metingen),
        "periode_ms": [metingen[0][0], laatste[0]],
        "min_m_nap": min(standen), "max_m_nap": max(standen),
        "reeks": reeks,
    })


# ---------------------------------------------------------------------------
# Isohypsen: hoogtelijnen van de grondwaterstand (m t.o.v. NAP)
# ---------------------------------------------------------------------------

ISO_RECENT_D = 730       # alleen putten met een meting uit de laatste 2 jaar
ISO_MIN_PUTTEN = 4
ISO_MAX_PUTTEN = 48      # standen live ophalen is de dure stap (BRO ± 3 req/s)
ISO_GRID = 110           # rastercellen in de breedte
ISO_INTERVALLEN = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0)
ISO_TTL_S = 1800.0

_iso_cache: dict = {}    # bbox (afgerond op 100 m) → (tijdstip, resultaat)


def isohypsen(bbox: tuple) -> dict:
    """Isohypsen uit de actuele standen van de GLD-putten in de bbox.

    De laatste stand per put wordt live opgehaald (met cache), met inverse-
    distance-weging naar een raster geïnterpoleerd en met marching squares
    (skimage) tot contourlijnen herleid. Cellen ver van elke put worden
    gemaskeerd: daar zegt interpolatie niets meer. Indicatief beeld — de
    metingen zijn niet gelijktijdig en buisfilters verschillen in diepte."""
    sleutel = tuple(round(x / 100) for x in bbox)
    cached = _uit_cache(_iso_cache, sleutel, ISO_TTL_S)
    if cached is not None:
        return cached

    alle = putten(bbox)
    grens = (date.today() - timedelta(days=ISO_RECENT_D)).isoformat()
    recent = [p for p in alle["putten"] if (p["laatste"] or "") >= grens]
    if len(recent) < ISO_MIN_PUTTEN:
        raise GrondwaterError(
            f"Isohypsen vragen minstens {ISO_MIN_PUTTEN} putten met een meting "
            f"van na {grens}; in dit kaartbeeld: {len(recent)}.")
    stap = max(1, math.ceil(len(recent) / ISO_MAX_PUTTEN))
    selectie = recent[::stap][:ISO_MAX_PUTTEN]

    metingen: list = []  # (x, y, stand_m_nap)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(stand, p["bro_id"]): p for p in selectie}
        for fut, p in futs.items():
            try:
                metingen.append((p["x"], p["y"],
                                 fut.result()["laatste"]["stand_m_nap"]))
            except GrondwaterError:
                pass  # put zonder bruikbare reeks of dienst-hik: overslaan
    if len(metingen) < ISO_MIN_PUTTEN:
        raise GrondwaterError(
            "Te weinig actuele standen opgehaald voor isohypsen "
            f"({len(metingen)} van {len(selectie)} putten).")

    xs = np.array([m[0] for m in metingen])
    ys = np.array([m[1] for m in metingen])
    ws = np.array([m[2] for m in metingen])
    nx = ISO_GRID
    ny = max(12, min(ISO_GRID,
                     round(nx * (bbox[3] - bbox[1]) / max(1.0, bbox[2] - bbox[0]))))
    gx = np.linspace(bbox[0], bbox[2], nx)
    gy = np.linspace(bbox[1], bbox[3], ny)
    GX, GY = np.meshgrid(gx, gy)
    d2 = (GX[..., None] - xs) ** 2 + (GY[..., None] - ys) ** 2
    d2 = np.maximum(d2, 1.0)
    gewicht = 1.0 / d2  # IDW, macht 2
    veld = (gewicht * ws).sum(-1) / gewicht.sum(-1)
    # buiten het bereik van de putten niets tekenen (geen extrapolatie)
    mask_m = max(300.0, 0.15 * math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1]))
    veld[np.sqrt(d2.min(-1)) > mask_m] = np.nan

    geldig = veld[~np.isnan(veld)]
    bereik = float(geldig.max() - geldig.min()) if geldig.size else 0.0
    interval = next((i for i in ISO_INTERVALLEN if bereik / i <= 14),
                    ISO_INTERVALLEN[-1])
    lijnen: list = []
    lvl = math.ceil(float(geldig.min()) / interval) * interval
    while geldig.size and lvl <= float(geldig.max()) + 1e-9:
        for c in measure.find_contours(veld, lvl):
            if len(c) < 6:
                continue
            coords = [[round(bbox[0] + col * (bbox[2] - bbox[0]) / (nx - 1), 1),
                       round(bbox[1] + rij * (bbox[3] - bbox[1]) / (ny - 1), 1)]
                      for rij, col in c[::2]]
            lijnen.append({"stand_m_nap": round(lvl, 2) + 0.0, "coords": coords})
        lvl += interval

    return _in_cache(_iso_cache, sleutel, {
        "isohypsen": lijnen,
        "interval_m": interval,
        "putten_gebruikt": len(metingen),
        "putten_recent": len(recent),
        "bereik_m_nap": [round(float(geldig.min()), 2),
                         round(float(geldig.max()), 2)] if geldig.size else None,
    })
