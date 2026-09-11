"""Kostenoppervlak, tracéberekening en kruisingsherkenning.

Werkwijze conform het functioneel ontwerp §3:
  1. BGT-lagen rasteriseren naar een kostenraster (cel 0,5 m, adaptief).
  2. Kortste gewogen pad tussen de stations (MCP/Dijkstra, scikit-image).
  3. Nabewerking: kostenbewust gladstrijken, kruisingssegmenten rechttrekken.
  4. Kruisingen herkennen en een techniek voorstellen uit de beslistabel §4.
  5. Segmentering naar ligging voor registers en kostenraming.

Lijnvormige obstakels (water, rijbaan, spoor) krijgen een hoge celprijs die
de vaste plus variabele kosten van een boring benadert; het algoritme kiest
daardoor zelf tussen omlopen en kruisen (§3.2). Alle gewichten komen uit een
instelbaar wegingsprofiel.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, mapping
from shapely.ops import unary_union
from skimage.graph import MCP_Geometric
from skimage.measure import label as connected_label

# ---------------------------------------------------------------------------
# Wegingsprofiel (startwaarden uit FO §3.1; per project instelbaar)
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    # basiskosten per meter naar ligging
    "berm_groen": 0.8,
    "voetpad": 1.0,
    "fietspad": 1.3,
    "parkeervlak": 1.5,
    "rijbaan": 3.0,
    "erf_prive": 2.5,          # proxy voor particulier terrein (BRK niet gekoppeld)
    "overig_onverhard": 1.0,
    "onbekend": 1.2,
    "natuur_groen": 1.2,       # bos, heide, duin: graafbaar maar kwetsbaar
    # vermenigvuldigers
    "gesloten_verharding": 1.5,
    # zonelagen (FO §3.1): vermenigvuldigers op de basiskosten
    "natura2000_weg": 1.2,     # binnen Natura 2000 via bestaande weg/berm; daarbuiten ∞
    "nnn": 3.0,                # Natuurnetwerk Nederland: strenge weging
    "grondwaterbescherming": 1.2,
    "bodem_verontreinigd": 2.0,  # vastgestelde verontreiniging/nazorg (BRO SLD; FO ×2,0)
    "bodem_verdacht": 1.5,     # onderzoekslocatie (BRO SAD of historisch Wbb)
    "bodem_elders": 1.0,       # dekking-signaal, geen kosteneffect (alleen melding)
    "archeologie": 1.5,        # AMK-terrein
    "boom_wortelzone": 2.0,    # wortelzone rond BGT-bomen (graven vaak niet toegestaan)
    # lijnvormige obstakels: celprijs per meter die een kruising benadert
    "water_kruising": 14.0,
    "spoor_kruising": 60.0,
}

# klasse-codes voor het class-raster (uint8)
CL_ONBEKEND = 0
CL_BERM = 1
CL_VOETPAD = 2
CL_FIETSPAD = 3
CL_PARKEER = 4
CL_RIJBAAN = 5
CL_ERF = 6
CL_ONVERHARD = 7
CL_WATER = 8
CL_SPOOR = 9
CL_PAND = 10
CL_VERBODEN = 11
CL_NATUURGROEN = 12

CLASS_NAMES = {
    CL_ONBEKEND: "onbekend",
    CL_BERM: "berm / groenstrook",
    CL_VOETPAD: "voetpad / trottoir",
    CL_FIETSPAD: "fietspad",
    CL_PARKEER: "parkeervlak",
    CL_RIJBAAN: "rijbaan",
    CL_ERF: "privaat (aanname: erf/agrarisch)",
    CL_ONVERHARD: "overig onverhard",
    CL_WATER: "watergang",
    CL_SPOOR: "spoor",
    CL_PAND: "pand / bouwwerk",
    CL_VERBODEN: "verboden zone",
    CL_NATUURGROEN: "bos / natuurlijk terrein",
}

# zonebits (FO §2: beschermde gebieden, bodem, archeologie)
ZN_NATURA = 1
ZN_NNN = 2
ZN_GWB = 4
ZN_BODEM = 8
ZN_ARCHEO = 16
ZN_BODEM_ELDERS = 32  # bevoegd gezag publiceert bodemdata via een eigen loket
ZN_BODEM_ONDERZOEK = 64  # bodemonderzoekslocatie (BRO SAD of historisch Wbb)
ZN_BOOM = 128  # wortelzone rond bomen (BGT vegetatieobject, plustopografie)

# minimale wortelzone-straal rond een boompunt; waar een gemeentelijk register
# een kroondiameter levert geldt de kroonprojectie (instelbaar)
BOOM_WORTELZONE_M = 2.5

ZONE_NAMES = {
    ZN_NATURA: "Natura 2000",
    ZN_NNN: "Natuurnetwerk Nederland",
    ZN_GWB: "grondwaterbeschermingsgebied",
    ZN_BODEM: "vastgestelde verontreiniging / nazorg (BRO SLD)",
    ZN_ARCHEO: "archeologisch monument (AMK)",
    ZN_BODEM_ELDERS: "bodemdata via eigen loket bevoegd gezag",
    ZN_BODEM_ONDERZOEK: "bodemonderzoekslocatie (BRO SAD / Wbb)",
    ZN_BOOM: "wortelzone bomen",
}

# BGT begroeid terrein: agrarisch gebruik is een proxy voor particulier bezit,
# natuurlijk groen is kwetsbaar en binnen Natura 2000 uitgesloten
BGT_AGRARISCH = {"bouwland", "grasland agrarisch", "fruitteelt", "boomteelt"}
BGT_URBAAN_GROEN = {"groenvoorziening"}

# klassen die binnen Natura 2000 begaanbaar blijven ("bestaande weg of berm")
NATURA_TOEGESTAAN = {CL_BERM, CL_VOETPAD, CL_FIETSPAD, CL_PARKEER, CL_RIJBAAN,
                     CL_ONVERHARD, CL_WATER, CL_SPOOR}

WEGDEEL_FUNCTIE = {
    "voetpad": CL_VOETPAD,
    "voetgangersgebied": CL_VOETPAD,
    "voetpad op trap": CL_VOETPAD,
    "fietspad": CL_FIETSPAD,
    "parkeervlak": CL_PARKEER,
    "woonerf": CL_PARKEER,
    "inrit": CL_FIETSPAD,
}


class EngineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Rasterisatie
# ---------------------------------------------------------------------------

class Grid:
    """Kosten- en klasse-raster over het projectgebied."""

    def __init__(self, bbox: tuple, cell: float):
        self.xmin, self.ymin, self.xmax, self.ymax = bbox
        self.cell = cell
        self.ncols = max(2, int(math.ceil((self.xmax - self.xmin) / cell)))
        self.nrows = max(2, int(math.ceil((self.ymax - self.ymin) / cell)))
        self.cost = np.full((self.nrows, self.ncols), np.nan, dtype=np.float32)
        self.klass = np.zeros((self.nrows, self.ncols), dtype=np.uint8)
        self.zones = np.zeros((self.nrows, self.ncols), dtype=np.uint8)

    def world_to_cell(self, x: float, y: float) -> tuple:
        col = int((x - self.xmin) / self.cell)
        row = int((self.ymax - y) / self.cell)
        return (min(max(row, 0), self.nrows - 1), min(max(col, 0), self.ncols - 1))

    def cell_to_world(self, row: int, col: int) -> tuple:
        return (self.xmin + (col + 0.5) * self.cell, self.ymax - (row + 0.5) * self.cell)

    def _draw_polygon(self, draw: ImageDraw.ImageDraw, poly: Polygon, value: int):
        def ring_to_px(ring):
            return [
                ((x - self.xmin) / self.cell, (self.ymax - y) / self.cell)
                for x, y in ring.coords
            ]

        ext = ring_to_px(poly.exterior)
        if len(ext) >= 3:
            draw.polygon(ext, fill=value)
        for hole in poly.interiors:
            px = ring_to_px(hole)
            if len(px) >= 3:
                draw.polygon(px, fill=0)

    def rasterize(self, geoms: list, buffer: float = 0.0) -> np.ndarray:
        """Booleaans masker (nrows × ncols) van een lijst shapely-geometrieën."""
        img = Image.new("L", (self.ncols, self.nrows), 0)
        draw = ImageDraw.Draw(img)
        for g in geoms:
            if buffer:
                g = g.buffer(buffer)
            if g.is_empty:
                continue
            if isinstance(g, Polygon):
                self._draw_polygon(draw, g, 1)
            elif isinstance(g, MultiPolygon):
                for p in g.geoms:
                    self._draw_polygon(draw, p, 1)
            elif isinstance(g, (LineString, MultiLineString)):
                lines = g.geoms if isinstance(g, MultiLineString) else [g]
                w = max(1, int(round((buffer * 2 or 1.0) / self.cell)))
                for line in lines:
                    px = [
                        ((x - self.xmin) / self.cell, (self.ymax - y) / self.cell)
                        for x, y in line.coords
                    ]
                    draw.line(px, fill=1, width=w)
        return np.array(img, dtype=bool)

    def paint(self, mask: np.ndarray, code: int, cost: float):
        self.klass[mask] = code
        self.cost[mask] = cost


class Painter:
    """Voorberekende maskers in schilderorde; per wegingsprofiel snel een Grid.

    De rasterisatie (duur) gebeurt één keer; het toepassen van een variant-
    wegingsprofiel is daarna alleen nog maskers inkleuren.
    """

    def __init__(self, bbox: tuple, cell: float):
        self.bbox = bbox
        self.cell = cell
        self.ops: list = []  # (mask, klasse-code, gewicht-sleutel | None, factor | vaste waarde)
        self.zone_ops: list = []  # (mask, zonebit, gewicht-sleutel)

    def add(self, mask: np.ndarray, code: int, key: str | None, factor: float = 1.0):
        if mask.any():
            self.ops.append((mask, code, key, factor))

    def add_zone(self, mask, bit: int, key: str):
        if mask is not None and mask.any():
            self.zone_ops.append((mask, bit, key))

    def build(self, weights: dict) -> Grid:
        w = {**DEFAULT_WEIGHTS, **(weights or {})}
        grid = Grid(self.bbox, self.cell)
        grid.cost[:] = w["onbekend"]
        grid.klass[:] = CL_ONBEKEND
        for mask, code, key, factor in self.ops:
            if key is None:
                grid.paint(mask, code, factor)  # vaste waarde (∞ voor uitsluitingen)
            elif key.endswith("*gesloten"):
                grid.paint(mask, code, w[key.split("*")[0]] * w["gesloten_verharding"] * factor)
            else:
                grid.paint(mask, code, w[key] * factor)
        # zonelagen: vermenigvuldigen bovenop de basiskosten; Natura 2000 sluit
        # alles buiten bestaande weg of berm hard uit (FO §3.1)
        for mask, bit, key in self.zone_ops:
            grid.zones[mask] |= bit
            if bit == ZN_NATURA:
                verboden = mask & ~np.isin(grid.klass, list(NATURA_TOEGESTAAN))
                grid.cost[verboden] = np.inf
                toegestaan = mask & ~verboden
                grid.cost[toegestaan] *= w[key]
            else:
                grid.cost[mask] *= w[key]
        return grid


def build_painter(bbox: tuple, bgt: dict, forbidden: list, cell: float,
                  zones: dict | None = None) -> Painter:
    """Maskers opbouwen in schilderorde: terrein → wegen → obstakels → zones."""
    painter = Painter(bbox, cell)
    grid = Grid(bbox, cell)  # alleen voor rasterize-hulpfuncties

    def geoms(coll, pred=None):
        return [g for g, p in bgt.get(coll, []) if pred is None or pred(p)]

    # 1. terrein — begroeid gesplitst: urbaan groen, agrarisch (privaat-proxy),
    #    natuurlijk groen (kwetsbaar; binnen Natura 2000 uitgesloten)
    begroeid = bgt.get("begroeidterreindeel", [])
    painter.add(grid.rasterize([g for g, p in begroeid
                                if (p.get("fysiek_voorkomen") or "") in BGT_URBAAN_GROEN]),
                CL_BERM, "berm_groen")
    painter.add(grid.rasterize([g for g, p in begroeid
                                if (p.get("fysiek_voorkomen") or "") in BGT_AGRARISCH]),
                CL_ERF, "erf_prive")
    painter.add(grid.rasterize([g for g, p in begroeid
                                if (p.get("fysiek_voorkomen") or "") not in
                                (BGT_URBAAN_GROEN | BGT_AGRARISCH)]),
                CL_NATUURGROEN, "natuur_groen")

    onbegroeid = bgt.get("onbegroeidterreindeel", [])
    painter.add(grid.rasterize([g for g, p in onbegroeid
                                if p.get("fysiek_voorkomen") not in ("erf", "gesloten verharding")]),
                CL_ONVERHARD, "overig_onverhard")
    painter.add(grid.rasterize([g for g, p in onbegroeid
                                if p.get("fysiek_voorkomen") == "gesloten verharding"]),
                CL_ONVERHARD, "overig_onverhard*gesloten")
    painter.add(grid.rasterize([g for g, p in onbegroeid if p.get("fysiek_voorkomen") == "erf"]),
                CL_ERF, "erf_prive")

    # 2. ondersteunend wegdeel (berm, verkeerseiland)
    painter.add(grid.rasterize(geoms("ondersteunendwegdeel", lambda p: p.get("functie") == "berm")),
                CL_BERM, "berm_groen")
    painter.add(grid.rasterize(geoms("ondersteunendwegdeel", lambda p: p.get("functie") != "berm")),
                CL_BERM, "berm_groen", 1.3)

    # 3. wegdelen per functie; gesloten verharding × factor
    wegdelen = bgt.get("wegdeel", [])
    per_functie = {CL_VOETPAD: "voetpad", CL_FIETSPAD: "fietspad",
                   CL_PARKEER: "parkeervlak", CL_RIJBAAN: "rijbaan"}
    for code, key in per_functie.items():
        open_g, dicht_g = [], []
        for g, p in wegdelen:
            functie = (p.get("functie") or "").lower()
            cl = WEGDEEL_FUNCTIE.get(functie,
                                     CL_RIJBAAN if "rijbaan" in functie or "baan" in functie else None)
            if cl != code:
                continue
            (dicht_g if p.get("fysiek_voorkomen") == "gesloten verharding" else open_g).append(g)
        painter.add(grid.rasterize(open_g), code, key)
        painter.add(grid.rasterize(dicht_g), code, f"{key}*gesloten")

    # 4. lijnvormige obstakels: water en spoor als "kruisingsprijs per meter" (§3.2)
    water = geoms("waterdeel") + geoms("ondersteunendwaterdeel")
    painter.add(grid.rasterize(water), CL_WATER, "water_kruising")
    spoor = geoms("spoor")
    if spoor:
        painter.add(grid.rasterize(spoor, buffer=2.5), CL_SPOOR, "spoor_kruising")

    # 5. harde uitsluitingen
    hard = (geoms("pand") + geoms("overigbouwwerk") + geoms("kunstwerkdeel_vlak")
            + geoms("overbruggingsdeel") + geoms("tunneldeel"))
    painter.add(grid.rasterize(hard), CL_PAND, None, np.inf)

    # 6. door de ontwerper getekende verboden zones
    if forbidden:
        polys = [Polygon(c) for c in forbidden if len(c) >= 3]
        painter.add(grid.rasterize(polys), CL_VERBODEN, None, np.inf)

    # 7. zonelagen (FO §2): vector (WFS) of masker (WMS)
    if zones:
        if zones.get("natura"):
            painter.add_zone(grid.rasterize(zones["natura"]), ZN_NATURA, "natura2000_weg")
        painter.add_zone(zones.get("nnn_mask"), ZN_NNN, "nnn")
        painter.add_zone(zones.get("gwb_mask"), ZN_GWB, "grondwaterbescherming")
        painter.add_zone(zones.get("bodem_mask"), ZN_BODEM, "bodem_verontreinigd")
        painter.add_zone(zones.get("bodem_onderzoek_mask"), ZN_BODEM_ONDERZOEK, "bodem_verdacht")
        painter.add_zone(zones.get("bodem_dekking_mask"), ZN_BODEM_ELDERS, "bodem_elders")
        if zones.get("archeo"):
            painter.add_zone(grid.rasterize(zones["archeo"]), ZN_ARCHEO, "archeologie")
        if zones.get("bomen"):
            # al gebufferde wortelzone-vlakken (kroonprojectie per boom,
            # minimaal r = BOOM_WORTELZONE_M; gebufferd in main.compute)
            painter.add_zone(grid.rasterize(zones["bomen"]), ZN_BOOM, "boom_wortelzone")

    return painter


def zone_lengtes(route: LineString, grid: Grid, sample_m: float = 2.0) -> dict:
    """Meters tracé per zonelaag (voor toetsing, vergunningen en onderzoeken)."""
    n = max(2, int(route.length / sample_m) + 1)
    tellers = {bit: 0 for bit in ZONE_NAMES}
    for i in range(n):
        p = route.interpolate(min(route.length, i * sample_m))
        r, c = grid.world_to_cell(p.x, p.y)
        z = int(grid.zones[r, c])
        for bit in tellers:
            if z & bit:
                tellers[bit] += 1
    stap = route.length / (n - 1) if n > 1 else 0
    return {bit: round(cnt * stap, 1) for bit, cnt in tellers.items()}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _free_station(grid: Grid, xy: tuple, radius_m: float = 6.0) -> None:
    """Maak cellen rond een station begaanbaar (stations zijn bereikbaar)."""
    r, c = grid.world_to_cell(*xy)
    n = max(1, int(radius_m / grid.cell))
    r0, r1 = max(0, r - n), min(grid.nrows, r + n + 1)
    c0, c1 = max(0, c - n), min(grid.ncols, c + n + 1)
    patch = grid.cost[r0:r1, c0:c1]
    vrij = ~np.isfinite(patch)
    patch[vrij] = 2.0
    grid.klass[r0:r1, c0:c1][vrij] = CL_ONBEKEND


def _straight_cost(grid: Grid, p0: tuple, p1: tuple) -> float:
    """Gewogen kosten van een recht lijnstuk over het raster (∞ bij uitsluiting)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    lengte = math.hypot(dx, dy)
    if lengte == 0:
        return 0.0
    n = max(2, int(math.ceil(lengte / (grid.cell / 2))))
    ts = (np.arange(n) + 0.5) / n
    cols = np.clip(((p0[0] + ts * dx - grid.xmin) / grid.cell).astype(np.int64),
                   0, grid.ncols - 1)
    rows = np.clip(((grid.ymax - (p0[1] + ts * dy)) / grid.cell).astype(np.int64),
                   0, grid.nrows - 1)
    vals = grid.cost[rows, cols]
    if not np.all(np.isfinite(vals)):
        return math.inf
    return float(vals.mean() * lengte)


def smooth_route(coords: list, grid: Grid, slack: float = 1.05) -> list:
    """Rasterpad gladstrijken: deelpaden vervangen door rechte lijnstukken.

    Op een vlak kostenveld zijn veel trappenpaden precies even duur en kiest de
    Dijkstra-traceback er willekeurig één — dat oogt als bochten zonder reden.
    Een recht alternatief wordt geaccepteerd zolang de gewogen kosten hoogstens
    `slack` × de kosten van het oorspronkelijke deelpad zijn; een bocht blijft
    dus alleen staan waar hij echt iets oplevert (omweg om dure cellen of een
    uitsluiting heen).
    """
    pts = [tuple(c) for c in coords]
    if len(pts) < 3:
        return pts
    # stapkosten tussen buurcellen exact zoals MCP ze rekent (gemiddelde van de
    # celkosten × afstand): lijnsampling raakt bij een diagonale stap soms
    # precies de celhoek van een uitgesloten buurcel en levert dan onterecht ∞;
    # één ∞ in cum zou daarna elke vergelijking "inf ≤ inf" laten slagen
    cum = [0.0]
    for a, b in zip(pts[:-1], pts[1:]):
        ra = grid.world_to_cell(*a)
        rb = grid.world_to_cell(*b)
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        cum.append(cum[-1] + (float(grid.cost[ra]) + float(grid.cost[rb])) / 2 * d)
    last = len(pts) - 1

    def ok(i: int, j: int) -> bool:
        sc = _straight_cost(grid, pts[i], pts[j])
        return math.isfinite(sc) and sc <= slack * (cum[j] - cum[i]) + 1e-6

    out = [pts[0]]
    i = 0
    while i < last:
        # exponentieel vooruit zoeken, daarna binair verfijnen naar het verste
        # acceptabele punt (één stap vooruit is per definitie acceptabel)
        j, span = i + 1, 1
        while j < last:
            k = min(last, i + span * 2)
            if ok(i, k):
                j, span = k, span * 2
            else:
                break
        lo, hi = j, min(last, i + span * 2)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(i, mid):
                lo = mid
            else:
                hi = mid
        i = lo
        out.append(pts[i])
    return out


def route_chunk(grid: Grid, start: tuple, end: tuple, end_radius_m: float = 0.0,
                slack: float = 1.05) -> list:
    """Kortste gewogen pad binnen één corridor-raster (deeltraject).

    Bij ``end_radius_m > 0`` ligt het eindpunt niet vast: de route eindigt op
    de goedkoopst bereikbare cel binnen die straal rond ``end``. Zo schuift
    het naadpunt tussen twee deeltrajecten vanzelf weg van gebouwen of water
    in plaats van er hard doorheen geforceerd te worden. De afstand tot het
    doelpunt telt licht mee zodat de naad niet onnodig ver van de hemelsbrede
    lijn afdrijft.
    """
    costs = grid.cost * grid.cell
    ra = grid.world_to_cell(*start)
    rb = grid.world_to_cell(*end)
    mcp = MCP_Geometric(costs, fully_connected=True)
    if end_radius_m > 0:
        cumcost, _ = mcp.find_costs(starts=[ra])
        n = max(1, int(end_radius_m / grid.cell))
        r0, r1 = max(0, rb[0] - n), min(grid.nrows, rb[0] + n + 1)
        c0, c1 = max(0, rb[1] - n), min(grid.ncols, rb[1] + n + 1)
        win = cumcost[r0:r1, c0:c1]
        eindig = np.isfinite(grid.cost)
        prijs_per_m = float(grid.cost[eindig].mean()) if eindig.any() else 1.0
        rr, cc = np.mgrid[r0:r1, c0:c1]
        score = np.where(np.isfinite(win),
                         win + np.hypot(rr - rb[0], cc - rb[1]) * grid.cell * prijs_per_m,
                         np.inf)
        if not np.isfinite(score).any():
            raise EngineError(
                "Geen begaanbare aansluiting op het volgende deeltraject gevonden. "
                "Controleer verboden zones of plaats een via-punt."
            )
        idx = np.unravel_index(int(np.argmin(score)), score.shape)
        rb = (r0 + int(idx[0]), c0 + int(idx[1]))
    else:
        cumcost, _ = mcp.find_costs(starts=[ra], ends=[rb])
        if not np.isfinite(cumcost[rb]):
            raise EngineError(
                "Geen begaanbare route binnen dit deeltraject gevonden. "
                "Controleer verboden zones of plaats een via-punt."
            )
    tb = mcp.traceback(rb)
    pts = [grid.cell_to_world(r, c) for r, c in tb]
    if len(pts) < 2:  # start en eind in dezelfde cel (heel korte verbinding)
        pts = pts * 2
    return smooth_route(pts, grid, slack)


def shortest_path(grid: Grid, waypoints: list, slack: float = 1.05) -> list:
    """Kortste gewogen pad langs alle waypoints; retourneert RD-coördinaten.

    Elke verbinding wordt apart gladgestreken zodat via-punten hoekpunten
    blijven en niet worden weggesneden.
    """
    costs = grid.cost * grid.cell  # kosten per meter → kosten per cel-stap
    coords: list = []
    for a, b in zip(waypoints[:-1], waypoints[1:]):
        ra = grid.world_to_cell(*a)
        rb = grid.world_to_cell(*b)
        mcp = MCP_Geometric(costs, fully_connected=True)
        cumcost, _ = mcp.find_costs(starts=[ra], ends=[rb])
        if not np.isfinite(cumcost[rb]):
            raise EngineError(
                "Geen begaanbare route tussen de stations gevonden. "
                "Controleer verboden zones en het projectgebied."
            )
        tb = mcp.traceback(rb)
        seg = smooth_route([grid.cell_to_world(r, c) for r, c in tb], grid, slack)
        if coords:
            seg = seg[1:]
        coords.extend(seg)
    return coords


# ---------------------------------------------------------------------------
# Nabewerking en kruisingen
# ---------------------------------------------------------------------------

def _substring(line: LineString, m0: float, m1: float) -> LineString:
    """Deel van een lijn tussen twee afstanden langs de lijn."""
    m0, m1 = max(0.0, m0), min(line.length, m1)
    pts = [line.interpolate(m0)]
    for c in line.coords:
        m = line.project(Point(c))
        if m0 < m < m1:
            pts.append(Point(c))
    pts.append(line.interpolate(m1))
    return LineString([(p.x, p.y) for p in pts])


def line_vrij(line: LineString, grid: Grid) -> bool:
    """True als de lijn nergens door een uitgesloten (∞-)cel loopt."""
    stap = grid.cell / 2
    n = max(2, int(line.length / stap))
    for i in range(n + 1):
        p = line.interpolate(i * line.length / n)
        r, c = grid.world_to_cell(p.x, p.y)
        if not np.isfinite(grid.cost[r, c]):
            return False
    return True


TECHNIEK_HDD = "Gestuurde boring (HDD)"
TECHNIEK_PERSING = "Persing (mantelbuis)"
TECHNIEK_OPEN = "Open sleuf"
TECHNIEK_NANO = "Nanodrill"
TECHNIEK_RAKET = "Raketboring (ongestuurd)"

# in-/uittredepunt: afstand vóór en na het obstakel (m), per techniek.
# HDD: intredehoek 8–12° met dekking ≥ 1,5 m vraagt ruime aanloop;
# persing/raket: kuip direct naast het obstakel.
BOOR_UITLOOP = {
    TECHNIEK_HDD: 10.0,
    TECHNIEK_NANO: 5.0,
    TECHNIEK_PERSING: 3.0,
    TECHNIEK_RAKET: 2.0,
}
BOOR_UITLOOP_DEFAULT = 3.0

# Beslisdrempels (m, gemeten langs het tracé): tot waar een open kruising de
# standaard is en vanaf waar een zwaardere techniek nodig is. Sleufloos wordt
# alleen voorgesteld waar de beheerder open ontgraving feitelijk niet toestaat.
WATER_OPEN_MAX_M = 3.0      # watergang B/C: open ontgraving met afdamming
WATER_PERSING_MAX_M = 8.0   # tot hier volstaat persing/nanodrill; daarboven HDD
RIJBAAN_OPEN_MAX_M = 7.0    # erftoegangsweg: open sleuf in halve rijbaan (AVOI)
RIJBAAN_PERSING_MAX_M = 12.0

# Indicatieve ruimtebehoefte werkterrein per techniek (m², instelbaar).
# Globale toets: aaneengesloten inzetbaar oppervlak rond het in-/uittredepunt;
# vorm en obstakelvrije opstellengte worden niet getoetst — bij twijfel
# beslist het type boorstelling.
WERKTERREIN_EIS = {
    TECHNIEK_HDD: {"intrede_m2": 200, "uittrede_m2": 80, "zoekstraal_m": 30},
    TECHNIEK_PERSING: {"intrede_m2": 60, "uittrede_m2": 25, "zoekstraal_m": 20},
    TECHNIEK_NANO: {"intrede_m2": 40, "uittrede_m2": 15, "zoekstraal_m": 15},
    TECHNIEK_RAKET: {"intrede_m2": 12, "uittrede_m2": 12, "zoekstraal_m": 12},
}
RAKET_MAX_BOORLENGTE_M = 18.0  # ongestuurd: alleen korte kruisingen

# cellen waarop een werkterrein kan worden ingericht; privaat terrein telt
# mee maar vraagt toestemming (opmerking in het register)
WERKTERREIN_KLASSEN = (CL_ONBEKEND, CL_BERM, CL_VOETPAD, CL_FIETSPAD,
                       CL_PARKEER, CL_ERF, CL_ONVERHARD, CL_NATUURGROEN)


def _techniek_voor_kruising(soort: str, breedte: float) -> dict:
    """Beslistabel FO §4 (vereenvoudigd naar de in het MVP geladen lagen).

    Kritisch toegepast: open kruising is de standaard waar de beheerder die
    toestaat; sleufloos alleen waar dat verplicht of feitelijk onvermijdelijk
    is (spoor, brede watergang, brede/drukke rijbaan). Elke keuze draagt een
    `noodzaak`-motivering.
    """
    if soort == "spoor":
        return {
            "techniek": TECHNIEK_HDD,
            "detail": "HDD of persing in stalen mantelbuis, haaks op het spoor",
            "richtlijn": "ProRail-voorschriften kabels en leidingen; beperkingengebied spoor",
            "bevoegd_gezag": "ProRail",
            "noodzaak": "sleufloos verplicht — open ontgraving onder spoor is niet toegestaan (ProRail)",
        }
    if soort == "water":
        if breedte <= WATER_OPEN_MAX_M:
            return {
                "techniek": TECHNIEK_OPEN,
                "detail": "Open ontgraving met afdamming/tijdelijke dam (watergang B/C, "
                          "aanname); herstel talud en bodem",
                "richtlijn": "Beleid waterschap; melding waterschapsverordening",
                "bevoegd_gezag": "Waterschap",
                "noodzaak": f"boring niet nodig — smalle watergang (≤ {WATER_OPEN_MAX_M:g} m), "
                            "open kruising met afdamming gangbaar",
            }
        if breedte <= WATER_PERSING_MAX_M:
            return {
                "techniek": TECHNIEK_PERSING,
                "detail": "Persing of nanodrill onder de watergang; dekking per keur "
                          "onder leggerbodem; kuipen buiten het onderhoudspad",
                "richtlijn": "Keur waterschap; NEN 3651; CROW 308",
                "bevoegd_gezag": "Waterschap",
                "noodzaak": f"sleufloos vereist — afdamming bij > {WATER_OPEN_MAX_M:g} m niet "
                            "reëel; korte kruising, dus persing volstaat (geen HDD nodig)",
            }
        return {
            "techniek": TECHNIEK_HDD,
            "detail": "HDD onder de watergang; dekking per keur onder leggerbodem "
                      "(veelal 1,0–1,5 m); in-/uittredepunt buiten het onderhoudspad",
            "richtlijn": "Keur waterschap; NEN 3651; CROW 308",
            "bevoegd_gezag": "Waterschap",
            "noodzaak": f"sleufloos vereist — brede watergang (> {WATER_PERSING_MAX_M:g} m), "
                        "open kruising of persing per keur niet toegestaan/haalbaar",
        }
    if soort == "rijbaan":
        if breedte <= RIJBAAN_OPEN_MAX_M:
            return {
                "techniek": TECHNIEK_OPEN,
                "detail": "Open sleuf in halve rijbaan met fasering en verkeersmaatregelen; "
                          "herstel verharding en degeneratievergoeding",
                "richtlijn": "AVOI gemeente; CROW 96b",
                "bevoegd_gezag": "Wegbeheerder (gemeente, aanname)",
                "noodzaak": f"boring niet nodig — smalle rijbaan (≤ {RIJBAAN_OPEN_MAX_M:g} m, "
                            "erftoegangsweg), open sleuf onder AVOI toegestaan",
            }
        if breedte <= RIJBAAN_PERSING_MAX_M:
            return {
                "techniek": TECHNIEK_PERSING,
                "detail": "Persing (mantelbuis); pers- en ontvangkuip buiten de verharding",
                "richtlijn": "AVOI gemeente; CROW 308",
                "bevoegd_gezag": "Wegbeheerder (gemeente, aanname)",
                "noodzaak": f"sleufloos vereist — rijbaan > {RIJBAAN_OPEN_MAX_M:g} m "
                            "(gebiedsontsluiting, aanname): wegbeheerder staat open sleuf "
                            "niet toe; korte kruising, dus persing volstaat",
            }
        return {
            "techniek": TECHNIEK_HDD,
            "detail": "HDD of persing; wegbeheerder staat open ontgraving vrijwel nooit toe; "
                      "pers- en ontvangkuip buiten de verharding",
            "richtlijn": "AVOI / RWS-richtlijn boortechnieken",
            "bevoegd_gezag": "Wegbeheerder",
            "noodzaak": f"sleufloos vereist — brede rijbaan (> {RIJBAAN_PERSING_MAX_M:g} m); "
                        "boorlengte te groot voor persing vanuit kuipen",
        }
    return {
        "techniek": TECHNIEK_OPEN,
        "detail": "Standaard graafwerk",
        "richtlijn": "CROW 500; kabelleggingsnormen netbeheerder",
        "bevoegd_gezag": "-",
        "noodzaak": "geen boring nodig",
    }


# Alleen een échte dwarsing telt als kruising: schampt het tracé enkel een
# rand (dwarsbreedte < MIN_DWARS) of loopt het in de lengterichting door/
# langs het obstakel (kruislengte > LANGS_FACTOR × dwarsbreedte), dan is
# het geen kruising — en dus ook geen boring.
KRUISING_MIN_DWARS_M = 0.8
KRUISING_LANGS_FACTOR = 3.0


def _dwarsbreedte(route: LineString, m0: float, m1: float, obs) -> float:
    """Echte dwarsbreedte van het obstakel ter plaatse van de passage.

    Geschat als 2× de grootste afstand van het tracé tot de obstakelrand
    binnen de passage — onafhankelijk van de kruisingshoek: een 6 m-weg
    blijft 6 m, ook bij een schuine oversteek (waar de lengte langs het
    tracé groter is) of een lange weg (waar een meting loodrecht op het
    tracé de wéglengte zou geven)."""
    grens = obs.boundary
    n = min(400, max(8, int((m1 - m0) * 4) + 1))
    diepte = 0.0
    for i in range(n + 1):
        p = route.interpolate(m0 + (m1 - m0) * i / n)
        if obs.covers(p):
            diepte = max(diepte, grens.distance(p))
    return 2.0 * diepte


def detect_crossings(route: LineString, bgt: dict) -> list:
    """Kruisingen van het tracé met water, rijbaan en spoor, met techniekvoorstel."""
    obstakels = {}
    # alleen het waterdeel zelf: de oeverzone (ondersteunendwaterdeel) kruisen
    # is geen waterkruising en rechtvaardigt geen boring
    water = [g for g, p in bgt.get("waterdeel", [])]
    if water:
        obstakels["water"] = unary_union(water)
    rijbanen = []
    for g, p in bgt.get("wegdeel", []):
        functie = (p.get("functie") or "").lower()
        if WEGDEEL_FUNCTIE.get(functie) is None and ("rijbaan" in functie or "baan" in functie):
            rijbanen.append(g)
    if rijbanen:
        obstakels["rijbaan"] = unary_union(rijbanen)
    spoor = [g.buffer(2.5) for g, p in bgt.get("spoor", [])]
    if spoor:
        obstakels["spoor"] = unary_union(spoor)

    crossings = []
    for soort, obs in obstakels.items():
        inter = route.intersection(obs)
        if inter.is_empty:
            continue
        parts = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
        # chainages bepalen en delen die dicht bij elkaar liggen samenvoegen
        spans = []
        for part in parts:
            if not isinstance(part, LineString) or part.length < 0.5:
                continue
            m0 = route.project(Point(part.coords[0]))
            m1 = route.project(Point(part.coords[-1]))
            spans.append((min(m0, m1), max(m0, m1)))
        spans.sort()
        merged = []
        for s in spans:
            if merged and s[0] - merged[-1][1] < 5.0:
                merged[-1] = (merged[-1][0], max(merged[-1][1], s[1]))
            else:
                merged.append(s)
        for m0, m1 in merged:
            langs = m1 - m0
            dwars = _dwarsbreedte(route, m0, m1, obs)
            if dwars < KRUISING_MIN_DWARS_M:
                continue  # schampt alleen een rand — geen kruising
            if langs > KRUISING_LANGS_FACTOR * max(dwars, 1.0):
                continue  # loopt in lengterichting door/langs het obstakel
            mid = route.interpolate((m0 + m1) / 2)
            voorstel = _techniek_voor_kruising(soort, round(dwars, 1))
            crossings.append({
                "soort": soort,
                "breedte_m": round(dwars, 1),
                "kruislengte_m": round(langs, 1),
                "chainage_van_m": round(m0, 1),
                "chainage_tot_m": round(m1, 1),
                "punt": (round(mid.x, 2), round(mid.y, 2)),
                **voorstel,
            })
    crossings.sort(key=lambda c: c["chainage_van_m"])
    for i, c in enumerate(crossings, 1):
        c["nr"] = f"KR-{i:03d}"
    return crossings


# ---------------------------------------------------------------------------
# Werkterrein-toets bij boringen (globaal) en sleufloze alternatieven
# ---------------------------------------------------------------------------

def _werkruimte_m2(grid: Grid, punt: Point, zoekstraal: float) -> tuple:
    """Aaneengesloten inzetbaar oppervlak (m²) rond een punt.

    Flood-fill op het klasseraster binnen de zoekstraal; cellen binnen
    Natura 2000 tellen niet mee. Retourneert (totaal m², waarvan privaat m²).
    """
    r, c = grid.world_to_cell(punt.x, punt.y)
    n = max(1, int(zoekstraal / grid.cell))
    ra, rb = max(0, r - n), min(grid.nrows, r + n + 1)
    ca, cb = max(0, c - n), min(grid.ncols, c + n + 1)
    win = grid.klass[ra:rb, ca:cb]
    inzetbaar = np.isin(win, WERKTERREIN_KLASSEN) & \
        ((grid.zones[ra:rb, ca:cb] & ZN_NATURA) == 0)
    labels = connected_label(inzetbaar, connectivity=2)
    # component bij het punt; punt kan nét op de obstakelrand liggen, dus ook
    # de directe omgeving (±2 m) meenemen en de grootste component kiezen
    marge = max(1, int(2.0 / grid.cell))
    lr, lc = r - ra, c - ca
    buurt = labels[max(0, lr - marge):lr + marge + 1, max(0, lc - marge):lc + marge + 1]
    kandidaten = np.unique(buurt[buurt > 0])
    if not len(kandidaten):
        return 0.0, 0.0
    beste = max(kandidaten, key=lambda l: np.sum(labels == l))
    comp = labels == beste
    cel_m2 = grid.cell * grid.cell
    totaal = float(np.sum(comp)) * cel_m2
    privaat = float(np.sum(comp & (win == CL_ERF))) * cel_m2
    return totaal, privaat


def _ruimte_oordeel(beschikbaar: float, benodigd: float) -> str:
    if beschikbaar >= 1.5 * benodigd:
        return "voldoende"
    if beschikbaar >= benodigd:
        return "onzeker"
    return "onvoldoende"


def _sleufloze_alternatieven(soort: str, breedte: float) -> list:
    """Toegestane sleufloze alternatieven binnen de richtlijnen, in volgorde
    van voorkeur bij ruimtegebrek (aflopende ruimtebehoefte)."""
    if soort == "spoor":
        # ProRail: alleen gestuurde boring of persing in stalen mantelbuis;
        # ongestuurde technieken zijn niet toegestaan
        return [TECHNIEK_HDD, TECHNIEK_PERSING]
    if soort == "water":
        if breedte <= WATER_OPEN_MAX_M:
            # voorstel is hier al open; sleufloos alleen als de open kruising
            # elders strandt (raket met instemming waterschap)
            return [TECHNIEK_NANO, TECHNIEK_RAKET, TECHNIEK_OPEN]
        if breedte <= WATER_PERSING_MAX_M:
            # afdamming niet reëel en ongestuurd per keur niet toegestaan:
            # alleen gestuurde/omhulde technieken blijven over
            return [TECHNIEK_PERSING, TECHNIEK_NANO]
        return [TECHNIEK_HDD, TECHNIEK_NANO, TECHNIEK_PERSING]
    if soort == "rijbaan":
        if breedte <= RIJBAAN_OPEN_MAX_M:
            return [TECHNIEK_NANO, TECHNIEK_RAKET, TECHNIEK_OPEN]
        if breedte <= RIJBAAN_PERSING_MAX_M:
            # open sleuf als laatste uitweg, alleen waar de wegbeheerder dat
            # bij ruimtegebrek alsnog toestaat
            return [TECHNIEK_PERSING, TECHNIEK_NANO, TECHNIEK_RAKET, TECHNIEK_OPEN]
        # brede weg: wegbeheerder staat open ontgraving vrijwel nooit toe
        return [TECHNIEK_HDD, TECHNIEK_PERSING, TECHNIEK_NANO]
    return [TECHNIEK_OPEN]


ALTERNATIEF_DETAIL = {
    TECHNIEK_HDD: ("HDD met compacte boorstelling; uitlegstrook boorstreng apart toetsen",
                   "NEN 3650/3651; richtlijn boortechnieken"),
    TECHNIEK_PERSING: ("Persing/avegaarboring vanuit pers- en ontvangkuip; compacte opstelling",
                       "NEN 3650/3651; CROW 308; eisen beheerder"),
    TECHNIEK_NANO: ("Mini-HDD/nanodrill met compacte boorstelling; beperkte diameter en lengte",
                    "Richtlijn boortechnieken; eisen beheerder"),
    TECHNIEK_RAKET: ("Raketboring (ongestuurd) vanuit kleine kuipen; alleen korte kruising, "
                     "koersvastheid beperkt — vrije ligging via KLIC controleren",
                     "CROW 500; instemming beheerder (ongestuurde techniek)"),
    TECHNIEK_OPEN: ("Open kruising met fasering/afdamming en herstel verharding of talud",
                    "AVOI / beleid waterschap; CROW 96b"),
}

_OORDEEL_ORDE = {"voldoende": 0, "onzeker": 1, "onvoldoende": 2}


def beoordeel_werkterreinen(route: LineString, crossings: list, grid: Grid) -> None:
    """Werkterrein-toets per boring (in place op de kruisingenlijst).

    Globaal, conform werkwijze: ruim voldoende ruimte → akkoord; twijfel →
    markeren als onzeker (afhankelijk van type boorstelling); geen ruimte →
    sleufloos alternatief toepassen dat binnen de richtlijnen past. Past
    niets, dan blijft het voorstel staan met een kritieke melding.
    """

    def toets(techniek, c):
        eis = WERKTERREIN_EIS[techniek]
        uitloop = BOOR_UITLOOP.get(techniek, BOOR_UITLOOP_DEFAULT)
        p_in = route.interpolate(max(0.0, c["chainage_van_m"] - uitloop))
        p_uit = route.interpolate(min(route.length, c["chainage_tot_m"] + uitloop))
        m2_in, prive_in = _werkruimte_m2(grid, p_in, eis["zoekstraal_m"])
        m2_uit, prive_uit = _werkruimte_m2(grid, p_uit, eis["zoekstraal_m"])
        o_in = _ruimte_oordeel(m2_in, eis["intrede_m2"])
        o_uit = _ruimte_oordeel(m2_uit, eis["uittrede_m2"])
        oordeel = max(o_in, o_uit, key=lambda o: _OORDEEL_ORDE[o])
        return {
            "oordeel": oordeel,
            "intrede_m2": round(m2_in), "intrede_eis_m2": eis["intrede_m2"],
            "uittrede_m2": round(m2_uit), "uittrede_eis_m2": eis["uittrede_m2"],
            "privaat": (prive_in + prive_uit) > 0.3 * (m2_in + m2_uit + 1e-9),
        }

    for c in crossings:
        if c["techniek"] not in WERKTERREIN_EIS:
            continue
        wt = toets(c["techniek"], c)
        opmerkingen = []
        if wt["oordeel"] == "onvoldoende":
            # sleufloze alternatieven aflopen; eerste techniek die past wint
            gekozen = None
            beste_onzeker = None
            for alt in _sleufloze_alternatieven(c["soort"], c["breedte_m"]):
                if alt == c["techniek"]:
                    continue
                boorlengte = (c.get("kruislengte_m", c["breedte_m"])
                              + 2 * BOOR_UITLOOP.get(alt, BOOR_UITLOOP_DEFAULT))
                if alt == TECHNIEK_RAKET and boorlengte > RAKET_MAX_BOORLENGTE_M:
                    continue
                if alt == TECHNIEK_OPEN:
                    gekozen = (alt, {"oordeel": "n.v.t.", "intrede_m2": None,
                                     "intrede_eis_m2": None, "uittrede_m2": None,
                                     "uittrede_eis_m2": None, "privaat": False})
                    break
                wt_alt = toets(alt, c)
                if wt_alt["oordeel"] == "voldoende":
                    gekozen = (alt, wt_alt)
                    break
                if wt_alt["oordeel"] == "onzeker" and beste_onzeker is None:
                    beste_onzeker = (alt, wt_alt)
            if gekozen is None:
                gekozen = beste_onzeker
            if gekozen is not None:
                c["techniek_oorspronkelijk"] = c["techniek"]
                c["techniek"], wt = gekozen
                detail, richtlijn = ALTERNATIEF_DETAIL[c["techniek"]]
                c["detail"], c["richtlijn"] = detail, richtlijn
                c["noodzaak"] = (f"werkterrein te krap voor "
                                 f"{c['techniek_oorspronkelijk'].lower()}; "
                                 "lichtste passende alternatief toegepast")
                opmerkingen.append(
                    f"Onvoldoende werkruimte voor {c['techniek_oorspronkelijk']}; "
                    f"sleufloos alternatief toegepast.")
            else:
                opmerkingen.append(
                    "Geen sleufloze techniek met voldoende werkruimte binnen de "
                    "richtlijnen; maatwerk (locatie in-/uittredepunt, langere "
                    "boring of ander tracé) vereist.")
        if wt["oordeel"] == "onzeker":
            opmerkingen.append("Ruimte onzeker; uitvoerbaarheid afhankelijk van "
                               "type boorstelling.")
        if wt.get("privaat"):
            opmerkingen.append("Werkterrein ligt grotendeels op privaat terrein; "
                               "toestemming eigenaar/ZRO nodig.")
        if c["techniek"] == TECHNIEK_HDD and wt["oordeel"] != "onvoldoende":
            opmerkingen.append("Uitlegstrook boorstreng niet getoetst.")
        wt["opmerking"] = " ".join(opmerkingen)
        c["werkterrein"] = wt


OPEN_UITLOOP = 2.0  # open kruising: alleen de directe aanloop haaks trekken


def straighten_crossings(route: LineString, crossings: list,
                         grid: Grid | None = None) -> LineString:
    """Kruisingssegmenten vervangen door een rechte lijn (boring/persing is recht).

    Recht over de volledige boorlengte — van intrede- tot uittredepunt, met de
    uitloop van de voorgestelde techniek — zodat de boring exact op het tracé
    ligt. Als de rechte vervanging door een uitgesloten cel loopt
    (bijvoorbeeld een gebouwhoek), blijft het oorspronkelijke pad staan.
    """
    if not crossings:
        return route

    def recht_kan(pa, pb) -> bool:
        if grid is None:
            return True
        lengte = math.hypot(pb.x - pa.x, pb.y - pa.y)
        n = max(2, int(lengte / (grid.cell / 2)))
        for i in range(n + 1):
            t = i / n
            x, y = pa.x + t * (pb.x - pa.x), pa.y + t * (pb.y - pa.y)
            r, c = grid.world_to_cell(x, y)
            if not np.isfinite(grid.cost[r, c]):
                return False
        return True

    spans = []
    for c in crossings:
        if c["techniek"] == TECHNIEK_OPEN:
            u = OPEN_UITLOOP
        else:
            # minimaal de nanodrill-uitloop: als de werkterrein-toets later
            # naar een alternatief wisselt, blijft de boorlijn op het tracé
            u = max(BOOR_UITLOOP.get(c["techniek"], BOOR_UITLOOP_DEFAULT),
                    BOOR_UITLOOP[TECHNIEK_NANO])
        spans.append((c["chainage_van_m"] - u, c["chainage_tot_m"] + u))
    spans.sort()
    merged = []
    for s in spans:
        if merged and s[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], s[1]))
        else:
            merged.append(list(s))
    # als de exacte koorde niet vrij is (bijv. een gebouwhoek), het in-/
    # uittredepunt stapsgewijs naar buiten verschuiven tot de rechte lijn
    # wél vrij ligt — de boring moet recht, dus het punt schuift, niet de lijn
    SCHUIF_STAPPEN = ((0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (2.0, 2.0),
                      (4.0, 2.0), (2.0, 4.0), (4.0, 4.0), (6.0, 4.0),
                      (4.0, 6.0), (6.0, 6.0), (8.0, 8.0))
    coords: list = []
    cursor = 0.0
    for m0, m1 in merged:
        a, b = max(0.0, m0), min(route.length, m1)
        if b <= cursor:
            continue
        a = max(a, cursor)
        recht = None
        for da, db in SCHUIF_STAPPEN:
            a2, b2 = max(cursor, a - da), min(route.length, b + db)
            pa, pb = route.interpolate(a2), route.interpolate(b2)
            if recht_kan(pa, pb):
                recht = (a2, b2, pa, pb)
                break
        if recht is not None:
            a2, b2, pa, pb = recht
            if a2 > cursor:
                coords.extend(list(_substring(route, cursor, a2).coords)[:-1])
            coords.extend([(pa.x, pa.y), (pb.x, pb.y)])
            cursor = b2
        else:
            if a > cursor:
                coords.extend(list(_substring(route, cursor, a).coords)[:-1])
            coords.extend(list(_substring(route, a, b).coords))
            cursor = b
    if cursor < route.length:
        seg = _substring(route, cursor, route.length)
        coords.extend(list(seg.coords)[1:])
    return LineString(coords)


def straighten_iteratief(route: LineString, bgt: dict, grid: Grid | None = None,
                         rondes: int = 2) -> LineString:
    """Kruisingen rechttrekken in meerdere rondes.

    Eén ronde volstaat niet: het rechttrekken kort het (kronkelige raster-)pad
    in, waardoor chainages verschuiven en de rechte koorde net naast het
    beoogde in-/uittredepunt kan beginnen. De tweede ronde meet op het al
    rechtgetrokken tracé en legt de boorlengte exact recht.
    """
    for _ in range(rondes):
        pre = detect_crossings(route, bgt)
        if not pre:
            return route
        route = straighten_crossings(route, pre, grid)
    return route


def build_segments(route: LineString, grid: Grid, sample_m: float = 2.0, min_len: float = 6.0) -> list:
    """Tracé opdelen in wegvakken met elk één ligging (FO §3.3)."""
    n = max(2, int(route.length / sample_m) + 1)
    samples = []
    for i in range(n):
        m = min(route.length, i * sample_m)
        p = route.interpolate(m)
        r, c = grid.world_to_cell(p.x, p.y)
        samples.append((m, int(grid.klass[r, c])))
    runs = []
    for m, k in samples:
        if runs and runs[-1][2] == k:
            runs[-1][1] = m
        else:
            runs.append([m, m, k])
    # korte runs samenvoegen met de buurman ervoor
    merged = []
    for run in runs:
        if merged and (run[1] - run[0]) < min_len:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    segments = []
    for i, (m0, m1, k) in enumerate(merged, 1):
        if m1 - m0 < 0.5:
            continue
        geom = _substring(route, m0, m1)
        segments.append({
            "nr": f"SEG-{i:03d}",
            "ligging": CLASS_NAMES.get(k, "onbekend"),
            "klasse": k,
            "van_m": round(m0, 1),
            "tot_m": round(m1, 1),
            "lengte_m": round(m1 - m0, 1),
            "geometry": mapping(geom),
        })
    return segments


def propose_moffen(route: LineString, crossings: list, segments: list,
                   haspel_m: float = 500.0) -> list:
    """Mofposities op haspellengte: niet in een boring, niet onder de rijbaan."""
    verboden = []
    for c in crossings:
        verboden.append((c["chainage_van_m"] - 10, c["chainage_tot_m"] + 10))
    for s in segments:
        if s["klasse"] in (CL_RIJBAAN, CL_WATER, CL_SPOOR, CL_PAND):
            verboden.append((s["van_m"], s["tot_m"]))

    def toegestaan(m):
        return not any(a <= m <= b for a, b in verboden)

    moffen = []
    m = haspel_m
    while m < route.length - 25:
        best = None
        for delta in range(0, 120):
            for kand in (m - delta, m + delta):
                if 10 < kand < route.length - 10 and toegestaan(kand):
                    best = kand
                    break
            if best is not None:
                break
        if best is None:
            best = m
        p = route.interpolate(best)
        moffen.append({
            "nr": f"MOF-{len(moffen) + 1:02d}",
            "chainage_m": round(best, 1),
            "punt": (round(p.x, 2), round(p.y, 2)),
            "opmerking": "" if toegestaan(best) else "Geen toegestane positie gevonden; handmatig verplaatsen.",
        })
        m = best + haspel_m
    return moffen
