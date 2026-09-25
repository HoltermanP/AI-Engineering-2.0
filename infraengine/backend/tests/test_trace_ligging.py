"""Tracé-ligging: zoveel mogelijk in de berm, niet onder verharding.

Synthetische BGT: een rijbaan, daarnaast een trottoir en daarnaast een smalle
berm (groenstrook). Het tracé tussen twee stations in die berm hoort de hele
lengte in de berm te liggen — vector-exact, ook al is het raster grof.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from engine import (  # noqa: E402
    CL_BERM, DEFAULT_WEIGHTS, _free_station, build_painter, build_segments,
    shortest_path, straighten_iteratief,
)

X, Y = 155000.13, 463000.27   # niet-ronde oorsprong: randen vallen niet op celranden
LENGTE = 120.0


def _bgt(berm_breedte: float, met_berm: bool = True, off: float = 0.0) -> dict:
    """Stroken in y-richting (van zuid naar noord): rijbaan 6 m, trottoir
    1,8 m, berm ``berm_breedte``, daarboven erf. ``off`` verschuift de hele
    straat ten opzichte van het raster (de fase van de randen in de cellen
    bepaalt welke cellen de smalle berm krijgt)."""
    y0 = Y + off
    rijbaan = box(X - 20, y0 + 0.0, X + LENGTE + 20, y0 + 6.0)
    trottoir = box(X - 20, y0 + 6.0, X + LENGTE + 20, y0 + 7.8)
    berm = box(X - 20, y0 + 7.8, X + LENGTE + 20, y0 + 7.8 + berm_breedte)
    erf = box(X - 20, y0 + 7.8 + berm_breedte, X + LENGTE + 20, y0 + 30)
    bgt = {
        "wegdeel": [(rijbaan, {"functie": "rijbaan, lokale weg",
                               "fysiek_voorkomen": "gesloten verharding"}),
                    (trottoir, {"functie": "voetpad",
                                "fysiek_voorkomen": "open verharding"})],
        "ondersteunendwegdeel": [],
        "begroeidterreindeel": [(berm, {"fysiek_voorkomen": "groenvoorziening"})]
        if met_berm else [],
        "onbegroeidterreindeel": [(erf, {"fysiek_voorkomen": "erf"})],
    }
    return bgt


def _route(bgt: dict, cell: float, y_station: float) -> tuple:
    bbox = (X - 10, Y - 5, X + LENGTE + 10, Y + 25)
    painter = build_painter(bbox, bgt, [], cell)
    grid = painter.build(DEFAULT_WEIGHTS)
    stations = [(X, Y + y_station), (X + LENGTE, Y + y_station)]
    for s in stations:
        _free_station(grid, s, radius_m=1.0)
    route = LineString(shortest_path(grid, stations))
    route = straighten_iteratief(route, bgt, grid)
    return route, grid


def _verharding(bgt: dict):
    return unary_union([g for g, _ in bgt["wegdeel"]])


class BermLiggingTest(unittest.TestCase):
    def _toets_in_berm(self, berm_breedte: float, cell: float, off: float = 0.0):
        bgt = _bgt(berm_breedte, off=off)
        route, grid = _route(bgt, cell, off + 7.8 + berm_breedte / 2)
        verhard = _verharding(bgt)
        over = route.intersection(verhard).length
        self.assertLess(over, 0.05,
                        f"berm {berm_breedte} m, cel {cell} m: {over:.2f} m tracé "
                        f"over de verharding")
        # en niet het erf op (buffer voor de afronding van het raster)
        erf = bgt["onbegroeidterreindeel"][0][0]
        self.assertLess(route.intersection(erf).length, 0.05,
                        f"berm {berm_breedte} m, cel {cell} m: tracé op het erf")
        # het overgrote deel wordt als berm geregistreerd
        segs = build_segments(route, grid)
        berm_m = sum(s["lengte_m"] for s in segs if s["klasse"] == CL_BERM)
        self.assertGreater(berm_m, 0.9 * route.length)

    def test_smalle_berm_fijn_raster(self):
        self._toets_in_berm(1.2, 0.5)

    def test_smalle_berm_grof_raster(self):
        # 1,0 m berm bij cellen van 0,75 m: het raster houdt hooguit één
        # rij bermcellen over; de vector-correctie legt het tracé erin
        self._toets_in_berm(1.0, 0.75)

    def test_ruime_berm(self):
        self._toets_in_berm(2.5, 1.0)

    def test_smalle_berm_grofste_raster_alle_fasen(self):
        # 1,0 m berm bij cellen van 1,0 m, straat in verschillende fasen ten
        # opzichte van het raster: vóór de omgekeerde schilderorde werd de
        # berm bij sommige fasen volledig door het trottoir weggeschilderd en
        # lag het hele tracé (120 m) op het trottoir
        for off in (0.0, 0.17, 0.33, 0.5, 0.66, 0.83):
            with self.subTest(off=off):
                self._toets_in_berm(1.0, 1.0, off)

    def test_afstand_tot_verhardingsrand(self):
        bgt = _bgt(1.5)
        route, _ = _route(bgt, 0.5, 8.55)
        verhard = _verharding(bgt)
        # binnenpunten (niet de stations) minstens de schamp-marge vrij
        for x, y in list(route.coords)[1:-1]:
            self.assertGreaterEqual(verhard.distance(LineString([(x, y), (x, y)]).centroid),
                                    0.15, f"punt ({x:.2f}, {y:.2f}) tegen de verharding")


class KruisingBlijftRechtTest(unittest.TestCase):
    def test_haakse_kruising_trottoir_niet_vervormd(self):
        """Stations aan weerszijden van het trottoir: het tracé kruist het
        trottoir haaks en wordt niet zijwaarts weggedrukt (dat zou een knik
        of zigzag in de kruising geven)."""
        bgt = _bgt(2.0)
        bbox = (X - 10, Y - 5, X + LENGTE + 10, Y + 25)
        painter = build_painter(bbox, bgt, [], 0.5)
        grid = painter.build(DEFAULT_WEIGHTS)
        # van de berm (y = 8,8) naar de overkant van de rijbaan (y = -2) — de
        # rijbaan is een echte kruising (diep), het trottoir van 1,8 m is de
        # "ondiepe" passage die de schamp-correctie met rust moet laten
        stations = [(X + 60, Y + 8.8), (X + 60, Y - 2.0)]
        for s in stations:
            _free_station(grid, s, radius_m=1.0)
        route = LineString(shortest_path(grid, stations))
        route = straighten_iteratief(route, bgt, grid)
        hemelsbreed = LineString(stations).length
        self.assertLess(route.length, hemelsbreed * 1.02)
        for x, _y in route.coords:
            self.assertAlmostEqual(x, X + 60, delta=0.6)


class ZonderBermTest(unittest.TestCase):
    def test_zonder_berm_geen_zigzag(self):
        """Geen berm: het tracé ligt op het trottoir en blijft daar recht;
        de schamp-correctie mag niet elk punt naar een andere rand drukken."""
        bgt = _bgt(1.5, met_berm=False)
        # geen begroeid terrein: alles boven het trottoir is erf
        route, _ = _route(bgt, 0.5, 6.9)
        ys = [y - Y for _x, y in route.coords]
        self.assertLess(max(ys) - min(ys), 0.6, f"zigzag: {ys}")


if __name__ == "__main__":
    unittest.main()


class RasterizeExactTest(unittest.TestCase):
    """Liggingsklassen exact op het celmidden: een strook van één cel breed
    krijgt precies de cellen waarvan het midden erin ligt, in elke fase ten
    opzichte van het raster; de PIL-vulling is tot een halve cel te dik."""

    def test_strook_van_een_cel_breed(self):
        from engine import Grid
        g = Grid((X, Y, X + 20, Y + 20), cell=1.0)
        for off in (0.0, 0.2, 0.5, 0.8):
            strook = box(X, Y + 10 + off, X + 20, Y + 11 + off)
            exact = g.rasterize([strook], exact=True)
            dik = g.rasterize([strook])
            kol = exact[:, 5]
            with self.subTest(off=off):
                # één cel; bij een grens exact op het celmidden (off 0,5)
                # claimen beide randcellen de strook (grens-inclusief)
                self.assertIn(int(kol.sum()), (1, 2))
                for r in range(g.nrows):
                    _x, y = g.cell_to_world(r, 5)
                    self.assertEqual(bool(kol[r]), Y + 10 + off <= y <= Y + 11 + off)
                self.assertGreaterEqual(int(dik[:, 5].sum()), int(kol.sum()))

    def test_lijnen_en_buffer_blijven_werken(self):
        from engine import Grid
        g = Grid((X, Y, X + 20, Y + 20), cell=0.5)
        spoor = LineString([(X, Y + 10), (X + 20, Y + 10)])
        m = g.rasterize([spoor], buffer=2.5, exact=True)  # buffer → vlak → exact
        self.assertGreater(int(m.sum()), 0)
        m2 = g.rasterize([spoor])  # kale lijn: PIL-pad
        self.assertGreater(int(m2.sum()), 0)
