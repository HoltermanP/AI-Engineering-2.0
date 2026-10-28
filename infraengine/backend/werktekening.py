"""Werktekeningen voor het uitvoeringsontwerp (UO): bladindeling, PDF-set,
revisies met wijzigingswolken.

Een tekeningenset bestaat uit:
  - overzichtsblad: het hele tracé met de bladindeling en de tekeningenlijst;
  - situatiebladen langs het tracé (1:250/1:500/1:1000), gedraaid zodat het
    tracé horizontaal over het blad loopt: BGT-, kadaster-, KLIC- en
    inmetingsondergrond, tracé met kilometrering, boringen, kruisingen,
    moffen, maatvoering, RD-coördinatenkruisen, aansluitlijnen naar de
    buurbladen, noordpijl, sleutelkaartje, legenda, revisietabel en stempel;
  - lengteprofielen van de boringen (maaiveld uit AHN of inmeting, boorlijn
    met in-/uittredehoek en diepte-eis, kruisende K&L uit KLIC);
  - standaard-dwarsprofielen per liggingstype (dekking uit het normenkader);
  - uitzetstaat: alle hoekpunten van het tracé in RD met metrering.

Het tekenvoorschrift (papier, schaal, stempelvelden, laagnamen) is
instelbaar in ``data/tekenvoorschrift.json`` en per project te overschrijven.
Alle geometrie blijft in RD New; afronding van coördinaten op 0,01 m
(normenkader ``afronding_m``).
"""
from __future__ import annotations

import copy
import json
import math
import os
import time
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import unary_union

import normen
import pdfdoc
from pdfdoc import tekstbreedte

ROOT = Path(__file__).resolve().parent.parent
VOORSCHRIFT_PAD = ROOT / "data" / "tekenvoorschrift.json"

STANDAARD = {
    "papier": "A3",
    "schaal": 500,
    "overlap_m": 10.0,
    "km_interval_m": 25,
    "rd_raster_m": 50,
    "bladen": {"overzicht": True, "situatie": True, "boorprofielen": True,
               "dwarsprofielen": True, "uitzetstaat": True},
    "ondergrond": {"bgt": True, "kadaster": True, "klic": True, "inmeting": True,
                   "bomen": True},
    "stempel": {
        "opdrachtgever": "Liander N.V.",
        "ontwerpbureau": "",
        "projectnummer": "",
        "werkordernummer": "",
        "ontwerper": "",
        "tekenaar": "",
        "controleur": "",
        "fase": "UO",
        "tekeningnummer_prefix": "",
    },
    "bron": "Standaard InfraEngine — vervang door het tekenvoorschrift van de "
            "netbeheerder (laagnamen, stempel, bladformaat).",
    "laagnamen": {
        "tracé": "MS_TRACE", "boring": "MS_BORING", "boorpunt": "MS_BOORPUNT",
        "kruising": "MS_KRUISING", "mof": "MS_MOF", "station": "MS_STATION",
        "maatvoering": "MS_MAATVOERING", "km": "MS_KILOMETRERING",
        "bladindeling": "BLADINDELING", "wolk": "REVISIEWOLK",
        "bgt": "OND_BGT", "kadaster": "OND_KADASTER", "klic": "OND_KLIC",
        "inmeting": "OND_INMETING", "bomen": "OND_BOMEN", "stempel": "STEMPEL",
    },
}

STATUSSEN = ["concept", "ter controle", "akkoord", "vervallen"]


def _schrijfbaar(pad: Path) -> Path:
    try:
        pad.parent.mkdir(parents=True, exist_ok=True)
        if os.access(pad.parent, os.W_OK):
            return pad
    except OSError:
        pass
    alt = Path.home() / ".infraengine" / pad.name
    alt.parent.mkdir(parents=True, exist_ok=True)
    return alt


def _diep_samen(basis: dict, over: dict | None) -> dict:
    uit = copy.deepcopy(basis)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(uit.get(k), dict):
            uit[k] = _diep_samen(uit[k], v)
        elif v is not None:
            uit[k] = v
    return uit


def voorschrift() -> dict:
    """Tekenvoorschrift: standaard + data/tekenvoorschrift.json."""
    over = {}
    for pad in (VOORSCHRIFT_PAD, Path.home() / ".infraengine" / VOORSCHRIFT_PAD.name):
        if pad.exists():
            try:
                over = json.loads(pad.read_text())
                break
            except (OSError, ValueError):
                pass
    return _diep_samen(STANDAARD, over)


def voorschrift_opslaan(waarden: dict) -> dict:
    pad = _schrijfbaar(VOORSCHRIFT_PAD)
    pad.write_text(json.dumps(waarden, ensure_ascii=False, indent=2))
    return voorschrift()


def instellingen(v: dict) -> dict:
    """Tekenvoorschrift met de projectinstellingen van deze variant erover."""
    return _diep_samen(voorschrift(), (v.get("tekeningen") or {}).get("instellingen"))


# ---------------------------------------------------------------- opmaak blad

RECHTS_MM = 100.0   # kolom voor noordpijl, sleutelkaart, legenda, stempel


def kaartvak(papier: str) -> tuple:
    """(x0, y0, x1, y1) van het kaartvak in mm op het blad."""
    w, h = pdfdoc.PAPIER.get(papier, pdfdoc.PAPIER["A3"])
    return (20.0, 10.0, w - 10.0 - RECHTS_MM - 4.0, h - 10.0)


def _nl(v: float, dec: int = 2) -> str:
    return f"{v:.{dec}f}".replace(".", ",")


def km_tekst(m: float) -> str:
    m = max(0.0, m)
    return f"{int(m // 1000)}+{int(round(m % 1000)):03d}" if round(m % 1000) < 1000 \
        else f"{int(m // 1000) + 1}+000"


# ---------------------------------------------------------------- bladindeling

def _route(v: dict) -> LineString:
    g = shape(v["route"])
    if g.geom_type == "MultiLineString":
        return LineString([c for l in g.geoms for c in l.coords])
    return g


def _substring(route: LineString, a: float, b: float) -> LineString:
    from shapely.ops import substring
    s = substring(route, a, b)
    return s if s.geom_type == "LineString" else LineString([route.interpolate(a), route.interpolate(b)])


def _past(lijn: LineString, hoek: float, w: float, h: float) -> tuple | None:
    """Past de lijn (gedraaid over -hoek) in w×h? Geeft (cx, cy) in RD."""
    ca, sa = math.cos(hoek), math.sin(hoek)
    us = [x * ca + y * sa for x, y in lijn.coords]
    vs = [-x * sa + y * ca for x, y in lijn.coords]
    if max(us) - min(us) > w or max(vs) - min(vs) > h:
        return None
    uc, vc = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
    return uc * ca - vc * sa, uc * sa + vc * ca


def bladindeling(v: dict, inst: dict | None = None) -> list[dict]:
    """Bladen langs het tracé: per blad een zo lang mogelijk stuk dat, gedraaid
    langs de koorde, binnen het kaartvak past (met ruimte voor labels)."""
    inst = inst or instellingen(v)
    route = _route(v)
    schaal = float(inst["schaal"])
    x0, y0, x1, y1 = kaartvak(inst["papier"])
    w_m = (x1 - x0) * schaal / 1000.0
    h_m = (y1 - y0) * schaal / 1000.0
    nut_w, nut_h = w_m * 0.90, h_m * 0.70
    overlap = float(inst.get("overlap_m", 10.0))
    stap = max(1.0, w_m / 40)
    bladen, s, L = [], 0.0, route.length
    while s < L - 0.01:
        beste = None
        e = min(L, s + stap)
        while True:
            stuk = _substring(route, s, e)
            a, b = stuk.coords[0], stuk.coords[-1]
            hoek = math.atan2(b[1] - a[1], b[0] - a[0]) if stuk.length > 0.5 else 0.0
            if hoek > math.pi / 2:
                hoek -= math.pi
            elif hoek <= -math.pi / 2:
                hoek += math.pi
            c = _past(stuk, hoek, nut_w, nut_h)
            if c is None:
                break
            beste = (e, hoek, c)
            if e >= L:
                break
            e = min(L, e + stap)
        if beste is None:  # zelfs het kleinste stuk past niet (scherpe lus)
            e = min(L, s + stap)
            stuk = _substring(route, s, e)
            m = stuk.interpolate(0.5, normalized=True)
            beste = (e, 0.0, (m.x, m.y))
        e, hoek, (cx, cy) = beste
        ca, sa = math.cos(hoek), math.sin(hoek)
        hw, hh = w_m / 2, h_m / 2
        hoeken = [(cx + u * ca - vv * sa, cy + u * sa + vv * ca)
                  for u, vv in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
        bladen.append({
            "nr": len(bladen) + 1,
            "van_m": round(s, 2), "tot_m": round(e, 2),
            "cx": round(cx, 2), "cy": round(cy, 2),
            "hoek_gr": round(math.degrees(hoek), 4),
            "w_m": round(w_m, 2), "h_m": round(h_m, 2),
            "polygoon": [[round(x, 2), round(y, 2)] for x, y in hoeken],
        })
        if e >= L - 0.01:
            break
        s = max(s + stap, e - overlap)
    return bladen


class Transform:
    """RD → papier (mm) voor één blad."""

    def __init__(self, blad: dict, vak: tuple, schaal: float):
        self.cx, self.cy = blad["cx"], blad["cy"]
        self.hoek = math.radians(blad["hoek_gr"])
        self.ca, self.sa = math.cos(self.hoek), math.sin(self.hoek)
        self.k = 1000.0 / schaal
        self.px = (vak[0] + vak[2]) / 2
        self.py = (vak[1] + vak[3]) / 2

    def __call__(self, x, y=None):
        if y is None:
            x, y = x
        dx, dy = x - self.cx, y - self.cy
        u = dx * self.ca + dy * self.sa
        w = -dx * self.sa + dy * self.ca
        return (self.px + u * self.k, self.py + w * self.k)

    def pts(self, coords):
        return [self(c[0], c[1]) for c in coords]

    def hoek_papier(self, hoek_rd_rad: float) -> float:
        """Richting in RD → richting op papier (graden)."""
        return math.degrees(hoek_rd_rad - self.hoek)


# ---------------------------------------------------------------- stijlen

BGT_STIJL = {  # collectie: (vulling, rand, dikte)
    "begroeidterreindeel": ("#F2F7EE", "#C9D6BF", 0.08),
    "onbegroeidterreindeel": ("#F7F5F0", "#D2CFC8", 0.08),
    "ondersteunendwegdeel": ("#EDF4E6", "#AEB8A6", 0.10),
    "wegdeel": ("#E9E9E9", "#8E8E8E", 0.13),
    "waterdeel": ("#D7EAF8", "#5E9ED6", 0.15),
    "ondersteunendwaterdeel": ("#E6F1E8", "#9DBFA6", 0.10),
    "overbruggingsdeel": ("#E0E0E0", "#6E6E6E", 0.18),
    "kunstwerkdeel_vlak": ("#E4E4E4", "#7A7A7A", 0.15),
    "tunneldeel": (None, "#7A7A7A", 0.15),
    "overigbouwwerk": ("#ECEAE6", "#7D7D7D", 0.15),
    "pand": ("#E4D8CC", "#6B5A4A", 0.22),
    "spoor": (None, "#3C3C3C", 0.30),
}
BGT_VOLGORDE = list(BGT_STIJL)
WEG_VUL = {"rijbaan": "#E2E2E2", "fietspad": "#F1E4E1", "voetpad": "#F0EDE6",
           "parkeervlak": "#ECECEC", "inrit": "#EEEEEE"}

KLIC_KLEUR = {
    "hoogspanning": "#FF0000", "landelijkHoogspanningsnet": "#FF0000",
    "middenspanning": "#A0122E", "laagspanning": "#D2691E",
    "datatransport": "#00A651", "gasHogeDruk": "#E69500", "gasLageDruk": "#D4B800",
    "petrochemie": "#FF6A00", "buisleidingGevaarlijkeInhoud": "#FF6A00",
    "water": "#1F5BFF", "rioolVrijverval": "#8B5A2B",
    "rioolOnderOverOfOnderdruk": "#5C3A1A", "warmte": "#B000B5", "overig": "#6E6E6E",
}

ROOD = "#C8322B"
CYAAN = "#00839A"
MAGENTA = "#9E2A8C"
GROEN = "#2E7D32"
WOLK = "#D81B60"


def _ringen(g):
    if g.geom_type == "Polygon":
        return [[g.exterior.coords] + [r.coords for r in g.interiors]]
    if g.geom_type == "MultiPolygon":
        return [[p.exterior.coords] + [r.coords for r in p.interiors] for p in g.geoms]
    return []


def _lijnen(g):
    if g.geom_type == "LineString":
        return [g.coords]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        return [l.coords for l in g.geoms if l.geom_type == "LineString"]
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return [r for poly in _ringen(g) for r in poly]
    return []


# ---------------------------------------------------------------- situatieblad

def _teken_ondergrond(p, T, bereik, og: dict, inst: dict):
    aan = inst.get("ondergrond", {})
    if aan.get("bgt", True):
        bgt = og.get("bgt") or {}
        for coll in BGT_VOLGORDE:
            vul, rand, dikte = BGT_STIJL[coll]
            for g, props in bgt.get(coll, []):
                if not g.intersects(bereik):
                    continue
                g = g.intersection(bereik)
                if g.is_empty:
                    continue
                if coll == "spoor":
                    for l in _lijnen(g):
                        p.lijn(T.pts(l), dikte, rand, streep=[3.0, 1.0])
                    continue
                if vul:
                    f = (props.get("functie") or "").lower()
                    v = next((c for k, c in WEG_VUL.items() if k in f), vul) \
                        if coll == "wegdeel" else vul
                    for poly in _ringen(g):
                        p.vlak_met_gaten([T.pts(r) for r in poly], v)
                for l in _lijnen(g):
                    p.lijn(T.pts(l), dikte, rand)
    if aan.get("kadaster", True):
        for g, props in og.get("percelen") or []:
            if not g.intersects(bereik):
                continue
            for l in _lijnen(g.boundary.intersection(bereik)):
                p.lijn(T.pts(l), 0.12, "#A0619A", streep=[2.0, 0.7, 0.4, 0.7])
            nr = props.get("perceelnummer")
            if nr and g.area > 40:
                c = g.representative_point()
                if bereik.contains(c):
                    p.tekst(*T(c.x, c.y), str(nr), 1.6, uitlijn="midden",
                            basis="midden", kleur="#A0619A")
    if aan.get("bomen", True):
        for b in og.get("bomen") or []:
            if bereik.contains(Point(b[0], b[1])):
                x, y = T(b[0], b[1])
                p.cirkel(x, y, max(0.6, b[2] * T.k), 0.12, GROEN, streep=[0.6, 0.6])
                p.cirkel(x, y, 0.4, 0.15, GROEN, vul=GROEN)
    if aan.get("inmeting", True):
        for meta, f, g in og.get("inmeting") or []:
            if not g.intersects(bereik):
                continue
            if g.geom_type == "Point":
                x, y = T(g.x, g.y)
                if f.get("t") and "b" not in f:
                    p.tekst(x, y, f["t"], max(1.2, min(2.2, f.get("h", 0.5) * T.k)),
                            hoek=f.get("r", 0.0) - math.degrees(T.hoek), kleur="#2B2B2B")
                else:
                    p.lijn([(x - 0.5, y), (x + 0.5, y)], 0.12, "#2B2B2B")
                    p.lijn([(x, y - 0.5), (x, y + 0.5)], 0.12, "#2B2B2B")
            else:
                for l in _lijnen(g.intersection(bereik)):
                    p.lijn(T.pts(l), 0.15, "#2B2B2B")
    if aan.get("klic", True):
        for g, props in og.get("klic") or []:
            if not g.intersects(bereik):
                continue
            kleur = KLIC_KLEUR.get(props.get("thema") or "", "#6E6E6E")
            for l in _lijnen(g.intersection(bereik)):
                p.lijn(T.pts(l), 0.25, kleur, streep=[2.5, 0.8])


def _maatlijn(p, a, b, tekst, kleur="#000000"):
    p.lijn([a, b], 0.13, kleur)
    hoek = math.atan2(b[1] - a[1], b[0] - a[0])
    n = (-math.sin(hoek) * 0.8, math.cos(hoek) * 0.8)
    for q in (a, b):
        p.lijn([(q[0] - n[0], q[1] - n[1]), (q[0] + n[0], q[1] + n[1])], 0.13, kleur)
    gr = math.degrees(hoek)
    if gr > 90:
        gr -= 180
    elif gr < -90:
        gr += 180
    m = ((a[0] + b[0]) / 2 + n[0] * 0.9, (a[1] + b[1]) / 2 + n[1] * 0.9)
    p.tekst(m[0], m[1], tekst, 1.6, hoek=gr, uitlijn="midden", kleur=kleur)


def _wolk(p, punten, kleur=WOLK):
    """Revisiewolk langs een (papier)polygoon: boogjes van ± 3 mm."""
    uit = []
    n = len(punten)
    for i in range(n):
        a, b = punten[i], punten[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(L / 3.0))
        for j in range(k):
            t0, t1 = j / k, (j + 1) / k
            p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
            p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            r = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / 2
            h0 = math.atan2(p0[1] - my, p0[0] - mx)
            for s in range(7):  # halve cirkel naar buiten (klokwijs)
                h = h0 - math.pi * s / 6
                uit.append((mx + r * math.cos(h), my + r * math.sin(h)))
    p.lijn(uit + uit[:1], 0.35, kleur)


def _noordpijl(p, x, y, hoek_blad_gr):
    """Noordpijl: RD-noord ten opzichte van het gedraaide blad."""
    a = math.radians(90 - hoek_blad_gr)
    L = 9.0
    tip = (x + L * math.cos(a), y + L * math.sin(a))
    staart = (x - L * 0.6 * math.cos(a), y - L * 0.6 * math.sin(a))
    l = (x + 2.6 * math.cos(a + math.pi / 2), y + 2.6 * math.sin(a + math.pi / 2))
    r = (x + 2.6 * math.cos(a - math.pi / 2), y + 2.6 * math.sin(a - math.pi / 2))
    p.vlak([tip, l, staart], vul="#000000", rand="#000000", dikte=0.2)
    p.vlak([tip, r, staart], vul="#FFFFFF", rand="#000000", dikte=0.2)
    p.tekst(tip[0] + 1.8 * math.cos(a), tip[1] + 1.8 * math.sin(a), "N", 3.2,
            hoek=-hoek_blad_gr, uitlijn="midden", basis="midden", vet=True)


def _legenda(p, x, y, w):
    regels = [
        ("lijn", ROOD, 0.6, None, "MS-tracé (nieuw)"),
        ("lijn", CYAAN, 0.45, [2.0, 1.0], "boring / persing"),
        ("punt", CYAAN, None, None, "intrede-/uittredepunt"),
        ("mof", MAGENTA, None, None, "mof"),
        ("kruis", "#B8771E", None, None, "kruising (bijzonder punt)"),
        ("station", GROEN, None, None, "MS-station"),
        ("lijn", "#000000", 0.13, None, "maatvoering (m)"),
        ("lijn", "#2B2B2B", 0.15, None, "inmeting"),
        ("lijn", "#A0122E", 0.25, [2.5, 0.8], "bestaand K&L (KLIC, indicatief)"),
        ("lijn", "#A0619A", 0.12, [2.0, 0.7, 0.4, 0.7], "kadastrale grens (DKK)"),
        ("cirkel", GROEN, None, None, "boom + wortelzone"),
        ("wolk", WOLK, None, None, "wijziging laatste revisie"),
    ]
    p.tekst(x, y, "LEGENDA", 2.4, vet=True)
    yy = y - 4.5
    for soort, kleur, dikte, streep, tekst in regels:
        if soort == "lijn":
            p.lijn([(x, yy + 0.8), (x + 9, yy + 0.8)], dikte, kleur, streep)
        elif soort == "punt":
            p.cirkel(x + 4.5, yy + 0.8, 0.8, 0.25, kleur)
        elif soort == "mof":
            p.cirkel(x + 4.5, yy + 0.8, 0.9, 0.25, kleur)
            p.lijn([(x + 3.6, yy + 0.8), (x + 5.4, yy + 0.8)], 0.25, kleur)
        elif soort == "kruis":
            p.lijn([(x + 3.5, yy - 0.2), (x + 5.5, yy + 1.8)], 0.3, kleur)
            p.lijn([(x + 3.5, yy + 1.8), (x + 5.5, yy - 0.2)], 0.3, kleur)
        elif soort == "station":
            p.rechthoek(x + 3.3, yy - 0.4, 2.4, 2.4, 0.3, kleur, vul="#FFFFFF")
        elif soort == "cirkel":
            p.cirkel(x + 4.5, yy + 0.8, 1.2, 0.12, kleur, streep=[0.6, 0.6])
        elif soort == "wolk":
            _wolk(p, [(x + 1, yy - 0.5), (x + 8, yy - 0.5), (x + 8, yy + 2.1), (x + 1, yy + 2.1)])
        p.tekst(x + 12, yy, tekst, 1.9)
        yy -= 4.0
    return yy


def _stempel(p, x, y, w, inst, velden, revisies):
    """Stempel rechtsonder; (x, y) = linksonder. Geeft de bovenkant terug."""
    st = inst["stempel"]
    rij = 4.6
    # revisietabel
    revs = [r for r in revisies if r.get("status") != "vervallen"][-5:]
    hoogte_rev = rij * (len(revs) + 1)
    regels = [
        ("Opdrachtgever", st.get("opdrachtgever", "")),
        ("Ontwerpbureau", st.get("ontwerpbureau", "")),
        ("Project", velden["project"]),
        ("Omschrijving", velden["omschrijving"]),
        ("Tekening", velden["tekening"]),
        ("Tekeningnr.", velden["tekeningnummer"]),
        ("Schaal / formaat", velden["schaal"]),
        ("Fase / status", velden["status"]),
        ("Ontwerper", st.get("ontwerper", "")),
        ("Getekend", st.get("tekenaar", "")),
        ("Gecontroleerd", st.get("controleur", "")),
        ("Datum", velden["datum"]),
    ]
    h = rij * len(regels)
    p.rechthoek(x, y, w, h, 0.35)
    for i, (k, waarde) in enumerate(regels):
        yy = y + h - (i + 1) * rij
        if i:
            p.lijn([(x, yy + rij), (x + w, yy + rij)], 0.1)
        p.tekst(x + 1.5, yy + 1.4, k, 1.7, kleur="#555555")
        t = str(waarde or "")
        while t and tekstbreedte(t, 2.0, vet=(k == "Tekening")) > w - 30:
            t = t[:-2]
        p.tekst(x + 28, yy + 1.4, t + ("…" if t != str(waarde or "") else ""), 2.0,
                vet=(k in ("Tekening", "Tekeningnr.")))
    p.lijn([(x + 26.5, y), (x + 26.5, y + h)], 0.1)
    # revisies boven het stempel
    yr = y + h + 1.5
    p.rechthoek(x, yr, w, hoogte_rev, 0.25)
    kol = [0, 9, 27, 72, w]
    kop = ["rev", "datum", "omschrijving", "door / akkoord"]
    yy = yr + hoogte_rev - rij
    for i, t in enumerate(kop):
        p.tekst(x + kol[i] + 1.2, yy + 1.4, t, 1.6, vet=True)
    for r in reversed(revs):
        yy -= rij
        p.lijn([(x, yy + rij), (x + w, yy + rij)], 0.1)
        vals = [r.get("rev", ""), r.get("datum", "")[:10],
                (r.get("omschrijving") or "")[:34],
                ((r.get("door") or "") + (" / " + r["akkoord_door"] if r.get("akkoord_door")
                                          else ""))[:18]]
        for i, t in enumerate(vals):
            p.tekst(x + kol[i] + 1.2, yy + 1.4, t, 1.6)
    for k in kol[1:-1]:
        p.lijn([(x + k, yr), (x + k, yr + hoogte_rev)], 0.1)
    return yr + hoogte_rev


def _bronnen_tekst(og_meta: dict) -> list[str]:
    regels = ["Coördinaten RD New (EPSG:28992), hoogten NAP; RDNAPTRANS™2018."]
    regels.append(f"Ondergrond: BGT (PDOK, opgehaald {og_meta.get('datum', '')}), DKK.")
    if og_meta.get("inmetingen"):
        regels.append("Inmeting: " + "; ".join(og_meta["inmetingen"])[:90])
    regels.append("K&L: " + (og_meta.get("klic") or "KLIC niet gekoppeld")[:90])
    regels.append("Ligging K&L indicatief; vooraf proefsleuven (CROW 500).")
    return regels


def _sleutelkaart(p, x, y, w, h, route: LineString, bladen, actief_nr):
    p.rechthoek(x, y, w, h, 0.2)
    xs = [c[0] for b in bladen for c in b["polygoon"]]
    ys = [c[1] for b in bladen for c in b["polygoon"]]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    k = min((w - 4) / max(1.0, bx1 - bx0), (h - 4) / max(1.0, by1 - by0))
    ox = x + (w - (bx1 - bx0) * k) / 2
    oy = y + (h - (by1 - by0) * k) / 2
    f = lambda c: (ox + (c[0] - bx0) * k, oy + (c[1] - by0) * k)
    for b in bladen:
        pts = [f(c) for c in b["polygoon"]]
        if b["nr"] == actief_nr:
            p.vlak(pts, vul="#FDE7E5", rand=ROOD, dikte=0.25)
        else:
            p.vlak(pts, vul="#FFFFFF", rand="#9A9A9A", dikte=0.1)
    p.lijn([f(c) for c in route.coords], 0.3, ROOD)
    for b in bladen:
        c = f((sum(q[0] for q in b["polygoon"]) / 4, sum(q[1] for q in b["polygoon"]) / 4))
        p.tekst(c[0], c[1], str(b["nr"]), 1.4, uitlijn="midden", basis="midden",
                kleur="#444444")


def _rd_raster(p, T, vak, bereik, stap):
    xb0, yb0, xb1, yb1 = bereik.bounds
    x = math.ceil(xb0 / stap) * stap
    while x <= xb1:
        y = math.ceil(yb0 / stap) * stap
        while y <= yb1:
            if bereik.contains(Point(x, y)):
                px, py = T(x, y)
                p.lijn([(px - 1.5, py), (px + 1.5, py)], 0.12, "#555555")
                p.lijn([(px, py - 1.5), (px, py + 1.5)], 0.12, "#555555")
                p.tekst(px + 0.8, py + 0.6, f"{int(x)}", 1.3, kleur="#555555",
                        hoek=-math.degrees(T.hoek) if abs(T.hoek) > 0.01 else 0)
                p.tekst(px + 0.8, py - 1.9, f"{int(y)}", 1.3, kleur="#555555",
                        hoek=-math.degrees(T.hoek) if abs(T.hoek) > 0.01 else 0)
            y += stap
        x += stap


def _upright(gr: float) -> float:
    while gr > 90:
        gr -= 180
    while gr < -90:
        gr += 180
    return gr


def teken_situatie(pdf, v, stations, blad, bladen, og: dict, og_meta: dict,
                   inst: dict, velden: dict, revisies: list, wolken: list):
    papier = inst["papier"]
    schaal = float(inst["schaal"])
    p = pdf.pagina(papier)
    vak = kaartvak(papier)
    T = Transform(blad, vak, schaal)
    route = _route(v)
    bereik = Polygon(blad["polygoon"])

    # kader
    p.rechthoek(5, 5, p.w - 10, p.h - 10, 0.5)
    p.rechthoek(vak[0], vak[1], vak[2] - vak[0], vak[3] - vak[1], 0.35)

    p.bewaar()
    p.clip(vak[0], vak[1], vak[2] - vak[0], vak[3] - vak[1])
    _teken_ondergrond(p, T, bereik, og, inst)
    _rd_raster(p, T, vak, bereik, float(inst.get("rd_raster_m", 50)))

    # tracé
    p.lijn(T.pts(route.coords), 0.6, ROOD)
    # kilometrering
    stap = float(inst.get("km_interval_m", 25))
    m = 0.0
    while m <= route.length + 1e-6:
        q = route.interpolate(m)
        if bereik.contains(q):
            a = route.interpolate(max(0, m - 0.5))
            b = route.interpolate(min(route.length, m + 0.5))
            h = math.atan2(b.y - a.y, b.x - a.x)
            px, py = T(q.x, q.y)
            hp = math.radians(T.hoek_papier(h))
            nx, ny = -math.sin(hp), math.cos(hp)
            groot = abs(m % (stap * 4)) < 1e-6
            L = 2.2 if groot else 1.3
            p.lijn([(px - nx * L, py - ny * L), (px + nx * L, py + ny * L)], 0.2, ROOD)
            p.tekst(px + nx * (L + 0.8), py + ny * (L + 0.8), km_tekst(m), 1.6,
                    hoek=_upright(math.degrees(hp) + 90), uitlijn="links", basis="midden",
                    kleur=ROOD)
        m += stap
    # hoekpunten (uitzetpunten) met nummer
    for i, c in enumerate(route.coords):
        if bereik.contains(Point(c)):
            px, py = T(c[0], c[1])
            p.cirkel(px, py, 0.45, 0.15, ROOD, vul="#FFFFFF")
            p.tekst(px + 0.9, py - 2.2, f"P{i + 1}", 1.3, kleur=ROOD)
    # boringen
    for b in v.get("boringen") or []:
        coords = (b.get("geometry") or {}).get("coordinates") or [b["intredepunt_rd"],
                                                                   b["uittredepunt_rd"]]
        if not LineString(coords).intersects(bereik):
            continue
        p.lijn(T.pts(coords), 0.45, CYAAN, streep=[2.0, 1.0])
        for punt, kuip, rol in ((b["intredepunt_rd"], b.get("kuip_in_rd"), "IN"),
                                (b["uittredepunt_rd"], b.get("kuip_uit_rd"), "UIT")):
            px, py = T(punt)
            p.cirkel(px, py, 0.9, 0.3, CYAAN, vul="#FFFFFF")
            if kuip:
                p.lijn([T(punt), T(kuip)], 1.0, CYAAN)
            p.tekst(px, py + 1.6, f"{b['nr']} {rol}", 1.6, uitlijn="midden", kleur=CYAAN)
        mid = LineString(coords).interpolate(0.5, normalized=True)
        a, c = coords[0], coords[-1]
        hp = T.hoek_papier(math.atan2(c[1] - a[1], c[0] - a[0]))
        px, py = T(mid.x, mid.y)
        p.tekst(px, py - 3.2, f"{b['nr']} {b['type']} L={_nl(b['lengte_m'], 1)} m",
                1.7, hoek=_upright(hp), uitlijn="midden", kleur=CYAAN)
    # kruisingen (bijzondere punten zonder boring)
    from engine import is_bijzonder_punt
    met_boring = {k for b in v.get("boringen") or [] for k in (b.get("kruisingen") or [])}
    for c in v.get("kruisingen") or []:
        if c["nr"] in met_boring or not is_bijzonder_punt(c):
            continue
        if bereik.contains(Point(c["punt"])):
            px, py = T(c["punt"])
            p.lijn([(px - 1.2, py - 1.2), (px + 1.2, py + 1.2)], 0.3, "#B8771E")
            p.lijn([(px - 1.2, py + 1.2), (px + 1.2, py - 1.2)], 0.3, "#B8771E")
            p.tekst(px + 1.6, py + 1.0, f"{c['nr']} {c['soort']}", 1.6, kleur="#B8771E")
    # moffen
    for mf in v.get("moffen") or []:
        if bereik.contains(Point(mf["punt"])):
            px, py = T(mf["punt"])
            p.cirkel(px, py, 1.0, 0.3, MAGENTA, vul="#FFFFFF")
            p.lijn([(px - 1.0, py), (px + 1.0, py)], 0.3, MAGENTA)
            p.tekst(px + 1.4, py + 1.2, mf["nr"], 1.6, kleur=MAGENTA)
    # stations
    for i, s in enumerate(stations or [], 1):
        if bereik.contains(Point(s[0], s[1])):
            px, py = T(s[0], s[1])
            z = 2.0 * T.k
            p.rechthoek(px - z, py - z, 2 * z, 2 * z, 0.35, GROEN, vul="#FFFFFF")
            p.tekst(px, py + z + 1.0, f"MS-station {i}", 1.9, uitlijn="midden",
                    vet=True, kleur=GROEN)
    # maatvoering
    for x in v.get("maatlijnen") or []:
        for ml in x.get("maatlijnen", []):
            if bereik.contains(Point(ml["van"])):
                _maatlijn(p, T(ml["van"]), T(ml["tot"]), _nl(ml["afstand_m"]))
    for ml in v.get("maatlijnen_handmatig") or []:
        if bereik.contains(Point(ml["van"])):
            _maatlijn(p, T(ml["van"]), T(ml["tot"]), _nl(ml["afstand_m"]), MAGENTA)
    # aansluitlijnen naar de buurbladen
    for buur, m in ((blad["nr"] - 1, blad["van_m"]), (blad["nr"] + 1, blad["tot_m"])):
        if not (1 <= buur <= len(bladen)) or m <= 0.01 or m >= route.length - 0.01:
            continue
        q = route.interpolate(m)
        a = route.interpolate(max(0, m - 0.5))
        b = route.interpolate(min(route.length, m + 0.5))
        hp = math.radians(T.hoek_papier(math.atan2(b.y - a.y, b.x - a.x)))
        nx, ny = -math.sin(hp), math.cos(hp)
        px, py = T(q.x, q.y)
        p.lijn([(px - nx * 14, py - ny * 14), (px + nx * 14, py + ny * 14)], 0.4,
               "#000000", streep=[4.0, 1.0, 1.0, 1.0])
        p.tekst(px + nx * 15, py + ny * 15, f"aansluiting blad {buur} ({km_tekst(m)})",
                1.8, hoek=_upright(math.degrees(hp) + 90), uitlijn="midden", vet=True)
    # wijzigingswolken (laatste revisie)
    for w in wolken:
        g = Polygon(w["polygoon"])
        if g.intersects(bereik):
            _wolk(p, T.pts(list(g.exterior.coords)[:-1]))
            c = g.exterior.coords[0]
            px, py = T(c)
            p.vlak([(px - 2, py + 2), (px + 2, py + 2), (px, py + 5.4)], vul="#FFFFFF",
                   rand=WOLK, dikte=0.3)
            p.tekst(px, py + 2.6, w["rev"], 1.8, uitlijn="midden", vet=True, kleur=WOLK)
    p.herstel()

    # rechterkolom
    rx = vak[2] + 4.0
    rw = RECHTS_MM
    top = p.h - 10
    _noordpijl(p, rx + 12, top - 13, blad["hoek_gr"])
    p.tekst(rx + 26, top - 6, f"Schaal 1:{int(schaal)} ({papier})", 2.4, vet=True)
    # schaalstok
    lengte_m = {250: 10, 500: 20, 1000: 50}.get(int(schaal), 20)
    sx, sy = rx + 26, top - 13
    seg = lengte_m * 1000 / schaal / 4
    for i in range(4):
        p.rechthoek(sx + i * seg, sy, seg, 1.2, 0.15, "#000000",
                    vul="#000000" if i % 2 == 0 else "#FFFFFF")
    for i in range(5):
        p.tekst(sx + i * seg, sy + 2.0, f"{int(lengte_m * i / 4)}", 1.4, uitlijn="midden")
    p.tekst(sx + 4 * seg + 2, sy, "m", 1.6)
    _sleutelkaart(p, rx, top - 82, rw, 60, route, bladen, blad["nr"])
    y_leg = _legenda(p, rx, top - 88, rw)
    regels = _bronnen_tekst(og_meta)
    yb = y_leg - 2
    for r in regels:
        p.tekst(rx, yb, r, 1.55, kleur="#444444")
        yb -= 2.6
    _stempel(p, rx, 10, rw, inst, velden, revisies)
    return p


# ---------------------------------------------------------------- overzicht

def teken_overzicht(pdf, v, stations, bladen, inst, velden, revisies, lijst_tekeningen):
    papier = inst["papier"]
    p = pdf.pagina(papier)
    vak = kaartvak(papier)
    route = _route(v)
    xs = [c[0] for b in bladen for c in b["polygoon"]]
    ys = [c[1] for b in bladen for c in b["polygoon"]]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    k = min((vak[2] - vak[0] - 10) / max(1, bx1 - bx0),
            (vak[3] - vak[1] - 10) / max(1, by1 - by0))
    schalen = [500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000]
    schaal = next((s for s in schalen if 1000.0 / s <= k), schalen[-1])
    T = Transform({"cx": (bx0 + bx1) / 2, "cy": (by0 + by1) / 2, "hoek_gr": 0}, vak, schaal)
    p.rechthoek(5, 5, p.w - 10, p.h - 10, 0.5)
    p.rechthoek(vak[0], vak[1], vak[2] - vak[0], vak[3] - vak[1], 0.35)
    p.bewaar()
    p.clip(vak[0], vak[1], vak[2] - vak[0], vak[3] - vak[1])
    for b in bladen:
        p.vlak(T.pts(b["polygoon"]), vul="#F7F7F7", rand="#7A7A7A", dikte=0.2)
    p.lijn(T.pts(route.coords), 0.6, ROOD)
    for b in bladen:
        c = (sum(q[0] for q in b["polygoon"]) / 4, sum(q[1] for q in b["polygoon"]) / 4)
        px, py = T(c)
        p.tekst(px, py + 2, str(b["nr"]) if len(bladen) > 20 else f"blad {b['nr']}",
                1.6 if len(bladen) > 20 else 2.0, uitlijn="midden", vet=True,
                kleur="#444444")
    for i, s in enumerate(stations or [], 1):
        px, py = T(s[0], s[1])
        p.rechthoek(px - 1.4, py - 1.4, 2.8, 2.8, 0.35, GROEN, vul="#FFFFFF")
        p.tekst(px, py + 2.4, f"MS-station {i}", 2.0, uitlijn="midden", vet=True, kleur=GROEN)
    for wp in v.get("werkpakketten") or []:
        q = route.interpolate((wp["chainage_van_m"] + wp["chainage_tot_m"]) / 2)
        px, py = T(q.x, q.y)
        p.tekst(px, py - 4, f"{wp['nr']}", 2.0, uitlijn="midden", kleur=ROOD)
    p.herstel()
    rx = vak[2] + 4.0
    top = p.h - 10
    _noordpijl(p, rx + 12, top - 13, 0.0)
    p.tekst(rx + 26, top - 6, f"Schaal 1:{schaal} ({papier})", 2.4, vet=True)
    p.tekst(rx, top - 30, "TEKENINGENLIJST", 2.4, vet=True)
    yy = top - 35
    for nr, oms in lijst_tekeningen:
        p.tekst(rx, yy, nr, 1.8, vet=True)
        p.tekst(rx + 34, yy, oms[:44], 1.8)
        yy -= 3.0
        if yy < 110:
            p.tekst(rx, yy, "…", 1.8)
            break
    _stempel(p, rx, 10, RECHTS_MM, inst, {**velden, "schaal": f"1:{schaal} ({papier})"},
             revisies)
    return p


# ---------------------------------------------------------------- boorprofiel

def boorlijn_profiel(b: dict, maaiveld: list, obstakels: list) -> list:
    """Indicatieve boorlijn [(s, z)] langs de boring.

    Gestuurd (HDD/nanodrill): intrede onder de intredehoek, met de boogstraal
    naar de diepte-eis onder het obstakel, horizontaal eronder door en onder
    de uittredehoek weer omhoog. Persing/raket: horizontaal op diepte tussen
    de kuipen. Diepte = onderkant buis onder het laagste maaiveld boven het
    obstakel (engine.boor_diepte_m)."""
    import engine

    L = b["lengte_m"]
    if not maaiveld:
        maaiveld = [(0.0, 0.0), (L, 0.0)]

    def mv(s):
        for (s0, z0), (s1, z1) in zip(maaiveld, maaiveld[1:]):
            if s0 <= s <= s1 and z0 is not None and z1 is not None:
                return z0 + (z1 - z0) * (s - s0) / max(1e-9, s1 - s0)
        zs = [z for _, z in maaiveld if z is not None]
        return zs[0] if s < maaiveld[0][0] else zs[-1]

    soort = (b.get("obstakel") or "rijbaan").split(" ")[0]
    soort = soort if soort in ("water", "spoor", "rijbaan") else "rijbaan"
    diepte = b.get("boordiepte_m") or engine.boor_diepte_m(b["type"], soort)
    o0 = min((o[0] for o in obstakels), default=L * 0.35)
    o1 = max((o[1] for o in obstakels), default=L * 0.65)
    z_mv_obst = min(mv(s) for s in [o0 + (o1 - o0) * i / 10 for i in range(11)])
    z_bodem = z_mv_obst - diepte
    z_in, z_uit = mv(0.0), mv(L)
    if b["type"] in engine.GESTUURD:
        pre = engine.GESTUURD[b["type"]]
        a_in = math.radians(normen.waarde(f"{pre}_intredehoek_gr"))
        a_uit = math.radians(normen.waarde(f"{pre}_uittredehoek_gr"))
        R = engine.boogstraal_m(b["type"])
        pts = []

        def tak(z_start, a, richting):
            """(s, z) vanaf het in-/uittredepunt tot horizontaal op z_bodem."""
            uit = [(0.0, z_start)]
            dz_boog = R * (1 - math.cos(a))
            dz = z_start - z_bodem
            if dz <= dz_boog:
                a = math.acos(max(-1.0, 1 - dz / R))
                recht = 0.0
            else:
                recht = (dz - dz_boog) / math.sin(a)
            s = recht * math.cos(a)
            uit.append((s, z_start - recht * math.sin(a)))
            for i in range(1, 13):
                t = a * (1 - i / 12)
                uit.append((s + R * (math.sin(a) - math.sin(t)),
                            z_start - recht * math.sin(a) - R * (math.cos(t) - math.cos(a))))
            return uit
        links = tak(z_in, a_in, 1)
        rechts = [(L - s, z) for s, z in tak(z_uit, a_uit, -1)]
        if links[-1][0] <= rechts[-1][0]:
            pts = links + list(reversed(rechts))
        else:  # te kort voor een horizontaal stuk: V-vorm, ondiepste snijpunt
            pts = [p for p in links if p[0] <= L / 2] + \
                  list(reversed([p for p in rechts if p[0] >= L / 2]))
        return pts
    return [(0.0, z_bodem), (L, z_bodem)]


def _mooi_schaal(nodig: float, reeks=(50, 100, 200, 250, 500, 1000, 2000, 2500, 5000)):
    return next((s for s in reeks if s >= nodig), reeks[-1])


def teken_boorprofielen(pdf, v, inst, velden_fn, revisies, klic_fn=None,
                        hoogte_fn=None):
    """Twee boringen per blad: lengteprofiel + gegevensblok."""
    route = _route(v)
    boringen = v.get("boringen") or []
    kruis = {c["nr"]: c for c in v.get("kruisingen") or []}
    pagina_nrs = []
    for i in range(0, len(boringen), 2):
        p = pdf.pagina(inst["papier"])
        vak = kaartvak(inst["papier"])
        p.rechthoek(5, 5, p.w - 10, p.h - 10, 0.5)
        hoogte_vak = (vak[3] - vak[1]) / 2
        for j, b in enumerate(boringen[i:i + 2]):
            y0 = vak[3] - (j + 1) * hoogte_vak
            _boorprofiel(p, b, route, kruis, (vak[0], y0 + 4, vak[2], y0 + hoogte_vak - 2),
                         klic_fn, hoogte_fn)
        nr = len(pdf.paginas)
        velden = velden_fn("boorprofiel", " + ".join(b["nr"] for b in boringen[i:i + 2]))
        rx = vak[2] + 4.0
        _stempel(p, rx, 10, RECHTS_MM, inst, velden, revisies)
        p.tekst(rx, p.h - 16, "LENGTEPROFIELEN BORINGEN", 2.6, vet=True)
        p.tekstblok(rx, p.h - 22, [
            "Boorlijn indicatief: definitief boorplan",
            "door de boorspecialist (NEN 3651/ NEN 3650).",
            "Maaiveld uit AHN (DTM 0,5 m) of inmeting.",
            "K&L uit KLIC: ligging en diepte indicatief,",
            "vooraf proefsleuven (CROW 500).",
            "Hoogten in m t.o.v. NAP.",
        ], 1.9, kleur="#444444")
        pagina_nrs.append(nr)
    return pagina_nrs


def _boorprofiel(p, b, route, kruis, kader, klic_fn, hoogte_fn):
    x0, y0, x1, y1 = kader
    L = b["lengte_m"]
    maaiveld = [(m, z) for m, z in (b.get("hoogteprofiel") or []) if z is not None]
    if hoogte_fn:
        coords = (b.get("geometry") or {}).get("coordinates") or []
        lijn = LineString(coords) if len(coords) >= 2 else None
        if lijn is not None:
            ingemeten = []
            for k in range(0, int(L) + 1, 2):
                q = lijn.interpolate(min(k, lijn.length))
                z = hoogte_fn(q.x, q.y)
                if z is not None:
                    ingemeten.append((float(k), z))
            if len(ingemeten) >= max(3, len(maaiveld) // 3):
                maaiveld = ingemeten
    s_in = route.project(Point(b["intredepunt_rd"]))
    obstakels = []
    for nr in b.get("kruisingen") or [b.get("kruising")]:
        c = kruis.get(nr)
        if c:
            obstakels.append((c["chainage_van_m"] - s_in, c["chainage_tot_m"] - s_in,
                              c["soort"], c.get("breedte_m")))
    profiel = boorlijn_profiel(b, maaiveld, obstakels)
    zs = [z for _, z in maaiveld] + [z for _, z in profiel]
    zmin, zmax = min(zs) - 0.8, max(zs) + 1.2
    links = 22.0
    bw = x1 - x0 - links - 4
    bh = y1 - y0 - 26
    hs = _mooi_schaal(L * 1000 / bw)
    vs = _mooi_schaal((zmax - zmin) * 1000 / bh, (20, 25, 50, 100, 200, 250, 500))
    ox, oy = x0 + links, y0 + 18
    fx = lambda s: ox + s * 1000 / hs
    fz = lambda z: oy + (z - zmin) * 1000 / vs
    titel = (f"{b['nr']} — {b['type']}, L = {_nl(L, 1)} m, "
             f"{b.get('obstakel', '')}")
    p.tekst(x0, y1 - 2, titel[:110], 2.6, vet=True)
    p.tekst(x0, y1 - 6, f"Horizontaal 1:{hs}, verticaal 1:{vs}; dekking: "
            f"{(b.get('dekking_eis') or '')[:120]}", 1.8, kleur="#444444")
    # assen en raster
    p.rechthoek(ox, oy, L * 1000 / hs, (zmax - zmin) * 1000 / vs, 0.2, "#888888")
    z = math.ceil(zmin)
    while z <= zmax:
        p.lijn([(ox, fz(z)), (fx(L), fz(z))], 0.08, "#CCCCCC")
        p.tekst(ox - 1.5, fz(z), f"{z:+.0f}".replace("+0", "0"), 1.6, uitlijn="rechts",
                basis="midden")
        z += 1
    p.tekst(x0, oy + (zmax - zmin) * 1000 / vs / 2, "NAP (m)", 1.6, hoek=90,
            uitlijn="midden")
    # obstakels
    for s0, s1, soort, br in obstakels:
        kleur = {"water": "#D7EAF8", "spoor": "#DDDDDD"}.get(soort, "#ECECEC")
        p.vlak([(fx(max(0, s0)), oy), (fx(min(L, s1)), oy),
                (fx(min(L, s1)), fz(zmax)), (fx(max(0, s0)), fz(zmax))], vul=kleur)
        p.tekst((fx(max(0, s0)) + fx(min(L, s1))) / 2, fz(zmax) - 3, soort, 1.7,
                uitlijn="midden", kleur="#555555")
    # maaiveld
    if maaiveld:
        p.lijn([(fx(s), fz(z)) for s, z in maaiveld], 0.35, "#5D4037")
        p.tekst(fx(maaiveld[0][0]) + 1, fz(maaiveld[0][1]) + 1.2, "maaiveld", 1.6,
                kleur="#5D4037")
    # boorlijn
    p.lijn([(fx(s), fz(z)) for s, z in profiel], 0.6, CYAAN)
    for s, rol in ((0.0, "intrede"), (L, "uittrede")):
        z = min(profiel, key=lambda q: abs(q[0] - s))[1]
        p.cirkel(fx(s), fz(z), 0.9, 0.3, CYAAN, vul="#FFFFFF")
        p.tekst(fx(s), fz(z) + 2.5, rol, 1.7, uitlijn="midden", kleur=CYAAN)
    z_bodem = min(z for _, z in profiel)
    p.tekst(fx(L / 2), fz(z_bodem) - 3, f"onderkant buis ca. {_nl(z_bodem, 2)} m NAP",
            1.7, uitlijn="midden", kleur=CYAAN)
    # kruisende K&L
    if klic_fn:
        coords = (b.get("geometry") or {}).get("coordinates") or []
        lijn = LineString(coords) if len(coords) >= 2 else None
        if lijn is not None:
            bb = lijn.bounds
            for g, props in klic_fn((bb[0] - 2, bb[1] - 2, bb[2] + 2, bb[3] + 2)):
                x = g.intersection(lijn)
                if x.is_empty:
                    continue
                pts = [x] if x.geom_type == "Point" else [q for q in getattr(x, "geoms", [])
                                                          if q.geom_type == "Point"]
                for q in pts:
                    s = lijn.project(q)
                    kleur = KLIC_KLEUR.get(props.get("thema") or "", "#6E6E6E")
                    zt = min(maaiveld, key=lambda m: abs(m[0] - s))[1] if maaiveld else 0
                    d = props.get("diepte")
                    try:
                        d = float(d) if d not in (None, "") else None
                    except (TypeError, ValueError):
                        d = None
                    z_k = zt - (d if d is not None else 0.8)
                    p.lijn([(fx(s), fz(zt)), (fx(s), fz(z_k))], 0.2, kleur, streep=[1, 0.6])
                    p.cirkel(fx(s), fz(z_k), 0.7, 0.25, kleur, vul="#FFFFFF")
                    p.tekst(fx(s) + 1, fz(z_k) - 2.2,
                            f"{props.get('thema') or 'K&L'}"
                            + (f" {_nl(d, 2)} m-mv" if d is not None else " (diepte ?)"),
                            1.4, kleur=kleur, hoek=0)
    # maatvoeringstabel onder de as: afstand / maaiveld / boorlijn
    p.tekst(x0, oy - 5, "afstand (m)", 1.5, kleur="#444444")
    p.tekst(x0, oy - 9, "maaiveld NAP", 1.5, kleur="#444444")
    p.tekst(x0, oy - 13, "boorlijn NAP", 1.5, kleur="#444444")
    stap = 5 if L <= 60 else 10 if L <= 150 else 25
    s = 0.0
    while s <= L + 1e-6:
        x = fx(s)
        p.lijn([(x, oy), (x, oy - 1.2)], 0.15)
        p.tekst(x, oy - 5, _nl(s, 0), 1.4, uitlijn="midden")
        if maaiveld:
            zm = min(maaiveld, key=lambda m: abs(m[0] - s))[1]
            p.tekst(x, oy - 9, _nl(zm, 2), 1.4, uitlijn="midden")
        zb = min(profiel, key=lambda q: abs(q[0] - s))[1]
        p.tekst(x, oy - 13, _nl(zb, 2), 1.4, uitlijn="midden")
        s += stap
    # coördinaten
    p.tekst(x1 - 2, y1 - 2, f"intrede X {_nl(b['intredepunt_rd'][0])}  Y "
            f"{_nl(b['intredepunt_rd'][1])}", 1.7, uitlijn="rechts")
    p.tekst(x1 - 2, y1 - 6, f"uittrede X {_nl(b['uittredepunt_rd'][0])}  Y "
            f"{_nl(b['uittredepunt_rd'][1])}", 1.7, uitlijn="rechts")
    buis = b.get("mantelbuis") or b.get("buis") or ""
    if buis:
        p.tekst(x1 - 2, y1 - 10, f"buis: {str(buis)[:70]}", 1.7, uitlijn="rechts")


# ---------------------------------------------------------------- dwarsprofiel

SLEUF_AANNAME = {"breedte_m": 0.40, "zand_m": 0.10, "lint_boven_m": 0.20}


def teken_dwarsprofielen(pdf, v, inst, velden, revisies):
    liggingen = []
    for s in v.get("segmenten") or []:
        lig = s.get("ligging") or ""
        if lig and lig not in liggingen and not any(
                w in lig for w in ("pand", "verboden", "onbekend", "spoor", "water")):
            liggingen.append(lig)
    if not liggingen:
        return None
    p = pdf.pagina(inst["papier"])
    vak = kaartvak(inst["papier"])
    p.rechthoek(5, 5, p.w - 10, p.h - 10, 0.5)
    n = len(liggingen)
    kol = 3 if n > 4 else 2
    rijen = math.ceil(n / kol)
    cw = (vak[2] - vak[0]) / kol
    ch = (vak[3] - vak[1]) / rijen
    diep_max = max(normen.waarde(r) for r in ("dekking_rijbaan_m", "dekking_trottoir_m",
                                                 "dekking_berm_m")) + 0.25
    schaal = next((s for s in (10, 20, 25, 50) if diep_max * 1000 / s <= ch - 24), 50)
    k = 1000 / schaal
    for i, lig in enumerate(liggingen):
        cx0 = vak[0] + (i % kol) * cw
        cy1 = vak[3] - (i // kol) * ch
        if "rijbaan" in lig:
            dek = normen.waarde("dekking_rijbaan_m")
        elif "water" in lig:
            dek = normen.waarde("dekking_water_m")
        elif any(w in lig for w in ("voetpad", "trottoir", "fietspad", "parkeer")):
            dek = normen.waarde("dekking_trottoir_m")
        else:
            dek = normen.waarde("dekking_berm_m")
        b = SLEUF_AANNAME["breedte_m"]
        d_kabel = 0.05
        diep = dek + d_kabel + SLEUF_AANNAME["zand_m"]
        mx = cx0 + cw / 2
        p.tekst(cx0 + 4, cy1 - 5, f"Dwarsprofiel — {lig}", 2.3, vet=True)
        p.tekst(cx0 + 4, cy1 - 9, f"schaal 1:{schaal}", 1.7, kleur="#444444")
        my = cy1 - 18
        # maaiveld
        bw = min(cw / 2 - 6, 1.6 * k)
        p.lijn([(mx - bw, my), (mx + bw, my)], 0.4, "#5D4037")
        verhard = any(w in lig for w in ("rijbaan", "voetpad", "fietspad", "parkeer", "trottoir"))
        if verhard:
            p.vlak([(mx - bw, my), (mx + bw, my), (mx + bw, my - 0.25 * k),
                    (mx - bw, my - 0.25 * k)], vul="#E2E2E2", rand="#8E8E8E", dikte=0.15)
            p.tekst(mx + bw, my + 1.0, "verharding + fundering (indicatief)", 1.5,
                    uitlijn="rechts", kleur="#555555")
        # sleuf
        sl = [(mx - b / 2 * k, my), (mx + b / 2 * k, my),
              (mx + b / 2 * k, my - diep * k), (mx - b / 2 * k, my - diep * k)]
        p.vlak(sl, vul="#FFF8E1", rand="#000000", dikte=0.3)
        # zandomhulling
        zb = SLEUF_AANNAME["zand_m"]
        p.vlak([(mx - b / 2 * k, my - (dek - zb) * k), (mx + b / 2 * k, my - (dek - zb) * k),
                (mx + b / 2 * k, my - diep * k), (mx - b / 2 * k, my - diep * k)],
               vul="#FFE9A8", rand="#C9A640", dikte=0.15)
        # kabel
        p.cirkel(mx, my - (dek + d_kabel / 2) * k, d_kabel / 2 * k, 0.3, ROOD, vul="#FFD7D3")
        # lint
        yl = my - (dek - SLEUF_AANNAME["lint_boven_m"]) * k
        p.lijn([(mx - b / 2 * k + 0.5, yl), (mx + b / 2 * k - 0.5, yl)], 0.3, "#E65100",
               streep=[1.0, 0.5])
        # maten
        _maatlijn(p, (mx + b / 2 * k + 6, my - dek * k), (mx + b / 2 * k + 6, my),
                  f"dekking ≥ {_nl(dek)}")
        _maatlijn(p, (mx - b / 2 * k, my + 4), (mx + b / 2 * k, my + 4), _nl(b))
        p.tekst(mx - b / 2 * k - 2, my - (dek + d_kabel / 2) * k, "MS-kabel", 1.6,
                uitlijn="rechts", basis="midden", kleur=ROOD)
        p.tekst(mx - b / 2 * k - 2, yl, "markeringslint", 1.6, uitlijn="rechts",
                basis="midden", kleur="#E65100")
        p.tekst(mx - b / 2 * k - 2, my - (diep - zb / 2) * k, "zandomhulling", 1.6,
                uitlijn="rechts", basis="midden", kleur="#8D6E00")
    rx = vak[2] + 4.0
    p.tekst(rx, p.h - 16, "STANDAARD DWARSPROFIELEN", 2.6, vet=True)
    p.tekstblok(rx, p.h - 22, [
        f"Dekking volgens normenkader ({normen.bron('dekking_berm_m')}).",
        f"Sleufbreedte {_nl(SLEUF_AANNAME['breedte_m'])} m, zandomhulling "
        f"{_nl(SLEUF_AANNAME['zand_m'])} m,",
        f"lint {_nl(SLEUF_AANNAME['lint_boven_m'])} m boven de kabel: aanname,",
        "toetsen aan de standaard van de netbeheerder.",
        f"Afstand tot parallelle kabels ≥ {_nl(normen.waarde('afstand_kabels_m'))} m",
        "(NEN 7171-1).",
    ], 1.9, kleur="#444444")
    _stempel(p, rx, 10, RECHTS_MM, inst, velden, revisies)
    return len(pdf.paginas)


# ---------------------------------------------------------------- uitzetstaat

def uitzetpunten(v: dict) -> list[dict]:
    route = _route(v)
    uit, m = [], 0.0
    cs = list(route.coords)
    for i, c in enumerate(cs):
        if i:
            m += math.hypot(c[0] - cs[i - 1][0], c[1] - cs[i - 1][1])
        hoek = None
        if 0 < i < len(cs) - 1:
            a = math.atan2(c[1] - cs[i - 1][1], c[0] - cs[i - 1][0])
            b = math.atan2(cs[i + 1][1] - c[1], cs[i + 1][0] - c[0])
            hoek = round(abs(math.degrees((b - a + math.pi) % (2 * math.pi) - math.pi)), 1)
        uit.append({"punt": f"P{i + 1}", "metrering_m": round(m, 2),
                    "x": round(c[0], 2), "y": round(c[1], 2), "knik_gr": hoek})
    return uit


def teken_uitzetstaat(pdf, v, inst, velden, revisies):
    punten = uitzetpunten(v)
    vak = kaartvak(inst["papier"])
    per_kolom = int((vak[3] - vak[1] - 16) / 3.6)
    kolommen = 3
    per_pagina = per_kolom * kolommen
    paginas = []
    for start in range(0, len(punten), per_pagina):
        p = pdf.pagina(inst["papier"])
        p.rechthoek(5, 5, p.w - 10, p.h - 10, 0.5)
        p.tekst(vak[0], vak[3] - 4, "UITZETSTAAT TRACÉ — hoekpunten in RD New (m), "
                "metrering langs het tracé", 2.6, vet=True)
        cw = (vak[2] - vak[0]) / kolommen
        for k in range(kolommen):
            x = vak[0] + k * cw
            y = vak[3] - 12
            for i, t in enumerate(["punt", "metrering", "X", "Y", "knik °"]):
                p.tekst(x + [0, 12, 30, 52, 74][i], y, t, 1.8, vet=True)
            y -= 3.6
            for q in punten[start + k * per_kolom: start + (k + 1) * per_kolom]:
                vals = [q["punt"], km_tekst(q["metrering_m"]) + f" ({_nl(q['metrering_m'])})",
                        _nl(q["x"]), _nl(q["y"]),
                        "" if q["knik_gr"] is None else _nl(q["knik_gr"], 1)]
                for i, t in enumerate(vals):
                    p.tekst(x + [0, 12, 30, 52, 74][i], y, t, 1.7)
                y -= 3.6
        rx = vak[2] + 4.0
        _stempel(p, rx, 10, RECHTS_MM, inst, velden, revisies)
        paginas.append(len(pdf.paginas))
    return paginas


# ---------------------------------------------------------------- revisies

def _snapshot(v: dict) -> dict:
    return {
        "route": [[round(x, 3), round(y, 3)] for x, y in _route(v).coords],
        "punten": ([list(b["intredepunt_rd"]) for b in v.get("boringen") or []]
                   + [list(b["uittredepunt_rd"]) for b in v.get("boringen") or []]
                   + [list(m["punt"]) for m in v.get("moffen") or []]),
    }


def _afwijkend(a: LineString, b: LineString, tol: float, stap: float = 0.5) -> list:
    """Punten van lijn a die verder dan tol van lijn b liggen (bemonsterd)."""
    uit = []
    n = max(1, int(a.length / stap))
    for i in range(n + 1):
        q = a.interpolate(a.length * i / n)
        if q.distance(b) > tol:
            uit.append(q)
    for c in a.coords:
        q = Point(c)
        if q.distance(b) > tol:
            uit.append(q)
    return uit


def wijzigingen(oud: dict | None, nieuw: dict, tol: float = 0.05,
                punt_tol: float = 0.25) -> list:
    """Gebieden (rechthoeken in RD) waar het tracé meer dan ``tol`` is
    verlegd of een boorpunt/mof meer dan ``punt_tol`` is verschoven.
    Afrondingsverschillen (mm) geven dus geen wolk."""
    if not oud:
        return []
    a, b = LineString(oud["route"]), LineString(nieuw["route"])
    punten = _afwijkend(a, b, tol) + _afwijkend(b, a, tol)
    oud_p = [Point(p) for p in oud.get("punten", [])]
    nieuw_p = [Point(p) for p in nieuw.get("punten", [])]
    for lijst, ander in ((oud_p, nieuw_p), (nieuw_p, oud_p)):
        for q in lijst:
            if not ander or min(q.distance(r) for r in ander) > punt_tol:
                punten.append(q)
    if not punten:
        return []
    groot = unary_union([q.buffer(3.0) for q in punten])
    polys = [groot] if groot.geom_type == "Polygon" else list(getattr(groot, "geoms", []))
    uit = []
    for g in polys:
        r = g.minimum_rotated_rectangle
        uit.append([[round(x, 2), round(y, 2)] for x, y in r.exterior.coords[:-1]])
    return uit


def gewijzigd_na_revisie(v: dict) -> bool:
    revs = [r for r in (v.get("tekeningen") or {}).get("revisies", [])
            if r.get("status") != "vervallen"]
    if not revs:
        return False
    return bool(wijzigingen(revs[-1].get("snapshot"), _snapshot(v), tol=0.01, punt_tol=0.05))


def set_van(v: dict) -> dict:
    t = v.setdefault("tekeningen", {})
    t.setdefault("instellingen", {})
    t.setdefault("revisies", [])
    return t


def volgende_rev(revisies: list) -> str:
    gebruikt = [r["rev"] for r in revisies]
    if not gebruikt:
        return "A"
    laatste = gebruikt[-1]
    if laatste.isdigit():
        return str(int(laatste) + 1)
    if len(laatste) == 1 and laatste.isalpha():
        return chr(ord(laatste.upper()) + 1) if laatste.upper() < "Z" else "AA"
    return laatste + "1"


def nieuwe_revisie(v: dict, omschrijving: str, door: str, definitief: bool = False) -> dict:
    t = set_van(v)
    revs = t["revisies"]
    vorige = next((r for r in reversed(revs) if r.get("status") != "vervallen"), None)
    snap = _snapshot(v)
    wolken = wijzigingen(vorige.get("snapshot") if vorige else None, snap)
    if definitief:
        nummers = [r["rev"] for r in revs if r["rev"].isdigit()]
        rev = str(int(nummers[-1]) + 1) if nummers else "0"
    else:
        rev = volgende_rev([r for r in revs if not r["rev"].isdigit()])
    bladen = bladindeling(v, instellingen(v))
    geraakt = sorted({b["nr"] for b in bladen for w in wolken
                      if Polygon(b["polygoon"]).intersects(Polygon(w))})
    r = {
        "rev": rev,
        "datum": time.strftime("%Y-%m-%d"),
        "omschrijving": (omschrijving or ("eerste uitgave" if not vorige else "wijziging"))[:120],
        "door": (door or "")[:60],
        "status": "concept",
        "akkoord_door": "", "akkoord_datum": "",
        "snapshot": snap,
        "wolken": wolken,
        "bladen_gewijzigd": geraakt if vorige else [b["nr"] for b in bladen],
        "aantal_bladen": len(bladen),
    }
    revs.append(r)
    return r


def zet_status(v: dict, rev: str, status: str, naam: str = "") -> dict:
    if status not in STATUSSEN:
        raise ValueError(f"Status moet een van {', '.join(STATUSSEN)} zijn.")
    t = set_van(v)
    r = next((r for r in t["revisies"] if r["rev"] == rev), None)
    if not r:
        raise ValueError("Revisie niet gevonden.")
    r["status"] = status
    if status == "akkoord":
        r["akkoord_door"] = (naam or "")[:60]
        r["akkoord_datum"] = time.strftime("%Y-%m-%d")
    return r


def revisies_zonder_snapshot(v: dict) -> list:
    return [{k: x for k, x in r.items() if k != "snapshot"}
            for r in (v.get("tekeningen") or {}).get("revisies", [])]


# ---------------------------------------------------------------- tekeningenset

def tekeningnummer(inst: dict, soort: str, nr: int) -> str:
    st = inst["stempel"]
    pre = st.get("tekeningnummer_prefix") or st.get("projectnummer") or "IE"
    code = {"overzicht": "OV", "situatie": "SIT", "boorprofiel": "BP",
            "dwarsprofiel": "DP", "uitzetstaat": "UZ"}[soort]
    return f"{pre}-{st.get('fase', 'UO')}-{code}-{nr:03d}"


def maak_pdf(v: dict, stations: list, projectnaam: str, ondergrond_fn,
             og_meta: dict, klic_fn=None, hoogte_fn=None, voortgang=None,
             selectie: set | None = None) -> bytes:
    """Volledige tekeningenset als één PDF.

    ``ondergrond_fn(bbox) -> {"bgt", "percelen", "klic", "inmeting", "bomen"}``
    levert per blad de ondergrond (zodat tests zonder netwerk kunnen draaien)."""
    inst = instellingen(v)
    bladen = bladindeling(v, inst)
    t = v.get("tekeningen") or {}
    revisies = [r for r in t.get("revisies", [])]
    laatste = next((r for r in reversed(revisies) if r.get("status") != "vervallen"), None)
    wolken = [{"rev": laatste["rev"], "polygoon": w} for w in (laatste or {}).get("wolken", [])]
    rev_tekst = laatste["rev"] if laatste else "—"
    status = (laatste or {}).get("status", "concept")
    if laatste and gewijzigd_na_revisie(v):
        status += " (tracé gewijzigd na deze revisie)"
    route = _route(v)
    wps = v.get("werkpakketten") or []
    st = inst["stempel"]

    def velden(soort, oms, nr=1, schaal=None):
        return {
            "project": projectnaam or "zonder projectnaam",
            "omschrijving": oms,
            "tekening": {"overzicht": "Overzicht en bladindeling",
                         "situatie": f"Situatie blad {nr} van {len(bladen)}",
                         "boorprofiel": "Lengteprofiel boringen",
                         "dwarsprofiel": "Standaard dwarsprofielen",
                         "uitzetstaat": "Uitzetstaat tracé"}[soort],
            "tekeningnummer": tekeningnummer(inst, soort, nr) + f"  rev. {rev_tekst}",
            "schaal": schaal or f"1:{int(inst['schaal'])} ({inst['papier']})",
            "status": f"{st.get('fase', 'UO')} — {status}",
            "datum": (laatste or {}).get("datum") or time.strftime("%Y-%m-%d"),
        }

    pdf = pdfdoc.PdfDoc(titel=f"Werktekeningen {projectnaam}")
    aan = inst.get("bladen", {})
    n_bp = math.ceil(len(v.get("boringen") or []) / 2) if aan.get("boorprofielen", True) else 0
    lijst = []
    if aan.get("overzicht", True):
        lijst.append((tekeningnummer(inst, "overzicht", 1), "Overzicht en bladindeling"))
    if aan.get("situatie", True):
        lijst += [(tekeningnummer(inst, "situatie", b["nr"]),
                   f"Situatie {km_tekst(b['van_m'])} – {km_tekst(b['tot_m'])}") for b in bladen]
    lijst += [(tekeningnummer(inst, "boorprofiel", i + 1), "Lengteprofiel boringen")
              for i in range(n_bp)]
    if aan.get("dwarsprofielen", True):
        lijst.append((tekeningnummer(inst, "dwarsprofiel", 1), "Standaard dwarsprofielen"))
    if aan.get("uitzetstaat", True):
        lijst.append((tekeningnummer(inst, "uitzetstaat", 1), "Uitzetstaat tracé"))

    if aan.get("overzicht", True):
        teken_overzicht(pdf, v, stations, bladen, inst,
                        velden("overzicht", f"MS-tracé {_nl(route.length / 1000, 2)} km, "
                                            f"{len(bladen)} situatiebladen"),
                        revisies, lijst)
    if aan.get("situatie", True):
        for b in bladen:
            if selectie is not None and b["nr"] not in selectie:
                continue
            if voortgang:
                voortgang(f"blad {b['nr']} van {len(bladen)} tekenen")
            xs = [c[0] for c in b["polygoon"]]
            ys = [c[1] for c in b["polygoon"]]
            og = ondergrond_fn((min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2))
            oms = ", ".join(w["nr"] for w in wps
                            if w["chainage_van_m"] < b["tot_m"] and w["chainage_tot_m"] > b["van_m"])
            teken_situatie(pdf, v, stations, b, bladen, og, og_meta, inst,
                           velden("situatie", f"{km_tekst(b['van_m'])} – "
                                              f"{km_tekst(b['tot_m'])}"
                                  + (f" ({oms})" if oms else ""), b["nr"]),
                           revisies, wolken)
    if selectie is not None:  # alleen boringen die op de gekozen bladen liggen
        gekozen = [b for b in bladen if b["nr"] in selectie]
        boringen = [x for x in v.get("boringen") or []
                    if any(Polygon(b["polygoon"]).intersects(
                        LineString([x["intredepunt_rd"], x["uittredepunt_rd"]]))
                        for b in gekozen)]
        v = {**v, "boringen": boringen}
        n_bp = math.ceil(len(boringen) / 2) if aan.get("boorprofielen", True) else 0
    if True:
        if n_bp:
            teller = {"n": 0}

            def vf(soort, oms):
                teller["n"] += 1
                return velden(soort, oms, teller["n"], "zie profiel")
            teken_boorprofielen(pdf, v, inst, vf, revisies, klic_fn, hoogte_fn)
        if aan.get("dwarsprofielen", True):
            teken_dwarsprofielen(pdf, v, inst, velden("dwarsprofiel", "per liggingstype", 1,
                                                      "zie profiel"), revisies)
        if aan.get("uitzetstaat", True):
            teken_uitzetstaat(pdf, v, inst, velden("uitzetstaat",
                                                   f"{len(route.coords)} hoekpunten", 1, "—"),
                              revisies)
    if not pdf.paginas:
        raise ValueError("Geen bladen om te tekenen (alle bladsoorten uit?).")
    return pdf.bytes()
