"""KLIC-koppelvlak: bestaande kabels en leidingen uit KLIC-leveringen.

Twee bronnen, die samen de KLIC-laag vormen:

1. **Gekoppelde hoofdmap(pen)** (``POST /api/klic/koppel``): de map wordt
   recursief doorzocht naar KLIC-leveringen zoals het Kadaster ze levert —
   uitgepakt (``<klicnr>_<volgnr>/GI_gebiedsinformatielevering_*.xml``),
   als ``Levering_<klicnr>_<volgnr>.zip`` of als zip in een zip (een
   verzamelzip van een hele map leveringen). Per KLIC-meldnummer telt de
   hoogste leveringsvolgnummer; dezelfde levering die zowel uitgepakt als
   gezipt in de map staat, wordt één keer ingelezen. De IMKL 2.0-GML wordt
   vertaald naar netten (kabels/leidingen per thema), omhullingen
   (mantelbuis, kabelbed), punten (appurtenances, mangaten, technische
   gebouwen), detailinfo (profielschetsen, PDF) en de leveringscontour.
   Het resultaat per levering wordt gecachet in ``data/klic/cache/`` (een
   levering verandert niet), zodat opnieuw opstarten seconden kost.
2. **Losse GeoJSON-bestanden** in ``data/klic/`` (oude, handmatige route:
   per thema geëxporteerd naar EPSG:28992).

De netten tellen mee als weging (``klic_netdichtheid``), in de
kruisingsinformatie van boringen en in de maatvoeringstoets. Zonder
koppeling en bestanden blijft alles op "onbekend — KLIC niet gekoppeld".
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from pathlib import Path, PurePosixPath

from shapely import STRtree
from shapely.geometry import box, shape

KLIC_DIR = Path(__file__).resolve().parent.parent / "data" / "klic"


def _staatmap() -> Path:
    """Waar koppeling en cache staan: data/klic/, of — als de app daar niet
    mag schrijven (andere gebruiker dan de eigenaar van data/) — in de
    thuismap van de gebruiker onder wie de server draait."""
    try:
        KLIC_DIR.mkdir(parents=True, exist_ok=True)
        if os.access(KLIC_DIR, os.W_OK):
            return KLIC_DIR
    except OSError:
        pass
    return Path.home() / ".infraengine" / "klic"


STAAT_DIR = _staatmap()
KOPPELING = STAAT_DIR / "koppeling.json"
CACHE_DIR = STAAT_DIR / "cache"
# ophogen bij een wijziging in de IMKL-vertaling: oude caches worden dan
# opnieuw uit de levering opgebouwd
PARSER_VERSIE = 3

_GML = "{http://www.opengis.net/gml/3.2}"
_HREF = "{http://www.w3.org/1999/xlink}href"
_GI_RE = re.compile(r"GI_gebiedsinformatielevering_([0-9A-Za-z]+?)_(\d+)[^/]*\.xml(\.gz)?$", re.I)
_BRONHOUDER_RE = re.compile(r"nl\.imkl-([A-Z]{2}\d{4})")

# IMKL-objecttypen met een eigen laagklasse (overige lijnen = net, punten = punt)
_OMHULLING = {"Mantelbuis", "Kabelbed"}
_CONTOUR = {"Orientatiepolygoon", "Graafpolygoon", "Informatiepolygoon"}

# leesbare namen voor de popup en de legenda
SOORT_LABEL = {
    "Elektriciteitskabel": "elektriciteitskabel",
    "Telecommunicatiekabel": "telecommunicatiekabel",
    "Waterleiding": "waterleiding",
    "Rioolleiding": "rioolleiding",
    "OlieGasChemicalienPijpleiding": "gas-/olie-/chemicaliënleiding",
    "Thermischepijpleiding": "warmteleiding",
    "Duct": "kabelbuis (duct)",
    "Overig": "overige kabel/leiding",
    "UtilityLink": "kabel/leiding (type onbekend)",
    "Mantelbuis": "mantelbuis",
    "Kabelbed": "kabelbed",
    "Appurtenance": "netcomponent",
    "Mangat": "mangat/put",
    "TechnischGebouw": "technisch gebouw/station",
    "ExtraDetailinfo": "detailinfo",
    "AanduidingEisVoorzorgsmaatregel": "eis voorzorgsmaatregel (EV)",
    "Kast": "kast", "Mast": "mast",
    "DiepteTovMaaiveld": "diepte t.o.v. maaiveld", "DiepteNAP": "diepte t.o.v. NAP",
}

_lock = threading.Lock()
_cache: dict = {"stempel": None, "geoms": []}       # losse GeoJSON-bestanden
_state: dict = {
    "leveringen": [],   # samenvatting per levering (zonder features)
    "bronnen": {},      # (klic, volgnr) → bronbeschrijving (map/zip/zip-in-zip)
    "fouten": [],
}
_scan = {"bezig": False, "stap": "", "gedaan": 0, "totaal": 0,
         "klaar_op": None, "duur_s": None}
_scan_lock = threading.Lock()


class KlicError(Exception):
    pass


# ---------------------------------------------------------------------------
# koppeling (welke hoofdmappen)
# ---------------------------------------------------------------------------

def mappen() -> list:
    try:
        return list(json.loads(KOPPELING.read_text()).get("mappen", []))
    except Exception:
        return []


def _schrijf_mappen(lijst: list) -> None:
    KOPPELING.parent.mkdir(parents=True, exist_ok=True)
    KOPPELING.write_text(json.dumps({"mappen": lijst}, indent=2, ensure_ascii=False))


def koppel(pad: str) -> list:
    p = Path(os.path.expanduser((pad or "").strip().strip('"'))).resolve()
    if not str(pad or "").strip():
        raise KlicError("Geef het pad van de hoofdmap op.")
    if not p.is_dir():
        raise KlicError(f"Map niet gevonden (op de server): {p}")
    if not os.access(p, os.R_OK | os.X_OK):
        raise KlicError(f"Geen leesrechten op deze map voor de gebruiker onder "
                        f"wie InfraEngine draait: {p}")
    lijst = mappen()
    if str(p) not in lijst:
        lijst.append(str(p))
        _schrijf_mappen(lijst)
    start_scan()
    return lijst


def ontkoppel(pad: str) -> list:
    lijst = [m for m in mappen() if m != pad]
    _schrijf_mappen(lijst)
    start_scan()
    return lijst


def kies_map() -> str | None:
    """Systeemdialoog 'map kiezen' op de server (lokale macOS-installatie).

    Geeft None bij annuleren; KlicError als er geen dialoog kan (Docker,
    Linux-server): dan voert de gebruiker het pad zelf in."""
    if sys.platform != "darwin":
        raise KlicError("Mapkeuze-dialoog alleen bij een lokale installatie "
                        "(macOS); voer het pad van de map in.")
    script = ('POSIX path of (choose folder with prompt '
              '"Kies de hoofdmap met KLIC-leveringen")')
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=600)
    except Exception as e:
        raise KlicError(f"Mapkeuze-dialoog niet beschikbaar: {e}")
    if r.returncode != 0:
        if "-128" in r.stderr:   # gebruiker annuleerde
            return None
        raise KlicError("Mapkeuze-dialoog niet beschikbaar; voer het pad in.")
    return r.stdout.strip().rstrip("/") or None


# ---------------------------------------------------------------------------
# leveringen vinden
# ---------------------------------------------------------------------------

def _sleutel(naam: str):
    m = _GI_RE.search(naam)
    return (m.group(1), int(m.group(2))) if m else None


def _ontdek(map_pad: Path, fouten: list) -> dict:
    """(klic, volgnr) → bron. Een bron is een uitgepakte map, een zip of een
    zip in een zip; uitgepakt gaat voor gezipt (sneller, zelfde inhoud)."""
    gevonden: dict = {}
    zips: list = []
    for root, dirs, files in os.walk(
            map_pad, onerror=lambda e: fouten.append(f"geen toegang: {e.filename}")):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__MACOSX"))
                         and d != "upload-delen")
        for f in sorted(files):
            if f.startswith("."):
                continue
            pad = Path(root) / f
            k = _sleutel(f)
            if k and f.lower().endswith((".xml", ".xml.gz")):
                gevonden.setdefault(k, {"soort": "map", "xml": str(pad),
                                        "basis": str(pad.parent)})
            elif f.lower().endswith(".zip"):
                zips.append(pad)
    # eerst alle zips die zelf een levering zijn, daarna pas de zips in zips
    # (verzamelzip): dezelfde levering komt dan uit de kleinste bron
    inhoud: dict = {}
    for zpad in zips:
        try:
            with zipfile.ZipFile(zpad) as z:
                inhoud[zpad] = z.namelist()
        except Exception as e:
            fouten.append(f"{zpad.name}: {e}")
            continue
        for n in inhoud[zpad]:
            k = _sleutel(n)
            if k and k not in gevonden:
                gevonden[k] = {"soort": "zip", "zip": str(zpad), "xml": n,
                               "basis": str(PurePosixPath(n).parent)}
    for zpad, namen in inhoud.items():
        try:
            with zipfile.ZipFile(zpad) as z:
                # alleen openen als er een levering in kan zitten die nog
                # niet gevonden is (naam Levering_<klicnr>_<volgnr>.zip)
                for n in namen:
                    if not n.lower().endswith(".zip"):
                        continue
                    m = re.search(r"([0-9A-Za-z]+)_(\d+)\.zip$", n)
                    if m and (m.group(1), int(m.group(2))) in gevonden:
                        continue
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(n))) as zi:
                            for ni in zi.namelist():
                                k = _sleutel(ni)
                                if k and k not in gevonden:
                                    gevonden[k] = {"soort": "zip-in-zip", "zip": str(zpad),
                                                   "binnen": n, "xml": ni,
                                                   "basis": str(PurePosixPath(ni).parent)}
                    except Exception as e:
                        fouten.append(f"{zpad.name}/{n}: {e}")
        except Exception as e:
            fouten.append(f"{zpad.name}: {e}")
    return gevonden


def _open_bron(bron: dict, relatief: str | None = None):
    """Bestandsobject voor de GI-xml (relatief=None) of een bestand dat de
    levering zelf noemt (relatief pad t.o.v. de leveringsmap)."""
    if bron["soort"] == "map":
        pad = bron["xml"] if relatief is None else str(Path(bron["basis"]) / relatief)
        return gzip.open(pad, "rb") if pad.lower().endswith(".gz") else open(pad, "rb")
    naam = bron["xml"] if relatief is None else str(PurePosixPath(bron["basis"]) / relatief)
    z = zipfile.ZipFile(bron["zip"])
    if bron["soort"] == "zip-in-zip":
        z = zipfile.ZipFile(io.BytesIO(z.read(bron["binnen"])))
    # streamen: een GI-xml is uitgepakt al gauw honderden MB's
    fh = z.open(naam)
    return gzip.GzipFile(fileobj=fh) if naam.lower().endswith(".gz") else fh


# ---------------------------------------------------------------------------
# IMKL 2.0 (GML) → features
# ---------------------------------------------------------------------------

def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _coords(tekst: str, dim: int) -> list:
    v = [float(x) for x in tekst.split()]
    return [[round(v[i], 3), round(v[i + 1], 3)] for i in range(0, len(v) - dim + 1, dim)]


def _dim(el, standaard: int = 2) -> int:
    try:
        return int(el.get("srsDimension") or standaard)
    except ValueError:
        return standaard


def _geometrie(el) -> dict | None:
    """gml-geometrie → GeoJSON-dict (alleen 2D, RD)."""
    t = _ln(el.tag)
    dim = _dim(el)
    if t == "Point":
        pos = el.find(f"{_GML}pos")
        return {"type": "Point", "coordinates": _coords(pos.text, _dim(pos, dim))[0]} \
            if pos is not None and pos.text else None
    if t in ("LineString", "LineStringSegment"):
        pl = el.find(f"{_GML}posList")
        if pl is not None and pl.text:
            c = _coords(pl.text, _dim(pl, dim))
        else:
            c = [_coords(p.text, _dim(p, dim))[0] for p in el.findall(f"{_GML}pos")]
        return {"type": "LineString", "coordinates": c} if len(c) >= 2 else None
    if t == "Curve":
        c: list = []
        for seg in el.iter():
            if _ln(seg.tag) in ("LineStringSegment", "ArcString", "Arc", "GeodesicString"):
                for pl in seg.iter(f"{_GML}posList"):
                    pts = _coords(pl.text, _dim(pl, dim))
                    c.extend(pts[1:] if c and pts and pts[0] == c[-1] else pts)
                for p in seg.findall(f"{_GML}pos"):
                    c.append(_coords(p.text, _dim(p, dim))[0])
        return {"type": "LineString", "coordinates": c} if len(c) >= 2 else None
    if t == "MultiCurve":
        lijnen = [g["coordinates"] for m in el for g0 in m
                  if (g := _geometrie(g0)) and g["type"] == "LineString"]
        return {"type": "MultiLineString", "coordinates": lijnen} if lijnen else None
    if t in ("Polygon", "PolygonPatch"):
        ringen = []
        for deel in el:
            if _ln(deel.tag) in ("exterior", "interior"):
                for pl in deel.iter(f"{_GML}posList"):
                    ringen.append(_coords(pl.text, _dim(pl, dim)))
        return {"type": "Polygon", "coordinates": ringen} if ringen else None
    if t in ("Surface", "MultiSurface"):
        polys = []
        for sub in el.iter():
            if _ln(sub.tag) in ("Polygon", "PolygonPatch") and sub is not el:
                g = _geometrie(sub)
                if g:
                    polys.append(g["coordinates"])
        if not polys:
            return None
        return ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                else {"type": "MultiPolygon", "coordinates": polys})
    return None


def _eerste_geometrie(el) -> dict | None:
    for kind in el:
        if _ln(kind.tag) in ("centrelineGeometry", "geometry", "geometrie", "ligging"):
            for g in kind:
                geo = _geometrie(g)
                if geo:
                    return geo
    return None


def _waarde(kind) -> str | None:
    href = kind.get(_HREF)
    if href:
        return href.rstrip("/").rsplit("/", 1)[-1] or None
    tekst = (kind.text or "").strip()
    if not tekst:
        return None
    uom = kind.get("uom")
    if uom:
        eenheid = uom.rsplit(":", 1)[-1]
        return f"{tekst} {eenheid}"
    return tekst


# eigenschappen die in de popup komen (lokale naam → label)
_ATTR = {
    "currentStatus": "status", "verticalPosition": "ligging",
    "operatingVoltage": "spanning", "nominalVoltage": "nominale spanning",
    "pipeDiameter": "diameter", "ductWidth": "breedte", "kabelDiameter": "diameter",
    "pressure": "druk", "oilGasChemicalsProductType": "product",
    "waterType": "watersoort", "sewerWaterType": "rioolwater",
    "buismateriaalType": "materiaal", "telecommunicationsCableMaterialType": "materiaal",
    "appurtenanceType": "type", "geoNauwkeurigheidXY": "nauwkeurigheid XY",
    "aantalKabelsLeidingen": "aantal kabels/leidingen", "label": "label",
    "bovengrondsZichtbaar": "bovengronds zichtbaar", "extraInfoType": "soort info",
    "dieptePeil": "diepte", "diepteAangrijpingspunt": "diepte t.o.v.",
}


def _lees_levering(fh, klic: str, volgnr: int) -> dict:
    netten: dict = {}       # Utiliteitsnet-id → {thema, diepte}
    beheerders: dict = {}   # bronhoudercode → naam
    links: dict = {}        # UtilityLink-id → geometrie
    elementen: list = []    # objecten die naar links verwijzen
    overig: list = []       # objecten met eigen geometrie
    bijlagen: list = []
    meta: dict = {"klic": klic, "volgnr": volgnr}
    contour = None

    for _, el in ET.iterparse(fh, events=("end",)):
        if _ln(el.tag) != "featureMember":
            continue
        for f in el:
            soort = _ln(f.tag)
            fid = f.get(f"{_GML}id") or ""
            m = _BRONHOUDER_RE.match(fid)
            bron = m.group(1) if m else ""
            if soort == "UtilityLink":
                g = _eerste_geometrie(f)
                if g:
                    links[fid] = g
                continue
            if soort == "Utiliteitsnet":
                d = {"thema": None, "diepte": None}
                for k in f:
                    if _ln(k.tag) == "thema":
                        d["thema"] = _waarde(k)
                    elif _ln(k.tag) == "standaardDieptelegging":
                        d["diepte"] = _waarde(k)
                netten[fid] = d
                continue
            if soort == "Beheerder":
                code = naam = None
                for k in f.iter():
                    if _ln(k.tag) == "bronhoudercode":
                        code = (k.text or "").strip()
                    elif _ln(k.tag) == "naam" and naam is None:
                        naam = (k.text or "").strip()
                if code:
                    beheerders[code] = naam or code
                continue
            if soort == "GebiedsinformatieAanvraag":
                for k in f:
                    n = _ln(k.tag)
                    if n in ("referentie", "ordernummer", "klicMeldnummer"):
                        meta[n] = (k.text or "").strip()
                    elif n == "aanvraagSoort":
                        meta["soort"] = _waarde(k)
                    elif n == "aanvraagDatum":
                        meta["datum"] = (k.text or "")[:10]
                    elif n == "soortWerkzaamheden":
                        meta.setdefault("werkzaamheden", []).append(_waarde(k))
                    elif n == "locatieWerkzaamheden":
                        delen = {_ln(a.tag): (a.text or "").strip() for a in k.iter()}
                        meta["locatie"] = " ".join(x for x in (
                            delen.get("openbareRuimteNaam"), delen.get("huisnummer"),
                            delen.get("woonplaatsNaam")) if x)
                continue
            if soort in _CONTOUR:
                g = _eerste_geometrie(f)
                if g and contour is None:
                    contour = g
                continue
            if soort == "Bijlage":
                d = {"bron": bron}
                for k in f:
                    if _ln(k.tag) == "bestandLocatie":
                        d["pad"] = (k.text or "").strip()
                    elif _ln(k.tag) == "bijlageType":
                        d["type"] = _waarde(k)
                if d.get("pad"):
                    bijlagen.append(d)
                continue
            if soort in ("Annotatie", "Maatvoering", "EigenTopografie", "Belang",
                         "Belanghebbende", "GebiedsinformatieLevering",
                         "ExtraGeometrie"):
                continue
            # kabels, leidingen, omhullingen, punten, detailinfo
            d = {"id": fid, "s": soort, "b": bron, "net": None, "links": [], "a": {}}
            for k in f:
                n = _ln(k.tag)
                if n == "inNetwork":
                    d["net"] = k.get(_HREF)
                elif n == "link":
                    if k.get(_HREF):
                        d["links"].append(k.get(_HREF))
                elif n == "bestandLocatie":
                    d["pad"] = (k.text or "").strip()
                elif n in _ATTR:
                    w = _waarde(k)
                    if w and w != "Unknown":
                        d["a"][_ATTR[n]] = w
            if d["links"]:
                elementen.append(d)
            else:
                g = _eerste_geometrie(f)
                if g:
                    d["g"] = g
                    overig.append(d)
        el.clear()

    features = []
    gebruikt: set = set()

    def thema_van(d):
        n = netten.get(d.get("net") or "", {})
        return n.get("thema"), n.get("diepte")

    def nieuw(d, klasse, geom):
        thema, diepte = thema_van(d)
        a = dict(d["a"])
        if diepte and klasse in ("net", "omhulling"):
            a["standaard diepteligging"] = diepte
        ft = {"id": d["id"], "k": klasse, "s": d["s"], "t": thema or "overig",
              "b": d["b"], "g": geom, "a": a}
        if d.get("pad"):
            ft["pad"] = d["pad"]
        return ft

    for d in elementen:
        lijnen = []
        for lid in d["links"]:
            g = links.get(lid)
            if not g:
                continue
            gebruikt.add(lid)
            if g["type"] == "LineString":
                lijnen.append(g["coordinates"])
            elif g["type"] == "MultiLineString":
                lijnen.extend(g["coordinates"])
        if not lijnen:
            continue
        geom = ({"type": "LineString", "coordinates": lijnen[0]} if len(lijnen) == 1
                else {"type": "MultiLineString", "coordinates": lijnen})
        klasse = "omhulling" if d["s"] in _OMHULLING else "net"
        features.append(nieuw(d, klasse, geom))
    # links waar geen kabel/leiding naar verwijst (onvolledige levering)
    for lid, g in links.items():
        if lid in gebruikt:
            continue
        m = _BRONHOUDER_RE.match(lid)
        features.append({"id": lid, "k": "net", "s": "UtilityLink", "t": "overig",
                         "b": m.group(1) if m else "", "g": g, "a": {}})
    for d in overig:
        if d["s"] == "AanduidingEisVoorzorgsmaatregel":
            klasse = "ev"
        elif d["s"] == "ExtraDetailinfo":
            klasse = "detail"
        elif d["g"]["type"] == "Point":
            klasse = "punt"
        elif d["s"] in _OMHULLING:
            klasse = "omhulling"
        elif d["g"]["type"] in ("LineString", "MultiLineString"):
            klasse = "net"
        else:
            klasse = "detail"
        features.append(nieuw(d, klasse, d["g"]))

    for ft in features:
        ft["klic"] = klic
    tellingen: dict = {}
    for ft in features:
        tellingen[ft["k"]] = tellingen.get(ft["k"], 0) + 1
    return {
        "versie": PARSER_VERSIE, "meta": meta, "contour": contour,
        "beheerders": beheerders, "bijlagen": bijlagen,
        "tellingen": tellingen, "features": features,
    }


def _cache_pad(klic: str, volgnr: int) -> Path:
    return CACHE_DIR / f"{klic}_{volgnr}.json.gz"


def _root_documenten(bron: dict) -> list:
    """PDF's in de leveringsmap zelf (niet in bronnen/), bv. LI_<klic>.pdf."""
    try:
        if bron["soort"] == "map":
            return sorted(p.name for p in Path(bron["basis"]).glob("*.pdf"))
        z = zipfile.ZipFile(bron["zip"])
        if bron["soort"] == "zip-in-zip":
            z = zipfile.ZipFile(io.BytesIO(z.read(bron["binnen"])))
        basis = PurePosixPath(bron["basis"])
        return sorted(PurePosixPath(n).name for n in z.namelist()
                      if PurePosixPath(n).parent == basis and n.lower().endswith(".pdf"))
    except Exception:
        return []


def _lees_cache(sleutel: tuple) -> dict | None:
    cp = _cache_pad(*sleutel)
    if not cp.is_file():
        return None
    try:
        with gzip.open(cp, "rt") as fh:
            d = json.load(fh)
        return d if d.get("versie") == PARSER_VERSIE else None
    except Exception:
        return None


def _schrijf_cache(sleutel: tuple, d: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_pad(*sleutel)
    tmp = cp.with_name(cp.name + ".tmp")
    with gzip.open(tmp, "wt", compresslevel=5) as fh:
        json.dump(d, fh, separators=(",", ":"))
    tmp.replace(cp)


def _laad_levering(sleutel: tuple, bron: dict) -> dict:
    d = _lees_cache(sleutel)
    if d is not None:
        return d
    with _open_bron(bron) as fh:
        d = _lees_levering(fh, *sleutel)
    # documenten die de levering zelf meebrengt (leveringsinformatie LI_*.pdf)
    d["documenten"] = _root_documenten(bron)
    _schrijf_cache(sleutel, d)
    return d


# ---------------------------------------------------------------------------
# features per levering (lui geladen, begrensd geheugen)
# ---------------------------------------------------------------------------
# Alleen de samenvatting per levering staat permanent in het geheugen; de
# objecten zelf komen per levering uit de cache, met een kleine LRU. Zo blijft
# het geheugen begrensd, ook met tientallen leveringen (2 GB-instance).

LRU_LEVERINGEN = 8
_lru: "OrderedDict[tuple, dict]" = OrderedDict()
_lru_lock = threading.Lock()


def _levering_objecten(sleutel: tuple) -> dict | None:
    """{'features', 'geoms', 'tree', 'netten'} van één levering."""
    with _lru_lock:
        if sleutel in _lru:
            _lru.move_to_end(sleutel)
            return _lru[sleutel]
    d = _lees_cache(sleutel)
    if d is None:
        with _lock:
            bron = _state["bronnen"].get(sleutel)
        if not bron:
            return None
        d = _laad_levering(sleutel, bron)
    feats, geoms, netten = [], [], []
    for f in d["features"]:
        try:
            g = shape(f["g"])
        except Exception:
            continue
        if g.is_empty:
            continue
        feats.append(f)
        geoms.append(g)
        if f["k"] == "net":
            netten.append((g, {
                "thema": f["t"], "soort": SOORT_LABEL.get(f["s"], f["s"]),
                "beheerder": f["b"], "klic": f["klic"], "bron": f"KLIC {f['klic']}",
            }))
    obj = {"features": feats, "geoms": geoms, "netten": netten,
           "tree": STRtree(geoms) if geoms else None}
    with _lru_lock:
        _lru[sleutel] = obj
        while len(_lru) > LRU_LEVERINGEN:
            _lru.popitem(last=False)
    return obj


def _leveringen_in(bbox: tuple) -> list:
    """Sleutels van de leveringen waarvan de omhullende de bbox raakt."""
    xmin, ymin, xmax, ymax = bbox
    with _lock:
        lev = list(_state["leveringen"])
    uit = []
    for l in lev:
        b = l.get("bbox")
        if b and not (b[2] < xmin or b[0] > xmax or b[3] < ymin or b[1] > ymax):
            uit.append((l["klic"], l["volgnr"]))
    return uit


# ---------------------------------------------------------------------------
# scannen (achtergrondthread) en index
# ---------------------------------------------------------------------------

def _bestaande_documenten(bron: dict, paden: set) -> set:
    """Bij een uitgepakte (of geüploade) levering staan niet alle documenten
    er per se: alleen wat er werkelijk is, is op te vragen."""
    if bron["soort"] != "map":
        return paden
    basis = Path(bron["basis"])
    return {p for p in paden if (basis / p).is_file()}


def _omhullende(coords) -> list | None:
    """[xmin, ymin, xmax, ymax] van (geneste) GeoJSON-coördinaten."""
    xs, ys = [], []

    def loop(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                loop(x)
    loop(coords)
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _scan_uitvoeren() -> None:
    t0 = time.time()
    fouten: list = []
    _scan.update(stap="mappen doorzoeken", gedaan=0, totaal=0)
    bronnen: dict = {}
    # geüploade leveringen (productie) eerst, dan de gekoppelde mappen
    if UPLOAD_DIR.is_dir():
        for k, b in _ontdek(UPLOAD_DIR, fouten).items():
            b["upload"] = True
            bronnen.setdefault(k, b)
    for m in mappen():
        p = Path(m)
        if not p.is_dir():
            fouten.append(f"gekoppelde map niet gevonden: {m}")
            continue
        for k, b in _ontdek(p, fouten).items():
            bronnen.setdefault(k, b)
    # per KLIC-meldnummer alleen de hoogste leveringsvolgnummer
    hoogste: dict = {}
    for (kn, vn) in bronnen:
        hoogste[kn] = max(hoogste.get(kn, 0), vn)
    sleutels = sorted(k for k in bronnen if k[1] == hoogste[k[0]])
    _scan.update(stap="leveringen inlezen", totaal=len(sleutels))

    leveringen = []
    for i, k in enumerate(sleutels):
        _scan.update(gedaan=i, stap=f"levering {k[0]} inlezen")
        bron = bronnen[k]
        try:
            d = _laad_levering(k, bron)
        except Exception as e:
            fouten.append(f"levering {k[0]}_{k[1]}: {e}")
            continue
        if bron["soort"] == "map":   # geüpload: documenten kunnen later bijkomen
            d["documenten"] = _root_documenten(bron)
        genoemd = {b["pad"] for b in d.get("bijlagen", [])}
        genoemd |= {f["pad"] for f in d["features"] if f.get("pad")}
        genoemd |= set(d.get("documenten", []))
        bron["toegestaan"] = _bestaande_documenten(bron, genoemd)
        bbox = _omhullende([f["g"]["coordinates"] for f in d["features"]]
                           + ([d["contour"]["coordinates"]] if d.get("contour") else []))
        leveringen.append({
            "klic": k[0], "volgnr": k[1], **{x: d["meta"].get(x) for x in (
                "referentie", "soort", "datum", "locatie", "ordernummer")},
            "contour": d.get("contour"), "bbox": bbox,
            "tellingen": d.get("tellingen", {}),
            "beheerders": d.get("beheerders", {}),
            "bijlagen": [dict(b, aanwezig=b["pad"] in bron["toegestaan"])
                         for b in d.get("bijlagen", [])],
            "documenten": [p for p in d.get("documenten", []) if p in bron["toegestaan"]],
            "bron": (bron.get("zip") or bron.get("basis")),
            "bron_soort": "upload" if bron.get("upload") else bron["soort"],
        })
        del d  # objecten niet vasthouden: die laadt _levering_objecten per levering
    with _lock:
        _state.update(leveringen=leveringen, bronnen=bronnen, fouten=fouten)
    with _lru_lock:
        _lru.clear()
    _scan.update(klaar_op=time.strftime("%Y-%m-%d %H:%M"),
                 duur_s=round(time.time() - t0, 1), stap="")


def _scan_draad() -> None:
    try:
        while True:
            _scan["opnieuw"] = False
            try:
                _scan_uitvoeren()
            except Exception as e:  # nooit de app laten struikelen
                with _lock:
                    _state["fouten"] = [f"KLIC-scan mislukt: {e}"]
            if not _scan.get("opnieuw"):
                break
    finally:
        _scan["bezig"] = False


def start_scan() -> None:
    """(Her)scan van de gekoppelde mappen op de achtergrond. Loopt er al een
    scan, dan volgt er direct na afloop nog één (nieuwe koppeling)."""
    with _scan_lock:
        if _scan["bezig"]:
            _scan["opnieuw"] = True
            return
        _scan["bezig"] = True
    threading.Thread(target=_scan_draad, daemon=True, name="klic-scan").start()


# ---------------------------------------------------------------------------
# uploaden vanuit de browser (productie: de server ziet geen lokale mappen)
# ---------------------------------------------------------------------------
# Per levering komt in uploads/<klic>_<volgnr>/ alleen wat de app gebruikt:
# de GI-xml (gzip) en de documenten die aan kaartobjecten hangen
# (profielschetsen/detailinfo, EV-documenten) plus de leveringsinformatie.
# Algemene brieven van netbeheerders en de achtergrondkaart blijven lokaal.
# Zips worden op de server uitgepakt en daarna verwijderd.

UPLOAD_DIR = STAAT_DIR / "uploads"
PART_DIR = STAAT_DIR / "upload-delen"
VRIJ_MARGE = 50 * 1024 * 1024   # zoveel moet er na een upload vrij blijven


def _veilige_naam(naam: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", PurePosixPath(naam).name)[:180] or "bestand"


def opslag() -> dict:
    """Schijfgebruik van de KLIC-uploads en vrije ruimte (MB)."""
    gebruikt = 0
    for basis in (UPLOAD_DIR, CACHE_DIR):
        if basis.is_dir():
            gebruikt += sum(p.stat().st_size for p in basis.rglob("*") if p.is_file())
    try:
        vrij = shutil.disk_usage(STAAT_DIR if STAAT_DIR.exists() else STAAT_DIR.parent).free
    except OSError:
        vrij = None
    return {"gebruikt_mb": round(gebruikt / 1e6, 1),
            "vrij_mb": round(vrij / 1e6) if vrij is not None else None}


def ontvang_deel(upload_id: str, naam: str, offset: int, totaal: int,
                 data: bytes) -> Path | None:
    """Eén stuk van een upload wegschrijven; geeft het pad van het complete
    bestand terug zodra het laatste stuk binnen is."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", upload_id or ""):
        raise KlicError("Ongeldige upload-id.")
    PART_DIR.mkdir(parents=True, exist_ok=True)
    deel = PART_DIR / f"{upload_id}.part"
    if offset == 0:
        try:
            vrij = shutil.disk_usage(PART_DIR).free
        except OSError:
            vrij = None
        if vrij is not None and totaal + VRIJ_MARGE > vrij:
            raise KlicError(f"Onvoldoende schijfruimte op de server voor {naam} "
                            f"({totaal / 1e6:.0f} MB; vrij {vrij / 1e6:.0f} MB). "
                            "Verwijder oude leveringen of vergroot de schijf.")
        deel.write_bytes(b"")
    elif not deel.is_file() or deel.stat().st_size != offset:
        raise KlicError("Upload onderbroken; begin opnieuw.")
    with open(deel, "ab") as fh:
        fh.write(data)
    if offset + len(data) < totaal:
        return None
    if deel.stat().st_size != totaal:
        deel.unlink(missing_ok=True)
        raise KlicError(f"Upload van {naam} onvolledig; probeer opnieuw.")
    return deel


def _aanwezig(sleutel: tuple) -> bool:
    """Is deze levering (of een nieuwere van hetzelfde meldnummer) al ingelezen
    of geüpload? De uploadmap telt mee: een scan kan nog lopen."""
    with _lock:
        if any(k[0] == sleutel[0] and k[1] >= sleutel[1] for k in _state["bronnen"]):
            return True
    if UPLOAD_DIR.is_dir():
        for p in UPLOAD_DIR.glob(f"{sleutel[0]}_*/GI_gebiedsinformatielevering_*.xml.gz"):
            k = _sleutel(p.name)
            if k and k[0] == sleutel[0] and k[1] >= sleutel[1]:
                return True
    return False


def _nodige_documenten(d: dict, profielschetsen: bool = True) -> list:
    """Documenten die op productie bewaard worden: die aan kaartobjecten
    hangen (profielschets, detailinfo, EV) en de leveringsinformatie. Zonder
    profielschetsen alleen de (kleine) leveringsinformatie."""
    paden = {f["pad"] for f in d["features"] if f.get("pad")} if profielschetsen else set()
    paden.add(f"LI_{d['meta']['klic']}_{d['meta']['volgnr']}.pdf")
    return sorted(p for p in paden if ".." not in PurePosixPath(p).parts)


def _bewaar_gi(sleutel: tuple, bron_fh, gz: bool) -> tuple:
    """GI-xml (stream) als gzip in uploads/<klic>_<volgnr>/ en inlezen."""
    doel = UPLOAD_DIR / f"{sleutel[0]}_{sleutel[1]}"
    doel.mkdir(parents=True, exist_ok=True)
    xml = doel / f"GI_gebiedsinformatielevering_{sleutel[0]}_{sleutel[1]}.xml.gz"
    tmp = xml.with_name(xml.name + ".tmp")
    if gz:
        with open(tmp, "wb") as uit:
            shutil.copyfileobj(bron_fh, uit, 1 << 20)
    else:
        with gzip.open(tmp, "wb", compresslevel=6) as uit:
            shutil.copyfileobj(bron_fh, uit, 1 << 20)
    tmp.replace(xml)
    bron = {"soort": "map", "xml": str(xml), "basis": str(doel)}
    with _lru_lock:
        _lru.pop(sleutel, None)
    _cache_pad(*sleutel).unlink(missing_ok=True)
    d = _laad_levering(sleutel, bron)
    return doel, d


def _verwerk_gi_in_zip(z: zipfile.ZipFile, naam: str, resultaat: dict,
                       documenten: bool = True) -> None:
    k = _sleutel(naam)
    if _aanwezig(k) or any(r["klic"] == k[0] and r["volgnr"] >= k[1]
                           for r in resultaat["leveringen"]):
        resultaat["overgeslagen"].append(f"{k[0]}_{k[1]} (al aanwezig)")
        return
    with z.open(naam) as fh:
        doel, d = _bewaar_gi(k, fh, naam.lower().endswith(".gz"))
    basis = PurePosixPath(naam).parent
    namen = set(z.namelist())
    ontbreekt = []
    for pad in _nodige_documenten(d, documenten):
        lid = str(basis / pad) if str(basis) != "." else pad
        if lid not in namen:
            ontbreekt.append(pad)
            continue
        uit = doel / pad
        uit.parent.mkdir(parents=True, exist_ok=True)
        with z.open(lid) as src, open(uit, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    resultaat["leveringen"].append({"klic": k[0], "volgnr": k[1],
                                    "referentie": d["meta"].get("referentie"),
                                    "nodig": ontbreekt})


def verwerk_upload(pad: Path, naam: str, documenten: bool = True) -> dict:
    """Complete upload verwerken: een zip (levering of verzamelzip) of een
    losse GI-xml(.gz). Geeft per levering de documenten die nog ontbreken
    (bij een losse xml stuurt de browser die daarna na)."""
    resultaat: dict = {"leveringen": [], "overgeslagen": []}
    try:
        laag = naam.lower()
        if laag.endswith(".zip"):
            try:
                z = zipfile.ZipFile(pad)
            except zipfile.BadZipFile:
                raise KlicError(f"{naam} is geen geldige zip.")
            with z:
                namen = z.namelist()
                for n in namen:
                    if _sleutel(n):
                        _verwerk_gi_in_zip(z, n, resultaat, documenten)
                for n in namen:   # zips in de zip (verzamelzip)
                    if not n.lower().endswith(".zip"):
                        continue
                    m = re.search(r"([0-9A-Za-z]+)_(\d+)\.zip$", n)
                    if m and _aanwezig((m.group(1), int(m.group(2)))):
                        resultaat["overgeslagen"].append(f"{m.group(1)}_{m.group(2)} (al aanwezig)")
                        continue
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(n))) as zi:
                            for ni in zi.namelist():
                                if _sleutel(ni):
                                    _verwerk_gi_in_zip(zi, ni, resultaat, documenten)
                    except zipfile.BadZipFile:
                        resultaat["overgeslagen"].append(f"{n} (geen geldige zip)")
            if not resultaat["leveringen"] and not resultaat["overgeslagen"]:
                raise KlicError(f"Geen KLIC-levering (GI_gebiedsinformatielevering_*.xml) "
                                f"gevonden in {naam}.")
        elif _sleutel(naam):
            k = _sleutel(naam)
            with open(pad, "rb") as fh:
                doel, d = _bewaar_gi(k, fh, laag.endswith(".gz"))
            resultaat["leveringen"].append({
                "klic": k[0], "volgnr": k[1], "referentie": d["meta"].get("referentie"),
                "nodig": [p for p in _nodige_documenten(d, documenten)
                          if not (doel / p).is_file()]})
        else:
            raise KlicError(f"{naam}: geen KLIC-levering (verwacht een zip of "
                            "GI_gebiedsinformatielevering_*.xml).")
    finally:
        pad.unlink(missing_ok=True)
    start_scan()
    return resultaat


def bewaar_document(klic: str, volgnr: int, relpad: str, pad: Path) -> None:
    """Document (PDF) bij een geüploade levering zetten — alleen documenten
    die de levering zelf aan een kaartobject koppelt."""
    try:
        d = _lees_cache((klic, volgnr))
        if d is None:
            raise KlicError(f"Levering {klic}_{volgnr} is (nog) niet geüpload.")
        if relpad not in _nodige_documenten(d):
            raise KlicError(f"{relpad} is geen document dat de app gebruikt.")
        doel = UPLOAD_DIR / f"{klic}_{volgnr}" / relpad
        doel.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pad), doel)
    finally:
        pad.unlink(missing_ok=True)


def verwijder_levering(klic: str) -> None:
    """Geüploade levering (alle volgnummers) van de server verwijderen."""
    if not re.fullmatch(r"[0-9A-Za-z]+", klic or ""):
        raise KlicError("Ongeldig KLIC-meldnummer.")
    weg = 0
    for p in UPLOAD_DIR.glob(f"{klic}_*") if UPLOAD_DIR.is_dir() else []:
        if p.is_dir() and re.fullmatch(rf"{klic}_\d+", p.name):
            shutil.rmtree(p)
            weg += 1
    if not weg:
        raise KlicError("Deze levering is niet geüpload (gekoppelde map: ontkoppel de map).")
    for c in CACHE_DIR.glob(f"{klic}_*.json.gz") if CACHE_DIR.is_dir() else []:
        c.unlink(missing_ok=True)
    start_scan()


# ---------------------------------------------------------------------------
# losse GeoJSON-bestanden in data/klic/
# ---------------------------------------------------------------------------

def _bestanden() -> list:
    if not KLIC_DIR.is_dir():
        return []
    return sorted(p for p in KLIC_DIR.iterdir()
                  if p.suffix.lower() in (".json", ".geojson") and p != KOPPELING)


def _laad_geojson() -> list:
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
    for k in _leveringen_in(bbox):
        obj = _levering_objecten(k)
        if not obj:
            continue
        for g, p in obj["netten"]:
            b = g.bounds
            if not (b[2] < xmin or b[0] > xmax or b[3] < ymin or b[1] > ymax):
                out.append((g, p))
    for g, p in _laad_geojson():
        b = g.bounds
        if not (b[2] < xmin or b[0] > xmax or b[3] < ymin or b[1] > ymax):
            out.append((g, p))
    return out


def status() -> dict:
    geojson = _laad_geojson()
    with _lock:
        lev = _state["leveringen"]
        n_net = sum(l.get("tellingen", {}).get("net", 0) for l in lev)
        n_lev = len(lev)
        fouten = list(_state["fouten"])
    return {
        "gekoppeld": bool(geojson) or n_net > 0,
        "bestanden": [p.name for p in _bestanden()],
        "features": len(geojson) + n_net,
        "mappen": mappen(),
        "leveringen": n_lev,
        "bezig": _scan["bezig"],
        "fouten": fouten[:20],
    }


# ---------------------------------------------------------------------------
# kaartlaag en documenten
# ---------------------------------------------------------------------------

def leveringen() -> dict:
    with _lock:
        lev = list(_state["leveringen"])
    return {
        **status(),
        "scan": {k: _scan.get(k) for k in ("stap", "gedaan", "totaal", "klaar_op", "duur_s")},
        "leveringen": lev,
        "opslag": opslag(),
        # mapkeuze-dialoog alleen bij een lokale macOS-installatie
        "lokaal_kiezen": sys.platform == "darwin",
    }


MAX_FEATURES = 15000
MAX_LEVERINGEN_IN_BEELD = LRU_LEVERINGEN


def features_in(bbox: tuple, resolutie: float | None = None) -> dict:
    """Features in het kaartbeeld voor de kaartlaag. Lijnen worden bij grote
    resolutie (uitgezoomd) vereenvoudigd; punten pas vanaf straatniveau. Te
    veel objecten in beeld → geen objecten (geen willekeurige greep) maar
    ``te_veel``: de kaart toont dan alleen de leveringscontouren."""
    sleutels = _leveringen_in(bbox)
    punten_tonen = resolutie is None or resolutie <= 1.0
    if len(sleutels) > MAX_LEVERINGEN_IN_BEELD:
        with _lock:
            n = sum(sum(l.get("tellingen", {}).values()) for l in _state["leveringen"]
                    if (l["klic"], l["volgnr"]) in set(sleutels))
        return {"features": [], "te_veel": n, "punten": punten_tonen}
    raak = box(*bbox)
    gekozen = []
    for k in sleutels:
        obj = _levering_objecten(k)
        if not obj or obj["tree"] is None:
            continue
        for i in obj["tree"].query(raak):
            f = obj["features"][int(i)]
            if punten_tonen or f["k"] != "punt":
                gekozen.append((obj, int(i)))
        if len(gekozen) > MAX_FEATURES:
            return {"features": [], "te_veel": len(gekozen), "punten": punten_tonen}
    tol = (resolutie or 0) * 0.5
    uit = []
    for obj, i in gekozen:
        f = obj["features"][i]
        g = f["g"]
        if tol > 0.25 and g["type"] in ("LineString", "MultiLineString"):
            sg = obj["geoms"][i].simplify(tol, preserve_topology=False)
            lijnen = [sg] if sg.geom_type == "LineString" else list(getattr(sg, "geoms", []))
            lijnen = [[[round(x, 2), round(y, 2)] for x, y in l.coords] for l in lijnen]
            g = ({"type": "LineString", "coordinates": lijnen[0]} if len(lijnen) == 1
                 else {"type": "MultiLineString", "coordinates": lijnen})
        uit.append({"id": f["id"], "k": f["k"], "s": f["s"], "t": f["t"],
                    "b": f["b"], "klic": f["klic"], "g": g, "a": f["a"],
                    **({"pad": f["pad"]} if f.get("pad") else {})})
    return {"features": uit, "te_veel": 0, "punten": punten_tonen}


def bestand(klic: str, pad: str) -> tuple:
    """(bytes, bestandsnaam) van een document uit de ingelezen levering van
    dit KLIC-meldnummer. Alleen bestanden die de levering zelf noemt
    (bijlagen, profielschetsen, leveringsinformatie) en die er ook zijn."""
    with _lock:
        volgnr = next((l["volgnr"] for l in _state["leveringen"] if l["klic"] == klic), None)
        bron = _state["bronnen"].get((klic, volgnr))
    if not bron:
        raise KlicError("Levering niet (meer) gekoppeld.")
    if pad not in bron.get("toegestaan", set()) or ".." in PurePosixPath(pad).parts:
        raise KlicError("Document niet beschikbaar in deze levering (op de server "
                        "worden alleen profielschetsen en EV-documenten bewaard).")
    try:
        with _open_bron(bron, pad) as fh:
            return fh.read(), PurePosixPath(pad).name
    except (FileNotFoundError, KeyError):
        raise KlicError(f"Bestand ontbreekt in de levering: {pad}")


# bij het opstarten: gekoppelde mappen en uploads meteen (uit de cache) inlezen
if mappen() or UPLOAD_DIR.is_dir():
    start_scan()
