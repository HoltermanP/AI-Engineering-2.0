"""Export van het berekende tracé naar AutoCAD (DXF, R2010).

Alle geometrie staat al in RD New (EPSG:28992) in meters en wordt één-op-één
weggeschreven, zodat de tekening in AutoCAD direct op de juiste plek ligt
ten opzichte van GBKN/BGT-ondergronden die de tekenaar zelf inlaadt.

Laagindeling (kleuren volgens ACI):

  TRACE               rood      het berekende kabeltracé (doorgetrokken)
  TRACE_BORING        cyaan     boorlijnen intrede→uittrede (gestreept)
  BORING_PUNT         cyaan     intrede-/uittredepunten met label
  KRUISINGEN          geel      bijzondere punten (kruisingen) met nr/soort/techniek
  MOFFEN              magenta   mofposities (cirkel + kruis)
  STATIONS            groen     MS-stations (vierkant + label)
  BOMEN_WORTELZONE    groen     wortelzones als cirkel met werkelijke straal
  SEGMENT_TEKST       grijs     liggingslabels per wegvak
  KADER               wit       tekstblok met project/variant/stelsel/datum

Teksthoogte 1,0 m: leesbaar op de gebruikelijke plotschalen 1:200–1:1000.
"""
from __future__ import annotations

import io
import math
import time

import ezdxf

from engine import is_bijzonder_punt
from ezdxf.enums import TextEntityAlignment

TEKST_H = 1.0        # teksthoogte in m (2 mm op papier bij 1:500)
KADER_H = 1.6

ACI_ROOD, ACI_GEEL, ACI_GROEN, ACI_CYAAN, ACI_MAGENTA, ACI_GRIJS, ACI_WIT = \
    1, 2, 3, 4, 6, 8, 7

LAGEN = [
    ("TRACE", ACI_ROOD, "CONTINUOUS"),
    ("TRACE_BORING", ACI_CYAAN, "DASHED"),
    ("BORING_PUNT", ACI_CYAAN, "CONTINUOUS"),
    ("KRUISINGEN", ACI_GEEL, "CONTINUOUS"),
    ("MOFFEN", ACI_MAGENTA, "CONTINUOUS"),
    ("STATIONS", ACI_GROEN, "CONTINUOUS"),
    ("BOMEN_WORTELZONE", ACI_GROEN, "DOT"),
    ("SEGMENT_TEKST", ACI_GRIJS, "CONTINUOUS"),
    ("MAATVOERING", ACI_WIT, "CONTINUOUS"),
    ("MAATVOERING_HANDMATIG", ACI_MAGENTA, "CONTINUOUS"),
    ("KADER", ACI_WIT, "CONTINUOUS"),
]


def _coords(geom: dict) -> list[list[tuple]]:
    """Coördinaatreeksen uit een GeoJSON-geometrie (LineString of Multi)."""
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return list(geom["coordinates"])
    return []


def _tekst(msp, tekst: str, punt: tuple, laag: str, hoogte: float = TEKST_H,
           dy: float = 1.0) -> None:
    """Label iets boven het punt; MTEXT zou hier overkill zijn."""
    msp.add_text(tekst, height=hoogte, dxfattribs={"layer": laag}).set_placement(
        (punt[0], punt[1] + dy), align=TextEntityAlignment.BOTTOM_CENTER)


def _kruis(msp, punt: tuple, r: float, laag: str) -> None:
    x, y = punt[0], punt[1]
    msp.add_line((x - r, y), (x + r, y), dxfattribs={"layer": laag})
    msp.add_line((x, y - r), (x, y + r), dxfattribs={"layer": laag})


def maak_dxf(variant: dict, stations: list, bomen: list,
             projectnaam: str = "") -> bytes:
    """DXF-tekening van één berekende variant, als bytes (ASCII-DXF)."""
    doc = _bouw_doc(variant, stations, bomen, projectnaam)
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _bouw_doc(variant: dict, stations: list, bomen: list, projectnaam: str = "",
              kader: bool = True):
    doc = ezdxf.new("R2010", setup=True)  # setup: linetypes DASHED/DOT e.d.
    doc.header["$INSUNITS"] = 6   # meters
    doc.header["$MEASUREMENT"] = 1
    for naam, kleur, lijntype in LAGEN:
        doc.layers.add(naam, color=kleur, linetype=lijntype)
    msp = doc.modelspace()

    # --- tracé -------------------------------------------------------------
    for reeks in _coords(variant["route"]):
        msp.add_lwpolyline([(x, y) for x, y in reeks],
                           dxfattribs={"layer": "TRACE"})

    # --- MS-stations ---------------------------------------------------------
    for i, s in enumerate(stations, 1):
        x, y = s[0], s[1]
        z = 2.0  # halve zijde stationssymbool
        msp.add_lwpolyline([(x - z, y - z), (x + z, y - z), (x + z, y + z),
                            (x - z, y + z)], close=True,
                           dxfattribs={"layer": "STATIONS"})
        _tekst(msp, f"MS-STATION {i}", (x, y), "STATIONS", dy=z + 0.6)

    # --- boringen: boorlijn + intrede/uittrede ------------------------------
    for b in variant.get("boringen", []):
        boorlijn = b.get("geometry") or {
            "type": "LineString",
            "coordinates": [list(b["intredepunt_rd"]), list(b["uittredepunt_rd"])]}
        for reeks in _coords(boorlijn):
            msp.add_lwpolyline([(x, y) for x, y in reeks],
                               dxfattribs={"layer": "TRACE_BORING"})
        p_in, p_uit = b["intredepunt_rd"], b["uittredepunt_rd"]
        for punt, rol in ((p_in, "intrede"), (p_uit, "uittrede")):
            msp.add_circle((punt[0], punt[1]), radius=0.75,
                           dxfattribs={"layer": "BORING_PUNT"})
            _tekst(msp, f"{b['nr']} {rol}", punt, "BORING_PUNT", dy=1.2)
        for punt, kuip in ((p_in, b.get("kuip_in_rd")), (p_uit, b.get("kuip_uit_rd"))):
            if kuip:  # pers-/ontvangstkuip in het verlengde van de boorlijn
                msp.add_lwpolyline([tuple(punt), tuple(kuip)],
                                   dxfattribs={"layer": "BORING_PUNT", "const_width": 1.0})
        midden = ((p_in[0] + p_uit[0]) / 2, (p_in[1] + p_uit[1]) / 2)
        hoek = (f" {b['kruisingshoek_gr']:g}°" if b.get("kruisingshoek_gr") is not None
                else "")
        _tekst(msp, f"{b['nr']} {b['type']} L={b['lengte_m']:g} m{hoek}",
               midden, "TRACE_BORING", dy=1.8)

    # --- kruisingen ----------------------------------------------------------
    for c in variant.get("kruisingen", []):
        if not is_bijzonder_punt(c):
            continue  # standaard open ontgraving (sleufwerk): niet op de tekening
        punt = c["punt"]
        _kruis(msp, punt, 1.0, "KRUISINGEN")
        _tekst(msp, f"{c['nr']} {c['soort']} {c['breedte_m']:g} m — {c['techniek']}",
               punt, "KRUISINGEN", dy=1.5)

    # --- moffen --------------------------------------------------------------
    for m in variant.get("moffen", []):
        punt = m["punt"]
        msp.add_circle((punt[0], punt[1]), radius=0.6,
                       dxfattribs={"layer": "MOFFEN"})
        _kruis(msp, punt, 0.6, "MOFFEN")
        _tekst(msp, m["nr"], punt, "MOFFEN", dy=1.1)

    # --- wortelzones bomen ---------------------------------------------------
    for boom in bomen or []:
        msp.add_circle((boom[0], boom[1]), radius=boom[2],
                       dxfattribs={"layer": "BOMEN_WORTELZONE"})

    # --- liggingslabels per wegvak ------------------------------------------
    for s in variant.get("segmenten", []):
        reeksen = _coords(s["geometry"])
        if not reeksen:
            continue
        reeks = reeksen[0]
        midden = reeks[len(reeks) // 2]
        _tekst(msp, f"{s['ligging']} ({s['lengte_m']:g} m)", midden,
               "SEGMENT_TEKST", dy=-2.2)

    # --- maatvoering: afstand tracé ↔ verhardingsrand/gevel (echte DXF-
    #     maatvoeringen, zodat CAD de maat zelf toont en meeschaalt) --------
    def maat(van, tot, laag):
        if math.hypot(tot[0] - van[0], tot[1] - van[1]) < 0.01:
            return
        dim = msp.add_aligned_dim(
            p1=(van[0], van[1]), p2=(tot[0], tot[1]), distance=0.6,
            dimstyle="EZDXF", dxfattribs={"layer": laag},
            override={"dimtxt": 0.45, "dimasz": 0.25, "dimdec": 2,
                      "dimexe": 0.15, "dimexo": 0.05, "dimclrd": 256,
                      "dimclre": 256, "dimclrt": 256})
        dim.render()

    for x in variant.get("maatlijnen", []):
        for ml in x.get("maatlijnen", []):
            maat(ml["van"], ml["tot"], "MAATVOERING")
    for ml in variant.get("maatlijnen_handmatig", []):
        maat(ml["van"], ml["tot"], "MAATVOERING_HANDMATIG")

    # --- tekstkader linksonder ----------------------------------------------
    xs = [x for reeks in _coords(variant["route"]) for x, _ in reeks]
    ys = [y for reeks in _coords(variant["route"]) for _, y in reeks]
    if xs and kader:
        regels = [
            f"InfraEngine tracétekening — {projectnaam or 'zonder projectnaam'}",
            f"Variant: {variant.get('naam', '')} · lengte {variant.get('lengte_m', 0):g} m",
            "Coördinatenstelsel: RD New (EPSG:28992) · eenheden: meter",
            f"Gegenereerd: {time.strftime('%Y-%m-%d %H:%M')} · "
            "concepttekening, geen uitvoeringsdocument",
        ]
        x0, y0 = min(xs), min(ys) - 8.0
        for i, regel in enumerate(regels):
            msp.add_text(regel, height=KADER_H,
                         dxfattribs={"layer": "KADER"}).set_placement(
                (x0, y0 - i * (KADER_H + 0.7)),
                align=TextEntityAlignment.TOP_LEFT)
    return doc


# ---------------------------------------------------------------------------
# Werktekening (UO): ondergrond + paperspace-layouts per blad
# ---------------------------------------------------------------------------

# interne laag → sleutel in het tekenvoorschrift (werktekening.STANDAARD["laagnamen"])
LAAG_SLEUTEL = {
    "TRACE": "tracé", "TRACE_BORING": "boring", "BORING_PUNT": "boorpunt",
    "KRUISINGEN": "kruising", "MOFFEN": "mof", "STATIONS": "station",
    "BOMEN_WORTELZONE": "bomen", "MAATVOERING": "maatvoering",
}
KLIC_ACI = {"hoogspanning": 1, "landelijkHoogspanningsnet": 1, "middenspanning": 14,
            "laagspanning": 30, "datatransport": 3, "gasHogeDruk": 40, "gasLageDruk": 50,
            "water": 5, "rioolVrijverval": 34, "rioolOnderOverOfOnderdruk": 36,
            "warmte": 6, "petrochemie": 30, "buisleidingGevaarlijkeInhoud": 30}
BGT_ACI = {"wegdeel": 8, "ondersteunendwegdeel": 82, "begroeidterreindeel": 92,
           "onbegroeidterreindeel": 253, "waterdeel": 150, "ondersteunendwaterdeel": 132,
           "pand": 33, "overigbouwwerk": 251, "kunstwerkdeel_vlak": 252,
           "overbruggingsdeel": 252, "tunneldeel": 252, "spoor": 250}


def _laag(doc, naam: str, kleur: int = 7, lijntype: str = "CONTINUOUS") -> str:
    naam = naam[:255].replace("/", "_")
    if naam not in doc.layers:
        doc.layers.add(naam, color=kleur, linetype=lijntype)
    return naam


def _lijnen_van(g):
    if g.geom_type == "LineString":
        return [list(g.coords)]
    if g.geom_type == "Polygon":
        return [list(g.exterior.coords)] + [list(r.coords) for r in g.interiors]
    if hasattr(g, "geoms"):
        return [c for d in g.geoms for c in _lijnen_van(d)]
    return []


def maak_werktekening_dxf(variant: dict, stations: list, bomen: list, projectnaam: str,
                          bladen: list, inst: dict, ondergrond_fn, revisies: list,
                          wolken: list, km_tekst) -> bytes:
    """DXF met de werktekeningen: modelspace in RD (alle lagen incl.
    ondergrond), en per situatieblad een layout op papierformaat met een
    gedraaide viewport op de bladschaal en een stempel in paperspace."""
    import pdfdoc
    from shapely.geometry import LineString, Point, Polygon, shape

    doc = _bouw_doc(variant, stations, bomen, projectnaam, kader=False)
    msp = doc.modelspace()
    ln = inst["laagnamen"]

    # --- ondergrond per blad, ontdubbeld ----------------------------------
    gezien = set()
    for b in bladen:
        xs = [c[0] for c in b["polygoon"]]
        ys = [c[1] for c in b["polygoon"]]
        og = ondergrond_fn((min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2))
        bereik = Polygon(b["polygoon"])
        for coll, feats in (og.get("bgt") or {}).items():
            if coll.startswith("_"):
                continue
            laag = _laag(doc, f"{ln['bgt']}_{coll.upper()}", BGT_ACI.get(coll, 8))
            for g, props in feats:
                sleutel = ("bgt", props.get("lokaal_id") or g.wkb)
                if sleutel in gezien or not g.intersects(bereik):
                    continue
                gezien.add(sleutel)
                for c in _lijnen_van(g):
                    msp.add_lwpolyline([(x, y) for x, y, *_ in c],
                                       close=c[0] == c[-1], dxfattribs={"layer": laag})
        laag = _laag(doc, ln["kadaster"], 6, "DASHDOT")
        for g, props in og.get("percelen") or []:
            sleutel = ("dkk", props.get("identificatieLokaalID") or g.wkb)
            if sleutel in gezien or not g.intersects(bereik):
                continue
            gezien.add(sleutel)
            for c in _lijnen_van(g):
                msp.add_lwpolyline([(x, y) for x, y, *_ in c], dxfattribs={"layer": laag})
            nr = props.get("perceelnummer")
            if nr:
                q = g.representative_point()
                msp.add_text(str(nr), height=1.0, dxfattribs={"layer": laag}).set_placement(
                    (q.x, q.y), align=TextEntityAlignment.MIDDLE_CENTER)
        for g, props in og.get("klic") or []:
            thema = props.get("thema") or "overig"
            sleutel = ("klic", g.wkb)
            if sleutel in gezien:
                continue
            gezien.add(sleutel)
            laag = _laag(doc, f"{ln['klic']}_{thema.upper()}", KLIC_ACI.get(thema, 8), "DASHED")
            for c in _lijnen_van(g):
                msp.add_lwpolyline([(x, y) for x, y, *_ in c], dxfattribs={"layer": laag})
        for meta, f, g in og.get("inmeting") or []:
            sleutel = ("inm", meta["id"], id(f))
            if sleutel in gezien:
                continue
            gezien.add(sleutel)
            laag = _laag(doc, f"{ln['inmeting']}_{f['l']}"[:255], 7)
            gt = f["g"]
            if gt["type"] == "LineString":
                c = gt["coordinates"]
                if len(c[0]) == 3:
                    msp.add_polyline3d([tuple(p) for p in c], dxfattribs={"layer": laag})
                else:
                    msp.add_lwpolyline([tuple(p) for p in c], dxfattribs={"layer": laag})
            elif f.get("t") and "b" not in f:
                msp.add_text(f["t"], height=f.get("h", 0.5), rotation=f.get("r", 0.0),
                             dxfattribs={"layer": laag}).set_placement(tuple(gt["coordinates"]))
            else:
                x, y = gt["coordinates"]
                msp.add_point((x, y, f.get("z", 0.0)), dxfattribs={"layer": laag})

    # --- kilometrering en hoekpunten ---------------------------------------
    route = shape(variant["route"])
    if route.geom_type != "LineString":
        route = LineString([c for l in route.geoms for c in l.coords])
    laag = _laag(doc, ln["km"], 1)
    stap = float(inst.get("km_interval_m", 25))
    m = 0.0
    while m <= route.length + 1e-6:
        q = route.interpolate(m)
        a = route.interpolate(max(0, m - 0.5))
        c = route.interpolate(min(route.length, m + 0.5))
        h = math.atan2(c.y - a.y, c.x - a.x)
        nx, ny = -math.sin(h), math.cos(h)
        msp.add_line((q.x - nx, q.y - ny), (q.x + nx, q.y + ny), dxfattribs={"layer": laag})
        gr = math.degrees(h) + 90
        gr = gr - 180 if gr > 90 else gr + 180 if gr < -90 else gr
        msp.add_text(km_tekst(m), height=0.8, rotation=gr, dxfattribs={"layer": laag}
                     ).set_placement((q.x + nx * 1.4, q.y + ny * 1.4),
                                     align=TextEntityAlignment.MIDDLE_LEFT)
        m += stap
    for i, (x, y) in enumerate(route.coords, 1):
        msp.add_text(f"P{i}", height=0.6, dxfattribs={"layer": laag}).set_placement(
            (x + 0.4, y - 0.9))

    # --- bladindeling en wolken --------------------------------------------
    laag_bl = _laag(doc, ln["bladindeling"], 9, "DASHED")
    for b in bladen:
        msp.add_lwpolyline(b["polygoon"], close=True, dxfattribs={"layer": laag_bl})
    laag_w = _laag(doc, ln["wolk"], 6)
    for w in wolken:
        msp.add_lwpolyline(w["polygoon"], close=True,
                           dxfattribs={"layer": laag_w, "const_width": 0.15})
        x, y = w["polygoon"][0]
        msp.add_text(w["rev"], height=1.2, dxfattribs={"layer": laag_w}).set_placement((x, y))

    # --- paperspace per blad ------------------------------------------------
    import werktekening as wt
    papier = inst["papier"]
    pw, ph = pdfdoc.PAPIER.get(papier, pdfdoc.PAPIER["A3"])
    vak = wt.kaartvak(papier)
    schaal = float(inst["schaal"])
    laag_st = _laag(doc, ln["stempel"], 7)
    st = inst["stempel"]
    laatste = next((r for r in reversed(revisies) if r.get("status") != "vervallen"), None)
    for b in bladen:
        lay = doc.layouts.new(f"Blad {b['nr']:03d}")
        lay.page_setup(size=(pw, ph), margins=(0, 0, 0, 0), units="mm")
        vw, vh = vak[2] - vak[0], vak[3] - vak[1]
        vp = lay.add_viewport(center=((vak[0] + vak[2]) / 2, (vak[1] + vak[3]) / 2),
                              size=(vw, vh), view_center_point=(0, 0),
                              view_height=vh * schaal / 1000.0)
        # gedraaide weergave: het tracé loopt horizontaal over het blad. In een
        # viewport met twist ligt view_center_point in het gedraaide stelsel.
        hoek = math.radians(b["hoek_gr"])
        ca, sa = math.cos(hoek), math.sin(hoek)
        vp.dxf.view_twist_angle = (-b["hoek_gr"]) % 360
        vp.dxf.view_center_point = (b["cx"] * ca + b["cy"] * sa,
                                    -b["cx"] * sa + b["cy"] * ca)
        vp.dxf.view_target_point = (0, 0, 0)
        vp.dxf.layer = laag_st
        lay.add_lwpolyline([(5, 5), (pw - 5, 5), (pw - 5, ph - 5), (5, ph - 5)], close=True,
                           dxfattribs={"layer": laag_st, "const_width": 0.5})
        lay.add_lwpolyline([(vak[0], vak[1]), (vak[2], vak[1]), (vak[2], vak[3]),
                            (vak[0], vak[3])], close=True, dxfattribs={"layer": laag_st})
        rx = vak[2] + 4
        regels = [
            ("Opdrachtgever", st.get("opdrachtgever", "")),
            ("Ontwerpbureau", st.get("ontwerpbureau", "")),
            ("Project", projectnaam),
            ("Omschrijving", f"{km_tekst(b['van_m'])} - {km_tekst(b['tot_m'])}"),
            ("Tekening", f"Situatie blad {b['nr']} van {len(bladen)}"),
            ("Tekeningnr.", wt.tekeningnummer(inst, "situatie", b["nr"])
             + (f" rev. {laatste['rev']}" if laatste else "")),
            ("Schaal", f"1:{int(schaal)} ({papier})"),
            ("Fase / status", f"{st.get('fase', 'UO')} - {(laatste or {}).get('status', 'concept')}"),
            ("Ontwerper", st.get("ontwerper", "")),
            ("Getekend", st.get("tekenaar", "")),
            ("Gecontroleerd", st.get("controleur", "")),
            ("Datum", (laatste or {}).get("datum") or time.strftime("%Y-%m-%d")),
        ]
        rij = 4.6
        h = rij * len(regels)
        lay.add_lwpolyline([(rx, 10), (rx + wt.RECHTS_MM, 10), (rx + wt.RECHTS_MM, 10 + h),
                            (rx, 10 + h)], close=True, dxfattribs={"layer": laag_st})
        for i, (k, waarde) in enumerate(regels):
            y = 10 + h - (i + 1) * rij
            lay.add_line((rx, y), (rx + wt.RECHTS_MM, y), dxfattribs={"layer": laag_st})
            lay.add_text(k, height=1.7, dxfattribs={"layer": laag_st}).set_placement((rx + 1.5, y + 1.4))
            lay.add_text(str(waarde or ""), height=2.0, dxfattribs={"layer": laag_st}
                         ).set_placement((rx + 28, y + 1.4))
        y = 10 + h + 3
        for r in reversed([r for r in revisies if r.get("status") != "vervallen"][-5:]):
            lay.add_text(f"{r['rev']}  {r.get('datum', '')}  {(r.get('omschrijving') or '')[:40]}"
                         f"  {r.get('status', '')}", height=1.7,
                         dxfattribs={"layer": laag_st}).set_placement((rx + 1.5, y))
            y += 3.2
        # noordpijl (paperspace, gedraaid met het blad)
        a = math.radians(90 - b["hoek_gr"])
        nx, ny = rx + 12, ph - 23
        tip = (nx + 9 * math.cos(a), ny + 9 * math.sin(a))
        lay.add_lwpolyline([(nx - 2.5 * math.sin(a), ny + 2.5 * math.cos(a)), tip,
                            (nx + 2.5 * math.sin(a), ny - 2.5 * math.cos(a))],
                           dxfattribs={"layer": laag_st})
        lay.add_text("N", height=3.0, dxfattribs={"layer": laag_st}).set_placement(
            (tip[0] + 1.5 * math.cos(a), tip[1] + 1.5 * math.sin(a)),
            align=TextEntityAlignment.MIDDLE_CENTER)
        lay.add_text(f"Schaal 1:{int(schaal)}", height=2.4,
                     dxfattribs={"layer": laag_st}).set_placement((rx + 26, ph - 16))

    if "Layout1" in doc.layouts and len(doc.layouts) > 2:
        doc.layouts.delete("Layout1")

    # --- laagnamen volgens het tekenvoorschrift -----------------------------
    for intern, sleutel in LAAG_SLEUTEL.items():
        nieuw = ln.get(sleutel)
        if nieuw and intern in doc.layers and nieuw not in doc.layers:
            doc.layers.get(intern).rename(nieuw)
    if "MAATVOERING_HANDMATIG" in doc.layers and ln.get("maatvoering"):
        doc.layers.get("MAATVOERING_HANDMATIG").rename(ln["maatvoering"] + "_HAND")
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
