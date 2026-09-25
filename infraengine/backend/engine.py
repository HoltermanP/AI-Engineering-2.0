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
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep
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
    "stiltegebied": 1.2,       # provinciaal stiltegebied: werkwijze-eisen, lichte weging
    "monument": 2.0,           # rijksmonument-contour (RCE): vergunningplicht, ontwijken
    "nge_verdacht": 1.5,       # NGE-verdacht gebied (gemeentelijke bodembelastingkaart)
    "kering": 3.0,             # waterkering + beschermingszone: waterschapsvergunning
    "buisleiding": 2.0,        # buisleiding gevaarlijke stoffen (Bevb): belemmeringenstrook
    "klic_netdichtheid": 1.5,  # bestaande kabels en leidingen (KLIC-import)
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
ZN_STILTE = 256      # provinciaal stiltegebied
ZN_MONUMENT = 512    # rijksmonument-contour (RCE)
ZN_NGE = 1024        # NGE-verdacht gebied (regionale bodembelastingkaart)
ZN_KERING = 2048     # waterkering + beschermingszone (IMWA)
ZN_BUISLEIDING = 4096  # buisleiding gevaarlijke stoffen (Bevb)
ZN_KLIC = 8192       # bestaande netten uit een KLIC-levering (import)

# minimale wortelzone-straal rond een boompunt; waar een gemeentelijk register
# een kroondiameter levert geldt de kroonprojectie (instelbaar)
BOOM_WORTELZONE_M = 2.5

# Nauwkeurigheid tracé (sub-cel): het raster is op celniveau grof, dus naast
# de rastertoets gelden vector-exacte controles tegen de BGT-geometrie.
# HARD_MARGE_M: veiligheidsmarge rond panden/bouwwerken in het kostenraster,
# zodat een pad tussen celmiddens nooit een gevelhoek kan snijden (minimaal
# de halve celdiagonaal). SLACK_ABS: absolute bovengrens op de extra kosten
# die het gladstrijken mag accepteren — de relatieve slack (5%) is bij lange
# koorden anders groot genoeg om een knip over een rijbaan te "kopen".
HARD_MARGE_M = 0.5
SLACK_ABS = 2.0
# schampen: een passage over een obstakel die nergens dieper komt dan deze
# diepte is geen kruising maar een randeffect van het raster; die wordt
# vector-exact van het obstakel afgedrukt met deze vrije marge
SCHAMP_DIEPTE_M = 0.8
SCHAMP_CLEARANCE_M = 0.2

ZONE_NAMES = {
    ZN_NATURA: "Natura 2000",
    ZN_NNN: "Natuurnetwerk Nederland",
    ZN_GWB: "grondwaterbeschermingsgebied",
    ZN_BODEM: "vastgestelde verontreiniging / nazorg (BRO SLD)",
    ZN_ARCHEO: "archeologisch monument (AMK)",
    ZN_BODEM_ELDERS: "bodemdata via eigen loket bevoegd gezag",
    ZN_BODEM_ONDERZOEK: "bodemonderzoekslocatie (BRO SAD / Wbb)",
    ZN_BOOM: "wortelzone bomen",
    ZN_STILTE: "stiltegebied (provincie)",
    ZN_MONUMENT: "rijksmonument (RCE)",
    ZN_NGE: "NGE-verdacht gebied",
    ZN_KERING: "waterkering + beschermingszone (IMWA)",
    ZN_BUISLEIDING: "buisleiding gevaarlijke stoffen (Bevb)",
    ZN_KLIC: "bestaande netten (KLIC-import)",
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
        # uint16: er zijn inmiddels meer dan 8 zonebits
        self.zones = np.zeros((self.nrows, self.ncols), dtype=np.uint16)
        # vector-exacte geometrie naast het raster (sub-cel-nauwkeurigheid)
        self.hard = None          # unie van panden/bouwwerken/verboden zones
        self._hard_prep = None
        self.schamp = []          # [(prepared, geometrie, prijs per m)]

    def hard_conflict(self, lijn: LineString) -> bool:
        """True als de lijn de exacte geometrie van een harde uitsluiting snijdt.

        Vector-toets naast de rastertoets: een cel waarvan het midden vrij is
        kan deels over een pand vallen; de rastersampling ziet dat niet."""
        if self.hard is None or not self._hard_prep.intersects(lijn):
            return False
        return self.hard.intersection(lijn).length > 1e-9

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
        self.hard_geom = None     # exacte unie van harde uitsluitingen (vector)
        self.schamp_geoms: list = []  # [(geometrie, gewicht-sleutel)] voor sub-celcorrectie

    def add(self, mask: np.ndarray, code: int, key: str | None, factor: float = 1.0):
        if mask.any():
            self.ops.append((mask, code, key, factor))

    def add_zone(self, mask, bit: int, key: str):
        if mask is not None and mask.any():
            self.zone_ops.append((mask, bit, key))

    def build(self, weights: dict) -> Grid:
        w = {**DEFAULT_WEIGHTS, **(weights or {})}

        def prijs(key: str) -> float:
            if key.endswith("*gesloten"):
                return w[key.split("*")[0]] * w["gesloten_verharding"]
            return w[key]

        grid = Grid(self.bbox, self.cell)
        grid.cost[:] = w["onbekend"]
        grid.klass[:] = CL_ONBEKEND
        for mask, code, key, factor in self.ops:
            if key is None:
                grid.paint(mask, code, factor)  # vaste waarde (∞ voor uitsluitingen)
            else:
                grid.paint(mask, code, prijs(key) * factor)
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
        grid.hard = self.hard_geom
        if self.hard_geom is not None:
            grid._hard_prep = prep(self.hard_geom)
        grid.schamp = [(prep(g), g, float(prijs(key))) for g, key in self.schamp_geoms]
        return grid


def build_painter(bbox: tuple, bgt: dict, forbidden: list, cell: float,
                  zones: dict | None = None) -> Painter:
    """Maskers opbouwen in schilderorde: terrein → verharding → berm/groen →
    obstakels → zones.

    Schilderorde bij de randcellen: de rasterisatie kent een cel aan een vlak
    toe zodra het vlak het celmidden dekt, maar op de grens is de vulling
    "te dik" (tot een halve cel). Wie het laatst schildert wint dus de
    randcellen. Daarom komt de goedkoopste ligging het laatst: de berm en
    groenstrook ná de wegdelen, en binnen de wegdelen het voetpad ná de
    rijbaan. Een smalle berm (1–1,5 m) naast een trottoir blijft zo in het
    raster bestaan in plaats van door de verharding te worden weggeschilderd;
    de exacte ligging tegen de verhardingsrand regelt daarna de vector-exacte
    schamp-correctie (`verwijder_schampen`), die voor alle verharding geldt.
    """
    painter = Painter(bbox, cell)
    grid = Grid(bbox, cell)  # alleen voor rasterize-hulpfuncties

    def geoms(coll, pred=None):
        return [g for g, p in bgt.get(coll, []) if pred is None or pred(p)]

    # 1. terrein — begroeid gesplitst: agrarisch (privaat-proxy), natuurlijk
    #    groen (kwetsbaar; binnen Natura 2000 uitgesloten); urbaan groen
    #    (groenvoorziening) is voorkeursligging en volgt ná de wegdelen (3)
    begroeid = bgt.get("begroeidterreindeel", [])
    groen_urbaan = [g for g, p in begroeid
                    if (p.get("fysiek_voorkomen") or "") in BGT_URBAAN_GROEN]
    painter.add(grid.rasterize([g for g, p in begroeid
                                if (p.get("fysiek_voorkomen") or "") in BGT_AGRARISCH]),
                CL_ERF, "erf_prive")
    painter.add(grid.rasterize([g for g, p in begroeid
                                if (p.get("fysiek_voorkomen") or "") not in
                                (BGT_URBAAN_GROEN | BGT_AGRARISCH)]),
                CL_NATUURGROEN, "natuur_groen")

    # onbegroeid terrein: onverhard, verhard buiten de weg (plein, pad op
    # terrein: open of gesloten verharding) en erf
    onbegroeid = bgt.get("onbegroeidterreindeel", [])
    terrein_open_verhard = [g for g, p in onbegroeid
                            if p.get("fysiek_voorkomen") == "open verharding"]
    terrein_dicht_verhard = [g for g, p in onbegroeid
                             if p.get("fysiek_voorkomen") == "gesloten verharding"]
    painter.add(grid.rasterize([g for g, p in onbegroeid
                                if p.get("fysiek_voorkomen") not in
                                ("erf", "open verharding", "gesloten verharding")]),
                CL_ONVERHARD, "overig_onverhard")
    painter.add(grid.rasterize([g for g, p in onbegroeid if p.get("fysiek_voorkomen") == "erf"]),
                CL_ERF, "erf_prive")
    painter.add(grid.rasterize(terrein_dicht_verhard), CL_ONVERHARD, "overig_onverhard*gesloten")
    painter.add(grid.rasterize(terrein_open_verhard), CL_ONVERHARD, "overig_onverhard")

    # 2. wegdelen per functie, duurste eerst (rijbaan) en goedkoopste laatst
    #    (voetpad), zodat op de grens de lichtste ligging de randcel houdt;
    #    gesloten verharding × factor
    wegdelen = bgt.get("wegdeel", [])
    per_functie = {CL_RIJBAAN: "rijbaan", CL_PARKEER: "parkeervlak",
                   CL_FIETSPAD: "fietspad", CL_VOETPAD: "voetpad"}
    verharding_geoms: dict = {}  # gewicht-sleutel → vlakken (voor de schamp-correctie)
    for code, key in per_functie.items():
        open_g, dicht_g = [], []
        for g, p in wegdelen:
            functie = (p.get("functie") or "").lower()
            cl = WEGDEEL_FUNCTIE.get(functie,
                                     CL_RIJBAAN if "rijbaan" in functie or "baan" in functie else None)
            if cl != code:
                continue
            (dicht_g if p.get("fysiek_voorkomen") == "gesloten verharding" else open_g).append(g)
        painter.add(grid.rasterize(dicht_g), code, f"{key}*gesloten")
        painter.add(grid.rasterize(open_g), code, key)
        # rijbaan: één vlak, beprijsd als open rijbaan (de schamp-toeslag is
        # een randcorrectie, geen asfaltprijs); overige klassen per gewicht
        if code == CL_RIJBAAN:
            verharding_geoms[key] = open_g + dicht_g
        else:
            verharding_geoms[key] = open_g
            verharding_geoms[f"{key}*gesloten"] = dicht_g
    verharding_geoms["overig_onverhard"] = terrein_open_verhard
    verharding_geoms["overig_onverhard*gesloten"] = terrein_dicht_verhard

    # 3. berm en groenstrook (voorkeursligging) ná de wegdelen: houdt de
    #    randcellen tegen de verharding; ondersteunend wegdeel zonder
    #    bermfunctie (verkeerseiland e.d.) iets zwaarder
    painter.add(grid.rasterize(geoms("ondersteunendwegdeel", lambda p: p.get("functie") != "berm")),
                CL_BERM, "berm_groen", 1.3)
    painter.add(grid.rasterize(geoms("ondersteunendwegdeel", lambda p: p.get("functie") == "berm")
                               + groen_urbaan),
                CL_BERM, "berm_groen")

    # 4. lijnvormige obstakels: water en spoor als "kruisingsprijs per meter" (§3.2)
    water = geoms("waterdeel") + geoms("ondersteunendwaterdeel")
    painter.add(grid.rasterize(water), CL_WATER, "water_kruising")
    spoor = geoms("spoor")
    spoor_vlak = [g.buffer(2.5) for g in spoor]
    if spoor:
        painter.add(grid.rasterize(spoor, buffer=2.5), CL_SPOOR, "spoor_kruising")

    # 5. harde uitsluitingen — in het raster met veiligheidsmarge (minimaal de
    #    halve celdiagonaal), zodat een pad tussen vrije celmiddens nooit een
    #    gevelhoek kan snijden; de vector-checks gebruiken de exacte geometrie
    hard = (geoms("pand") + geoms("overigbouwwerk") + geoms("kunstwerkdeel_vlak")
            + geoms("overbruggingsdeel") + geoms("tunneldeel"))
    marge = max(HARD_MARGE_M, 0.75 * cell)
    painter.add(grid.rasterize(hard, buffer=marge), CL_PAND, None, np.inf)

    # 6. door de ontwerper getekende verboden zones (exact, zonder marge:
    #    de getekende grens is de bedoelde grens)
    forb_polys = [Polygon(c) for c in (forbidden or []) if len(c) >= 3]
    if forb_polys:
        painter.add(grid.rasterize(forb_polys), CL_VERBODEN, None, np.inf)

    # exacte geometrie voor de vector-checks in de nabewerking: harde
    # uitsluitingen, en alle verharding plus water en spoor voor de
    # sub-celcorrectie (schampen); de prijs per meter is die van de ligging
    if hard or forb_polys:
        painter.hard_geom = unary_union(hard + forb_polys)
    waterdeel_zelf = geoms("waterdeel")
    verharding_geoms["water_kruising"] = waterdeel_zelf
    verharding_geoms["spoor_kruising"] = spoor_vlak
    for key, geoms_lijst in verharding_geoms.items():
        if geoms_lijst:
            painter.schamp_geoms.append((unary_union(geoms_lijst), key))

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
        painter.add_zone(zones.get("stilte_mask"), ZN_STILTE, "stiltegebied")
        if zones.get("monument"):
            painter.add_zone(grid.rasterize(zones["monument"]), ZN_MONUMENT, "monument")
        painter.add_zone(zones.get("nge_mask"), ZN_NGE, "nge_verdacht")
        if zones.get("keringen"):
            # keringlijnen met beschermingszone (buffer in main.compute)
            painter.add_zone(grid.rasterize(zones["keringen"]), ZN_KERING, "kering")
        painter.add_zone(zones.get("buisleiding_mask"), ZN_BUISLEIDING, "buisleiding")
        if zones.get("klic"):
            # bestaande netten uit een KLIC-import, gebufferd in main.compute
            painter.add_zone(grid.rasterize(zones["klic"]), ZN_KLIC, "klic_netdichtheid")

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


def zone_trajecten(route: LineString, grid: Grid, sample_m: float = 2.0) -> dict:
    """Tracédelen per zonelaag: per ZN_-bit de aaneengesloten stukken van het
    tracé die in die zone liggen, als lijsten van LineStrings (voor het
    aanwijzen op de kaart van waar een onderzoek of toets betrekking op heeft).
    Bemonstering en steek zijn gelijk aan `zone_lengtes`."""
    n = max(2, int(route.length / sample_m) + 1)
    stap = route.length / (n - 1) if n > 1 else 0
    open_vanaf = {bit: None for bit in ZONE_NAMES}
    delen = {bit: [] for bit in ZONE_NAMES}

    def sluit(bit, tot_m):
        m0 = open_vanaf[bit]
        if m0 is None:
            return
        open_vanaf[bit] = None
        # een enkel bemonsterd punt krijgt een halve steek aan weerszijden,
        # zodat het deel op de kaart niet tot een punt ineenvalt
        m0, m1 = max(0.0, m0 - stap / 2), min(route.length, tot_m + stap / 2)
        if m1 - m0 <= 0:
            return
        delen[bit].append(_substring(route, m0, m1))

    for i in range(n):
        m = min(route.length, i * stap)
        p = route.interpolate(m)
        r, c = grid.world_to_cell(p.x, p.y)
        z = int(grid.zones[r, c])
        for bit in delen:
            if z & bit:
                if open_vanaf[bit] is None:
                    open_vanaf[bit] = m
            else:
                sluit(bit, m - stap)
    for bit in delen:
        sluit(bit, route.length)
    return delen


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


def _schamp_extra(grid: Grid, p0: tuple, p1: tuple) -> float:
    """Sub-celcorrectie: vector-exacte meters van het lijnstuk over verharding
    (rijbaan, fietspad, voetpad, parkeervlak, verhard terrein), water of
    spoor, beprijsd per meter met het gewicht van die ligging.

    Het raster kent een cel volledig aan één klasse toe; een lijnstuk tussen
    "berm-cellen" kan daardoor toch nét over de rand van het trottoir of de
    rijbaan lopen zonder dat de rasterkosten dat zien. Deze toeslag maakt dat
    schampen zichtbaar duur, zodat het gladstrijken het tracé er vanzelf
    naast legt (in de berm). Echte (haakse) kruisingen betalen de toeslag aan
    beide zijden van elke vergelijking en blijven dus gewoon mogelijk."""
    if not grid.schamp:
        return 0.0
    lijn = LineString([p0, p1])
    extra = 0.0
    for prep_g, geom, prijs in grid.schamp:
        if prep_g.intersects(lijn):
            extra += geom.intersection(lijn).length * prijs
    return extra


def _straight_cost(grid: Grid, p0: tuple, p1: tuple) -> float:
    """Gewogen kosten van een recht lijnstuk over het raster (∞ bij uitsluiting).

    Naast de rastersampling geldt een vector-exacte toets: snijdt het lijnstuk
    de échte geometrie van een pand of verboden zone, dan ∞ — ook als alle
    bemonsterde celmiddens vrij zijn. Schampen over rijbaan/water/spoor krijgt
    een sub-celtoeslag (zie `_schamp_extra`)."""
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
    if grid.hard_conflict(LineString([p0, p1])):
        return math.inf
    return float(vals.mean() * lengte) + _schamp_extra(grid, p0, p1)


def smooth_route(coords: list, grid: Grid, slack: float = 1.05) -> list:
    """Rasterpad gladstrijken: deelpaden vervangen door rechte lijnstukken.

    Op een vlak kostenveld zijn veel trappenpaden precies even duur en kiest de
    Dijkstra-traceback er willekeurig één — dat oogt als bochten zonder reden.
    Een recht alternatief wordt geaccepteerd zolang de gewogen kosten hoogstens
    `slack` × de kosten van het oorspronkelijke deelpad zijn; een bocht blijft
    dus alleen staan waar hij echt iets oplevert (omweg om dure cellen of een
    uitsluiting heen).

    Twee passes: eerst gretig vooruit springen langs het rasterpad, daarna de
    overgebleven hoekpunten heroverwegen. De gretige pass laat hoekpunten
    achter op willekeurige punten van het trappenpad (zijwaarts van de
    logische lijn), wat als kleine bochtjes zonder reden oogt. De napass
    verschuift elk tussenpunt eerst lokaal naar de goedkoopste positie (op een
    vlak veld is dat de rechte lijn, dus zigzag strijkt glad) en laat het punt
    daarna vervallen als de directe koorde binnen de slack even goedkoop is
    als de twee koorden eromheen — met dezelfde kostmaat aan beide kanten.
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
        cum.append(cum[-1] + (float(grid.cost[ra]) + float(grid.cost[rb])) / 2 * d
                   + _schamp_extra(grid, a, b))
    last = len(pts) - 1

    # slack: relatief én absoluut begrensd — bij een lange koorde is 5% extra
    # anders genoeg om een knip over een rijbaan of pandhoek te "kopen"
    def ok(i: int, j: int) -> bool:
        basis = cum[j] - cum[i]
        sc = _straight_cost(grid, pts[i], pts[j])
        return (math.isfinite(sc)
                and sc - basis <= min((slack - 1.0) * basis, SLACK_ABS) + 1e-6)

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
    # napass: hoekpunten van het trappenpad liggen zijwaarts van de logische
    # lijn; eerst elk tussenpunt lokaal naar de goedkoopste positie schuiven,
    # daarna vervalt het punt als de directe koorde binnen de slack even
    # goedkoop is als de twee koorden eromheen (koorde-vs-koorde, zodat het
    # trappenpad geen rol meer speelt in de vergelijking)
    stap = grid.cell
    buren = [(dx * stap, dy * stap)
             for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2) if dx or dy]
    for _ in range(4):
        veranderd = False
        for k in range(1, len(out) - 1):
            a, b, c = out[k - 1], out[k], out[k + 1]
            beste = b
            beste_kost = _straight_cost(grid, a, b) + _straight_cost(grid, b, c)
            for dx, dy in buren:
                kand = (b[0] + dx, b[1] + dy)
                kost = _straight_cost(grid, a, kand) + _straight_cost(grid, kand, c)
                if kost < beste_kost - 1e-6:
                    beste, beste_kost = kand, kost
            if beste != b:
                out[k] = beste
                veranderd = True
        gladder = [out[0]]
        for k in range(1, len(out) - 1):
            a, b, c = gladder[-1], out[k], out[k + 1]
            sc = _straight_cost(grid, a, c)
            via = _straight_cost(grid, a, b) + _straight_cost(grid, b, c)
            if math.isfinite(sc) and sc - via <= min((slack - 1.0) * via, SLACK_ABS) + 1e-6:
                veranderd = True
            else:
                gladder.append(b)
        gladder.append(out[-1])
        out = gladder
        if not veranderd:
            break
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
    del mcp, cumcost, tb  # MCP-rasters vrij vóór het gladstrijken
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
        # de MCP-structuren (kosten-, cumulatieve-kosten- en heap-rasters, samen
        # ~50 bytes/cel) niet vasthouden tot de volgende verbinding klaar is:
        # anders leven er bij via-punten twee tegelijk
        del mcp, cumcost, tb
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
    if grid.hard_conflict(line):
        return False
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
# in-/uittredepunt op vrij terrein: ligt het punt op de uitloopafstand nog
# in een wegdeel, water, talud of spoor (of tegen een pand), dan schuift het
# in stappen langs het tracé naar buiten, tot dit maximum
BOOR_VRIJ_STAP_M = 0.5
BOOR_VRIJ_MAX_M = 25.0


def boorvrij_toets(bgt: dict | None = None, grid: "Grid | None" = None):
    """Toets of een in-/uittredepunt op vrij terrein ligt.

    Vrij = niet in een wegdeel (rijbaan, fietspad, voetpad, parkeervlak, …),
    niet in water of de oeverzone (talud/onderhoudspad), niet binnen de
    spoorzone en niet tegen een pand of andere harde uitsluiting. Berm en
    (on)begroeid terrein zijn vrij. Retourneert een functie Point → bool.
    """
    geoms = []
    if bgt:
        for laag in ("waterdeel", "ondersteunendwaterdeel", "wegdeel"):
            geoms.extend(g for g, _ in bgt.get(laag, []))
        geoms.extend(g.buffer(2.5) for g, _ in bgt.get("spoor", []))
    obst = prep(unary_union(geoms)) if geoms else None
    hard = getattr(grid, "hard", None) if grid is not None else None

    def vrij(p: Point) -> bool:
        if obst is not None and obst.intersects(p):
            return False
        if hard is not None and hard.distance(p) < 0.1:
            return False
        return True
    return vrij


def boorspan(route: LineString, m0: float, m1: float, uitloop: float,
             vrij=None) -> dict:
    """In-/uittredepunt (chainage) van een boring onder obstakel [m0, m1].

    Basis: de uitloop van de techniek vóór en na de obstakelrand, gemeten
    langs het tracé. Ligt dat punt niet op vrij terrein (``vrij``), dan
    schuift het in stappen van BOOR_VRIJ_STAP_M verder naar buiten, tot
    BOOR_VRIJ_MAX_M; lukt dat niet, dan blijft het basispunt staan en wordt
    ``vrij_in``/``vrij_uit`` False.
    """
    lengte = route.length

    def zoek(m: float, richting: int) -> tuple:
        basis = min(max(m, 0.0), lengte)
        if vrij is None:
            return basis, 0.0, True
        d = 0.0
        while d <= BOOR_VRIJ_MAX_M + 1e-9:
            mm = basis + richting * d
            if mm < 0.0 or mm > lengte:
                break
            if vrij(route.interpolate(mm)):
                return mm, d, True
            d += BOOR_VRIJ_STAP_M
        return basis, 0.0, False

    m_in, v_in, ok_in = zoek(m0 - uitloop, -1)
    m_uit, v_uit, ok_uit = zoek(m1 + uitloop, +1)
    return {"m_in": m_in, "m_uit": m_uit,
            "verschoven_in_m": round(v_in, 1), "verschoven_uit_m": round(v_uit, 1),
            "vrij_in": ok_in, "vrij_uit": ok_uit}


def bepaal_boorpunten(route: LineString, crossings: list, bgt: dict | None = None,
                      grid: "Grid | None" = None) -> set:
    """In-/uittredepunt per sleufloze kruising vastleggen (in place).

    Per kruising en per techniek (de definitieve techniek kan bij het
    groeperen tot boringen nog opschalen): uitloop vanaf de obstakelrand,
    verschoven naar vrij terrein (``boorspan``). De punten worden als
    RD-coördinaat bewaard — ``boor_punten[techniek]`` en, voor de eigen
    techniek, ``boor_in_rd``/``boor_uit_rd`` — zodat ze na het samenvoegen en
    normaliseren van deeltrajecten exact op het tracé terug te vinden zijn;
    een chainage verschuift dan, een coördinaat niet.

    Retourneert de tracé-hoekpunten die de boorlijnen dragen: die mogen bij
    het normaliseren niet meer verschuiven (``maatvoering.normaliseer_route``,
    parameter ``vast``).
    """
    vrij = boorvrij_toets(bgt, grid)
    hoekpunten = [(route.project(Point(xy)), xy) for xy in route.coords]
    vast: set = set()
    for c in crossings:
        if c.get("techniek") not in BOOR_UITLOOP:
            for k in ("boor_punten", "boor_in_rd", "boor_uit_rd"):
                c.pop(k, None)
            continue
        punten = {}
        m_min, m_max = route.length, 0.0
        for techniek, uitloop in BOOR_UITLOOP.items():
            s = boorspan(route, c["chainage_van_m"], c["chainage_tot_m"], uitloop, vrij)
            p_in, p_uit = route.interpolate(s["m_in"]), route.interpolate(s["m_uit"])
            punten[techniek] = {
                "in_rd": (round(p_in.x, 2), round(p_in.y, 2)),
                "uit_rd": (round(p_uit.x, 2), round(p_uit.y, 2)),
                "verschoven_in_m": s["verschoven_in_m"],
                "verschoven_uit_m": s["verschoven_uit_m"],
                "vrij_in": s["vrij_in"], "vrij_uit": s["vrij_uit"],
            }
            m_min, m_max = min(m_min, s["m_in"]), max(m_max, s["m_uit"])
        c["boor_punten"] = punten
        c["boor_in_rd"] = punten[c["techniek"]]["in_rd"]
        c["boor_uit_rd"] = punten[c["techniek"]]["uit_rd"]
        # dragende hoekpunten: alles binnen de ruimste span plus het eerste
        # hoekpunt ervoor en erna (die begrenzen de lijnstukken waarop de
        # in-/uittredepunten liggen)
        binnen = [i for i, (m, _) in enumerate(hoekpunten) if m_min - 0.01 <= m <= m_max + 0.01]
        i_voor = max((i for i, (m, _) in enumerate(hoekpunten) if m < m_min - 0.01), default=None)
        i_na = min((i for i, (m, _) in enumerate(hoekpunten) if m > m_max + 0.01), default=None)
        for i in binnen + [i for i in (i_voor, i_na) if i is not None]:
            vast.add(tuple(hoekpunten[i][1]))
    return vast


# Beslisdrempels (m, gemeten langs het tracé): tot waar een open kruising de
# standaard is en vanaf waar een zwaardere techniek nodig is. Sleufloos wordt
# alleen voorgesteld waar de beheerder open ontgraving feitelijk niet toestaat.
WATER_OPEN_MAX_M = 3.0      # watergang B/C: open ontgraving met afdamming
WATER_PERSING_MAX_M = 8.0   # tot hier volstaat persing/nanodrill; daarboven HDD
RIJBAAN_OPEN_MAX_M = 7.0    # erftoegangsweg in elementenverharding: open sleuf (AVOI)
RIJBAAN_PERSING_MAX_M = 12.0

# Rijbaan: verharding en wegfunctie (BGT) en wegbeheerder (NWB) wegen
# zwaarder dan de breedte. Onder gesloten verharding (asfalt/beton) is een
# open sleuf in de praktijk de uitzondering: zagen, frezen, volledig herstel
# van de deklaag, degeneratievergoeding en een langdurige afsluiting — de
# wegbeheerder staat dat bij asfalt in de regel niet toe. Persing vanuit
# kuipen buiten de verharding is dan de standaard. Alleen onder open
# verharding (klinkers/tegels: herstraten is gangbaar en goedkoop) blijft de
# open sleuf tot RIJBAAN_OPEN_MAX_M de standaard.
VERHARDING_GESLOTEN = "gesloten verharding"
VERHARDING_RANG = {"gesloten verharding": 3, "open verharding": 2,
                   "half verhard": 1, "onverhard": 0}
# wegfuncties waar open ontgraving door de beheerder niet wordt toegestaan
# (stroomwegen, regionale wegen, busbanen): altijd sleufloos
WEGFUNCTIE_SLEUFLOOS = {"rijbaan autosnelweg", "rijbaan autoweg",
                        "rijbaan regionale weg", "ov-baan", "spoorbaan",
                        "baan voor vliegverkeer"}
WEGFUNCTIE_RANG = {"rijbaan autosnelweg": 6, "rijbaan autoweg": 5,
                   "baan voor vliegverkeer": 5, "spoorbaan": 4,
                   "rijbaan regionale weg": 3, "ov-baan": 2,
                   "rijbaan lokale weg": 1}
# NWB-beheerdersoort waarvoor open ontgraving niet aan de orde is
WEGBEHEERDER_SLEUFLOOS = {"R": "Rijkswaterstaat", "P": "de provincie"}


def _zwaarste(waarden, rang: dict):
    """Zwaarste waarde volgens de ranglijst; onbekende waarden tellen als 0."""
    waarden = [w for w in waarden if w]
    if not waarden:
        return None
    return max(waarden, key=lambda w: rang.get(w, 0))


def _sleufloos_verplicht(kenmerken: dict | None) -> str | None:
    """Reden waarom een rijbaankruising sleufloos móet, of None."""
    if not kenmerken:
        return None
    functie = (kenmerken.get("wegfunctie") or "").lower()
    if functie in WEGFUNCTIE_SLEUFLOOS:
        return (f"{functie}: wegbeheerder staat open ontgraving in de rijbaan "
                "niet toe")
    srt = (kenmerken.get("wegbeheerder_srt") or "").upper()
    if srt in WEGBEHEERDER_SLEUFLOOS:
        return (f"weg in beheer bij {WEGBEHEERDER_SLEUFLOOS[srt]}: open "
                "ontgraving in de rijbaan niet toegestaan")
    return None

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


def _techniek_voor_kruising(soort: str, breedte: float,
                            kenmerken: dict | None = None) -> dict:
    """Beslistabel FO §4 (vereenvoudigd naar de in het MVP geladen lagen).

    Kritisch toegepast: open kruising is de standaard waar de beheerder die
    toestaat; sleufloos waar dat verplicht of feitelijk onvermijdelijk is
    (spoor, brede watergang, asfaltrijbaan, stroom-/regionale weg). Elke
    keuze draagt een `noodzaak`-motivering.

    `kenmerken` (rijbaan): dict — mag de kruising zelf zijn — met
    `verharding` en `wegfunctie` (BGT-wegdeel) en `wegbeheerder_srt` (NWB).
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
        verharding = (kenmerken or {}).get("verharding")
        verplicht = _sleufloos_verplicht(kenmerken)
        gemeente = {"richtlijn": "AVOI gemeente; CROW 308",
                    "bevoegd_gezag": "Wegbeheerder (gemeente, aanname)"}
        if verplicht:
            basis = {"richtlijn": "Eisen wegbeheerder; RWS-richtlijn boortechnieken; "
                                  "NEN 3651; CROW 308",
                     "bevoegd_gezag": "Wegbeheerder"}
            if breedte <= RIJBAAN_PERSING_MAX_M:
                return {
                    "techniek": TECHNIEK_PERSING,
                    "detail": "Persing (mantelbuis) haaks onder de rijbaan; pers- en "
                              "ontvangkuip buiten de verharding en de obstakelvrije zone",
                    **basis,
                    "noodzaak": f"sleufloos verplicht — {verplicht}; korte kruising, "
                                "dus persing volstaat",
                }
            return {
                "techniek": TECHNIEK_HDD,
                "detail": "HDD onder de rijbaan; in-/uittredepunt buiten de verharding "
                          "en de obstakelvrije zone",
                **basis,
                "noodzaak": f"sleufloos verplicht — {verplicht}; rijbaan > "
                            f"{RIJBAAN_PERSING_MAX_M:g} m, te lang voor persing vanuit kuipen",
            }
        if verharding is None or verharding == VERHARDING_GESLOTEN:
            # asfalt/beton (of verharding onbekend: rijbanen zijn overwegend
            # asfalt, dus veilige aanname): open sleuf is hier de uitzondering
            reden = ("gesloten verharding (asfalt/beton)" if verharding
                     else "verharding onbekend in BGT, aanname asfalt")
            if breedte <= RIJBAAN_PERSING_MAX_M:
                return {
                    "techniek": TECHNIEK_PERSING,
                    "detail": "Persing (mantelbuis) onder de asfaltrijbaan; pers- en "
                              "ontvangkuip in berm/trottoir buiten de verharding; deklaag "
                              "blijft intact, geen zaagsneden of degeneratievergoeding",
                    **gemeente,
                    "noodzaak": f"sleufloos gewenst — {reden}: open sleuf vraagt zagen, "
                                "frezen en volledig herstel van de deklaag met "
                                "degeneratievergoeding en afsluiting; wegbeheerder staat "
                                "dit onder asfalt in de regel niet toe; korte kruising, dus "
                                "persing volstaat (open sleuf alleen met instemming beheerder)",
                }
            return {
                "techniek": TECHNIEK_HDD,
                "detail": "HDD of lange persing; pers- en ontvangkuip buiten de verharding",
                "richtlijn": "AVOI / RWS-richtlijn boortechnieken",
                "bevoegd_gezag": "Wegbeheerder",
                "noodzaak": f"sleufloos vereist — {reden} én brede rijbaan "
                            f"(> {RIJBAAN_PERSING_MAX_M:g} m): open ontgraving niet aan de "
                            "orde en boorlengte te groot voor persing vanuit kuipen",
            }
        # open verharding / half verhard / onverhard
        if breedte <= RIJBAAN_OPEN_MAX_M:
            return {
                "techniek": TECHNIEK_OPEN,
                "detail": "Open sleuf in halve rijbaan met fasering en verkeersmaatregelen; "
                          "elementen opnemen, herstraten en degeneratievergoeding",
                "richtlijn": "AVOI gemeente; CROW 96b",
                "bevoegd_gezag": "Wegbeheerder (gemeente, aanname)",
                "noodzaak": f"boring niet nodig — {verharding} (klinkers/tegels): "
                            f"herstraten is gangbaar; smalle rijbaan (≤ {RIJBAAN_OPEN_MAX_M:g} "
                            "m, erftoegangsweg), open sleuf onder AVOI toegestaan",
            }
        if breedte <= RIJBAAN_PERSING_MAX_M:
            return {
                "techniek": TECHNIEK_PERSING,
                "detail": "Persing (mantelbuis); pers- en ontvangkuip buiten de verharding",
                **gemeente,
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
    rijbaan_delen = []  # (prepared, functie, fysiek_voorkomen) voor de kenmerken
    for g, p in bgt.get("wegdeel", []):
        functie = (p.get("functie") or "").lower()
        if WEGDEEL_FUNCTIE.get(functie) is None and ("rijbaan" in functie or "baan" in functie):
            rijbanen.append(g)
            rijbaan_delen.append((prep(g), functie,
                                  (p.get("fysiek_voorkomen") or "").lower() or None))
    if rijbanen:
        obstakels["rijbaan"] = unary_union(rijbanen)

    def wegkenmerken(m0: float, m1: float) -> dict:
        """Verharding en wegfunctie van de gekruiste wegdelen (BGT): de
        zwaarste telt — één asfaltstrook in de passage maakt de kruising
        een asfaltkruising."""
        n = max(4, int((m1 - m0) * 2) + 1)
        functies, verhardingen = set(), set()
        for i in range(n + 1):
            pt = route.interpolate(m0 + (m1 - m0) * i / n)
            for pg, functie, verharding in rijbaan_delen:
                if pg.covers(pt):
                    functies.add(functie)
                    verhardingen.add(verharding)
        return {"verharding": _zwaarste(verhardingen, VERHARDING_RANG),
                "wegfunctie": _zwaarste(functies, WEGFUNCTIE_RANG)}
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
            p0, p1 = route.interpolate(m0), route.interpolate(m1)
            kenmerken = wegkenmerken(m0, m1) if soort == "rijbaan" else {}
            voorstel = _techniek_voor_kruising(soort, round(dwars, 1), kenmerken)
            c = {
                "soort": soort,
                "breedte_m": round(dwars, 1),
                "kruislengte_m": round(langs, 1),
                "chainage_van_m": round(m0, 1),
                "chainage_tot_m": round(m1, 1),
                # obstakelranden als coördinaat: na samenvoegen van
                # deeltrajecten worden de chainages hierop opnieuw gemeten
                "van_rd": (round(p0.x, 2), round(p0.y, 2)),
                "tot_rd": (round(p1.x, 2), round(p1.y, 2)),
                "punt": (round(mid.x, 2), round(mid.y, 2)),
                **kenmerken,
                **voorstel,
            }
            if _sleufloos_verplicht(kenmerken):
                c["sleufloos_verplicht"] = True
            # zonder legger/NWB (verrijk_kruisingen) is elke kruising bijzonder
            markeer_bijzonder_punt(c, legger_beschikbaar=False, nwb_beschikbaar=False)
            crossings.append(c)
    crossings.sort(key=lambda c: c["chainage_van_m"])
    for i, c in enumerate(crossings, 1):
        c["nr"] = f"KR-{i:03d}"
    return crossings


# IMWA-categorie (landelijke leggerdataset) → gangbare waterschapsaanduiding.
# Primair (≈ A): open ontgraving met afdamming staat het waterschap niet toe;
# secundair/tertiair (≈ B/C) bevestigt de breedte-aanname van de beslistabel.
LEGGER_AANDUIDING = {"primair": "A (primair)", "secundair": "B (secundair)",
                     "tertiair": "C (tertiair)"}
LEGGER_ZOEKAFSTAND_M = 15.0
NWB_BEHEERDER = {"R": "Rijkswaterstaat", "P": "Provincie", "G": "Gemeente",
                 "W": "Waterschap", "T": "Overige wegbeheerder"}
NWB_ZOEKAFSTAND_M = 20.0


def verrijk_kruisingen(crossings: list, legger_water: list | None = None,
                       nwb: list | None = None,
                       waterschap_bij=None) -> None:
    """Kruisingen verrijken met legger- en beheerdergegevens (in place).

    - water: dichtstbijzijnde leggerwatergang (IMWA) levert de categorie;
      bij een primaire (A-)watergang is open ontgraving niet toegestaan en
      wordt de techniek kritisch opgeschaald naar de lichtste sleufloze
      techniek die past. Het bevoegde waterschap komt uit de IMSO-grenzen
      (``waterschap_bij``: callable (x, y) → naam of None).
    - rijbaan: dichtstbijzijnd NWB-wegvak levert de echte wegbeheerder
      (Rijk/provincie/gemeente/waterschap) voor het bevoegd gezag. Bij een
      rijks- of provinciale weg is open ontgraving niet aan de orde: een
      open voorstel wordt dan opgeschaald naar persing/HDD.
    """
    for c in crossings:
        p = Point(c["punt"])
        if c["soort"] == "water":
            if waterschap_bij is not None:
                naam = waterschap_bij(p.x, p.y)
                if naam:
                    c["bevoegd_gezag"] = naam
            if legger_water:
                beste = min(
                    ((g, props) for g, props in legger_water),
                    key=lambda gp: gp[0].distance(p), default=None)
                if beste is None or beste[0].distance(p) > LEGGER_ZOEKAFSTAND_M:
                    continue
                cat = (beste[1].get("categoriewater") or "").lower()
                if not cat:
                    continue
                c["legger_categorie"] = LEGGER_AANDUIDING.get(cat, cat)
                naam_wl = beste[1].get("naam") or ""
                if naam_wl:
                    c["legger_naam"] = naam_wl
                if cat == "primair":
                    c["legger_verbiedt_open"] = True
                    if c["techniek"] == TECHNIEK_OPEN:
                        # kritisch opschalen: lichtste sleufloze techniek
                        nieuw = (TECHNIEK_PERSING
                                 if c["breedte_m"] > WATER_OPEN_MAX_M
                                 else TECHNIEK_NANO)
                        if c["breedte_m"] > WATER_PERSING_MAX_M:
                            nieuw = TECHNIEK_HDD
                        c["techniek"] = nieuw
                        detail, richtlijn = ALTERNATIEF_DETAIL[nieuw]
                        c["detail"], c["richtlijn"] = detail, richtlijn
                    c["noodzaak"] = ("sleufloos vereist — primaire (A-)watergang "
                                     "volgens de legger van het waterschap: open "
                                     "ontgraving met afdamming niet toegestaan")
                elif c["techniek"] == TECHNIEK_OPEN:
                    c["noodzaak"] += (f" — bevestigd door de legger: "
                                      f"{c['legger_categorie']}-watergang")
        elif c["soort"] == "rijbaan" and nwb:
            beste = min(((g, props) for g, props in nwb),
                        key=lambda gp: gp[0].distance(p), default=None)
            if beste is None or beste[0].distance(p) > NWB_ZOEKAFSTAND_M:
                continue
            props = beste[1]
            srt = (props.get("wegbehsrt") or "").strip().upper()
            naam = (props.get("wegbehnaam") or "").strip()
            straat = (props.get("sttNaam") or "").strip()
            if srt:
                soort_naam = NWB_BEHEERDER.get(srt, "Wegbeheerder")
                c["wegbeheerder_srt"] = srt
                if srt in WEGBEHEERDER_SLEUFLOOS and not c.get("sleufloos_verplicht"):
                    # rijks-/provinciale weg: beslistabel opnieuw met de
                    # beheerder erbij (open → persing/HDD, motivering mee)
                    c["sleufloos_verplicht"] = True
                    c.update(_techniek_voor_kruising("rijbaan", c["breedte_m"], c))
                c["bevoegd_gezag"] = (f"{soort_naam} {naam}".strip()
                                      if naam and naam.lower() != soort_naam.lower()
                                      else soort_naam)
                c["wegbeheerder_bron"] = "NWB"
            if straat:
                c["wegnaam"] = straat
    for c in crossings:
        markeer_bijzonder_punt(c, legger_beschikbaar=bool(legger_water),
                               nwb_beschikbaar=bool(nwb))


# ---------------------------------------------------------------------------
# Bijzondere punten: welke kruisingen worden vermeld
# ---------------------------------------------------------------------------
# Een open ontgraving is het standaard sleufwerk en hoort niet als kruising
# in register, nota en tekening. Vermeld wordt alleen een bijzonder punt:
# elke sleufloze passage, en een open ontgraving waar een beheerder in het
# spel is — een leggerwatergang van het waterschap (afdamming, melding
# waterschapsverordening) of een openbare weg met wegbeheerder in het NWB
# (verkeersmaatregelen, instemming wegbeheerder). Een sloot buiten de legger
# of een erftoegang/pad zonder wegbeheerder is gewoon sleufwerk. Ontbreekt
# de bron (legger of NWB niet geladen), dan blijft de kruising uit voorzorg
# vermeld: liever één te veel dan een echte weg verzwijgen.
BIJZONDER_SLEUFLOOS = "sleufloze passage"
BIJZONDER_AFWIJKING = ("open ontgraving in afwijking van het techniekadvies "
                       "of waar sleufloos verplicht is")


def markeer_bijzonder_punt(c: dict, legger_beschikbaar: bool = True,
                           nwb_beschikbaar: bool = True) -> None:
    """`bijzonder_punt` (bool) en `bijzonder_reden` op een kruising zetten."""
    def zet(bijzonder: bool, reden: str) -> None:
        c["bijzonder_punt"] = bijzonder
        c["bijzonder_reden"] = reden

    if c.get("techniek") != TECHNIEK_OPEN:
        return zet(True, BIJZONDER_SLEUFLOOS)
    if (c.get("techniek_oorspronkelijk") or c.get("sleufloos_verplicht")
            or c.get("legger_verbiedt_open")):
        return zet(True, BIJZONDER_AFWIJKING)
    if c["soort"] == "water":
        cat = c.get("legger_categorie")
        if cat:
            return zet(True, f"leggerwatergang {cat}: afdamming en melding "
                             "bij het waterschap")
        if not legger_beschikbaar:
            return zet(True, "legger niet geladen: beheer watergang onbekend")
        return zet(False, "sloot buiten de legger van het waterschap: "
                          "standaard sleufwerk")
    if c["soort"] == "rijbaan":
        srt = c.get("wegbeheerder_srt")
        if srt:
            beheerder = NWB_BEHEERDER.get(srt, "wegbeheerder").lower()
            return zet(True, f"openbare weg ({beheerder}): verkeersmaatregelen "
                             "en instemming wegbeheerder")
        if not nwb_beschikbaar:
            return zet(True, "NWB niet geladen: wegbeheerder onbekend")
        return zet(False, "geen wegbeheerder in het NWB (erftoegang, pad): "
                          "standaard sleufwerk")
    return zet(True, "open kruising van een bijzonder object")


def is_bijzonder_punt(c: dict) -> bool:
    """Kruisingen zonder markering (oudere projecten) tellen als bijzonder."""
    return c.get("bijzonder_punt", True) is not False


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


def _sleufloze_alternatieven(soort: str, breedte: float,
                             kenmerken: dict | None = None) -> list:
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
        verharding = (kenmerken or {}).get("verharding")
        if _sleufloos_verplicht(kenmerken):
            # stroom-/regionale weg of rijks-/provinciale weg: geen open
            # sleuf en geen ongestuurde techniek
            if breedte <= RIJBAAN_PERSING_MAX_M:
                return [TECHNIEK_PERSING, TECHNIEK_NANO, TECHNIEK_HDD]
            return [TECHNIEK_HDD, TECHNIEK_PERSING, TECHNIEK_NANO]
        if verharding is None or verharding == VERHARDING_GESLOTEN:
            # asfalt: eerst elke sleufloze techniek die past; open sleuf pas
            # als laatste uitweg met instemming van de wegbeheerder
            if breedte <= RIJBAAN_PERSING_MAX_M:
                return [TECHNIEK_PERSING, TECHNIEK_NANO, TECHNIEK_RAKET, TECHNIEK_OPEN]
            return [TECHNIEK_HDD, TECHNIEK_PERSING, TECHNIEK_NANO]
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


def beoordeel_werkterreinen(route: LineString, crossings: list, grid: Grid,
                            bgt: dict | None = None) -> None:
    """Werkterrein-toets per boring (in place op de kruisingenlijst).

    Globaal, conform werkwijze: ruim voldoende ruimte → akkoord; twijfel →
    markeren als onzeker (afhankelijk van type boorstelling); geen ruimte →
    sleufloos alternatief toepassen dat binnen de richtlijnen past. Past
    niets, dan blijft het voorstel staan met een kritieke melding.

    Gemeten op het werkelijke in-/uittredepunt (``boorspan``: uitloop van de
    techniek, verschoven naar vrij terrein als ``bgt`` is meegegeven).
    """
    vrij = boorvrij_toets(bgt, grid) if bgt else None

    def toets(techniek, c):
        eis = WERKTERREIN_EIS[techniek]
        uitloop = BOOR_UITLOOP.get(techniek, BOOR_UITLOOP_DEFAULT)
        span = boorspan(route, c["chainage_van_m"], c["chainage_tot_m"], uitloop, vrij)
        p_in, p_uit = route.interpolate(span["m_in"]), route.interpolate(span["m_uit"])
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
            for alt in _sleufloze_alternatieven(c["soort"], c["breedte_m"], c):
                if alt == c["techniek"]:
                    continue
                if alt == TECHNIEK_OPEN and (c.get("legger_verbiedt_open")
                                             or c.get("sleufloos_verplicht")):
                    continue  # A-watergang / rijks-, provinciale of stroomweg: open blijft verboden
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
                # techniek gewijzigd (ook naar open): markering bijwerken —
                # een afwijking van het advies blijft altijd een bijzonder punt
                markeer_bijzonder_punt(c)
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
                         grid: Grid | None = None, vrij=None) -> LineString:
    """Kruisingssegmenten vervangen door een rechte lijn (boring/persing is recht).

    Recht over de volledige boorlengte — van intrede- tot uittredepunt, met de
    uitloop van de voorgestelde techniek en verschoven naar vrij terrein
    (``boorspan`` met ``vrij``) — zodat de boring exact op het tracé ligt.
    Als de rechte vervanging door een uitgesloten cel loopt (bijvoorbeeld
    een gebouwhoek), schuift het in-/uittredepunt naar buiten tot de rechte
    lijn wél vrij ligt; lukt ook dat niet, dan blijft het oorspronkelijke
    pad staan.
    """
    if not crossings:
        return route

    def recht_kan(pa, pb) -> bool:
        if grid is None:
            return True
        if grid.hard_conflict(LineString([(pa.x, pa.y), (pb.x, pb.y)])):
            return False
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
            spans.append((c["chainage_van_m"] - OPEN_UITLOOP,
                          c["chainage_tot_m"] + OPEN_UITLOOP))
            continue
        # minimaal de nanodrill-uitloop: als de werkterrein-toets later
        # naar een alternatief wisselt, blijft de boorlijn op het tracé
        u = max(BOOR_UITLOOP.get(c["techniek"], BOOR_UITLOOP_DEFAULT),
                BOOR_UITLOOP[TECHNIEK_NANO])
        span = boorspan(route, c["chainage_van_m"], c["chainage_tot_m"], u, vrij)
        spans.append((span["m_in"], span["m_uit"]))
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


def _schamp_zijde(route: LineString, part: LineString, grens) -> int | None:
    """Aan welke kant van het tracé ligt de vrije ruimte bij een schamp-passage?

    Bemonstert de passage (``part``: het deel van het tracé binnen het
    obstakel) en kijkt per punt waar de dichtstbijzijnde obstakelrand ligt
    ten opzichte van de looprichting. Retourneert +1 of −1 als dat consequent
    dezelfde zijde is (een echte schamp: het tracé scheert langs één rand),
    en None als de rand vóór/achter ligt (een kruising: naar buiten drukken
    zou de kruising verbuigen) of de zijde wisselt (ligging midden op een
    smal pad zonder berm ernaast: elk punt naar zijn eigen dichtstbijzijnde
    rand drukken zou een zigzag geven)."""
    n = max(3, int(part.length / 0.5) + 1)
    stemmen = {1: 0, -1: 0, 0: 0}
    for i in range(n):
        p = part.interpolate(i * part.length / (n - 1))
        q = nearest_points(grens, p)[0]
        d = p.distance(q)
        if d < 1e-6:
            continue
        m = route.project(p)
        a = route.interpolate(max(0.0, m - 0.5))
        b = route.interpolate(min(route.length, m + 0.5))
        dx, dy = b.x - a.x, b.y - a.y
        lengte = math.hypot(dx, dy)
        if lengte < 1e-9:
            continue
        # sinus van de hoek tussen looprichting en richting naar de rand
        sin = (dx * (q.y - p.y) - dy * (q.x - p.x)) / (lengte * d)
        if abs(sin) < 0.5:
            stemmen[0] += 1  # rand ligt vóór of achter (< 30° uit de as)
        else:
            stemmen[1 if sin > 0 else -1] += 1
    totaal = sum(stemmen.values())
    if not totaal:
        return None
    for zijde in (1, -1):
        if stemmen[zijde] >= 0.8 * totaal and stemmen[0] <= 0.2 * totaal:
            return zijde
    return None


def verwijder_schampen(route: LineString, grid: Grid | None,
                       rondes: int = 2) -> LineString:
    """Sub-cel-nudge: schampen over verharding, water of spoor van het
    obstakel afdrukken, de berm in.

    Het raster werkt op celmiddens; een tracé dat in een smalle berm naast
    een trottoir, fietspad of rijbaan ligt kan daardoor met enkele decimeters
    over de échte verhardingsrand scheren. Hele-celverschuivingen in het
    gladstrijken kunnen dat niet corrigeren (de goedkoopste hele cel ís de
    randcel). Hier worden passages die nergens dieper dan SCHAMP_DIEPTE_M in
    het obstakel komen — dus randeffecten, geen echte kruisingen — vector-
    exact haaks naar buiten gedrukt tot SCHAMP_CLEARANCE_M vrije marge.

    Alleen passages met een eenduidige vrije zijde (`_schamp_zijde`) worden
    verschoven: een kruising blijft recht en een ligging midden op een smal
    pad zonder berm ernaast blijft liggen. Elk verschoven punt moet
    begaanbaar blijven (geen ∞-cel, pand of ander obstakel) en mag niet in
    een duurdere ligging terechtkomen (van het trottoir het erf op), anders
    blijft het staan.
    """
    if grid is None or not grid.schamp:
        return route

    def punt_ok(p: Point, kand: Point, eigen_geom) -> bool:
        for prep_g, geom, _ in grid.schamp:
            if geom is not eigen_geom and prep_g.intersects(kand):
                return False
        if grid.hard is not None and grid.hard.distance(kand) < 0.1:
            return False
        r, c = grid.world_to_cell(kand.x, kand.y)
        kost = float(grid.cost[r, c])
        if np.isfinite(kost):
            r0, c0 = grid.world_to_cell(p.x, p.y)
            was = float(grid.cost[r0, c0])
            return not np.isfinite(was) or kost <= was + 1e-6
        # een ∞-cel kan hier van de pand-veiligheidsmarge in het raster komen
        # (smal voetpad tussen rijbaan en gevel); vector-exact is het punt dan
        # wél vrij — de afstand tot de echte harde geometrie is al getoetst
        return grid.hard is not None

    def zijde_van(p: Point, m: float, dx: float, dy: float) -> int:
        a = route.interpolate(max(0.0, m - 0.5))
        b = route.interpolate(min(route.length, m + 0.5))
        rx, ry = b.x - a.x, b.y - a.y
        return 1 if rx * dy - ry * dx > 0 else -1

    for _ in range(rondes):
        # schamp-vensters langs het tracé bepalen (chainage, obstakel, zijde)
        vensters = []
        for prep_g, geom, _ in grid.schamp:
            if not prep_g.intersects(route):
                continue
            inter = route.intersection(geom)
            parts = [g for g in (inter.geoms if hasattr(inter, "geoms") else [inter])
                     if isinstance(g, LineString) and g.length > 0.05]
            grens = geom.boundary
            for part in parts:
                n = max(2, int(part.length / 0.5) + 1)
                diepte = max(grens.distance(part.interpolate(i * part.length / (n - 1)))
                             for i in range(n))
                if diepte >= SCHAMP_DIEPTE_M:
                    continue  # echte kruising, blijft staan
                zijde = _schamp_zijde(route, part, grens)
                if zijde is None:
                    continue  # kruising of ligging midden op het pad
                m0 = route.project(Point(part.coords[0]))
                m1 = route.project(Point(part.coords[-1]))
                vensters.append((min(m0, m1) - 1.0, max(m0, m1) + 1.0, geom, grens, zijde))
        if not vensters:
            break
        # tracé opnieuw opbouwen: bestaande hoekpunten plus verdichting binnen
        # de vensters, zodat er punten zíjn om te verschuiven
        mss = {0.0, route.length}
        mss.update(route.project(Point(c)) for c in route.coords)
        for a, b, _, _, _ in vensters:
            mss.update(np.arange(max(0.0, a), min(route.length, b), 0.75))
        coords = []
        for m in sorted(mss):
            p = route.interpolate(m)
            for a, b, geom, grens, zijde in vensters:
                if not (a <= m <= b):
                    continue
                d_rand = grens.distance(p)
                binnen = geom.covers(p)
                if not binnen and d_rand >= SCHAMP_CLEARANCE_M:
                    continue
                q = nearest_points(grens, p)[0]
                dx, dy = (q.x - p.x, q.y - p.y) if binnen else (p.x - q.x, p.y - q.y)
                lengte = math.hypot(dx, dy)
                if lengte < 1e-9:
                    continue  # exact op de rand: richting onbepaald
                if zijde_van(p, m, dx, dy) != zijde:
                    continue  # zou naar de verkeerde kant (de kruising in) drukken
                schaal = (d_rand + SCHAMP_CLEARANCE_M) / lengte if binnen \
                    else (SCHAMP_CLEARANCE_M - d_rand) / lengte
                kand = Point(p.x + dx * schaal, p.y + dy * schaal)
                if punt_ok(p, kand, geom):
                    p = kand
            coords.append((p.x, p.y))
        route = LineString(coords)
    return route


def straighten_iteratief(route: LineString, bgt: dict, grid: Grid | None = None,
                         rondes: int = 2) -> LineString:
    """Kruisingen rechttrekken in meerdere rondes, daarna schampen afdrukken.

    Eén ronde volstaat niet: het rechttrekken kort het (kronkelige raster-)pad
    in, waardoor chainages verschuiven en de rechte koorde net naast het
    beoogde in-/uittredepunt kan beginnen. De tweede ronde meet op het al
    rechtgetrokken tracé en legt de boorlengte exact recht.
    """
    vrij = boorvrij_toets(bgt, grid)
    for _ in range(rondes):
        pre = detect_crossings(route, bgt)
        if not pre:
            break
        route = straighten_crossings(route, pre, grid, vrij)
    return verwijder_schampen(route, grid)


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
