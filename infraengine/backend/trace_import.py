"""Trace-import: een bestaand tracé inlezen uit DXF of vector-PDF.

Beide bestandstypen worden verondersteld al in RD New (EPSG:28992, meter) te
staan — net als de DXF-export (zie dxf_export.py) wordt hier één-op-één
overgenomen, zonder georeferentie. Voor PDF geldt dat alleen vector-PDF's
(rechtstreeks vanuit CAD geëxporteerd) worden ondersteund; een scan/
raster-afbeelding van een tekening bevat geen vectorpaden en levert een
duidelijke foutmelding op in plaats van een leeg of onbruikbaar resultaat.

Een DXF/PDF-tekening bevat meestal meerdere lagen (ondergrond, kadaster,
bestaande kabels, tekstkader) naast de eigenlijke tracélijn. `inspecteer_*`
somt daarom eerst de kandidaat-lagen op (naam, aantal entiteiten, lengte,
geometrietypes) zodat de gebruiker kan aanwijzen welke laag(en) het tracé
voorstellen; `bouw_route_*` bouwt daarna pas de daadwerkelijke lijn.
"""
from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass

import ezdxf
import ezdxf.path
import ezdxf.recover
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

# fitz (PyMuPDF) alleen bij daadwerkelijk PDF-gebruik importeren: het is een
# los te installeren dependency (requirements.txt) en mag de rest van de app
# (DXF-import, en de hele backend die dit module importeert) niet blokkeren
# als het nog niet geïnstalleerd is.
def _fitz():
    try:
        import fitz
    except ImportError as e:
        raise TraceImportError(
            "PDF-import vereist het pakket 'pymupdf', dat nog niet is "
            "geïnstalleerd op de server. DXF-import werkt intussen gewoon."
        ) from e
    return fitz

# entiteiten die een tracé kunnen voorstellen; tekst, arceringen, blok- en
# maatvoeringsobjecten (TEXT, HATCH, DIMENSION, MTEXT, ...) worden genegeerd
DXF_LIJN_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ARC"}
DXF_MAX_INSERT_DIEPTE = 5  # geneste blokverwijzingen (INSERT) volgen, met een limiet
DXF_FLATTEN_M = 0.05  # bogen/splines benaderen tot op 5 cm nauwkeurig

GAP_BRUG_M = 0.5  # kleine hiaten (streeplijntype, afrondingsverschil) automatisch overbruggen


class TraceImportError(Exception):
    """Onleesbaar bestand, geen bruikbare lijngeometrie, of ongeldige laagkeuze."""


@dataclass
class CandidateLayer:
    naam: str
    aantal_entiteiten: int
    lengte_m: float
    geometrie_types: list[str]
    bbox: tuple[float, float, float, float] | None = None


# ---------------------------------------------------------------------------
# Gedeeld: lagen → kandidatenlijst, losse lijnstukken → één doorlopende route
# ---------------------------------------------------------------------------

def _kandidaten_van(per_laag: dict[str, list[tuple[str, list[tuple[float, float]]]]]
                    ) -> list[CandidateLayer]:
    kandidaten = []
    for naam, entiteiten in per_laag.items():
        lijnen = [LineString(p) for _, p in entiteiten if len(p) >= 2]
        alle_x = [x for _, p in entiteiten for x, _ in p]
        alle_y = [y for _, p in entiteiten for _, y in p]
        kandidaten.append(CandidateLayer(
            naam=naam,
            aantal_entiteiten=len(entiteiten),
            lengte_m=round(sum(l.length for l in lijnen), 1),
            geometrie_types=sorted({t for t, _ in entiteiten}),
            bbox=(min(alle_x), min(alle_y), max(alle_x), max(alle_y)) if alle_x else None,
        ))
    kandidaten.sort(key=lambda c: c.lengte_m, reverse=True)
    return kandidaten


def _ontdubbel(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Opeenvolgende exact gelijke punten weglaten (hygiëne, geen herpositionering)."""
    out: list[tuple[float, float]] = []
    for c in coords:
        if not out or math.hypot(c[0] - out[-1][0], c[1] - out[-1][1]) > 1e-9:
            out.append(c)
    return out


def _lijnen_samenvoegen(segmenten: list[LineString]) -> tuple[LineString, list[str]]:
    """Losse lijnstukken van de gekozen laag/lagen tot één doorlopende route.

    Aaneensluitende stukken worden via shapely.ops.linemerge samengevoegd.
    Wat daarna nog los ligt (bijv. een streeplijntype dat als losse
    segmentjes is opgeslagen) wordt op volgorde van dichtstbijzijnde
    eindpunten aaneengeregen: kleine hiaten (≤ GAP_BRUG_M) worden stilzwijgend
    overbrugd en gemeld als waarschuwing, grote hiaten wijzen op een verkeerde
    laagkeuze en leveren een fout op — geen geometrie verzinnen.
    """
    segmenten = [s for s in segmenten if s.length > 0]
    if not segmenten:
        raise TraceImportError(
            "Geselecteerde laag/lagen bevatten geen bruikbare lijnstukken.")

    samengevoegd = linemerge(MultiLineString([list(s.coords) for s in segmenten]))
    if samengevoegd.geom_type == "LineString":
        return LineString(_ontdubbel(list(samengevoegd.coords))), []

    stukken = list(samengevoegd.geoms)
    volgorde = [stukken.pop(0)]
    while stukken:
        staart = volgorde[-1].coords[-1]
        beste = min(
            range(len(stukken)),
            key=lambda i: min(
                math.hypot(stukken[i].coords[0][0] - staart[0],
                          stukken[i].coords[0][1] - staart[1]),
                math.hypot(stukken[i].coords[-1][0] - staart[0],
                          stukken[i].coords[-1][1] - staart[1]),
            ),
        )
        stuk = stukken.pop(beste)
        d_kop = math.hypot(stuk.coords[0][0] - staart[0], stuk.coords[0][1] - staart[1])
        d_staart = math.hypot(stuk.coords[-1][0] - staart[0], stuk.coords[-1][1] - staart[1])
        if d_staart < d_kop:
            stuk = LineString(list(stuk.coords)[::-1])
        volgorde.append(stuk)

    hiaten: list[float] = []
    coords: list[tuple[float, float]] = list(volgorde[0].coords)
    for vorig, stuk in zip(volgorde[:-1], volgorde[1:]):
        gat = Point(vorig.coords[-1]).distance(Point(stuk.coords[0]))
        if gat > 1e-6:
            hiaten.append(gat)
        coords.extend(list(stuk.coords))
    coords = _ontdubbel(coords)

    grote_hiaten = [g for g in hiaten if g > GAP_BRUG_M]
    if grote_hiaten:
        raise TraceImportError(
            f"De gekozen laag/lagen bevatten {len(grote_hiaten)} onderbreking(en) "
            f"groter dan {GAP_BRUG_M:g} m (grootste {max(grote_hiaten):.1f} m) — dit "
            f"lijkt geen doorlopend tracé. Kies een andere of aanvullende laag.")
    waarschuwingen = (
        [f"{len(hiaten)} kleine onderbreking(en) (≤ {GAP_BRUG_M:g} m, bijvoorbeeld een "
         f"streeplijntype) automatisch overbrugd."] if hiaten else [])
    return LineString(coords), waarschuwingen


# ---------------------------------------------------------------------------
# DXF
# ---------------------------------------------------------------------------

def _dxf_entities(entities, diepte: int = 0):
    """Modelspace-entiteiten, met INSERT (blokverwijzingen) uitgevouwen."""
    if diepte > DXF_MAX_INSERT_DIEPTE:
        return
    for e in entities:
        dxftype = e.dxftype()
        if dxftype == "INSERT":
            try:
                yield from _dxf_entities(e.virtual_entities(), diepte + 1)
            except Exception:
                continue
        elif dxftype in DXF_LIJN_TYPES:
            yield e


def _dxf_entity_punten(e) -> list[tuple[float, float]] | None:
    try:
        pad = ezdxf.path.make_path(e)
        punten = [(v.x, v.y) for v in pad.flattening(DXF_FLATTEN_M)]
    except Exception:
        return None
    return punten if len(punten) >= 2 else None


def _lees_dxf_doc(data: bytes):
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp.write(data)
        pad = tmp.name
    try:
        doc, _auditor = ezdxf.recover.readfile(pad)
    except Exception as e:
        raise TraceImportError(f"Kon het DXF-bestand niet lezen: {e}") from e
    finally:
        os.unlink(pad)
    return doc


def _dxf_lagen_verzamelen(doc) -> dict[str, list[tuple[str, list[tuple[float, float]]]]]:
    per_laag: dict[str, list] = {}
    for e in _dxf_entities(doc.modelspace()):
        punten = _dxf_entity_punten(e)
        if not punten:
            continue
        per_laag.setdefault(e.dxf.layer, []).append((e.dxftype(), punten))
    return per_laag


def inspecteer_dxf(data: bytes) -> list[CandidateLayer]:
    per_laag = _dxf_lagen_verzamelen(_lees_dxf_doc(data))
    if not per_laag:
        raise TraceImportError(
            "Geen lijngeometrie (LINE/LWPOLYLINE/POLYLINE/SPLINE/ARC) gevonden "
            "in dit DXF-bestand.")
    return _kandidaten_van(per_laag)


def bouw_route_dxf(data: bytes, lagen: list[str]) -> tuple[LineString, list[str]]:
    per_laag = _dxf_lagen_verzamelen(_lees_dxf_doc(data))
    gekozen = [LineString(p) for naam in lagen for _, p in per_laag.get(naam, [])
              if len(p) >= 2]
    if not gekozen:
        raise TraceImportError("Geen van de gekozen lagen bevat lijngeometrie.")
    return _lijnen_samenvoegen(gekozen)


# ---------------------------------------------------------------------------
# Vector-PDF
# ---------------------------------------------------------------------------

def _pdf_kleur_naam(kleur) -> str:
    if not kleur:
        return "(geen laagnaam — ongekleurde lijnen)"
    r, g, b = (round(max(0.0, min(1.0, c)) * 255) for c in kleur[:3])
    return f"kleur #{r:02X}{g:02X}{b:02X}"


def _bezier_flatten(p0, p1, p2, p3, segmenten: int = 12) -> list[tuple[float, float]]:
    punten = []
    for i in range(segmenten + 1):
        t = i / segmenten
        mt = 1 - t
        x = mt**3 * p0.x + 3 * mt**2 * t * p1.x + 3 * mt * t**2 * p2.x + t**3 * p3.x
        y = mt**3 * p0.y + 3 * mt**2 * t * p1.y + 3 * mt * t**2 * p2.y + t**3 * p3.y
        punten.append((x, y))
    return punten


def _pdf_item_punten(item: tuple) -> list[tuple[float, float]] | None:
    """Eén get_drawings()-item ('l' lijn of 'c' cubic bezier) naar punten.

    Rechthoeken/quads ('re'/'qu') stellen nooit een tracé voor en worden
    genegeerd.
    """
    soort = item[0]
    if soort == "l":
        return [(item[1].x, item[1].y), (item[2].x, item[2].y)]
    if soort == "c":
        return _bezier_flatten(item[1], item[2], item[3], item[4])
    return None


def _lees_pdf_doc(data: bytes):
    fitz = _fitz()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise TraceImportError(f"Kon het PDF-bestand niet lezen: {e}") from e
    if doc.page_count == 0:
        raise TraceImportError("PDF-bestand bevat geen pagina's.")
    return doc


def _pdf_lagen_verzamelen(doc) -> dict[str, list[tuple[str, list[tuple[float, float]]]]]:
    """Vectorpaden gegroepeerd per CAD-laag (OCG); zonder laagnaam (geen
    Optional Content Groups in de PDF) wordt op pen-kleur gegroepeerd, zodat
    de gebruiker nog steeds "de rode lijn" kan aanwijzen als tracé."""
    per_laag: dict[str, list] = {}
    for pagina in doc:
        for pad in pagina.get_drawings():
            laag = pad.get("layer") or _pdf_kleur_naam(pad.get("color"))
            for item in pad.get("items", []):
                punten = _pdf_item_punten(item)
                if punten and len(punten) >= 2:
                    per_laag.setdefault(laag, []).append((item[0], punten))
    return per_laag


def inspecteer_pdf(data: bytes) -> list[CandidateLayer]:
    doc = _lees_pdf_doc(data)
    per_laag = _pdf_lagen_verzamelen(doc)
    if not per_laag:
        raise TraceImportError(
            "Geen vector-lijnpaden gevonden in deze PDF. Deze import is voor "
            "CAD-tekeningen (vector-PDF met echte lijnobjecten, of DXF). Is dit "
            "een investeringsvoorstel (IV) of een document met kaartfiguren? "
            "Ga dan naar de procespagina (◫ Proces): upload het IV bij stap "
            "IV-01 en kies bij IV-03 '🤖 IV → kaart' — de AI haalt de "
            "knooppunten en adressen uit het document en zet ze op de kaart.")
    return _kandidaten_van(per_laag)


def bouw_route_pdf(data: bytes, lagen: list[str]) -> tuple[LineString, list[str]]:
    doc = _lees_pdf_doc(data)
    per_laag = _pdf_lagen_verzamelen(doc)
    gekozen = [LineString(p) for naam in lagen for _, p in per_laag.get(naam, [])
              if len(p) >= 2]
    if not gekozen:
        raise TraceImportError("Geen van de gekozen lagen bevat lijngeometrie.")
    return _lijnen_samenvoegen(gekozen)


# ---------------------------------------------------------------------------
# Dispatch op bestandstype
# ---------------------------------------------------------------------------

def _bestandstype(bestandsnaam: str) -> str:
    ext = bestandsnaam.rsplit(".", 1)[-1].lower() if "." in bestandsnaam else ""
    if ext in ("dxf", "pdf"):
        return ext
    raise TraceImportError(
        f"Bestandstype '.{ext}' wordt niet ondersteund — alleen .dxf en .pdf.")


def inspecteer(bestandsnaam: str, data: bytes) -> tuple[str, list[CandidateLayer]]:
    bestandstype = _bestandstype(bestandsnaam)
    kandidaten = inspecteer_dxf(data) if bestandstype == "dxf" else inspecteer_pdf(data)
    return bestandstype, kandidaten


def bouw_route(bestandstype: str, data: bytes, lagen: list[str]) -> tuple[LineString, list[str]]:
    if bestandstype == "dxf":
        return bouw_route_dxf(data, lagen)
    if bestandstype == "pdf":
        return bouw_route_pdf(data, lagen)
    raise TraceImportError(f"Onbekend bestandstype '{bestandstype}'.")
