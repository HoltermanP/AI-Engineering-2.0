"""In-/uittredepunten van boringen: uitloop vanaf de obstakelrand, op vrij
terrein, vastgehouden door de normalisatie en exact op het tracé.

Synthetisch: een oost-westtracé kruist een asfaltweg (6 m) met direct
daarnaast (2 m berm) een sloot van 4 m. Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString, Point, box  # noqa: E402

import engine  # noqa: E402
import maatvoering  # noqa: E402
from registers import build_boringen  # noqa: E402

X, Y = 155000.0, 463000.0
WEG = box(X + 100, Y - 50, X + 106, Y + 50)        # rijbaan, 6 m breed
SLOOT = box(X + 108, Y - 50, X + 112, Y + 50)      # water, 4 m breed, 2 m oostelijk
BGT = {
    "wegdeel": [(WEG, {"functie": "rijbaan lokale weg",
                       "fysiek_voorkomen": "gesloten verharding"})],
    "waterdeel": [(SLOOT, {})],
}


def tracé(*punten):
    return LineString([(X + dx, Y + dy) for dx, dy in punten])


class BoorspanTest(unittest.TestCase):
    def test_uitloop_zonder_toets(self):
        route = tracé((0, 0), (200, 0))
        s = engine.boorspan(route, 100.0, 106.0, 3.0)
        self.assertEqual((s["m_in"], s["m_uit"]), (97.0, 109.0))
        self.assertTrue(s["vrij_in"] and s["vrij_uit"])

    def test_uittrede_schuift_uit_de_sloot(self):
        route = tracé((0, 0), (200, 0))
        vrij = engine.boorvrij_toets(BGT)
        s = engine.boorspan(route, 100.0, 106.0, 3.0, vrij)
        self.assertEqual(s["m_in"], 97.0)               # berm westzijde is vrij
        self.assertGreaterEqual(s["m_uit"], 112.0)      # voorbij de sloot
        self.assertGreater(s["verschoven_uit_m"], 0)
        self.assertTrue(s["vrij_uit"])

    def test_geen_vrij_terrein_binnen_bereik(self):
        route = tracé((0, 0), (200, 0))
        bgt = {"wegdeel": [(box(X + 106, Y - 50, X + 140, Y + 50),
                            {"functie": "parkeervlak"})]}
        vrij = engine.boorvrij_toets(bgt)
        s = engine.boorspan(route, 100.0, 106.0, 3.0, vrij)
        self.assertEqual(s["m_uit"], 109.0)             # basispunt blijft staan
        self.assertFalse(s["vrij_uit"])


class BoorpuntenOpTraceTest(unittest.TestCase):
    def setUp(self):
        # licht gebogen aanloop zodat rechttrekken en normaliseren iets doen
        self.route = tracé((0, 0), (60, 4), (90, 1), (100, 0), (106, 0),
                           (118, 0), (140, 3), (200, 0))
        self.route = engine.straighten_iteratief(self.route, BGT, None)
        self.kruisingen = engine.detect_crossings(self.route, BGT)

    def test_kruisingen_en_technieken(self):
        self.assertEqual([c["soort"] for c in self.kruisingen], ["rijbaan", "water"])
        for c in self.kruisingen:
            self.assertIn(c["techniek"], engine.BOOR_UITLOOP,
                          f"{c['soort']} moet sleufloos zijn: {c['techniek']}")

    def test_boorpunten_op_vrij_terrein_en_exact_op_trace(self):
        vast = engine.bepaal_boorpunten(self.route, self.kruisingen, BGT)
        weg = self.kruisingen[0]
        p_in = Point(weg["boor_in_rd"])
        p_uit = Point(weg["boor_uit_rd"])
        self.assertLess(self.route.distance(p_in), 0.02)
        self.assertLess(self.route.distance(p_uit), 0.02)
        self.assertLessEqual(p_in.x, X + 100 - 3 + 0.01)   # ≥ uitloop vóór de weg
        self.assertGreaterEqual(p_uit.x, X + 112)          # niet in de sloot
        self.assertFalse(WEG.intersects(p_in) or SLOOT.intersects(p_uit))
        self.assertTrue(vast)
        # alle technieken hebben een eigen punt; HDD ligt verder van de rand
        hdd = weg["boor_punten"][engine.TECHNIEK_HDD]
        self.assertLess(hdd["in_rd"][0], p_in.x)

    def test_normaliseren_laat_boorpunten_staan(self):
        vast = engine.bepaal_boorpunten(self.route, self.kruisingen, BGT)
        genormaliseerd = maatvoering.normaliseer_route(self.route, vast=vast)
        coords = {(round(x, 2), round(y, 2)) for x, y in genormaliseerd.coords}
        for x, y in vast:
            self.assertIn((round(x, 2), round(y, 2)), coords,
                          "dragend hoekpunt van de boorlijn is verschoven of vervallen")
        for c in self.kruisingen:
            for p in (Point(c["boor_in_rd"]), Point(c["boor_uit_rd"])):
                self.assertLess(genormaliseerd.distance(p), 0.03)

    def test_boorregister_gebruikt_de_punten(self):
        engine.bepaal_boorpunten(self.route, self.kruisingen, BGT)
        boringen = build_boringen(self.route, self.kruisingen)
        self.assertEqual(len(boringen), 1, "weg + sloot moeten één boring zijn")
        b = boringen[0]
        self.assertEqual(b["kruisingen"], [c["nr"] for c in self.kruisingen])
        self.assertLessEqual(b["intredepunt_rd"][0], X + 100 - 3 + 0.01)
        self.assertGreaterEqual(b["uittredepunt_rd"][0], X + 112 + 3 - 0.01)
        self.assertTrue(b["recht"], b["afwijking_recht_m"])
        self.assertLess(self.route.distance(Point(b["intredepunt_rd"])), 0.02)
        self.assertLess(self.route.distance(Point(b["uittredepunt_rd"])), 0.02)


if __name__ == "__main__":
    unittest.main()
