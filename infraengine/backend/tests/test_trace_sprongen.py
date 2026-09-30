"""Sprongen in het tracé, wegprofiel en maatlijnen.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from shapely.geometry import LineString, box  # noqa: E402

import engine  # noqa: E402
import maatvoering  # noqa: E402

X, Y = 155000.0, 463000.0


def lengte(pts):
    return LineString(pts).length


class TestSpieken(unittest.TestCase):
    def test_zijwaartse_spiek_weg(self):
        pts = [(0, 0), (50, 0), (50, 20), (50.5, 0.2), (100, 0)]
        uit = engine.verwijder_spieken(pts)
        self.assertLess(lengte(uit), 102)
        self.assertLess(max(p[1] for p in uit), 1.0)

    def test_v_sprong_weg(self):
        pts = [(0, 0), (100, 0), (10, 2), (10, 50)]
        uit = engine.verwijder_spieken(pts)
        self.assertLess(lengte(uit), 65)

    def test_spiek_naar_station_blijft(self):
        pts = [(0, 0), (50, 0), (50, 20), (50.5, 0.2), (100, 0)]
        self.assertEqual(engine.verwijder_spieken(pts, beschermd=[(50, 20)]),
                         [tuple(p) for p in pts])

    def test_vast_punt_blijft(self):
        pts = [(0, 0), (50, 0), (50, 20), (50.5, 0.2), (100, 0)]
        uit = engine.verwijder_spieken(pts, vast=[(50, 10)])
        self.assertAlmostEqual(lengte(uit), lengte(pts), places=6)

    def test_rechte_lijn_ongewijzigd(self):
        pts = [(0, 0), (50, 0), (100, 5)]
        self.assertEqual(engine.verwijder_spieken(pts), pts)

    def test_omweg_om_uitsluiting_blijft_met_raster(self):
        # pand (uitgesloten) tussen heen- en terugweg: niet wegsnijden
        grid = engine.Grid((X, Y, X + 100, Y + 40), 0.5)
        grid.cost[:] = 1.0
        r0, c0 = grid.world_to_cell(X + 10, Y + 21.5)
        r1, c1 = grid.world_to_cell(X + 60, Y + 18.5)
        grid.cost[r0:r1 + 1, c0:c1 + 1] = np.inf  # pand van 3 m breed
        pts = [(X + 10, Y + 23), (X + 62, Y + 23), (X + 62, Y + 17), (X + 10, Y + 17)]
        uit = engine.verwijder_spieken(pts, grid=grid, tol=6.5)
        self.assertAlmostEqual(lengte(uit), lengte(pts), places=6)

    def test_oversteek_goedkoper_dan_omweg_wordt_gesneden(self):
        # goedkope sloot: rechtdoor is goedkoper dan 104 m omlopen (= weging)
        grid = engine.Grid((X, Y, X + 100, Y + 40), 0.5)
        grid.cost[:] = 1.0
        pts = [(X + 10, Y + 23), (X + 62, Y + 23), (X + 62, Y + 17), (X + 10, Y + 17)]
        uit = engine.verwijder_spieken(pts, grid=grid, tol=6.5)
        self.assertLess(lengte(uit), 20)


class TestMicroknikken(unittest.TestCase):
    def test_zigzagje_weg(self):
        pts = [(0, 0), (50, 0), (50.5, 0.2), (50.3, 0.0), (100, 0)]
        uit = engine.verwijder_microknikken(pts)
        self.assertEqual(uit, [(0, 0), (100, 0)])

    def test_echte_bocht_blijft(self):
        pts = [(0, 0), (50, 0), (50, 50)]
        self.assertEqual(engine.verwijder_microknikken(pts), pts)

    def test_station_blijft(self):
        pts = [(0, 0), (50, 0.2), (100, 0)]
        self.assertEqual(engine.verwijder_microknikken(pts, beschermd=[(50, 0.2)]), pts)


class TestZachtVia(unittest.TestCase):
    def test_via_naast_de_weg_trekt_geen_spiek(self):
        # goedkope "weg" over y = Y+10; via-punt 6 m ernaast in duur terrein
        grid = engine.Grid((X, Y, X + 120, Y + 30), 0.5)
        grid.cost[:] = 3.0
        r, _c = grid.world_to_cell(X, Y + 10)
        grid.cost[r - 1:r + 2, :] = 0.8
        wps = [(X + 5, Y + 10), (X + 60, Y + 16), (X + 115, Y + 10)]
        hard = engine.shortest_path(grid, wps, zacht=[0, 0, 0])
        zacht = engine.shortest_path(grid, wps, zacht=[0, 10.0, 0])
        self.assertGreater(max(p[1] for p in hard), Y + 15)
        self.assertLess(max(p[1] for p in zacht), Y + 12)


class TestWegprofiel(unittest.TestCase):
    def test_terrein_ver_van_de_weg_zwaarder(self):
        painter = engine.Painter((X, Y, X + 40, Y + 40), 1.0)
        berm = np.zeros((40, 40), dtype=bool)
        berm[:, :4] = True
        painter.add(berm, engine.CL_BERM, "berm_groen")
        grid = painter.build({"buiten_wegprofiel": 2.0})
        dichtbij = float(grid.cost[20, 5])   # 1–2 m van de berm
        ver = float(grid.cost[20, 30])       # ~26 m van de berm
        self.assertAlmostEqual(dichtbij, engine.DEFAULT_WEIGHTS["onbekend"], places=4)
        self.assertAlmostEqual(ver, engine.DEFAULT_WEIGHTS["onbekend"] * 2.0, places=4)
        self.assertAlmostEqual(float(grid.cost[20, 1]), engine.DEFAULT_WEIGHTS["berm_groen"],
                               places=4)


class TestMaatlijnen(unittest.TestCase):
    BGT = {"wegdeel": [(box(X, Y - 3, X + 200, Y + 3),
                        {"functie": "rijbaan lokale weg",
                         "fysiek_voorkomen": "gesloten verharding"})],
           "pand": [(box(X + 80, Y - 12, X + 100, Y - 9), {})]}

    def test_constante_afstand_en_zijde(self):
        route = LineString([(X, Y - 4), (X + 100, Y - 4)])
        uit = maatvoering.maatlijnen(route, self.BGT)
        v = [x for x in uit if x["categorie"] == "verharding"]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["afstand_m"], 1.0)
        self.assertEqual(v[0]["zijde"], "links")
        self.assertFalse(v[0]["verlopend"])
        self.assertGreaterEqual(len(v[0]["maatlijnen"]), 4)
        g = [x for x in uit if x["categorie"] == "gevel"]
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["zijde"], "rechts")

    def test_verlopend_en_in_verharding(self):
        route = LineString([(X, Y - 8), (X + 50, Y - 4.9), (X + 50, Y + 2)])
        uit = maatvoering.maatlijnen(route, self.BGT)
        self.assertTrue(any(x["verlopend"] for x in uit))
        self.assertTrue(any(x["status"] == "in" for x in uit))

    def test_herprojecteren(self):
        route = LineString([(X, Y - 4), (X + 100, Y - 4)])
        uit = maatvoering.maatlijnen(route, self.BGT)
        langer = LineString([(X - 50, Y - 4), (X + 100, Y - 4)])
        maatvoering.maatlijnen_herprojecteren(uit, langer)
        self.assertTrue(all(x["van_m"] >= 49.9 for x in uit))


if __name__ == "__main__":
    unittest.main()
