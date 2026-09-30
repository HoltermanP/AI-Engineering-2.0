"""In-/uittredepunten van boringen: uitloop volgens boorgeometrie en
invloedslijn, kuip op vrij terrein, afstand tot de insteek, haaks op het
obstakel, vastgehouden door de normalisatie en exact op het tracé.

Synthetisch: een oost-westtracé kruist een asfaltweg (6 m) met direct
daarnaast (2 m berm) een sloot van 4 m. Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString, Point, box  # noqa: E402

import engine  # noqa: E402
import maatvoering  # noqa: E402
import normen  # noqa: E402
from registers import build_boringen  # noqa: E402

X, Y = 155000.0, 463000.0
WEG = box(X + 100, Y - 50, X + 106, Y + 50)        # rijbaan, 6 m breed
SLOOT = box(X + 108, Y - 50, X + 112, Y + 50)      # water, 4 m breed, 2 m oostelijk
ASFALT = {"functie": "rijbaan lokale weg", "fysiek_voorkomen": "gesloten verharding"}
BGT = {
    "wegdeel": [(WEG, ASFALT)],
    "waterdeel": [(SLOOT, {})],
}
MARGE = engine.BOOR_VRIJ_MARGE_M


def tracé(*punten):
    return LineString([(X + dx, Y + dy) for dx, dy in punten])


def zone_rijbaan(techniek=engine.TECHNIEK_PERSING):
    return engine.zone_afstand_m("rijbaan", engine.ontgraving_m(techniek, "rijbaan"))


class BoorgeometrieTest(unittest.TestCase):
    def test_aanloop_alleen_boog(self):
        # 1 m diep met R 100 m: vlakker intreden, boog komt precies uit
        self.assertAlmostEqual(engine.aanloop_m(1.0, 15, 100), math.sqrt(199), places=2)

    def test_aanloop_recht_plus_boog(self):
        a = math.radians(15)
        recht = (10 - 100 * (1 - math.cos(a))) / math.sin(a)
        self.assertAlmostEqual(engine.aanloop_m(10.0, 15, 100),
                               recht * math.cos(a) + 100 * math.sin(a), places=6)

    def test_hdd_verder_dan_persing(self):
        self.assertGreater(engine.uitloop_m(engine.TECHNIEK_HDD, "rijbaan"),
                           engine.uitloop_m(engine.TECHNIEK_PERSING, "rijbaan"))
        u, reden = engine.uitloop_detail(engine.TECHNIEK_HDD, "rijbaan")
        self.assertIn("intredehoek", reden)

    def test_perskuip_buiten_invloedslijn(self):
        # RWS: kuip ≥ 1,0 m + 1,5 × ontgravingsdiepte uit de kant verharding
        h = engine.ontgraving_m(engine.TECHNIEK_PERSING, "rijbaan")
        u, reden = engine.uitloop_detail(engine.TECHNIEK_PERSING, "rijbaan")
        self.assertAlmostEqual(u, 1.0 + 1.5 * h, places=2)
        self.assertIn("invloedslijn", reden)

    def test_a_watergang_bredere_zone(self):
        self.assertEqual(engine.uitloop_m(engine.TECHNIEK_PERSING, "water", primair=True),
                         normen.waarde("boring_afstand_insteek_primair_m"))


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
        # westzijde: de uitloop valt binnen de invloedszone van de weg, dus
        # exact tot de rand van die zone
        self.assertAlmostEqual(s["m_in"], 100.0 - zone_rijbaan() - MARGE, places=2)
        # oostzijde: voorbij de sloot plus de afstand tot de insteek
        self.assertAlmostEqual(s["m_uit"], 112.0 + normen.waarde("boring_afstand_insteek_m")
                               + MARGE, places=2)
        self.assertGreater(s["verschoven_uit_m"], 0)
        self.assertTrue(s["vrij_uit"])

    def test_kuip_moet_passen(self):
        # parkeervlak 90–93 m: tussen parkeervlak en invloedszone van de weg
        # is minder dan een perskuip (3 m) vrij, dus het punt schuift voorbij
        # het parkeervlak
        route = tracé((0, 0), (200, 0))
        bgt = {"wegdeel": [(WEG, ASFALT),
                           (box(X + 90, Y - 50, X + 93, Y + 50), {"functie": "parkeervlak"})]}
        vrij = engine.boorvrij_toets(bgt)
        kuip = engine.kuip_m(engine.TECHNIEK_PERSING)
        s = engine.boorspan(route, 100.0, 106.0, zone_rijbaan(), vrij, kuip)
        self.assertAlmostEqual(s["m_in"], 90.0 - MARGE, places=2)
        self.assertAlmostEqual(s["m_kuip_in"], 90.0 - MARGE - kuip[0], places=2)
        self.assertTrue(s["vrij_in"])

    def test_talud_telt_mee_tot_de_insteek(self):
        route = tracé((0, 0), (200, 0))
        bgt = {"wegdeel": [(WEG, ASFALT)],
               "waterdeel": [(SLOOT, {})],
               "ondersteunendwaterdeel": [(box(X + 112, Y - 50, X + 113.5, Y + 50), {})]}
        s = engine.boorspan(route, 100.0, 106.0, 3.0, engine.boorvrij_toets(bgt))
        self.assertAlmostEqual(s["m_uit"], 113.5 + normen.waarde("boring_afstand_insteek_m")
                               + MARGE, places=2)

    def test_geen_vrij_terrein_binnen_bereik(self):
        route = tracé((0, 0), (200, 0))
        bgt = {"wegdeel": [(box(X + 106, Y - 50, X + 140, Y + 50),
                            {"functie": "parkeervlak"})]}
        vrij = engine.boorvrij_toets(bgt)
        s = engine.boorspan(route, 100.0, 106.0, 3.0, vrij)
        self.assertEqual(s["m_uit"], 109.0)             # basispunt blijft staan
        self.assertFalse(s["vrij_uit"])


class HaaksTest(unittest.TestCase):
    """Schuine kruising (45°) van een noord-zuidweg: de boorlijn moet haaks."""

    def setUp(self):
        self.bgt = {"wegdeel": [(box(X + 100, Y - 100, X + 106, Y + 100), ASFALT)]}
        self.route = tracé((0, -60), (40, -60), (163, 63), (220, 63))

    def test_schuine_kruising_wordt_haaks(self):
        voor = engine.detect_crossings(self.route, self.bgt)
        self.assertAlmostEqual(voor[0]["kruisingshoek_gr"], 45.0, delta=1.0)
        route = engine.straighten_iteratief(self.route, self.bgt, None)
        na = engine.detect_crossings(route, self.bgt)
        self.assertEqual(len(na), 1)
        self.assertGreaterEqual(na[0]["kruisingshoek_gr"], 89.0)
        self.assertAlmostEqual(na[0]["breedte_m"], 6.0, delta=0.3)
        self.assertLess(na[0]["kruislengte_m"], 6.2)     # recht over, niet schuin
        engine.bepaal_boorpunten(route, na, self.bgt)
        p_in, p_uit = Point(na[0]["boor_in_rd"]), Point(na[0]["boor_uit_rd"])
        self.assertAlmostEqual(p_in.y, p_uit.y, delta=0.05)   # oost-west = haaks
        self.assertAlmostEqual(p_in.x, X + 100 - zone_rijbaan() - MARGE, delta=0.05)
        # de perskuip ligt in het verlengde, ook op het rechte stuk
        kuip = Point(na[0]["boor_punten"][engine.TECHNIEK_PERSING]["kuip_in_rd"])
        self.assertLess(route.distance(kuip), 0.02)
        self.assertAlmostEqual(kuip.y, p_in.y, delta=0.05)
        # begin en eind van het tracé blijven staan
        self.assertEqual(route.coords[0], self.route.coords[0])
        self.assertEqual(route.coords[-1], self.route.coords[-1])

    def test_register_meldt_hoek(self):
        route = engine.straighten_iteratief(self.route, self.bgt, None)
        krs = engine.detect_crossings(route, self.bgt)
        engine.bepaal_boorpunten(route, krs, self.bgt)
        b = build_boringen(route, krs)[0]
        self.assertTrue(b["haaks"])
        self.assertTrue(b["recht"], b["afwijking_recht_m"])
        self.assertIsNotNone(b["kuip_in_rd"])
        self.assertIn("invloedslijn", b["plaatsing"])

    def test_bijna_haaks_blijft_liggen(self):
        # 85°: binnen de tolerantie, geen omweg
        dy = math.tan(math.radians(5)) * 206
        route = tracé((0, 0), (206, dy))
        na = engine.straighten_iteratief(route, self.bgt, None)
        self.assertLess(na.hausdorff_distance(route), 0.01)


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
            self.assertGreaterEqual(c["kruisingshoek_gr"], 80.0)

    def test_boorpunten_op_vrij_terrein_en_exact_op_trace(self):
        vast = engine.bepaal_boorpunten(self.route, self.kruisingen, BGT)
        weg = self.kruisingen[0]
        p_in = Point(weg["boor_in_rd"])
        p_uit = Point(weg["boor_uit_rd"])
        self.assertLess(self.route.distance(p_in), 0.02)
        self.assertLess(self.route.distance(p_uit), 0.02)
        self.assertLessEqual(p_in.x, X + 100 - zone_rijbaan() + 0.01)  # buiten de invloedslijn
        self.assertGreaterEqual(p_uit.x, X + 112 + 1.0)    # 1 m uit de insteek
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
        self.assertLessEqual(b["intredepunt_rd"][0], X + 100 - zone_rijbaan() + 0.01)
        self.assertGreaterEqual(b["uittredepunt_rd"][0],
                                X + 112 + normen.waarde("boring_afstand_insteek_m") - 0.01)
        self.assertTrue(b["recht"], b["afwijking_recht_m"])
        self.assertTrue(b["haaks"])
        self.assertLess(self.route.distance(Point(b["intredepunt_rd"])), 0.02)
        self.assertLess(self.route.distance(Point(b["uittredepunt_rd"])), 0.02)


class AWatergangTest(unittest.TestCase):
    def test_beschermingszone_a_watergang(self):
        route = tracé((0, 0), (200, 0))
        bgt = {"waterdeel": [(box(X + 100, Y - 50, X + 110, Y + 50), {})]}
        krs = engine.detect_crossings(route, bgt)
        krs[0]["legger_verbiedt_open"] = True
        engine.bepaal_boorpunten(route, krs, bgt)
        zone = normen.waarde("boring_afstand_insteek_primair_m")
        pers = krs[0]["boor_punten"][engine.TECHNIEK_PERSING]
        self.assertLessEqual(pers["in_rd"][0], X + 100 - zone + 0.01)
        self.assertGreaterEqual(pers["uit_rd"][0], X + 110 + zone - 0.01)


if __name__ == "__main__":
    unittest.main()
