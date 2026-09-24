"""NDFF-verspreidingsdata van beschermde soorten (open data, per km-hok).

Bron: de open-datadiensten achter de **Flora & Fauna Verkenner** van de NDFF
(BIJ12); de NDFF is sinds 11-02-2025 open data. Er is geen gedocumenteerde
open API, maar de Verkenner zelf draait op twee anonieme diensten (uit zijn
`assets/config/application-config.json`):

- WFS  ``https://opendata-ogc-services.prd.ndffcloud.nl`` — lagen
  ``Kilometerhokken`` (1×1 km, RD), ``Uurhokken`` (5×5 km) en
  ``WaarnemingenUnique``; ondersteunt EPSG:28992.
- REST ``https://opendata-rest-api.prd.ndffcloud.nl`` — ``POST /taxongroups``
  en ``POST /taxon_group/{slug}/taxa`` met body
  ``{"gridSquare": [hok-ids], "periodStart": jaar, "periodStop": jaar,
  "taxonPolicies": [beleidsslugs]}``; ``GET /taxonpolicies`` geeft de
  beleidsstatussen (Ow-Habitatrichtlijn, Ow-Vogelrichtlijn, Ow-andere
  soorten, Rode Lijst-klassen, exoten).

Kanttekeningen die het ontwerp bepalen:

- Kwetsbare soorten (± 1.200, "Lijst Kwetsbare Soorten") zijn in de open
  data **vervaagd** tot km-hok (soms 5×5/10×10 km of verborgen) en tonen
  alleen het jaar. Deze laag is dus per definitie hok-niveau en
  **informatief**: ze weegt niet mee in het kostenoppervlak (0,5 m-raster),
  maar voedt de natuur-quickscan (onderzoeksregister), de toetsing en de
  ontwikkelnota's.
- Bijsluiter NDFF: bronvermelding verplicht ("NDFF, <jaar>") en altijd
  recente data gebruiken — de cache leeft daarom maximaal één dag.
- De diensten zijn niet als koppelvlak gedocumenteerd: geen SLA, kunnen
  wijzigen. Elke fout wordt gemeld in ``laag_fouten`` en blokkeert nooit
  een berekening. Het aantal verzoeken blijft beperkt (per hok ± 4–9 kleine
  POST's, gecachet; de kaartlaag laadt maximaal MAX_HOKKEN_KAART hokken).
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from shapely.geometry import shape

NDFF_WFS = "https://opendata-ogc-services.prd.ndffcloud.nl"
NDFF_REST = "https://opendata-rest-api.prd.ndffcloud.nl"
VERKENNER_URL = "https://florafaunaverkenner.nl"
BIJSLUITER_URL = "https://ndff.nl/natuurdata/bijsluiter/"

# Periode (jaren terug t/m nu) — quickscans hanteren doorgaans 5–10 jaar;
# oudere waarnemingen zeggen weinig over de actuele aanwezigheid
PERIODE_JAREN = 10

# beleidsstatussen onder de Omgevingswet (slug → label). Rode Lijst-klassen
# en exoten worden bewust niet meegenomen: ze zijn geen beschermingsregime.
BELEID = {
    "ow--habitatrichtlijn": "Habitatrichtlijn",
    "ow--vogelrichtlijn": "Vogelrichtlijn",
    "ow--andere-soorten": "andere soorten (nationaal beschermd)",
}
STRIKT = {"ow--habitatrichtlijn", "ow--vogelrichtlijn"}  # Europees strikt beschermd

# Aangepaste lijst jaarrond beschermde vogelnesten (LNV, augustus 2009),
# categorie 1 t/m 4 — nesten die het hele jaar beschermd zijn. Categorie 5
# (alleen jaarrond bij zwaarwegende ecologische omstandigheden) valt onder
# de gewone broedvogelbescherming en staat hier niet in.
JAARROND_NEST = {
    "steenuil",                                              # cat. 1
    "gierzwaluw", "huismus",                                 # cat. 2
    "grote gele kwikstaart", "kerkuil", "oehoe", "ooievaar", "slechtvalk",  # cat. 3
    "boomvalk", "buizerd", "havik", "ransuil", "roek", "wespendief",
    "zwarte wouw",                                           # cat. 4
}

# Gevraagde soortcategorieën → NDFF-soortgroepen (slugs van /taxongroups).
# `filter`: alleen soorten met deze Nederlandse naam (kleine letters) tellen.
CATEGORIEEN = [
    {"key": "vaatplanten", "naam": "Vaatplanten", "groepen": ["vaatplanten"]},
    {"key": "broedvogels_jaarrond", "naam": "Broedvogels met jaarrond beschermd nest",
     "groepen": ["vogels"], "filter": JAARROND_NEST},
    {"key": "vleermuizen", "naam": "Vleermuizen", "groepen": ["vleermuizen"]},
    {"key": "zoogdieren", "naam": "Grondgebonden zoogdieren",
     "groepen": ["zoogdieren-overig"]},
    {"key": "amfibieen_reptielen", "naam": "Amfibieën en reptielen",
     "groepen": ["amfibieen", "reptielen"]},
]
CATEGORIE_NAAM = {c["key"]: c["naam"] for c in CATEGORIEEN}

MAX_HOKKEN_KAART = 25      # kaartlaag: meer hokken → "zoom verder in"
MAX_HOKKEN_BEREKENING = 40  # per datalagen-bbox (gebied ≤ 3 km² ≈ 9 hokken)
TIMEOUT_S = 25
CACHE_TTL_S = 24 * 3600    # bijsluiter: recente data; NDFF verwerkt 's nachts

_session = requests.Session()
_session.headers["User-Agent"] = "InfraEngine-prototype/0.1 (MS-tracéontwerp)"
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() - item[0] < CACHE_TTL_S:
            return True, item[1]
    return False, None


def _cache_put(key, waarde):
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (time.time(), waarde)
    return waarde


def periode() -> tuple:
    jaar = _dt.date.today().year
    return jaar - PERIODE_JAREN, jaar


# ---------------------------------------------------------------------------
# WFS: kilometerhokken
# ---------------------------------------------------------------------------

def kmhokken(bbox: tuple) -> list:
    """Kilometerhokken (1×1 km) die de bbox (RD) raken: [{id, label, geom}]."""
    key = ("hokken", tuple(round(v / 50) for v in bbox))
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typename": "ms:Kilometerhokken", "outputFormat": "application/json",
        "srsname": "urn:ogc:def:crs:EPSG::28992",
        "bbox": ",".join(f"{v:.0f}" for v in bbox) + ",urn:ogc:def:crs:EPSG::28992",
    }
    r = _session.get(NDFF_WFS + "/", params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    hokken = []
    for f in r.json().get("features", []):
        p = f.get("properties") or {}
        if p.get("id") is None or not f.get("geometry"):
            continue
        hokken.append({"id": int(p["id"]), "label": p.get("label") or str(p["id"]),
                       "geom": shape(f["geometry"])})
    hokken.sort(key=lambda h: h["id"])
    return _cache_put(key, hokken)


# ---------------------------------------------------------------------------
# REST: soorten per hok
# ---------------------------------------------------------------------------

def _post(path: str, body: dict, order: str = "observation_count_high-low") -> dict:
    r = _session.post(NDFF_REST + path, json=body, params={"order": order},
                      headers={"accept": "application/json"}, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def _body(hok_ids: list, beleid: list) -> dict:
    start, stop = periode()
    return {"gridSquare": list(hok_ids), "periodStart": start, "periodStop": stop,
            "taxonPolicies": list(beleid)}


def categoriseer(groep_slug: str, soorten: list) -> list:
    """Soorten van één NDFF-soortgroep verdelen over de gevraagde categorieën.

    Retourneert [(categorie_key, soort)] — een soort telt alleen mee in een
    categorie die de groep kent en waarvan een eventueel naamfilter matcht.
    """
    uit = []
    for cat in CATEGORIEEN:
        if groep_slug not in cat["groepen"]:
            continue
        filt = cat.get("filter")
        for s in soorten:
            naam = (s.get("naam") or "").strip().lower()
            if filt is None or naam in filt:
                uit.append((cat["key"], s))
    return uit


def _taxa(hok_id: int, groep: str, beleid: str) -> list:
    d = _post(f"/taxon_group/{groep}/taxa", _body([hok_id], [beleid]))
    return [{"slug": r.get("id"), "naam": a.get("nameNl"), "naam_wet": a.get("nameSci"),
             "aantal": int(a.get("totalObservations") or 0)}
            for r in d.get("data", []) for a in [r.get("attributes") or {}]]


def hok_soorten(hok_id: int) -> dict:
    """Beschermde soorten (Ow) in één km-hok, per gevraagde categorie.

    {"hok": id, "categorieen": {key: [{naam, naam_wet, aantal, beleid: [..]}]},
     "soorten": n, "waarnemingen": n, "fout": None|str}
    Per hok: 3× /taxongroups (één per beleidsstatus) + één /taxa per
    (soortgroep, beleidsstatus) met waarnemingen — gecachet (CACHE_TTL_S).
    """
    key = ("hok", hok_id, periode())
    hit, waarde = _cache_get(key)
    if hit:
        return waarde
    uit = {"hok": hok_id, "categorieen": {c["key"]: [] for c in CATEGORIEEN},
           "soorten": 0, "waarnemingen": 0, "fout": None}
    try:
        gewenst = {g for c in CATEGORIEEN for g in c["groepen"]}
        combos = []  # (groep, beleid) met waarnemingen
        for b in BELEID:
            d = _post("/taxongroups", _body([hok_id], [b]))
            for r in d.get("data", []):
                a = r.get("attributes") or {}
                if r.get("id") in gewenst and int(a.get("totalObservations") or 0) > 0:
                    combos.append((r["id"], b))
        # soorten per (groep, beleid) samenvoegen; één soort kan onder
        # meerdere regimes vallen (bijv. Habitatrichtlijn én andere soorten)
        per_cat: dict = {c["key"]: {} for c in CATEGORIEEN}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {(g, b): ex.submit(_taxa, hok_id, g, b) for g, b in combos}
            for (g, b), fut in futs.items():
                for cat_key, s in categoriseer(g, fut.result()):
                    rec = per_cat[cat_key].setdefault(s["slug"], {
                        "naam": s["naam"], "naam_wet": s["naam_wet"],
                        "aantal": 0, "beleid": []})
                    # dezelfde waarneming telt per regime opnieuw; neem het
                    # maximum i.p.v. de som als schatting van het aantal
                    rec["aantal"] = max(rec["aantal"], s["aantal"])
                    if b not in rec["beleid"]:
                        rec["beleid"].append(b)
        for cat_key, soorten in per_cat.items():
            lijst = sorted(soorten.values(), key=lambda s: (-s["aantal"], s["naam"] or ""))
            uit["categorieen"][cat_key] = lijst
            uit["soorten"] += len(lijst)
            uit["waarnemingen"] += sum(s["aantal"] for s in lijst)
    except Exception as e:  # dienst onbereikbaar/gewijzigd: melden, niet blokkeren
        uit["fout"] = f"{type(e).__name__}: {e}"[:160]
        return uit  # fouten niet cachen
    return _cache_put(key, uit)


def hokken_soorten(hok_ids: list, max_workers: int = 4) -> dict:
    """Soortdata voor meerdere hokken (parallel); {hok_id: hok_soorten(...)}."""
    ids = list(dict.fromkeys(int(h) for h in hok_ids))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return dict(zip(ids, ex.map(hok_soorten, ids)))


# ---------------------------------------------------------------------------
# Samenvatting voor registers, toetsing en nota's
# ---------------------------------------------------------------------------

def samenvatting(hokken: dict) -> dict:
    """Soorten over alle hokken samengevat per categorie (pure functie).

    hokken: {hok_id: hok_soorten(...)} → {
      "hokken": n, "hokken_fout": n, "soorten_totaal": n, "strikt_totaal": n,
      "periode": [start, stop], "bron": str,
      "categorieen": [{key, naam, soorten: [{naam, naam_wet, beleid, strikt,
                        hokken, aantal}], aantal_soorten, strikt}]}
    """
    start, stop = periode()
    per_cat: dict = {c["key"]: {} for c in CATEGORIEEN}
    fout = 0
    for d in hokken.values():
        if d.get("fout"):
            fout += 1
        for cat_key, soorten in (d.get("categorieen") or {}).items():
            for s in soorten:
                rec = per_cat.setdefault(cat_key, {}).setdefault(s["naam"], {
                    "naam": s["naam"], "naam_wet": s.get("naam_wet"),
                    "beleid": [], "hokken": 0, "aantal": 0})
                rec["hokken"] += 1
                rec["aantal"] += int(s.get("aantal") or 0)
                for b in s.get("beleid") or []:
                    if b not in rec["beleid"]:
                        rec["beleid"].append(b)
    cats = []
    for c in CATEGORIEEN:
        soorten = sorted(per_cat[c["key"]].values(),
                         key=lambda s: (-s["hokken"], -s["aantal"], s["naam"] or ""))
        for s in soorten:
            s["strikt"] = any(b in STRIKT for b in s["beleid"])
            s["beleid_label"] = ", ".join(BELEID.get(b, b) for b in s["beleid"])
        cats.append({"key": c["key"], "naam": c["naam"], "soorten": soorten,
                     "aantal_soorten": len(soorten),
                     "strikt": sum(1 for s in soorten if s["strikt"])})
    return {
        "hokken": len(hokken),
        "hokken_fout": fout,
        "soorten_totaal": sum(c["aantal_soorten"] for c in cats),
        "strikt_totaal": sum(c["strikt"] for c in cats),
        "periode": [start, stop],
        "bron": (f"NDFF open data (Flora & Fauna Verkenner), {start}–{stop}, "
                 f"geraadpleegd {_dt.date.today():%d-%m-%Y}; km-hokniveau, "
                 f"kwetsbare soorten vervaagd"),
        "categorieen": cats,
    }


def tekst(sam: dict | None, max_per_cat: int = 3) -> str:
    """Korte Nederlandse opsomming per categorie voor registers/toetsing.

    Bijv. "vleermuizen: 5 soorten (o.a. gewone dwergvleermuis, laatvlieger);
    broedvogels met jaarrond beschermd nest: 2 soorten (huismus, buizerd)".
    """
    if not sam or not sam.get("soorten_totaal"):
        return ""
    delen = []
    for c in sam["categorieen"]:
        if not c["soorten"]:
            continue
        namen = [s["naam"] for s in c["soorten"][:max_per_cat]]
        rest = c["aantal_soorten"] - len(namen)
        strikt = f", {c['strikt']} strikt beschermd" if c["strikt"] else ""
        delen.append(f"{c['naam'].lower()}: {c['aantal_soorten']} soort"
                     f"{'en' if c['aantal_soorten'] != 1 else ''}"
                     f" ({'o.a. ' if rest > 0 else ''}{', '.join(namen)}{strikt})")
    return "; ".join(delen)


def strikte_soorten(sam: dict | None) -> list:
    """Namen (met regime) van Europees strikt beschermde soorten in de samenvatting."""
    if not sam:
        return []
    uit = []
    for c in sam["categorieen"]:
        for s in c["soorten"]:
            if s.get("strikt"):
                uit.append(f"{s['naam']} ({s['beleid_label']})")
    return uit


# ---------------------------------------------------------------------------
# Koppelvlakken voor main.py
# ---------------------------------------------------------------------------

def voor_bbox(bbox: tuple) -> dict:
    """Datalaag-bundel voor een berekenings-bbox: {"hokken": {id: data},
    "hok_meta": {id: label}, "fout": None|str}. Fouten blokkeren niets."""
    uit = {"hokken": {}, "hok_meta": {}, "fout": None}
    try:
        hokken = kmhokken(bbox)
    except Exception as e:
        uit["fout"] = f"kilometerhokken (WFS): {type(e).__name__}"
        return uit
    if len(hokken) > MAX_HOKKEN_BEREKENING:
        uit["fout"] = (f"{len(hokken)} km-hokken in gebied; maximaal "
                       f"{MAX_HOKKEN_BEREKENING} per deelgebied")
        return uit
    uit["hok_meta"] = {h["id"]: h["label"] for h in hokken}
    uit["hokken"] = hokken_soorten([h["id"] for h in hokken])
    fouten = [d["fout"] for d in uit["hokken"].values() if d.get("fout")]
    if fouten:
        uit["fout"] = f"{len(fouten)} van {len(hokken)} hokken niet opgehaald ({fouten[0]})"
    return uit


def kaartlaag(bbox: tuple) -> dict:
    """Kaartlaag-antwoord voor GET /api/ndff/hokken: hokken in beeld met
    geometrie (RD) en de soortdata per categorie."""
    hokken = kmhokken(bbox)
    if len(hokken) > MAX_HOKKEN_KAART:
        return {"te_veel": len(hokken), "max": MAX_HOKKEN_KAART, "hokken": []}
    data = hokken_soorten([h["id"] for h in hokken])
    uit = []
    for h in hokken:
        d = data[h["id"]]
        uit.append({
            "id": h["id"], "label": h["label"],
            "coords": [[round(x, 1), round(y, 1)] for x, y in h["geom"].exterior.coords],
            "soorten": d["soorten"], "waarnemingen": d["waarnemingen"],
            "strikt": sum(1 for s_lijst in d["categorieen"].values() for s in s_lijst
                          if any(b in STRIKT for b in s["beleid"])),
            "fout": d.get("fout"),
            "categorieen": [{"key": c["key"], "naam": c["naam"],
                             "soorten": [{**s, "beleid_label": ", ".join(
                                 BELEID.get(b, b) for b in s["beleid"])}
                                 for s in d["categorieen"].get(c["key"], [])]}
                            for c in CATEGORIEEN],
        })
    start, stop = periode()
    return {"hokken": uit, "periode": [start, stop], "bron": VERKENNER_URL,
            "bijsluiter": BIJSLUITER_URL, "beleid": BELEID}
