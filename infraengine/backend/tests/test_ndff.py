"""Unit tests voor de NDFF-koppeling (pure functies: categoriseren,
samenvatten en de registertekst). Geen netwerk.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ndff  # noqa: E402
import registers  # noqa: E402


def _hok(hok_id, cats, fout=None):
    d = {"hok": hok_id, "categorieen": {c["key"]: [] for c in ndff.CATEGORIEEN},
         "soorten": 0, "waarnemingen": 0, "fout": fout}
    for key, soorten in cats.items():
        d["categorieen"][key] = soorten
        d["soorten"] += len(soorten)
        d["waarnemingen"] += sum(s["aantal"] for s in soorten)
    return d


class TestCategoriseer(unittest.TestCase):
    def test_vogels_alleen_jaarrond_nest(self):
        soorten = [{"slug": "huismus", "naam": "Huismus", "aantal": 5},
                   {"slug": "buizerd", "naam": "Buizerd", "aantal": 2},
                   {"slug": "ijsvogel", "naam": "IJsvogel", "aantal": 9}]
        uit = ndff.categoriseer("vogels", soorten)
        self.assertEqual([(k, s["naam"]) for k, s in uit],
                         [("broedvogels_jaarrond", "Huismus"),
                          ("broedvogels_jaarrond", "Buizerd")])

    def test_reptielen_en_amfibieen_zelfde_categorie(self):
        self.assertEqual(ndff.categoriseer("reptielen", [{"slug": "x", "naam": "Ringslang",
                                                          "aantal": 1}])[0][0],
                         "amfibieen_reptielen")
        self.assertEqual(ndff.categoriseer("amfibieen", [{"slug": "x", "naam": "Gewone pad",
                                                          "aantal": 1}])[0][0],
                         "amfibieen_reptielen")

    def test_onbekende_groep_telt_niet(self):
        self.assertEqual(ndff.categoriseer("dagvlinders", [{"slug": "x", "naam": "Y",
                                                            "aantal": 1}]), [])


class TestSamenvatting(unittest.TestCase):
    def setUp(self):
        self.hokken = {
            1: _hok(1, {"vleermuizen": [
                {"naam": "Gewone dwergvleermuis", "naam_wet": "P. pipistrellus",
                 "aantal": 10, "beleid": ["ow--habitatrichtlijn"]}],
                "amfibieen_reptielen": [
                {"naam": "Gewone pad", "naam_wet": "Bufo bufo", "aantal": 3,
                 "beleid": ["ow--andere-soorten"]}]}),
            2: _hok(2, {"vleermuizen": [
                {"naam": "Gewone dwergvleermuis", "naam_wet": "P. pipistrellus",
                 "aantal": 4, "beleid": ["ow--habitatrichtlijn"]},
                {"naam": "Laatvlieger", "naam_wet": "E. serotinus", "aantal": 1,
                 "beleid": ["ow--habitatrichtlijn"]}]}),
            3: _hok(3, {}, fout="HTTPError: 502"),
        }

    def test_telt_soorten_over_hokken_ontdubbeld(self):
        sam = ndff.samenvatting(self.hokken)
        self.assertEqual(sam["hokken"], 3)
        self.assertEqual(sam["hokken_fout"], 1)
        self.assertEqual(sam["soorten_totaal"], 3)
        self.assertEqual(sam["strikt_totaal"], 2)
        vleer = next(c for c in sam["categorieen"] if c["key"] == "vleermuizen")
        self.assertEqual(vleer["soorten"][0]["naam"], "Gewone dwergvleermuis")
        self.assertEqual(vleer["soorten"][0]["hokken"], 2)
        self.assertEqual(vleer["soorten"][0]["aantal"], 14)
        self.assertTrue(vleer["soorten"][0]["strikt"])
        amf = next(c for c in sam["categorieen"] if c["key"] == "amfibieen_reptielen")
        self.assertFalse(amf["soorten"][0]["strikt"])

    def test_tekst_en_strikte_soorten(self):
        sam = ndff.samenvatting(self.hokken)
        t = ndff.tekst(sam)
        self.assertIn("vleermuizen: 2 soorten", t)
        self.assertIn("Gewone dwergvleermuis", t)
        self.assertIn("amfibieën en reptielen: 1 soort (Gewone pad)", t)
        self.assertEqual(ndff.strikte_soorten(sam),
                         ["Gewone dwergvleermuis (Habitatrichtlijn)",
                          "Laatvlieger (Habitatrichtlijn)"])

    def test_leeg(self):
        sam = ndff.samenvatting({})
        self.assertEqual(sam["soorten_totaal"], 0)
        self.assertEqual(ndff.tekst(sam), "")
        self.assertEqual(ndff.strikte_soorten(None), [])


class TestRegisters(unittest.TestCase):
    def test_quickscan_door_ndff_zonder_natuurgebied(self):
        sam = ndff.samenvatting({1: _hok(1, {"vleermuizen": [
            {"naam": "Laatvlieger", "naam_wet": "E. serotinus", "aantal": 1,
             "beleid": ["ow--habitatrichtlijn"]}]})})
        items = registers.build_onderzoeken(None, [], ndff=sam)
        qs = [o for o in items if o["soort"].startswith("Natuur-quickscan")]
        self.assertEqual(len(qs), 1)
        self.assertIn("Laatvlieger", qs[0]["aanleiding"])
        self.assertIn("NDFF", qs[0]["aanleiding"])

    def test_geen_quickscan_zonder_aanleiding(self):
        items = registers.build_onderzoeken(None, [], ndff=ndff.samenvatting({}))
        self.assertFalse([o for o in items if o["soort"].startswith("Natuur-quickscan")])

    def test_toetsing_waarschuwing_bij_strikte_soort(self):
        from shapely.geometry import LineString
        sam = ndff.samenvatting({1: _hok(1, {"broedvogels_jaarrond": [
            {"naam": "Huismus", "naam_wet": "Passer domesticus", "aantal": 7,
             "beleid": ["ow--vogelrichtlijn"]}]})})
        checks = registers.build_checks(LineString([(0, 0), (10, 0)]), [], [], [],
                                        ndff=sam)
        soort = [c for c in checks if c["toets"].startswith("Soortenbescherming")]
        self.assertEqual(len(soort), 1)
        self.assertEqual(soort[0]["ernst"], "waarschuwing")
        self.assertIn("Huismus (Vogelrichtlijn)", soort[0]["melding"])


if __name__ == "__main__":
    unittest.main()
