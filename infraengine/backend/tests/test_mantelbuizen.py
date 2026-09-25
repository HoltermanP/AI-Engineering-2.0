"""Mantelbuizen: altijd het minimum aantal buizen per boring.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString  # noqa: E402

import mantelbuizen  # noqa: E402
from engine import (  # noqa: E402
    TECHNIEK_HDD, TECHNIEK_NANO, TECHNIEK_OPEN, TECHNIEK_PERSING, TECHNIEK_RAKET,
)
from registers import build_boringen, groepeer_boringen  # noqa: E402

X, Y = 155000.0, 463000.0


def _kruising(nr, soort, van, tot, techniek, route, breedte=None):
    mid = route.interpolate((van + tot) / 2)
    return {
        "nr": nr, "soort": soort, "chainage_van_m": van, "chainage_tot_m": tot,
        "kruislengte_m": tot - van, "breedte_m": breedte or (tot - van),
        "techniek": techniek, "detail": "", "noodzaak": f"noodzaak {nr}",
        "punt": (round(mid.x, 2), round(mid.y, 2)),
    }


class BuismaatTest(unittest.TestCase):
    def test_een_circuit_een_hdpe_160(self):
        r = mantelbuizen.bepaal(TECHNIEK_HDD, "water", 1, 40.0)
        self.assertEqual(r["circuits"], 1)
        self.assertEqual(len(r["mantelbuizen"]), 1)
        self.assertEqual(r["mantelbuizen"][0]["aantal"], 1)
        self.assertEqual(r["mantelbuizen"][0]["diameter_mm"], 160)
        self.assertEqual(r["mantelbuis"], "1× HDPE Ø160 SDR11")
        self.assertIn("1 circuit → 1 buis", r["mantelbuis_motivering"])

    def test_twee_circuits_twee_hdpe_in_een_boring(self):
        r = mantelbuizen.bepaal(TECHNIEK_HDD, "rijbaan", 2, 30.0)
        self.assertEqual(r["mantelbuis"], "2× HDPE Ø160 SDR11")
        self.assertEqual(sum(mb["aantal"] for mb in r["mantelbuizen"]), 2)

    def test_persing_een_stalen_mantelbuis_met_binnenbuis(self):
        r = mantelbuizen.bepaal(TECHNIEK_PERSING, "rijbaan", 1, 18.0)
        staal = [mb for mb in r["mantelbuizen"] if mb["materiaal"] == mantelbuizen.STAAL]
        hdpe = [mb for mb in r["mantelbuizen"] if mb["materiaal"] == mantelbuizen.HDPE]
        self.assertEqual(len(staal), 1)
        self.assertEqual(staal[0]["aantal"], 1)
        self.assertEqual(staal[0]["diameter_mm"], 219.1)
        self.assertEqual(hdpe[0]["aantal"], 1)
        self.assertIn("geen stalen buis per circuit", r["mantelbuis_motivering"])

    def test_persing_twee_circuits_blijft_een_stalen_buis(self):
        r = mantelbuizen.bepaal(TECHNIEK_PERSING, "rijbaan", 2, 18.0)
        staal = [mb for mb in r["mantelbuizen"] if mb["materiaal"] == mantelbuizen.STAAL]
        self.assertEqual(staal[0]["aantal"], 1)
        self.assertGreater(staal[0]["diameter_mm"], 219.1)  # groter, niet méér
        self.assertEqual(mantelbuizen.meters([{"type": TECHNIEK_PERSING,
                                               "lengte_m": 18.0, **r}],
                                             mantelbuizen.HDPE), 36.0)

    def test_spoor_altijd_staal(self):
        r = mantelbuizen.bepaal(TECHNIEK_HDD, "spoor", 1, 60.0)
        self.assertEqual(r["mantelbuizen"][0]["materiaal"], mantelbuizen.STAAL)
        self.assertIn("ProRail", r["mantelbuis_motivering"])


class GroeperenTest(unittest.TestCase):
    def setUp(self):
        self.route = LineString([(X, Y), (X + 1000, Y)])

    def test_open_kruising_geen_boring(self):
        krs = [_kruising("KR-001", "rijbaan", 100, 106, TECHNIEK_OPEN, self.route)]
        self.assertEqual(build_boringen(self.route, krs), [])

    def test_losse_kruisingen_blijven_los(self):
        krs = [_kruising("KR-001", "rijbaan", 100, 110, TECHNIEK_PERSING, self.route),
               _kruising("KR-002", "water", 400, 404, TECHNIEK_HDD, self.route)]
        b = build_boringen(self.route, krs)
        self.assertEqual([x["kruisingen"] for x in b], [["KR-001"], ["KR-002"]])
        self.assertEqual([x["mantelbuis"] for x in b],
                         ["1× staal Ø219,1×6,3 met 1× HDPE Ø160 SDR11 binnenbuis",
                          "1× HDPE Ø160 SDR11"])
        self.assertEqual(krs[0]["boring"], "BOR-001")

    def test_water_naast_rijbaan_wordt_een_boring(self):
        # persing rijbaan 100–110, HDD watergang 118–122: uitloop 3 + 10 > gat 8
        krs = [_kruising("KR-001", "rijbaan", 100, 110, TECHNIEK_PERSING, self.route),
               _kruising("KR-002", "water", 118, 122, TECHNIEK_HDD, self.route)]
        b = build_boringen(self.route, krs)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["kruisingen"], ["KR-001", "KR-002"])
        self.assertEqual(b[0]["type"], TECHNIEK_HDD)      # zwaarste techniek
        self.assertEqual(b[0]["mantelbuis"], "1× HDPE Ø160 SDR11")  # één buis
        self.assertEqual(b[0]["lengte_m"], 42.0)          # 100-10 … 122+10
        self.assertIn("één mantelbuis in plaats van 2", b[0]["samengevoegd"])
        # de rijbaankruising volgt nu de HDD; oorspronkelijk voorstel bewaard
        self.assertEqual(krs[0]["techniek"], TECHNIEK_HDD)
        self.assertEqual(krs[0]["techniek_oorspronkelijk"], TECHNIEK_PERSING)
        self.assertEqual(krs[0]["boring"], krs[1]["boring"])

    def test_twee_raketten_naast_elkaar_worden_nanodrill(self):
        # twee korte kruisingen op 3 m: samen in één boring; raket trekt maar
        # één buis en de gezamenlijke boorlengte blijft onder de raketgrens,
        # dus alleen bij >1 circuit of te lang wordt opgeschaald
        krs = [_kruising("KR-001", "rijbaan", 100, 104, TECHNIEK_RAKET, self.route),
               _kruising("KR-002", "rijbaan", 107, 111, TECHNIEK_RAKET, self.route)]
        b = build_boringen(self.route, krs)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["type"], TECHNIEK_RAKET)  # 11 + 2×2 = 15 m ≤ 18
        krs = [_kruising("KR-001", "rijbaan", 100, 108, TECHNIEK_RAKET, self.route),
               _kruising("KR-002", "rijbaan", 110, 118, TECHNIEK_RAKET, self.route)]
        b = build_boringen(self.route, krs)
        self.assertEqual(b[0]["type"], TECHNIEK_HDD)  # 18 m obstakel > persingsgrens 12

    def test_dubbele_passage_ring_een_boring_twee_buizen(self):
        # ring: heen op chainage 100–110, terug over dezelfde weg op 700–710
        # heen naar station 2, zijtak naar station 3 en terug over hetzelfde
        # wegvak naar station 1 (chainage 0–400 en 800–1200 liggen op elkaar)
        route = LineString([(X, Y), (X + 400, Y), (X + 400, Y + 200),
                            (X + 400, Y), (X, Y)])
        krs = [_kruising("KR-001", "rijbaan", 100, 110, TECHNIEK_PERSING, route),
               _kruising("KR-002", "rijbaan", route.length - 110, route.length - 100,
                         TECHNIEK_PERSING, route)]
        # zelfde plek: KR-002 ligt op de terugweg over het eerste been
        self.assertLess(LineString([krs[0]["punt"], krs[1]["punt"]]).length, 5.0)
        b = build_boringen(route, krs)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["circuits"], 2)
        self.assertEqual(b[0]["kruisingen"], ["KR-001", "KR-002"])
        self.assertEqual(b[0]["mantelbuis"],
                         "1× staal Ø406,4×8,8 met 2× HDPE Ø160 SDR11 binnenbuis")
        self.assertIn("tweede passage", b[0]["samengevoegd"])
        self.assertEqual(krs[1]["boring"], "BOR-001")

    def test_groepen_zonder_sleufloos(self):
        self.assertEqual(groepeer_boringen(self.route, []), [])

    def test_totalen(self):
        krs = [_kruising("KR-001", "rijbaan", 100, 110, TECHNIEK_PERSING, self.route),
               _kruising("KR-002", "water", 400, 404, TECHNIEK_NANO, self.route)]
        b = build_boringen(self.route, krs)
        t = mantelbuizen.totalen(b)
        self.assertEqual([x["materiaal"] for x in t], ["HDPE", "staal"])
        hdpe = t[0]
        self.assertEqual(hdpe["aantal"], 2)
        self.assertEqual(hdpe["lengte_m"], 16.0 + 14.0)


if __name__ == "__main__":
    unittest.main()
