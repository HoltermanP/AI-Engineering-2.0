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
# Nationaal Wegen Bestand: wegvakken met wegbeheerder (R/P/G/W/T) — levert het
# echte bevoegde gezag bij rijbaankruisingen i.p.v. de aanname "gemeente"
NWB_WFS = "https://service.pdok.nl/rws/nwbwegen/wfs/v1_0"
# Landelijke IMWA-datasets van de waterschappen (PDOK OGC API Features):
# leggerwatergangen mét categorie (primair/secundair/tertiair ≈ A/B/C) en
# waterkeringen — vervangen de breedte-aanname bij waterkruisingen
IMWA_WATER_OGC = "https://api.pdok.nl/hwh/waterschappen-oppervlaktewateren-imwa/ogc/v1"
IMWA_KERING_OGC = "https://api.pdok.nl/hwh/waterschappen-keringen-imwa/ogc/v1"
# Waterschapsgrenzen (IMSO): naam van het bevoegde waterschap op een punt
WATERSCHAP_WMS = ("https://service.pdok.nl/hwh/waterschappen-waterschapsgrenzen-imso"
                  "/wms/v2_0")

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
_session.headers["User-Agent"] = "InfraEngine-prototype/0.1"

# LRU-cache met plafond: de corridor-modus haalt per deeltraject een eigen
# bbox op — zonder plafond zou een tracé van 70 km alle data vasthouden.
# Naast een plafond op het aantal entries ook een plafond op het geschatte
# geheugenbeslag: bij grote "gebied"-berekeningen (tot MAX_GEBIED_KM2) zijn
# losse entries (WMS-maskers, WFS-featurelijsten) groot genoeg dat 300 stuks
# ruim over de 1 GB konden oplopen en, samen met de rest van een berekening,
# het proces uit zijn geheugen lieten lopen (Render OOM-restart).
import sys
from collections import OrderedDict

_cache: OrderedDict = OrderedDict()
_cache_bytes: dict = {}          # key -> geschatte grootte van de waarde (bytes)
_cache_bytes_totaal = 0
_cache_lock = threading.Lock()
_CACHE_MAX = 300  # entries (~10 per deeltraject → ± 30 recente deeltrajecten)
_CACHE_MAX_BYTES = 150 * 1024 * 1024  # 150 MB, ongeacht aantal entries


def _byte_estimaat(obj, _seen=None) -> int:
    """Grove schatting van het geheugenbeslag van een cache-waarde. Geen
    exacte deep-sizeof, maar goed genoeg om de cache op werkelijk
    geheugengebruik te begrenzen i.p.v. alleen op aantal entries."""
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return 0
    _seen.add(oid)
    nbytes = getattr(obj, "nbytes", None)  # numpy-arrays (WMS-maskers)
    if isinstance(nbytes, int):
        return nbytes
    grootte = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            grootte += _byte_estimaat(k, _seen) + _byte_estimaat(v, _seen)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            grootte += _byte_estimaat(v, _seen)
    return grootte


def _cache_get(key):
    """(gevonden, waarde) — waarde kan legitiem None zijn (falend WMS-masker)."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return True, _cache[key]
    return False, None


def _cache_put(key, waarde):
    global _cache_bytes_totaal
    with _cache_lock:
        if key in _cache:
            _cache_bytes_totaal -= _cache_bytes.pop(key, 0)
        grootte = _byte_estimaat(waarde)
        _cache[key] = waarde
        _cache_bytes[key] = grootte
        _cache_bytes_totaal += grootte
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX or _cache_bytes_totaal > _CACHE_MAX_BYTES:
            if len(_cache) <= 1:
                break  # altijd minstens de zojuist toegevoegde waarde bewaren
            oude_key, _ = _cache.popitem(last=False)
            _cache_bytes_totaal -= _cache_bytes.pop(oude_key, 0)
    return waarde


def _bbox_key(bbox: tuple) -> tuple:
    return tuple(round(v, 1) for v in bbox)


def _fetch_ogc_collection(api: str, collection: str, bbox: tuple) -> list:
    """Alle features van één OGC API Features-collectie binnen de bbox, met paging."""
    feats = []
    url = f"{api}/collections/{collection}/items"
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


def _fetch_bgt_collection(collection: str, bbox: tuple) -> list:
    """Alle features van één BGT-collectie binnen de bbox, met paging."""
    return _fetch_ogc_collection(BGT_OGC, collection, bbox)


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
    {"naam": "Utrecht: bomenkaart (gemeentelijke straat- en parkbomen)",
     "url": ("https://services-eu1.arcgis.com/SMnoOtmU2UWf0vRp/arcgis/rest/"
             "services/Bomenkaart_update_2024/FeatureServer/0/query"),
     "extent": (126800, 448900, 141900, 461200)},
    {"naam": "Amsterdam: bomen in beheer (stamgegevens)",
     "url": "https://api.data.amsterdam.nl/v1/wfs/bomen/",
     "wfs_typename": "stamgegevens",
     "extent": (110000, 476000, 132000, 497000)},
    {"naam": "Groningen: gemeentelijke bomen",
     "url": ("https://services2.arcgis.com/chCSiGO4ORzXeSGk/arcgis/rest/"
             "services/Bomen_gemeente_Groningen/FeatureServer/0/query"),
     "extent": (227000, 569800, 246700, 592700)},
    {"naam": "Almere: gemeentelijke bomen",
     "url": ("https://services2.arcgis.com/rtefou6JFIxFvYTf/arcgis/rest/"
             "services/Bomen_Almere/FeatureServer/0/query"),
     "extent": (137500, 480300, 154300, 493700)},
    {"naam": "Breda: gemeentelijke bomen",
     "url": ("https://services-eu1.arcgis.com/SgHNk1qzR4I13Wum/arcgis/rest/"
             "services/Bomen/FeatureServer/0/query"),
     "extent": (104500, 388600, 119200, 405700)},
    {"naam": "Delft: bomen in gemeentelijk beheer",
     "url": ("https://services3.arcgis.com/j07voPd56xoB4c87/arcgis/rest/"
             "services/Bomen%20in%20beheer%20door%20gemeente%20Delft/"
             "FeatureServer/0/query"),
     "extent": (81600, 442600, 87500, 449900)},
    {"naam": "Assen: bomen (VTA-inventarisatie)",
     "url": ("https://services1.arcgis.com/p5QhXC0i0sZjprM1/arcgis/rest/"
             "services/Dataset_VTA_Bomen_2025/FeatureServer/0/query"),
     "extent": (228900, 551300, 238500, 564300)},
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


def _fetch_wfs_punten(url: str, typename: str, bbox: tuple,
                      max_features: int = 20000) -> list:
    """Puntfeatures uit een WFS 2.0 GetFeature (GeoJSON, RD, paging)."""
    feats = []
    start = 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typenames": typename,
            "outputFormat": "geojson",
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
        start += len(batch)
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
            if bron.get("wfs_typename"):
                batch = _fetch_wfs_punten(bron["url"], bron["wfs_typename"], bbox)
            else:
                batch = _fetch_arcgis_punten(bron["url"], bbox)
        except Exception:
            continue
        actief.append(bron["naam"])
        for g, p in batch:
            if g.geom_type != "Point":
                g = g.representative_point()
            kroon = p.get(bron.get("kroon_veld") or "")
            try:
                kroon = float(kroon)
            except (TypeError, ValueError):
                kroon = None
            feats.append((g, {"bron": bron["naam"], "kroon_m": kroon}))
    return _cache_put(key, (feats, actief))


# RIVM Bomenkaart: landsdekkend AHN4-raster (10 m) van alle bomen > 2,5 m.
# Geen puntdata, dus alleen bruikbaar als dekkingssignaal: staan er volgens
# het AHN bomen waar BGT en gemeentelijke registers niets leveren?
ANK_WMS = "https://data.rivm.nl/geo/ank/wms"
BOMENKAART_LAAG = "rivm_20231221_bomenkaart_2022"


def fetch_bomenkaart_dekking(bbox: tuple):
    """Fractie (0–1) van de bbox met boombedekking volgens de RIVM Bomenkaart.

    None als de dienst faalt; de laag telt dan niet mee.
    """
    mask = fetch_wms_mask(ANK_WMS, BOMENKAART_LAAG, bbox, 256, 256)
    return None if mask is None else float(mask.mean())


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
# Stiltegebieden (provincies, INSPIRE): lichte weging + melding werkwijze
STILTE_WMS = "https://service.pdok.nl/provincies/stiltegebieden/wms/v1_0"
# BRO geotechnisch sondeeronderzoek (CPT): bestaande sonderingen nabij een
# boringslocatie — geen weging, wel het onderzoeksregister (sondering al
# beschikbaar of nieuw te ramen)
CPT_WMS = "https://service.pdok.nl/tno/bro-geotechnischsondeeronderzoek/wms/v1_0"

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
    {"naam": "Fryslân: bodemkwaliteit saneren (bodematlas)",
     "url": ("https://geoportaal.fryslan.nl/arcgis/services/Themas/bodem/"
             "MapServer/WMSServer"),
     "layer": "Lokale_chemische_bodemkwaliteit_saneren9231",
     "zone": "verontreinigd",
     "extent": (113000, 531000, 220000, 620000)},
    {"naam": "Gelderland: stortplaatsen nazorg (Wm)",
     "url": "https://geoserver.gelderland.nl/geoserver/ows",
     "layer": "ngr_b:MiBo_Stortpltsn_Nazrg_Wm", "zone": "onderzoek",
     "extent": (150000, 429000, 228000, 497000)},
    {"naam": "Gelderland: gesloten stortplaatsen (omgevingsverordening)",
     "url": "https://geoserver.gelderland.nl/geoserver/ows",
     "layer": "ngr_verordening:POVE_Gesloten_stortplaatsen", "zone": "onderzoek",
     "extent": (140000, 419000, 229000, 497000)},
    {"naam": "Overijssel: stortplaatsen",
     "url": "https://services.geodataoverijssel.nl/geoserver/ows",
     "layer": "B34_beheer_grondwater:B3_Stortplaatsen", "zone": "onderzoek",
     "extent": (185000, 462000, 268000, 538000)},
    {"naam": "Limburg: Wbb-locaties overgangsrecht",
     "url": "https://portal.prvlimburg.nl/geodata/ows",
     "layer": "MILIEU:OVERGANGSRECHTLOC_WBB_V", "zone": "verontreinigd",
     "extent": (168000, 307000, 209000, 420000)},
    {"naam": "Limburg: voormalige stortplaatsen",
     "url": "https://portal.prvlimburg.nl/geodata/ows",
     "layer": "MILIEU:VOORMALIGE_STORTPLAATSEN_P", "zone": "onderzoek",
     "extent": (172000, 308000, 210000, 419000)},
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


# NGE (niet-gesprongen explosieven): er is geen landelijke open bron; gemeenten
# en regio's publiceren eigen bodembelastingkaarten. Uitbreidbaar per bron,
# zelfde patroon als BODEM_REGIONAAL. `zone` "verdacht" weegt mee en triggert
# het NGE-vooronderzoek; "gevrijwaard" is alleen informatief (geen weging).
NGE_REGIONAAL = [
    {"naam": "Amsterdam: CE-bodembelastingkaart (verdachte gebieden)",
     "url": "https://map.data.amsterdam.nl/maps/bommenkaart",
     "layer": "verdachte_gebieden", "zone": "verdacht",
     "extent": (110000, 476000, 135000, 495000)},
]


def regionale_nge_masks(bbox: tuple, ncols: int, nrows: int) -> tuple:
    """(verdacht-maskers, actieve bronnamen) van NGE-bronnen die de bbox raken."""
    verdacht, actief = [], []
    for bron in NGE_REGIONAAL:
        e = bron["extent"]
        if bbox[2] < e[0] or bbox[0] > e[2] or bbox[3] < e[1] or bbox[1] > e[3]:
            continue
        if bron["zone"] != "verdacht":
            continue
        mask = fetch_wms_mask(bron["url"], bron["layer"], bbox, ncols, nrows)
        if mask is None:
            continue
        actief.append(bron["naam"])
        verdacht.append(mask)
    return verdacht, actief


# Buisleidingen gevaarlijke stoffen (Bevb): het REV is sinds 2024 de wettelijke
# registratieplek, maar publiceert (nog) geen open buisleidingenservice; de
# oude Risicokaart-services zijn afgeschermd. Zodra een open WMS bekend is
# (landelijk of per provincie) is hij hier toe te voegen — zelfde patroon als
# BODEM_REGIONAAL; de zone weegt mee en triggert de Bevb-afstemming.
BUISLEIDING_REGIONAAL: list = []


def regionale_buisleiding_masks(bbox: tuple, ncols: int, nrows: int) -> tuple:
    """(maskers, actieve bronnamen) van buisleidingbronnen die de bbox raken."""
    masks, actief = [], []
    for bron in BUISLEIDING_REGIONAAL:
        e = bron["extent"]
        if bbox[2] < e[0] or bbox[0] > e[2] or bbox[3] < e[1] or bbox[1] > e[3]:
            continue
        mask = fetch_wms_mask(bron["url"], bron["layer"], bbox, ncols, nrows)
        if mask is None:
            continue
        actief.append(bron["naam"])
        masks.append(mask)
    return masks, actief


def regionale_bronnen_voor_bbox(bbox: tuple) -> dict:
    """Regionale bronnen (per laag) waarvan de extent de bbox raakt.

    Eén bron van waarheid voor de frontend: de kaartlagen en de
    dekkingsmelding volgen dezelfde registers als het kostenoppervlak
    (BODEM_REGIONAAL, NGE_REGIONAAL, BUISLEIDING_REGIONAAL, BOMEN_REGIONAAL).
    WMS-bronnen leveren url+layer voor een kaartlaag; bomenbronnen zijn
    puntservices (ArcGIS/WFS) en leveren alleen de naam (dekkingsinfo — de
    punten zelf komen na een berekening als vectorlaag mee).
    """
    def raakt(bron):
        e = bron["extent"]
        return not (bbox[2] < e[0] or bbox[0] > e[2]
                    or bbox[3] < e[1] or bbox[1] > e[3])

    def wms(bronnen):
        return [{"naam": b["naam"], "url": b["url"], "layer": b["layer"],
                 "zone": b.get("zone")} for b in bronnen if raakt(b)]

    return {
        "bodem": wms(BODEM_REGIONAAL),
        "nge": wms(NGE_REGIONAAL),
        "buisleiding": wms(BUISLEIDING_REGIONAAL),
        "bomen": [{"naam": b["naam"]} for b in BOMEN_REGIONAAL if raakt(b)],
    }


def fetch_nwb_wegvakken(bbox: tuple) -> list:
    """NWB-wegvakken (lijnen) met wegbeheerder binnen de bbox."""
    key = ("nwb", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_wfs(NWB_WFS, "nwbwegen:wegvakken", bbox))


def fetch_legger_watergangen(bbox: tuple) -> list:
    """Leggerwatergangen (IMWA oppervlaktewaterlichaam, lijnen) met categorie.

    `categoriewater`: primair / secundair / tertiair — de waterschapsindeling
    die (per waterschap benoemd als A/B/C) bepaalt of een open kruising met
    afdamming is toegestaan.
    """
    key = ("legger_water", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_ogc_collection(
        IMWA_WATER_OGC, "oppervlaktewaterlichaam", bbox))


def fetch_keringen(bbox: tuple) -> list:
    """Waterkeringen (IMWA, lijnen) met categorie (primair/regionaal/overig)."""
    key = ("keringen", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_ogc_collection(
        IMWA_KERING_OGC, "waterkering", bbox))


def fetch_rijksmonumenten(bbox: tuple) -> list:
    """Rijksmonument-contouren (RCE, vlakken) binnen de bbox."""
    key = ("rijksmonument", _bbox_key(bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    return _cache_put(key, _fetch_wfs(RCE_WFS, "openbaar:rijksmonumentcontouren", bbox))


def waterschap_naam(x: float, y: float) -> str | None:
    """Naam van het waterschap op een punt (IMSO-grenzen, GetFeatureInfo)."""
    key = ("waterschap", round(x / 500), round(y / 500))  # 500 m-raster volstaat
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    naam = None
    try:
        d = 100
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
            "LAYERS": "waterschap", "QUERY_LAYERS": "waterschap",
            "SRS": "EPSG:28992", "BBOX": f"{x - d},{y - d},{x + d},{y + d}",
            "WIDTH": 10, "HEIGHT": 10, "X": 5, "Y": 5,
            "INFO_FORMAT": "application/json",
        }
        r = _session.get(WATERSCHAP_WMS, params=params, timeout=15)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            p = feats[0].get("properties", {})
            naam = p.get("waterbeheerder") or p.get("naam")
    except Exception:
        naam = None
    return _cache_put(key, naam)


def cpt_nabij(x: float, y: float, straal_m: float = 100.0) -> bool | None:
    """True als de BRO een sondering (CPT) binnen de straal rond het punt kent.

    Via een klein WMS-masker (de CPT-dienst heeft geen open WFS); None als de
    dienst faalt — dan is er geen uitspraak.
    """
    bbox = (x - straal_m, y - straal_m, x + straal_m, y + straal_m)
    mask = fetch_wms_mask(CPT_WMS, "cpt_kenset", bbox, 40, 40)
    if mask is None:
        return None
    return bool(mask.any())


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
