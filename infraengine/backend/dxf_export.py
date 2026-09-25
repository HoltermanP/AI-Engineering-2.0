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
        midden = ((p_in[0] + p_uit[0]) / 2, (p_in[1] + p_uit[1]) / 2)
        _tekst(msp, f"{b['nr']} {b['type']} L={b['lengte_m']:g} m",
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

    # --- tekstkader linksonder ----------------------------------------------
    xs = [x for reeks in _coords(variant["route"]) for x, _ in reeks]
    ys = [y for reeks in _coords(variant["route"]) for _, y in reeks]
    if xs:
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

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
