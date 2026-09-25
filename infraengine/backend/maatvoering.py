"""Maatvoerings- en nauwkeurigheidstoetsing van tracégeometrie.

Alle grenswaarden komen uit het regelbestand ``normen.py`` (met overrides in
``data/normen.json``); hier staat uitsluitend meet- en toetslogica.

Twee taken:

1. ``normaliseer_route`` — nabewerking van gegenereerde routes zodat ze de
   eisen vooraf respecteren: coördinaten afgerond op de norm-afronding
   (RD New), dubbele punten verwijderd, korte knik-segmenten samengevoegd,
   punten binnen de snaptolerantie exact op de referentieranden
   (BGT-wegkant/verhardingsrand) gesnapt, en te scherpe bochten verruimd tot
   de buigradius-eis (``verruim_bochten``) — optioneel getoetst tegen de
   harde uitsluitingen van het kostenraster (``grid``) zodat een verruiming
   nooit een pand of verboden zone raakt.

2. ``toets`` — maatvoeringstoets die bevindingen oplevert in hetzelfde
   formaat als de bestaande basis-toets (``registers.build_checks``:
   ernst/toets/grondslag/melding/punt), aangevuld met exacte locatie
   (RD-punt + metrering langs het tracé), gemeten waarde en eis. Daarnaast
   een maatvoeringsoverzicht per tracé (lengtes, kleinste buigradius,
   kleinste afstand per categorie).

Aannames (expliciet, zie ook de meldingteksten):
- tracépunten hebben geen z-waarde: diepte/dekking wordt als eis per
  liggingstype (uit de BGT-afgeleide segmenten) gerapporteerd, niet gemeten;
- het segmenttype (berm/trottoir/rijbaan/kruising) komt uit de BGT-laag;
- de buigradiustoets meet per knikpunt de grootst inpasbare boogstraal
  R = min(l1, l2) / (2·tan(θ/2)) — de ruimte die de kabel in de sleufhoek
  heeft — en toetst die aan de norm-radius (factor × diameter).
"""
from __future__ import annotations

import math
import re

from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

import normen


# ---------------------------------------------------------------------------
# Hulpfuncties geometrie
# ---------------------------------------------------------------------------

def _rond(v: float) -> float:
    stap = float(normen.waarde("afronding_m"))
    return round(round(v / stap) * stap, 2)


def _hoek_verandering(a, b, c) -> float:
    """Richtingsverandering (°) in punt b van polyline a→b→c."""
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    l1, l2 = math.hypot(*v1), math.hypot(*v2)
    if l1 < 1e-9 or l2 < 1e-9:
        return 0.0
    cosa = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
    return math.degrees(math.acos(cosa))


def _inpasbare_radius(a, b, c) -> float:
    """Grootst inpasbare boogstraal in knikpunt b (m).

    De kabel snijdt de hoek af met een boog; de raaklengte R·tan(θ/2) moet
    binnen de helft van elk aanliggend segment passen (de andere helft is
    voor de buurknik): R = min(l1, l2) / (2·tan(θ/2))."""
    theta = math.radians(_hoek_verandering(a, b, c))
    if theta < 1e-6:
        return float("inf")
    l1 = math.hypot(b[0] - a[0], b[1] - a[1])
    l2 = math.hypot(c[0] - b[0], c[1] - b[1])
    t = math.tan(theta / 2)
    if t < 1e-9:
        return float("inf")
    return min(l1, l2) / (2 * t)


def _richting_bij(lijn: LineString, m: float, stap: float = 0.5) -> tuple:
    """Richtingsvector van een lijn rond metrering m."""
    a = lijn.interpolate(max(0.0, m - stap))
    b = lijn.interpolate(min(lijn.length, m + stap))
    dx, dy = b.x - a.x, b.y - a.y
    ln = math.hypot(dx, dy)
    return (dx / ln, dy / ln) if ln > 1e-9 else (1.0, 0.0)


def kruisingshoek_gr(route: LineString, ander: LineString, punt: Point) -> float:
    """Kruisingshoek (0–90°) tussen het tracé en een andere lijn in punt."""
    r1 = _richting_bij(route, route.project(punt))
    r2 = _richting_bij(ander, ander.project(punt))
    cosa = abs(r1[0] * r2[0] + r1[1] * r2[1])
    return math.degrees(math.acos(max(-1.0, min(1.0, cosa))))


# ---------------------------------------------------------------------------
# Referentieranden (snapping): BGT-wegkant/verhardingsrand + erfgrenzen
# ---------------------------------------------------------------------------

def _segment_vrij(grid, p0: tuple, p1: tuple) -> bool:
    """True als het lijnstuk geen harde uitsluiting van het kostenraster raakt.

    Zonder ``grid`` (geen raster beschikbaar op dit punt in de pijplijn,
    bijvoorbeeld bij het aaneenhechten van deeltrajecten) wordt niets
    getoetst en is elk lijnstuk toegestaan — de eindtoets (``toets``) vangt
    een eventuele resterende overschrijding dan af."""
    if grid is None:
        return True
    lijn = LineString([p0, p1])
    if grid.hard_conflict(lijn):
        return False
    lengte = lijn.length
    if lengte == 0:
        return True
    n = max(2, int(math.ceil(lengte / (grid.cell / 2))))
    for k in range(n + 1):
        t = k / n
        x, y = p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])
        r, c = grid.world_to_cell(x, y)
        if not math.isfinite(float(grid.cost[r, c])):
            return False
    return True


def verruim_bochten(coords: list, r_eis: float, knik_gr: float,
                    grid=None, vast: set | None = None) -> list:
    """Bochten die de buigradius-eis niet halen vóór de toetsing verruimen.

    Schuift een te scherp knikpunt in kleine stappen naar het midden van de
    koorde tussen de buurpunten — de kleinste verschuiving die de inpasbare
    boogstraal (``_inpasbare_radius``) wel laat halen — zodat het tracé de
    eis al bij de generatie respecteert in plaats van de overschrijding pas
    bij de toetsing te melden. Elke kandidaatpositie wordt (met ``grid``)
    getoetst tegen de harde uitsluitingen; lukt geen enkele stap zonder een
    uitsluiting te raken, dan blijft het punt ongewijzigd en meldt ``toets``
    de overschrijding zoals voorheen.

    Beperkt zich bewust tot geïsoleerde overschrijdingen: een knikpunt
    waarvan de buur zélf ook een te scherpe bocht is, wordt overgeslagen,
    want de buurpunten a/c van dat knikpunt liggen dan niet vast (ze
    schuiven mee als de buur wordt verruimd) en de twee aanpassingen kunnen
    elkaar tegenwerken. Twee aanpalende scherpe knikken op een kort
    tussensegment (een echte haarspeld) blijven zo ongemoeid en komen
    ongewijzigd in de toetsing terecht — verruimen is een preventie bovenop
    de bestaande vangnet-toets, geen vervanging ervan.

    Punten in ``vast`` (in-/uittredepunten en de hoekpunten van boorlijnen)
    blijven staan: de knik bij een intredepunt is een echte knik — het tracé
    buigt daar in de rechte boorlijn — en mag niet worden afgesneden."""
    coords = list(coords)
    vast = vast or set()
    stappen = 20

    def is_bocht(i: int) -> bool:
        a, b, c = coords[i - 1], coords[i], coords[i + 1]
        return (_hoek_verandering(a, b, c) >= knik_gr
                and _inpasbare_radius(a, b, c) < r_eis)

    for i in range(1, len(coords) - 1):
        if coords[i] in vast or not is_bocht(i):
            continue
        if (i - 1 >= 1 and is_bocht(i - 1)) or (
                i + 1 <= len(coords) - 2 and is_bocht(i + 1)):
            continue  # aanpalende overschrijding: buurpunten liggen niet vast
        a, b, c = coords[i - 1], coords[i], coords[i + 1]
        mx, my = (a[0] + c[0]) / 2, (a[1] + c[1]) / 2
        for stap in range(stappen - 1, -1, -1):
            t = stap / stappen
            kand = (mx + t * (b[0] - mx), my + t * (b[1] - my))
            if _inpasbare_radius(a, kand, c) < r_eis:
                continue
            if _segment_vrij(grid, a, kand) and _segment_vrij(grid, kand, c):
                coords[i] = (_rond(kand[0]), _rond(kand[1]))
                break
    return coords


def referentieranden(bgt: dict | None, percelen: list | None = None):
    """Lijst randlijnen waar tracépunten op horen te snappen."""
    randen = []
    for coll in ("wegdeel", "ondersteunendwegdeel"):
        for g, _p in (bgt or {}).get(coll, []):
            if g.geom_type in ("Polygon", "MultiPolygon"):
                randen.append(g.boundary)
    for item in (percelen or []):
        g = item[0] if isinstance(item, tuple) else item
        if getattr(g, "geom_type", "") in ("Polygon", "MultiPolygon"):
            randen.append(g.boundary)
    return randen or None


# ---------------------------------------------------------------------------
# 1. Normalisatie van (gegenereerde) routes
# ---------------------------------------------------------------------------

def normaliseer_route(route: LineString, referentie: list | None = None,
                      grid=None, vast=None) -> LineString:
    """Afronden, ontdubbelen, korte knik-segmenten samenvoegen, snappen en
    te scherpe bochten verruimen.

    Wordt in de routegeneratie aangeroepen zodat gegenereerde tracés de
    nauwkeurigheidseisen vooraf respecteren. Begin- en eindpunt (stations)
    blijven op hun plaats. ``grid`` (optioneel, het kostenraster van deze
    berekening) laat de buigradius-verruiming toetsen tegen harde
    uitsluitingen — zonder ``grid`` gebeurt de verruiming ongetoetst.

    ``vast``: coördinaten (x, y) die niet mogen verschuiven of vervallen —
    de in-/uittredepunten en hoekpunten van rechtgetrokken boorlijnen
    (``engine.bepaal_boorpunten``). Ze worden wél afgerond, net als de rest."""
    coords = [(_rond(x), _rond(y)) for x, y in route.coords]
    vast_r = {(_rond(x), _rond(y)) for x, y in (vast or ())}

    # snappen: punt binnen de snaptolerantie van een referentierand → exact
    # op de rand (behalve de stations op begin en eind en de boorpunten)
    if referentie:
        tol = float(normen.waarde("snap_tolerantie_m"))
        boom = STRtree(referentie)
        for i in range(1, len(coords) - 1):
            if coords[i] in vast_r:
                continue
            p = Point(coords[i])
            idx = boom.nearest(p)
            rand = referentie[int(idx)]
            d = rand.distance(p)
            if 0 < d <= tol:
                s = nearest_points(rand, p)[0]
                coords[i] = (_rond(s.x), _rond(s.y))

    # dubbele punten verwijderen
    coords = [c for i, c in enumerate(coords)
              if i == 0 or c != coords[i - 1]]

    # korte knik-segmenten samenvoegen: een segment korter dan de norm dat
    # een echte richtingsverandering vormt, verliest zijn tussenpunt
    min_seg = float(normen.waarde("min_segment_m"))
    knik_gr = float(normen.waarde("knik_hoek_gr"))
    for _ in range(6):  # itereren tot stabiel (begrensd)
        gewijzigd = False
        i = 1
        while i < len(coords) - 1:
            l_vorig = math.hypot(coords[i][0] - coords[i - 1][0],
                                 coords[i][1] - coords[i - 1][1])
            hoek = _hoek_verandering(coords[i - 1], coords[i], coords[i + 1])
            if l_vorig < min_seg and hoek >= knik_gr and coords[i] not in vast_r:
                del coords[i]
                gewijzigd = True
            else:
                i += 1
        if not gewijzigd:
            break

    if len(coords) >= 3:
        coords = verruim_bochten(coords, normen.buigradius_eis_m(), knik_gr,
                                 grid, vast_r)

    if len(coords) < 2:
        return route
    return LineString(coords)


# ---------------------------------------------------------------------------
# 2. Maatvoeringstoets + overzicht
# ---------------------------------------------------------------------------

_KLIC_CATEGORIE_EIS = {
    "kabels": "afstand_kabels_m",
    "leidingen": "afstand_leidingen_m",
    "hd_gas": "afstand_hd_gas_m",
    "onbekend": "afstand_leidingen_m",  # conservatieve aanname
}
_KLIC_LABEL = {"kabels": "LS/MS-/datakabel", "leidingen": "gas-/waterleiding",
               "hd_gas": "HD-gasleiding", "onbekend": "net (thema onbekend)"}


def _klic_categorie(props: dict) -> str:
    tekst = " ".join(str(v) for v in (props or {}).values()).lower()
    if "hoge druk" in tekst or "hogedruk" in tekst or re.search(r"\bhd\b", tekst):
        return "hd_gas"
    if "gas" in tekst:
        return "leidingen"
    if "water" in tekst and "afval" not in tekst:
        return "leidingen"
    if "riool" in tekst or "afvalwater" in tekst or "warmte" in tekst:
        return "leidingen"
    if any(t in tekst for t in ("laagspanning", "middenspanning",
                                "hoogspanning", "elektric", "datatransport",
                                "telecom", "glasvezel")):
        return "kabels"
    return "onbekend"


def _bevinding(checks: list, rid: str, toets: str, melding: str,
               punt, metrering_m: float | None, gemeten=None, eis=None,
               ernst: str | None = None) -> None:
    r = normen.regel(rid)
    checks.append({
        "ernst": ernst or r["ernst"], "toets": toets,
        "grondslag": r["bron"], "melding": melding,
        "punt": (round(punt[0], 2), round(punt[1], 2)) if punt else None,
        "maatvoering": True,
        "metrering_m": round(metrering_m, 1) if metrering_m is not None else None,
        "gemeten": gemeten, "eis": eis,
    })


def _mv(route: LineString, p: Point) -> float:
    return route.project(p)


def toets(route: LineString, segments: list, bomen: list | None,
          klic_features: list | None, bgt: dict | None = None,
          referentie: list | None = None) -> tuple[list, dict]:
    """Maatvoeringstoets. Retourneert (bevindingen, maatvoeringsoverzicht)."""
    checks: list = []
    coords = list(route.coords)

    # --- geometrische integriteit -----------------------------------------
    dubbel = sum(1 for i in range(1, len(coords))
                 if (round(coords[i][0], 2), round(coords[i][1], 2))
                 == (round(coords[i - 1][0], 2), round(coords[i - 1][1], 2)))
    if dubbel:
        _bevinding(checks, "afronding_m", "Geometrie: dubbele punten",
                   f"Tracé bevat {dubbel} dubbel(e) punt(en) na afronding op "
                   f"{normen.waarde('afronding_m')} m; normaliseer de "
                   "geometrie.", coords[0], 0.0, gemeten=dubbel, eis=0,
                   ernst="waarschuwing")

    if not route.is_simple:
        p = _zelf_intersectie_punt(coords)
        _bevinding(checks, "zelf_intersectie_ernst",
                   "Geometrie: zelf-intersectie",
                   "Tracé snijdt zichzelf; pas de ligging aan.",
                   p, _mv(route, Point(p)) if p else None)

    # korte knik-segmenten
    min_seg = float(normen.waarde("min_segment_m"))
    knik_gr = float(normen.waarde("knik_hoek_gr"))
    for i in range(1, len(coords) - 1):
        l_vorig = math.hypot(coords[i][0] - coords[i - 1][0],
                             coords[i][1] - coords[i - 1][1])
        hoek = _hoek_verandering(coords[i - 1], coords[i], coords[i + 1])
        if 0 < l_vorig < min_seg and hoek >= knik_gr:
            m = _mv(route, Point(coords[i]))
            _bevinding(checks, "min_segment_m", "Geometrie: kort knik-segment",
                       f"Knik met segment van {l_vorig:.2f} m (< "
                       f"{min_seg:.2f} m) en richtingsverandering "
                       f"{hoek:.0f}° op metrering {m:.1f} m.",
                       coords[i], m, gemeten=round(l_vorig, 2), eis=min_seg)

    # --- snapping op referentieranden -------------------------------------
    if referentie:
        tol = float(normen.waarde("snap_tolerantie_m"))
        zoek = float(normen.waarde("snap_zoekafstand_m"))
        boom = STRtree(referentie)
        for i in range(1, len(coords) - 1):
            p = Point(coords[i])
            rand = referentie[int(boom.nearest(p))]
            d = rand.distance(p)
            if tol < d <= zoek:
                m = _mv(route, p)
                _bevinding(checks, "snap_tolerantie_m",
                           "Nauwkeurigheid: niet gesnapt op referentierand",
                           f"Tracépunt ligt {d:.2f} m naast een BGT-weg-/"
                           f"verhardingsrand of erfgrens (snaptolerantie "
                           f"{tol:.2f} m) op metrering {m:.1f} m; snap het "
                           "punt op de rand of leg de afwijking vast.",
                           coords[i], m, gemeten=round(d, 2), eis=tol)

    # --- buigradius per knikpunt ------------------------------------------
    r_eis = normen.buigradius_eis_m()
    kleinste_radius = None
    for i in range(1, len(coords) - 1):
        hoek = _hoek_verandering(coords[i - 1], coords[i], coords[i + 1])
        if hoek < knik_gr:
            continue
        r_fit = _inpasbare_radius(coords[i - 1], coords[i], coords[i + 1])
        if kleinste_radius is None or r_fit < kleinste_radius["waarde"]:
            m = _mv(route, Point(coords[i]))
            kleinste_radius = {"waarde": round(min(r_fit, 999.0), 2),
                               "eis": round(r_eis, 2),
                               "metrering_m": round(m, 1),
                               "punt": (round(coords[i][0], 2),
                                        round(coords[i][1], 2))}
        if r_fit < r_eis:
            m = _mv(route, Point(coords[i]))
            _bevinding(checks, "buigradius_factor", "Maatvoering: buigradius",
                       f"Bocht op metrering {m:.1f} m: inpasbare boogstraal "
                       f"{r_fit:.2f} m < eis {r_eis:.2f} m "
                       f"({normen.waarde('buigradius_factor'):.0f} × Ø "
                       f"{normen.waarde('kabel_diameter_m')} m); maak de "
                       f"bocht ruimer (richtingsverandering {hoek:.0f}°).",
                       coords[i], m, gemeten=round(r_fit, 2),
                       eis=round(r_eis, 2))

    # --- afstanden en kruisingshoeken t.o.v. KLIC-netten -------------------
    kleinste_afstand: dict = {}
    kleinste_hoek = None
    for geom, props in (klic_features or []):
        if geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        lijnen = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        cat = _klic_categorie(props)
        rid = _KLIC_CATEGORIE_EIS[cat]
        eis = float(normen.waarde(rid))
        label = _KLIC_LABEL[cat]
        for lijn in lijnen:
            if route.crosses(lijn):
                snij = route.intersection(lijn)
                punten = (list(snij.geoms) if hasattr(snij, "geoms")
                          else [snij])
                for sp in punten:
                    if sp.geom_type != "Point":
                        continue
                    hoek = kruisingshoek_gr(route, lijn, sp)
                    m = _mv(route, sp)
                    if kleinste_hoek is None or hoek < kleinste_hoek["waarde"]:
                        kleinste_hoek = {
                            "waarde": round(hoek, 1),
                            "eis": float(normen.waarde("min_kruisingshoek_gr")),
                            "metrering_m": round(m, 1),
                            "punt": (round(sp.x, 2), round(sp.y, 2))}
                    if hoek < float(normen.waarde("min_kruisingshoek_gr")):
                        _bevinding(
                            checks, "min_kruisingshoek_gr",
                            "Maatvoering: scherpe kruising K&L",
                            f"Kruising met {label} onder {hoek:.0f}° op "
                            f"metrering {m:.1f} m (eis ≥ "
                            f"{normen.waarde('min_kruisingshoek_gr'):.0f}°); "
                            "kruis zo haaks mogelijk.",
                            (sp.x, sp.y), m, gemeten=round(hoek, 1),
                            eis=float(normen.waarde("min_kruisingshoek_gr")))
                continue
            d = route.distance(lijn)
            if cat not in kleinste_afstand or d < kleinste_afstand[cat]["waarde"]:
                op_route, _ = nearest_points(route, lijn)
                kleinste_afstand[cat] = {
                    "waarde": round(d, 2), "eis": eis,
                    "metrering_m": round(_mv(route, op_route), 1),
                    "punt": (round(op_route.x, 2), round(op_route.y, 2))}
            if d < eis:
                op_route, _ = nearest_points(route, lijn)
                m = _mv(route, op_route)
                extra = (" (thema onbekend — conservatieve leidingeis, "
                         "aanname)" if cat == "onbekend" else "")
                _bevinding(checks, rid,
                           "Maatvoering: afstand tot parallelle K&L",
                           f"Tracé ligt {d:.2f} m van een {label}"
                           f"{extra} op metrering {m:.1f} m (eis ≥ "
                           f"{eis:.2f} m).",
                           (op_route.x, op_route.y), m,
                           gemeten=round(d, 2), eis=eis)

    # --- bomen: stamafstand + kroonprojectie -------------------------------
    stam_eis = float(normen.waarde("afstand_boom_stam_m"))
    for boom_xyr in (bomen or []):
        x, y, r = boom_xyr[0], boom_xyr[1], (boom_xyr[2]
                                             if len(boom_xyr) > 2 else 0.0)
        stam = Point(x, y)
        d = route.distance(stam)
        if d > max(stam_eis, r) + 0.01:
            continue
        if "bomen" not in kleinste_afstand or d < kleinste_afstand["bomen"]["waarde"]:
            op_route, _ = nearest_points(route, stam)
            kleinste_afstand["bomen"] = {
                "waarde": round(d, 2), "eis": stam_eis,
                "metrering_m": round(_mv(route, op_route), 1),
                "punt": (round(op_route.x, 2), round(op_route.y, 2))}
        op_route, _ = nearest_points(route, stam)
        m = _mv(route, op_route)
        if d < stam_eis:
            _bevinding(checks, "afstand_boom_stam_m",
                       "Maatvoering: afstand tot boomstam",
                       f"Tracé ligt {d:.2f} m van een boomstam op metrering "
                       f"{m:.1f} m (eis ≥ {stam_eis:.2f} m).",
                       (op_route.x, op_route.y), m, gemeten=round(d, 2),
                       eis=stam_eis)
        elif r and d < r:
            _bevinding(checks, "kroonprojectie_ernst",
                       "Maatvoering: binnen kroonprojectie",
                       f"Tracé loopt op {d:.2f} m van een boomstam binnen de "
                       f"kroonprojectie (± {r:.1f} m) op metrering "
                       f"{m:.1f} m; wortelschade mogelijk (BEA).",
                       (op_route.x, op_route.y), m, gemeten=round(d, 2),
                       eis=round(r, 1))

    # --- gevel-/erfgrensafstand (BGT-panden) --------------------------------
    gevel_eis = float(normen.waarde("gevel_erf_afstand_m"))
    panden = [g for g, _p in (bgt or {}).get("pand", [])]
    if panden:
        pand_geom = unary_union(panden)
        d = route.distance(pand_geom)
        if d < gevel_eis:
            op_route, _ = nearest_points(route, pand_geom)
            m = _mv(route, op_route)
            _bevinding(checks, "gevel_erf_afstand_m",
                       "Maatvoering: gevelafstand",
                       f"Tracé ligt {d:.2f} m van een gevel (pand) op "
                       f"metrering {m:.1f} m (eis ≥ {gevel_eis:.2f} m, "
                       "NEN 7171-ligprofiel).",
                       (op_route.x, op_route.y), m, gemeten=round(d, 2),
                       eis=gevel_eis)

    # --- maatvoeringsoverzicht ---------------------------------------------
    overzicht = _overzicht(route, segments, kleinste_radius, kleinste_afstand,
                           kleinste_hoek, checks)
    return checks, overzicht


def _zelf_intersectie_punt(coords: list):
    """Eerste snijpunt van niet-aangrenzende segmenten (voor de melding)."""
    segs = [LineString([coords[i], coords[i + 1]])
            for i in range(len(coords) - 1)]
    boom = STRtree(segs)
    for i, s in enumerate(segs):
        for j in boom.query(s):
            j = int(j)
            if abs(j - i) <= 1:
                continue
            snij = s.intersection(segs[j])
            if not snij.is_empty and snij.geom_type == "Point":
                return (snij.x, snij.y)
    return coords[0]


def _overzicht(route, segments, kleinste_radius, kleinste_afstand,
               kleinste_hoek, checks) -> dict:
    pct = float(normen.waarde("overlengte_pct"))
    per_ligging: dict = {}
    for s in (segments or []):
        per_ligging[s["ligging"]] = round(
            per_ligging.get(s["ligging"], 0.0) + s["lengte_m"], 1)
    dekking = [
        {"ligging": "berm/groenstrook",
         "eis_m": normen.waarde("dekking_berm_m"),
         "bron": normen.bron("dekking_berm_m")},
        {"ligging": "trottoir/voetpad/fietspad/parkeervlak",
         "eis_m": normen.waarde("dekking_trottoir_m"),
         "bron": normen.bron("dekking_trottoir_m")},
        {"ligging": "rijbaan",
         "eis_m": normen.waarde("dekking_rijbaan_m"),
         "bron": normen.bron("dekking_rijbaan_m")},
        {"ligging": "kruising watergang",
         "eis_m": normen.waarde("dekking_water_m"),
         "bron": normen.bron("dekking_water_m")},
    ]
    telling = {"kritiek": 0, "waarschuwing": 0, "info": 0}
    for c in checks:
        telling[c["ernst"]] = telling.get(c["ernst"], 0) + 1
    return {
        "crs": normen.waarde("crs"),
        "totale_lengte_m": round(route.length, 1),
        "overlengte_pct": pct,
        "kabellengte_incl_overlengte_m": round(route.length * (1 + pct / 100), 1),
        "lengte_per_ligging_m": per_ligging,
        "kleinste_buigradius": kleinste_radius,
        "kleinste_afstand": kleinste_afstand,   # per categorie
        "kleinste_kruisingshoek": kleinste_hoek,
        "dekking_eisen": dekking,
        "bevindingen": telling,
        "aannames": [
            "segmenttype afgeleid uit de BGT-laag (geen segmenttype per "
            "tracépunt in het datamodel)",
            "geen z-waarden: dekking is als eis per liggingstype "
            "gerapporteerd, niet gemeten",
        ],
    }


def klic_nabij(route: LineString, marge_m: float = 25.0) -> list:
    """KLIC-features binnen de zoekmarge rond het tracé (voor de toets)."""
    import klic as klic_mod
    zone = route.buffer(marge_m)
    return [(g, p) for g, p in klic_mod._laad() if g.intersects(zone)]
