"""Ophalen van PDOK-datalagen (open bronnen) voor een projectgebied.

Alle geometrie in RD New (EPSG:28992). Resultaten worden per bbox gecachet
zodat herberekenen van een tracé geen nieuwe downloads vraagt.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from shapely.geometry import shape

BGT_OGC = "https://api.pdok.nl/lv/bgt/ogc/v1"
DKK_WFS = "https://service.pdok.nl/kadaster/kadastralekaart/wfs/v5_0"
BESTUUR_WFS = "https://service.pdok.nl/kadaster/bestuurlijkegebieden/wfs/v1_0"

CRS_RD = "http://www.opengis.net/def/crs/EPSG/0/28992"

# BGT-collecties die het kostenoppervlak en de kruisingsherkenning voeden.
BGT_COLLECTIONS = [
    "wegdeel",
    "ondersteunendwegdeel",
    "begroeidterreindeel",
    "onbegroeidterreindeel",
    "waterdeel",
    "ondersteunendwaterdeel",
    "pand",
    "overigbouwwerk",
    "kunstwerkdeel_vlak",
    "overbruggingsdeel",
    "tunneldeel",
    "spoor",
]

_session = requests.Session()
_session.headers["User-Agent"] = "Kabelbed-prototype/0.1"

# LRU-cache met plafond: de corridor-modus haalt per deeltraject een eigen
# bbox op — zonder plafond zou een tracé van 70 km alle data vasthouden.
from collections import OrderedDict

_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 300  # entries (~10 per deeltraject → ± 30 recente deeltrajecten)


def _cache_get(key):
    """(gevonden, waarde) — waarde kan legitiem None zijn (falend WMS-masker)."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return True, _cache[key]
    return False, None


def _cache_put(key, waarde):
    with _cache_lock:
        _cache[key] = waarde
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return waarde


def _bbox_key(bbox: tuple) -> tuple:
    return tuple(round(v, 1) for v in bbox)


def _fetch_bgt_collection(collection: str, bbox: tuple) -> list:
    """Alle features van één BGT-collectie binnen de bbox, met paging."""
    feats = []
    url = f"{BGT_OGC}/collections/{collection}/items"
    params = {
        "f": "json",
        "limit": 1000,
        "bbox": ",".join(str(v) for v in bbox),
        "bbox-crs": CRS_RD,
        "crs": CRS_RD,
    }
    while url:
        r = _session.get(url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        for f in data.get("features", []):
            props = f.get("properties", {})
            if props.get("eind_registratie"):
                continue  # historisch object
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = shape(geom)
            except Exception:
                continue
            if g.is_empty:
                continue
            feats.append((g, props))
        # paging via next-link
        url = None
        params = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                url = link["href"]
                break
    return feats


def fetch_bgt(bbox: tuple) -> dict:
    """Alle relevante BGT-collecties binnen de bbox (parallel, met cache)."""
    key = ("bgt", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    result: dict = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_fetch_bgt_collection, c, bbox): c for c in BGT_COLLECTIONS}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                result[c] = fut.result()
            except Exception as e:  # laag niet beschikbaar: doorgaan met minder data
                result[c] = []
                result.setdefault("_errors", []).append(f"{c}: {e}")
    return _cache_put(key, result)


def fetch_bomen(bbox: tuple) -> list:
    """Bomen (BGT vegetatieobject, punt) binnen de bbox.

    Plustopografie: bronhouders leveren dit optioneel aan, dus de dekking
    wisselt per gemeente — geen punten betekent niet "geen bomen".
    """
    key = ("bomen", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    feats = [(g, p) for g, p in _fetch_bgt_collection("vegetatieobject_punt", bbox)
             if (p.get("plus_type") or "boom") == "boom"]
    return _cache_put(key, feats)


# Gemeentelijke bomenregisters (open ArcGIS FeatureServers): vullen de
# BGT-plustopografie aan waar die niet gevuld is (zoals Amersfoort-centrum).
# Uitbreidbaar per gemeente; `kroon_veld` levert waar beschikbaar een
# kroondiameter (m) zodat de wortelzone per boom de kroonprojectie volgt.
# `extent` (RD) voorkomt onnodige requests buiten het werkgebied van de bron.
BOMEN_REGIONAAL = [
    {"naam": "Amersfoort: gemeentelijke en monumentale bomen",
     "url": ("https://services.arcgis.com/emS4w7iyWEQiulAb/arcgis/rest/services/"
             "amersfoort_gemeente_en_monumentale_bomen/FeatureServer/0/query"),
     "kroon_veld": "KROONDIAMETER",
     "extent": (146000, 456000, 163000, 472000)},
]


def _fetch_arcgis_punten(url: str, bbox: tuple, max_features: int = 20000) -> list:
    """Puntfeatures uit een ArcGIS FeatureServer-query (GeoJSON, RD, paging)."""
    feats = []
    offset = 0
    while True:
        params = {
            "geometry": ",".join(str(v) for v in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 28992, "outSR": 28992,
            "where": "1=1", "outFields": "*",
            "resultOffset": offset, "resultRecordCount": 2000,
            "f": "geojson",
        }
        r = _session.get(url, params=params, timeout=60)
        r.raise_for_status()
        batch = r.json().get("features", [])
        for f in batch:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = shape(geom)
            except Exception:
                continue
            if not g.is_empty:
                feats.append((g, f.get("properties", {})))
        if len(batch) < 2000 or offset >= max_features:
            break
        offset += len(batch)
    return feats


def fetch_bomen_regionaal(bbox: tuple) -> tuple:
    """Bomen uit gemeentelijke registers die de bbox raken.

    Retourneert (features, actieve bronnamen); features als (Point, props) met
    genormaliseerde 'kroon_m' waar de bron een kroondiameter levert. Een
    falende bron wordt overgeslagen (zelfde tolerantie als de zonelagen).
    """
    key = ("bomen_reg", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    feats, actief = [], []
    for bron in BOMEN_REGIONAAL:
        e = bron["extent"]
        if bbox[2] < e[0] or bbox[0] > e[2] or bbox[3] < e[1] or bbox[1] > e[3]:
            continue
        try:
            batch = _fetch_arcgis_punten(bron["url"], bbox)
        except Exception:
            continue
        actief.append(bron["naam"])
        for g, p in batch:
            kroon = p.get(bron.get("kroon_veld") or "")
            try:
                kroon = float(kroon)
            except (TypeError, ValueError):
                kroon = None
            feats.append((g, {"bron": bron["naam"], "kroon_m": kroon}))
    return _cache_put(key, (feats, actief))


def fetch_percelen(bbox: tuple) -> list:
    """Kadastrale percelen (DKK) binnen de bbox via WFS."""
    key = ("dkk", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    feats = []
    start = 0
    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "kadastralekaart:Perceel",
            "outputFormat": "application/json",
            "srsName": "EPSG:28992",
            "bbox": ",".join(str(v) for v in bbox) + ",EPSG:28992",
            "count": 1000,
            "startIndex": start,
        }
        r = _session.get(DKK_WFS, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get("features", [])
        for f in batch:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = shape(geom)
            except Exception:
                continue
            feats.append((g, f.get("properties", {})))
        if len(batch) < 1000:
            break
        start += 1000
        if start > 20000:  # veiligheidsgrens
            break
    return _cache_put(key, feats)


# ---------------------------------------------------------------------------
# Zonelagen (FO §2): beschermde gebieden, bodem, archeologie
# ---------------------------------------------------------------------------

NATURA_WFS = "https://service.pdok.nl/rvo/natura2000/wfs/v1_0"
RCE_WFS = "https://data.geo.cultureelerfgoed.nl/openbaar/wfs"

NATURA_WMS = "https://service.pdok.nl/rvo/natura2000/wms/v1_0"
NNN_WMS = "https://service.pdok.nl/provincies/natuurnetwerk-nederland/wms/v1_0"
GWB_WMS = "https://service.pdok.nl/provincies/grondwaterbeschermingsgebieden/wms/v1_0"
BODEMLOKET_WMS = ("https://gis.gdngeoservices.nl/standalone/services/blk_gdn/"
                  "lks_blk_rd_v1/MapServer/WMSServer")
# BRO-milieukwaliteit (opvolgers van het Wbb-Bodemloket onder de Omgevingswet):
# SAD = milieuhygiënisch bodemonderzoek (verplichte aanlevering sinds 1-7-2025),
# SLD = overheidsbesluit bodemverontreiniging/nazorg (in werking per 1-1-2026)
SAD_WMS = "https://service.pdok.nl/tno/bro-milieuhygienisch-bodemonderzoek/wms/v1_0"
SLD_WMS = "https://service.pdok.nl/tno/bro-overheidsbesluit-bodemverontreiniging/wms/v1_0"

# Regionale bodembronnen: na de Wbb is verontreinigingsdata versnipperd per
# bevoegd gezag; zolang de BRO-migratie (SAD/SLD) loopt vullen regionale open
# services de gaten. Uitbreidbaar per organisatie. `zone` bepaalt de zwaarte:
# "verontreinigd" telt mee als vastgestelde verontreiniging (×2,0),
# "onderzoek" als onderzoekslocatie (×1,5). `extent` (RD) voorkomt onnodige
# requests buiten het werkgebied van de bron.
BODEM_REGIONAAL = [
    {"naam": "Zuid-Holland: spoedlocaties bodemsanering",
     "url": "https://geodata.zuid-holland.nl/geoserver/bodem/wms",
     "layer": "BS_SPOEDLOCATIES", "zone": "verontreinigd",
     "extent": (43000, 406000, 139000, 483000)},
    {"naam": "Zuid-Holland: bodemsanering bedrijfsterreinen",
     "url": "https://geodata.zuid-holland.nl/geoserver/bodem/wms",
     "layer": "BS_BSB_TOT_LIJST_GEGEOCODEERD", "zone": "onderzoek",
     "extent": (43000, 406000, 139000, 483000)},
    {"naam": "Zuid-Holland: stortplaatsen (Wm)",
     "url": "https://geodata.zuid-holland.nl/geoserver/bodem/wms",
     "layer": "WM_STORTLOCATIES", "zone": "onderzoek",
     "extent": (43000, 406000, 139000, 483000)},
    {"naam": "Zaanstad (Nazca): verontreinigingen",
     "url": "https://maps.zaanstad.nl/geoserver/wms",
     "layer": "geo:nazca_verontreiniging_geo", "zone": "verontreinigd",
     "extent": (108000, 487000, 124000, 507000)},
    {"naam": "Zaanstad (Nazca): bodeminformatie-activiteiten",
     "url": "https://maps.zaanstad.nl/geoserver/wms",
     "layer": "geo:bodem_bodeminformatie_activiteiten", "zone": "onderzoek",
     "extent": (108000, 487000, 124000, 507000)},
]


def regionale_bodem_masks(bbox: tuple, ncols: int, nrows: int) -> tuple:
    """Maskers en bronnamen van regionale bodembronnen die de bbox raken.

    Retourneert (verontreinigd_masks, onderzoek_masks, actieve_bronnen).
    """
    verontreinigd, onderzoek, actief = [], [], []
    for bron in BODEM_REGIONAAL:
        e = bron["extent"]
        if bbox[2] < e[0] or bbox[0] > e[2] or bbox[3] < e[1] or bbox[1] > e[3]:
            continue
        mask = fetch_wms_mask(bron["url"], bron["layer"], bbox, ncols, nrows)
        if mask is None:
            continue
        actief.append(bron["naam"])
        (verontreinigd if bron["zone"] == "verontreinigd" else onderzoek).append(mask)
    return verontreinigd, onderzoek, actief


def _fetch_wfs(url: str, typename: str, bbox: tuple, max_features: int = 20000) -> list:
    """Generieke WFS 2.0-fetcher (GeoJSON, RD, met paging via startIndex)."""
    feats = []
    start = 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": typename, "outputFormat": "application/json",
            "srsName": "EPSG:28992",
            "bbox": ",".join(str(v) for v in bbox) + ",EPSG:28992",
            "count": 1000, "startIndex": start,
        }
        r = _session.get(url, params=params, timeout=60)
        r.raise_for_status()
        batch = r.json().get("features", [])
        for f in batch:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                g = shape(geom)
            except Exception:
                continue
            if not g.is_empty:
                feats.append((g, f.get("properties", {})))
        if len(batch) < 1000 or start >= max_features:
            break
        start += 1000
    return feats


def fetch_natura2000(bbox: tuple) -> list:
    key = ("natura2000", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_wfs(NATURA_WFS, "natura2000:natura2000", bbox))


def fetch_amk(bbox: tuple) -> list:
    """Archeologische Monumentenkaart 2014 (RCE)."""
    key = ("amk", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_wfs(RCE_WFS,
                                      "openbaar:Archeologische_Monumentenkaart_2014", bbox))


def fetch_wms_mask(url: str, layer: str, bbox: tuple, ncols: int, nrows: int):
    """Booleaans masker uit een WMS GetMap (alfakanaal > 0 = in zone).

    Voor zonelagen zonder open WFS (NNN, grondwaterbescherming, Bodemloket).
    Boven 2000 px per as wordt op halve resolutie opgevraagd en opgeschaald.
    Retourneert None als de dienst faalt; de laag telt dan niet mee.
    """
    import io as _io

    import numpy as _np
    from PIL import Image as _Image

    key = ("wmsmask", url, layer, _bbox_key(bbox), ncols, nrows)
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    try:
        w, h = ncols, nrows
        schaal = 1
        while w > 2000 or h > 2000:
            schaal *= 2
            w, h = (ncols + schaal - 1) // schaal, (nrows + schaal - 1) // schaal
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
            "LAYERS": layer, "STYLES": "", "SRS": "EPSG:28992",
            "BBOX": ",".join(str(v) for v in bbox),
            "WIDTH": w, "HEIGHT": h,
            "FORMAT": "image/png", "TRANSPARENT": "TRUE",
        }
        r = _session.get(url, params=params, timeout=60)
        r.raise_for_status()
        if "image" not in r.headers.get("Content-Type", ""):
            raise ValueError("geen afbeelding terug")
        im = _Image.open(_io.BytesIO(r.content)).convert("RGBA")
        if (w, h) != (ncols, nrows):
            im = im.resize((ncols, nrows), _Image.NEAREST)
        mask = _np.array(im)[:, :, 3] > 0
    except Exception:
        mask = None
    return _cache_put(key, mask)


def gemeente_naam(x: float, y: float) -> str | None:
    """Gemeentenaam op een punt via bestuurlijke gebieden; None bij falen."""
    try:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "bestuurlijkegebieden:Gemeentegebied",
            "outputFormat": "application/json",
            "srsName": "EPSG:28992",
            "count": 1,
            "bbox": f"{x - 1},{y - 1},{x + 1},{y + 1},EPSG:28992",
        }
        r = _session.get(BESTUUR_WFS, params=params, timeout=15)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            return feats[0]["properties"].get("naam")
    except Exception:
        pass
    return None
