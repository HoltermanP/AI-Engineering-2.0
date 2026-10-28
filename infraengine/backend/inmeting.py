"""Inmetingen van de landmeter als ondergrond (DXF/DWG of puntenbestand).

Een uitvoeringsontwerp vraagt een ondergrond op centimeterniveau; BGT en
KLIC zijn dat niet. Een topografische inmeting wordt hier ingelezen en
bewaard als vectorlaag in RD New (EPSG:28992) met NAP-hoogtes waar het
bestand die heeft. De laag is op de kaart te tonen, vangbaar bij het
intekenen van het tracé en gaat mee in de werktekeningen en de DXF/DWG.

Ondersteunde invoer:
  - DXF (alle versies; INSERT-blokken uitgevouwen, bogen/splines
    afgevlakt op 1 cm, teksten als punt met tekst);
  - DWG, alleen als de gratis ODA File Converter op de server staat
    (omgevingsvariabele ``ODA_CONVERTER`` of de standaardinstallatie);
  - CSV/TXT met meetpunten: puntnummer, x, y, z, code (scheidingsteken en
    kolomvolgorde worden herkend; ETRS89-graden worden via RDNAPTRANS™2018
    omgerekend naar RD/NAP).

Alleen RD-coördinaten worden geaccepteerd: een tekening in een lokaal
stelsel zou op de verkeerde plek komen en wordt geweigerd met uitleg.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from shapely import STRtree
from shapely.geometry import box, shape

import rdnap

INMETING_DIR = Path(__file__).resolve().parent.parent / "data" / "inmeting"
AFVLAK_M = 0.01           # bogen/splines afvlakken op 1 cm
MAX_FEATURES_BEELD = 20000
MAX_INSERT_DIEPTE = 4
LIJN_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE",
              "SPLINE", "3DFACE", "SOLID"}
TEKST_TYPES = {"TEXT", "MTEXT", "ATTRIB"}


class InmetingError(Exception):
    pass


def _opslagmap() -> Path:
    """data/inmeting/, of de thuismap als de app daar niet mag schrijven
    (zelfde terugval als klic.py)."""
    try:
        INMETING_DIR.mkdir(parents=True, exist_ok=True)
        if os.access(INMETING_DIR, os.W_OK):
            return INMETING_DIR
    except OSError:
        pass
    p = Path.home() / ".infraengine" / "inmeting"
    p.mkdir(parents=True, exist_ok=True)
    return p


_lock = threading.Lock()
_index: dict = {}   # id → {"meta", "features", "geoms", "tree"}


# ------------------------------------------------------------------ DXF/DWG

def oda_converter() -> str | None:
    kandidaten = [os.environ.get("ODA_CONVERTER", ""),
                  shutil.which("ODAFileConverter") or "",
                  "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
                  "/usr/bin/ODAFileConverter", "/usr/local/bin/ODAFileConverter"]
    return next((k for k in kandidaten if k and os.path.isfile(k)
                 and os.access(k, os.X_OK)), None)


def dwg_naar_dxf(data: bytes) -> bytes:
    exe = oda_converter()
    if not exe:
        raise InmetingError(
            "DWG inlezen vraagt de gratis ODA File Converter op de server "
            "(opendesign.com); die is niet gevonden. Sla de tekening in "
            "AutoCAD/BricsCAD op als DXF, of installeer de converter en zet "
            "ODA_CONVERTER=/pad/naar/ODAFileConverter.")
    with tempfile.TemporaryDirectory() as d_in, tempfile.TemporaryDirectory() as d_uit:
        Path(d_in, "inmeting.dwg").write_bytes(data)
        try:
            subprocess.run([exe, d_in, d_uit, "ACAD2018", "DXF", "0", "1", "*.DWG"],
                           check=True, timeout=180, capture_output=True)
        except (subprocess.SubprocessError, OSError) as e:
            raise InmetingError(f"DWG-conversie mislukt: {e}") from e
        uit = list(Path(d_uit).glob("*.dxf"))
        if not uit:
            raise InmetingError("DWG-conversie leverde geen DXF op.")
        return uit[0].read_bytes()


def dxf_naar_dwg(dxf: bytes) -> bytes | None:
    """DXF → DWG (ACAD2018) met de ODA File Converter; None zonder converter."""
    exe = oda_converter()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d_in, tempfile.TemporaryDirectory() as d_uit:
        Path(d_in, "tekening.dxf").write_bytes(dxf)
        try:
            subprocess.run([exe, d_in, d_uit, "ACAD2018", "DWG", "0", "1", "*.DXF"],
                           check=True, timeout=180, capture_output=True)
        except (subprocess.SubprocessError, OSError):
            return None
        uit = list(Path(d_uit).glob("*.dwg"))
        return uit[0].read_bytes() if uit else None


def _entities(entities, diepte=0, blok=""):
    if diepte > MAX_INSERT_DIEPTE:
        return
    for e in entities:
        t = e.dxftype()
        if t == "INSERT":
            naam = e.dxf.get("name", "")
            ins = e.dxf.get("insert")
            if ins is not None:
                yield ("blok", e, naam)
            try:
                yield from _entities(e.virtual_entities(), diepte + 1, blok or naam)
            except Exception:
                continue
            for a in getattr(e, "attribs", []) or []:
                yield ("tekst", a, blok or naam)
        elif t in LIJN_TYPES:
            yield ("lijn", e, blok)
        elif t == "POINT":
            yield ("punt", e, blok)
        elif t in TEKST_TYPES:
            yield ("tekst", e, blok)


def _r(v: float, n: int = 3) -> float:
    return round(float(v), n)


def _lees_dxf(data: bytes) -> list[dict]:
    import ezdxf
    from ezdxf import recover
    from ezdxf.path import make_path

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp.write(data)
        pad = tmp.name
    try:
        doc, _ = recover.readfile(pad)
    except Exception as e:
        raise InmetingError(f"Kon het DXF-bestand niet lezen: {e}") from e
    finally:
        os.unlink(pad)

    feats = []
    for soort, e, blok in _entities(doc.modelspace()):
        laag = e.dxf.get("layer", "0")
        try:
            if soort == "lijn":
                p = make_path(e)
                pts = [(v.x, v.y, v.z) for v in p.flattening(AFVLAK_M)]
                if len(pts) < 2:
                    continue
                met_z = any(abs(z) > 1e-6 for _, _, z in pts)
                coords = [[_r(x), _r(y)] + ([_r(z)] if met_z else []) for x, y, z in pts]
                f = {"l": laag, "g": {"type": "LineString", "coordinates": coords}}
            elif soort == "punt":
                q = e.dxf.location
                f = {"l": laag, "g": {"type": "Point",
                                      "coordinates": [_r(q.x), _r(q.y)]}}
                if abs(q.z) > 1e-6:
                    f["z"] = _r(q.z)
            elif soort == "blok":
                q = e.dxf.insert
                f = {"l": laag, "b": blok, "g": {"type": "Point",
                                                 "coordinates": [_r(q.x), _r(q.y)]}}
                if abs(q.z) > 1e-6:
                    f["z"] = _r(q.z)
                f["r"] = _r(e.dxf.get("rotation", 0.0), 2)
            else:  # tekst
                if e.dxftype() == "MTEXT":
                    tekst = e.plain_text()
                    q = e.dxf.insert
                else:
                    tekst = e.dxf.get("text", "")
                    q = e.dxf.get("align_point") if e.dxf.get("halign", 0) else None
                    q = q if q is not None else e.dxf.insert
                tekst = (tekst or "").strip()
                if not tekst:
                    continue
                f = {"l": laag, "t": tekst[:120], "g": {"type": "Point",
                                                        "coordinates": [_r(q.x), _r(q.y)]},
                     "h": _r(e.dxf.get("char_height" if e.dxftype() == "MTEXT"
                                       else "height", 0.5), 2),
                     "r": _r(e.dxf.get("rotation", 0.0), 2)}
            if blok and "b" not in f:
                f["b"] = blok
            feats.append(f)
        except Exception:
            continue
    if not feats:
        raise InmetingError("Geen lijnen, punten of teksten gevonden in de modelspace.")
    return feats


# ---------------------------------------------------------------- CSV/TXT

def _getal(s: str) -> float | None:
    s = s.strip().replace(" ", "")
    if not s:
        return None
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _lees_punten(data: bytes) -> list[dict]:
    tekst = data.decode("utf-8-sig", errors="replace")
    regels = [r for r in tekst.splitlines() if r.strip() and not r.lstrip().startswith("#")]
    if not regels:
        raise InmetingError("Leeg puntenbestand.")
    proef = "\n".join(regels[:20])
    try:
        sep = csv.Sniffer().sniff(proef, delimiters=";,\t ").delimiter
    except csv.Error:
        sep = ";" if ";" in proef else ("\t" if "\t" in proef else ",")
    rijen = [[c.strip() for c in r] for r in csv.reader(io.StringIO("\n".join(regels)),
                                                         delimiter=sep)]
    if sep == " ":
        rijen = [[c for c in r if c] for r in rijen]
    kop = [c.lower() for c in rijen[0]]
    idx = {}
    if not any(_getal(c) is not None for c in rijen[0][1:3]):
        for i, c in enumerate(kop):
            if c in ("x", "rdx", "x_rd", "easting", "oost", "e"):
                idx["x"] = i
            elif c in ("y", "rdy", "y_rd", "northing", "noord", "n"):
                idx["y"] = i
            elif c in ("z", "h", "nap", "hoogte", "z_nap"):
                idx["z"] = i
            elif c in ("lat", "latitude", "breedte", "phi"):
                idx["lat"] = i
            elif c in ("lon", "lng", "longitude", "lengte", "lambda"):
                idx["lon"] = i
            elif c in ("code", "omschrijving", "beschrijving", "description", "type"):
                idx["code"] = i
            elif c in ("puntnr", "punt", "nr", "id", "naam", "name", "pnr"):
                idx["nr"] = i
        rijen = rijen[1:]
    if not ({"x", "y"} <= idx.keys() or {"lat", "lon"} <= idx.keys()):
        # zonder kop: puntnr, x, y, z, code (of x, y, z als de eerste kolom een getal is
        # in RD-bereik)
        r0 = rijen[0]
        eerste = _getal(r0[0]) if r0 else None
        if eerste is not None and eerste > 1000 and len(r0) >= 2 and (_getal(r0[1]) or 0) > 1000:
            idx = {"x": 0, "y": 1, "z": 2, "code": 3}
        else:
            idx = {"nr": 0, "x": 1, "y": 2, "z": 3, "code": 4}

    def kol(r, k):
        i = idx.get(k)
        return r[i] if i is not None and i < len(r) else ""

    feats, geodetisch = [], 0
    for r in rijen:
        if "lat" in idx:
            lat, lon = _getal(kol(r, "lat")), _getal(kol(r, "lon"))
            a, b = lat, lon
        else:
            a, b = _getal(kol(r, "x")), _getal(kol(r, "y"))
        if a is None or b is None:
            continue
        z = _getal(kol(r, "z"))
        if "lat" in idx or (abs(a) <= 90 and abs(b) <= 180 and 50 <= max(a, b) <= 54):
            lat, lon = (a, b) if "lat" in idx or a > b else (b, a)
            t = rdnap.etrs89_naar_rd(lat, lon, z)
            a, b = t["x"], t["y"]
            z = t.get("nap") if z is not None else None
            geodetisch += 1
        f = {"l": kol(r, "code") or "meetpunt",
             "g": {"type": "Point", "coordinates": [_r(a), _r(b)]}}
        if z is not None:
            f["z"] = _r(z)
        if kol(r, "nr"):
            f["t"] = kol(r, "nr")[:40]
        if kol(r, "code"):
            f["c"] = kol(r, "code")[:60]
        feats.append(f)
    if not feats:
        raise InmetingError("Geen meetpunten herkend (verwacht: puntnr; x; y; z; code).")
    for f in feats:
        f["etrs89"] = geodetisch > 0
    return feats


# ---------------------------------------------------------------- opslag

def _bbox_van(feats: list[dict]) -> list[float]:
    xs, ys = [], []
    for f in feats:
        g = f["g"]
        pts = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]
        for p in pts:
            xs.append(p[0])
            ys.append(p[1])
    return [min(xs), min(ys), max(xs), max(ys)]


def _controleer_rd(feats: list[dict]) -> None:
    bb = _bbox_van(feats)
    if not (rdnap.is_rd(bb[0], bb[1]) and rdnap.is_rd(bb[2], bb[3])):
        raise InmetingError(
            f"De coördinaten (x {bb[0]:.0f}…{bb[2]:.0f}, y {bb[1]:.0f}…{bb[3]:.0f}) "
            "liggen niet in RD New (EPSG:28992). Lever de inmeting aan in RD/NAP; "
            "een tekening in een lokaal stelsel komt anders op de verkeerde plek.")
    if (bb[2] - bb[0]) > 50000 or (bb[3] - bb[1]) > 50000:
        raise InmetingError("De inmeting beslaat meer dan 50 km; vermoedelijk staan er "
                            "losse objecten ver buiten het werkgebied (bijv. op 0,0).")


def importeer(bestandsnaam: str, data: bytes, naam: str = "") -> dict:
    ext = Path(bestandsnaam).suffix.lower()
    if ext == ".dwg":
        feats = _lees_dxf(dwg_naar_dxf(data))
    elif ext == ".dxf":
        feats = _lees_dxf(data)
    elif ext in (".csv", ".txt", ".xyz", ".pts"):
        feats = _lees_punten(data)
    else:
        raise InmetingError("Kies een DXF, DWG of puntenbestand (CSV/TXT).")
    _controleer_rd(feats)
    iid = uuid.uuid4().hex[:12]
    lagen: dict[str, int] = {}
    met_z = 0
    for f in feats:
        lagen[f["l"]] = lagen.get(f["l"], 0) + 1
        if "z" in f or (f["g"]["type"] == "LineString" and len(f["g"]["coordinates"][0]) == 3):
            met_z += 1
    meta = {
        "id": iid,
        "naam": (naam or Path(bestandsnaam).stem)[:80],
        "bestand": Path(bestandsnaam).name[:120],
        "datum": time.strftime("%Y-%m-%d %H:%M"),
        "aantal": len(feats),
        "met_hoogte": met_z,
        "bbox": [round(v, 2) for v in _bbox_van(feats)],
        "lagen": [{"naam": k, "aantal": v} for k, v in sorted(lagen.items())],
        "etrs89_omgerekend": any(f.get("etrs89") for f in feats),
        "zichtbaar": True,
    }
    for f in feats:
        f.pop("etrs89", None)
    pad = _opslagmap() / f"{iid}.json"
    pad.write_text(json.dumps({"meta": meta, "features": feats}, ensure_ascii=False))
    with _lock:
        _index.pop(iid, None)
    return meta


def _laad(iid: str) -> dict | None:
    with _lock:
        if iid in _index:
            return _index[iid]
    pad = _opslagmap() / f"{iid}.json"
    if not pad.exists():
        return None
    d = json.loads(pad.read_text())
    geoms = [shape(f["g"]) for f in d["features"]]
    obj = {"meta": d["meta"], "features": d["features"], "geoms": geoms,
           "tree": STRtree(geoms) if geoms else None}
    with _lock:
        _index[iid] = obj
    return obj


def lijst() -> list[dict]:
    uit = []
    for p in sorted(_opslagmap().glob("*.json")):
        obj = _laad(p.stem)
        if obj:
            uit.append(obj["meta"])
    return uit


def zet_zichtbaar(iid: str, zichtbaar: bool) -> None:
    obj = _laad(iid)
    if not obj:
        raise InmetingError("Inmeting niet gevonden.")
    obj["meta"]["zichtbaar"] = bool(zichtbaar)
    pad = _opslagmap() / f"{iid}.json"
    pad.write_text(json.dumps({"meta": obj["meta"], "features": obj["features"]},
                              ensure_ascii=False))


def verwijder(iid: str) -> None:
    """Naar een prullenbakmap verplaatsen, niet definitief wissen."""
    if not re.fullmatch(r"[0-9a-f]{12}", iid):
        raise InmetingError("Ongeldig id.")
    pad = _opslagmap() / f"{iid}.json"
    if not pad.exists():
        raise InmetingError("Inmeting niet gevonden.")
    prul = _opslagmap() / "prullenbak"
    prul.mkdir(exist_ok=True)
    pad.rename(prul / pad.name)
    with _lock:
        _index.pop(iid, None)


def objecten_in(bbox: tuple, alleen_zichtbaar: bool = True) -> list[tuple]:
    """(meta, feature, shapely-geom) van alle inmetingsobjecten in de bbox."""
    raak = box(*bbox)
    uit = []
    for meta in lijst():
        if alleen_zichtbaar and not meta.get("zichtbaar", True):
            continue
        b = meta["bbox"]
        if b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]:
            continue
        obj = _laad(meta["id"])
        if not obj or obj["tree"] is None:
            continue
        for i in obj["tree"].query(raak):
            uit.append((meta, obj["features"][int(i)], obj["geoms"][int(i)]))
    return uit


def features_in(bbox: tuple) -> dict:
    objs = objecten_in(bbox)
    if len(objs) > MAX_FEATURES_BEELD:
        return {"features": [], "te_veel": len(objs)}
    return {"features": [{**f, "i": m["id"]} for m, f, _ in objs], "te_veel": 0}


def lijnen_in(bbox: tuple) -> list:
    """Shapely-lijnen van de inmeting (voor referentieranden en vangen)."""
    return [g for _, f, g in objecten_in(bbox)
            if g.geom_type in ("LineString", "MultiLineString")]


def hoogte_bij(x: float, y: float, straal: float = 2.0) -> float | None:
    """Dichtstbijzijnde ingemeten NAP-hoogte (punt of lijnhoekpunt) binnen straal."""
    beste, bd = None, straal
    for _, f, g in objecten_in((x - straal, y - straal, x + straal, y + straal)):
        if "z" in f:
            d = math.hypot(g.x - x, g.y - y) if g.geom_type == "Point" else straal + 1
            if d < bd:
                beste, bd = f["z"], d
        elif f["g"]["type"] == "LineString":
            for c in f["g"]["coordinates"]:
                if len(c) == 3:
                    d = math.hypot(c[0] - x, c[1] - y)
                    if d < bd:
                        beste, bd = c[2], d
    return beste
