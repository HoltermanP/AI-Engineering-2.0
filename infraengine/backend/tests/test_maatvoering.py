"""Unit tests voor het normenkader en de maatvoeringstoetsing.

Kleine synthetische tracés in RD-coördinaten (EPSG:28992, omgeving
Amersfoort 155000/463000). Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString  # noqa: E402

import maatvoering  # noqa: E402
import normen  # noqa: E402

X, Y = 155000.0, 463000.0


def lijn(*punten):
    return LineString([(X + dx, Y + dy) for dx, dy in punten])


def bevindingen(checks, toets_naam):
    return [c for c in checks if toets_naam in c["toets"]]


class TestNormen(unittest.TestCase):
    def test_regels_hebben_bron_en_ernst(self):
        for rid, r in normen.alle().items():
            self.assertTrue(r.get("bron"), f"regel {rid} mist bronvermelding")
            self.assertIn(r.get("ernst"), ("kritiek", "waarschuwing", "info"),
                          f"regel {rid} heeft ongeldig ernst-niveau")

    def test_buigradius_eis(self):
        self.assertAlmostEqual(
            normen.buigradius_eis_m(),
            float(normen.waarde("buigradius_factor"))
            * float(normen.waarde("kabel_diameter_m")))


class TestNormalisatie(unittest.TestCase):
    def test_afronding_en_dedupe(self):
        r = lijn((0, 0), (0.004, 0.004), (10, 0))  # tweede punt ≈ eerste
        n = maatvoering.normaliseer_route(r)
        self.assertEqual(len(n.coords), 2)
        for x, y in n.coords:  # afgerond op 0,01 m
            self.assertAlmostEqual(x, round(x, 2))
            self.assertAlmostEqual(y, round(y, 2))

    def test_kort_kniksegment_wordt_samengevoegd(self):
        # zigzag van 0,3 m midden op een recht stuk
        r = lijn((0, 0), (10, 0), (10.3, 0.3), (10.6, 0), (30, 0))
        n = maatvoering.normaliseer_route(r)
        self.assertLess(len(n.coords), 5)
        self.assertAlmostEqual(n.length, 30.0, delta=0.7)

    def test_snapping_binnen_tolerantie(self):
        # referentierand y=1; tussenliggend punt ligt 0,06 m ernaast → snap
        rand = LineString([(X - 50, Y + 1), (X + 50, Y + 1)])
        r = lijn((0, 0), (10, 1.06), (20, 0))
        n = maatvoering.normaliseer_route(r, [rand])
        ys = [c[1] - Y for c in n.coords]
        self.assertAlmostEqual(ys[1], 1.0, places=2)
        # punt buiten de tolerantie blijft liggen
        r2 = lijn((0, 0), (10, 1.30), (20, 0))
        n2 = maatvoering.normaliseer_route(r2, [rand])
        self.assertAlmostEqual(list(n2.coords)[1][1] - Y, 1.30, places=2)

    def test_stations_blijven_op_hun_plek(self):
        rand = LineString([(X - 50, Y + 0.05), (X + 50, Y + 0.05)])
        r = lijn((0, 0), (10, 0), (20, 0))
        n = maatvoering.normaliseer_route(r, [rand])
        self.assertEqual(list(n.coords)[0], (X, Y))
        self.assertEqual(list(n.coords)[-1], (X + 20, Y))


class TestToets(unittest.TestCase):
    def test_lengte_en_overlengte(self):
        r = lijn((0, 0), (30, 0), (30, 40))  # 70 m werkelijke polyline
        _checks, mv = maatvoering.toets(r, [], [], [])
        self.assertEqual(mv["totale_lengte_m"], 70.0)
        pct = float(normen.waarde("overlengte_pct"))
        self.assertAlmostEqual(mv["kabellengte_incl_overlengte_m"],
                               round(70.0 * (1 + pct / 100), 1))
        self.assertEqual(mv["crs"], "EPSG:28992")

    def test_krappe_bocht_geflagd(self):
        # haarspeld: 0,4 m-segmenten met 120° richtingsverandering →
        # inpasbare boogstraal ≪ eis (0,75 m)
        r = lijn((0, 0), (10, 0), (10.2, 0.35), (9.8, 0.7), (0, 0.7))
        checks, mv = maatvoering.toets(r, [], [], [])
        bochten = bevindingen(checks, "buigradius")
        self.assertTrue(bochten, "krappe bocht niet geflagd")
        b = bochten[0]
        self.assertLess(b["gemeten"], normen.buigradius_eis_m())
        self.assertIsNotNone(b["punt"])
        self.assertIsNotNone(b["metrering_m"])
        self.assertLess(abs(b["metrering_m"] - 10.4), 1.5)
        self.assertIsNotNone(mv["kleinste_buigradius"])

    def test_ruime_haakse_bocht_niet_geflagd(self):
        # 90° met lange benen: boogstraal past ruim
        r = lijn((0, 0), (20, 0), (20, 20))
        checks, _mv = maatvoering.toets(r, [], [], [])
        self.assertFalse(bevindingen(checks, "buigradius"))

    def test_afstand_tot_parallelle_klic_kabel(self):
        r = lijn((0, 0), (50, 0))
        kabel = (LineString([(X, Y + 0.20), (X + 50, Y + 0.20)]),
                 {"thema": "middenspanning"})
        checks, mv = maatvoering.toets(r, [], [], [kabel])
        gevonden = bevindingen(checks, "afstand tot parallelle K&L")
        self.assertEqual(len(gevonden), 1)
        c = gevonden[0]
        self.assertAlmostEqual(c["gemeten"], 0.20, places=2)
        self.assertEqual(c["eis"], float(normen.waarde("afstand_kabels_m")))
        self.assertIn("NEN 7171", c["grondslag"])
        self.assertAlmostEqual(mv["kleinste_afstand"]["kabels"]["waarde"],
                               0.20, places=2)

    def test_hd_gas_strengere_eis_en_kritiek(self):
        r = lijn((0, 0), (50, 0))
        leiding = (LineString([(X, Y + 1.5), (X + 50, Y + 1.5)]),
                   {"thema": "gas hoge druk"})
        checks, _mv = maatvoering.toets(r, [], [], [leiding])
        gevonden = bevindingen(checks, "afstand tot parallelle K&L")
        self.assertEqual(len(gevonden), 1)
        self.assertEqual(gevonden[0]["ernst"], normen.ernst("afstand_hd_gas_m"))
        self.assertEqual(gevonden[0]["eis"],
                         float(normen.waarde("afstand_hd_gas_m")))

    def test_scherpe_kruising_geflagd_haaks_niet(self):
        r = lijn((0, 0), (50, 0))
        # kruising onder ± 27°
        schuin = (LineString([(X + 20, Y - 5), (X + 40, Y + 5)]),
                  {"thema": "datatransport"})
        haaks = (LineString([(X + 10, Y - 5), (X + 10, Y + 5)]),
                 {"thema": "water"})
        checks, mv = maatvoering.toets(r, [], [], [schuin, haaks])
        scherp = bevindingen(checks, "scherpe kruising")
        self.assertEqual(len(scherp), 1)
        self.assertLess(scherp[0]["gemeten"],
                        float(normen.waarde("min_kruisingshoek_gr")))
        self.assertAlmostEqual(scherp[0]["metrering_m"], 30.0, delta=0.5)
        self.assertIsNotNone(mv["kleinste_kruisingshoek"])

    def test_boom_stam_en_kroon(self):
        r = lijn((0, 0), (50, 0))
        bomen = [(X + 10, Y + 0.3, 2.5),   # stam op 0,30 m → flag
                 (X + 30, Y + 1.5, 2.5),   # binnen kroon → waarschuwing
                 (X + 40, Y + 10, 2.5)]    # ver weg → niets
        checks, mv = maatvoering.toets(r, [], bomen, [])
        self.assertEqual(len(bevindingen(checks, "boomstam")), 1)
        self.assertEqual(len(bevindingen(checks, "kroonprojectie")), 1)
        self.assertAlmostEqual(mv["kleinste_afstand"]["bomen"]["waarde"],
                               0.30, places=2)

    def test_zelfintersectie(self):
        r = lijn((0, 0), (20, 0), (20, 10), (10, 10), (10, -5))
        checks, _mv = maatvoering.toets(r, [], [], [])
        zelf = bevindingen(checks, "zelf-intersectie")
        self.assertEqual(len(zelf), 1)
        self.assertEqual(zelf[0]["ernst"],
                         normen.ernst("zelf_intersectie_ernst"))

    def test_niet_gesnapt_punt_geflagd(self):
        rand = LineString([(X - 50, Y + 0.3), (X + 50, Y + 0.3)])
        r = lijn((0, 0), (25, 0), (50, 0))  # tussenpunt 0,30 m van de rand
        checks, _mv = maatvoering.toets(r, [], [], [], referentie=[rand])
        snap = bevindingen(checks, "niet gesnapt")
        self.assertEqual(len(snap), 1)
        self.assertAlmostEqual(snap[0]["gemeten"], 0.30, places=2)


class TestAcceptatie(unittest.TestCase):
    """Acceptatiecriterium: één testtracé met een te krappe bocht, een
    segment op < 0,25 m van een KLIC-kabel en een scherpe kruising levert
    drie geflagde bevindingen met correcte locatie, metrering en waarde."""

    def test_drie_bevindingen(self):
        route = lijn((0, 0), (40, 0),
                     (40.3, 0.4), (40.0, 0.8),   # krappe bocht rond m 40
                     (0, 0.8))
        klic = [
            # parallelle MS-kabel op 0,20 m onder het eerste been
            (LineString([(X + 5, Y - 0.20), (X + 35, Y - 0.20)]),
             {"thema": "middenspanning"}),
            # scherpe kruising (± 27°) door het eerste been rond x=20
            (LineString([(X + 10, Y - 5), (X + 30, Y + 5)]),
             {"thema": "laagspanning"}),
        ]
        checks, mv = maatvoering.toets(route, [], [], klic)

        bocht = bevindingen(checks, "buigradius")
        afstand = bevindingen(checks, "afstand tot parallelle K&L")
        kruising = bevindingen(checks, "scherpe kruising")
        self.assertTrue(bocht and afstand and kruising,
                        f"verwacht 3 soorten bevindingen, kreeg: "
                        f"{[c['toets'] for c in checks]}")

        for c in bocht + afstand + kruising:
            self.assertIsNotNone(c["punt"], c["toets"])
            self.assertIsNotNone(c["metrering_m"], c["toets"])
            self.assertIsNotNone(c["gemeten"], c["toets"])
            self.assertTrue(c["maatvoering"])
            self.assertTrue(c["grondslag"])

        self.assertAlmostEqual(afstand[0]["gemeten"], 0.20, places=2)
        self.assertLess(bocht[0]["gemeten"], normen.buigradius_eis_m())
        for k in kruising:
            self.assertLess(k["gemeten"], 45.0)
        # de kabel kruist zowel het heen- als het teruggaande been; de
        # kruising op het eerste been ligt op metrering ± 20 m
        self.assertTrue(any(abs(k["metrering_m"] - 20.0) < 0.5
                            for k in kruising),
                        [k["metrering_m"] for k in kruising])
        # overzicht aanwezig met kleinste waarden per categorie
        self.assertIn("kabels", mv["kleinste_afstand"])
        self.assertIsNotNone(mv["kleinste_buigradius"])


if __name__ == "__main__":
    unittest.main()
