"""Alles is open ontgraving, tenzij: alleen bijzondere punten worden vermeld.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine  # noqa: E402
from engine import (TECHNIEK_HDD, TECHNIEK_OPEN, TECHNIEK_PERSING,  # noqa: E402
                    is_bijzonder_punt, markeer_bijzonder_punt)
from registers import build_vergunningen  # noqa: E402


def _open(soort: str, **extra) -> dict:
    return {"soort": soort, "techniek": TECHNIEK_OPEN, "breedte_m": 2.5, **extra}


class BijzonderPuntTest(unittest.TestCase):
    def test_sleufloos_altijd_bijzonder(self):
        for techniek in (TECHNIEK_HDD, TECHNIEK_PERSING):
            c = {"soort": "water", "techniek": techniek, "breedte_m": 9.0}
            markeer_bijzonder_punt(c)
            self.assertTrue(c["bijzonder_punt"])
            self.assertEqual(c["bijzonder_reden"], engine.BIJZONDER_SLEUFLOOS)

    def test_open_is_sleufwerk_ongeacht_legger_of_beheerder(self):
        # ook een leggerwatergang of een openbare weg met wegbeheerder in het
        # NWB: open ontgraving is het standaard sleufwerk en wordt niet vermeld
        for c in (_open("water"),
                  _open("water", legger_categorie="C (tertiair)"),
                  _open("water", legger_categorie="B (secundair)"),
                  _open("rijbaan", verharding="open verharding"),
                  _open("rijbaan", wegbeheerder_srt="G", wegnaam="Dorpsstraat")):
            with self.subTest(c=c):
                markeer_bijzonder_punt(c)
                self.assertFalse(c["bijzonder_punt"])
                self.assertFalse(is_bijzonder_punt(c))
                self.assertEqual(c["bijzonder_reden"], engine.BIJZONDER_NEE)

    def test_bron_niet_geladen_maakt_open_niet_bijzonder(self):
        w = _open("water")
        markeer_bijzonder_punt(w, legger_beschikbaar=False)  # oude aanroepvorm
        self.assertFalse(w["bijzonder_punt"])
        r = _open("rijbaan")
        markeer_bijzonder_punt(r, nwb_beschikbaar=False)
        self.assertFalse(r["bijzonder_punt"])

    def test_afwijking_van_advies_is_bijzonder(self):
        for extra in ({"techniek_oorspronkelijk": TECHNIEK_PERSING},
                      {"sleufloos_verplicht": True},
                      {"legger_verbiedt_open": True}):
            c = _open("rijbaan", **extra)
            markeer_bijzonder_punt(c)
            self.assertTrue(c["bijzonder_punt"])
            self.assertEqual(c["bijzonder_reden"], engine.BIJZONDER_AFWIJKING)

    def test_oude_resultaten_zonder_markering_volgen_de_techniek(self):
        self.assertFalse(is_bijzonder_punt({"soort": "water", "techniek": TECHNIEK_OPEN}))
        self.assertTrue(is_bijzonder_punt({"soort": "water", "techniek": TECHNIEK_HDD}))


class VergunningenOpenVerzamelpostTest(unittest.TestCase):
    def test_open_kruisingen_als_verzamelpost(self):
        kr = [dict(_open("water"), nr="KR-001"), dict(_open("water"), nr="KR-002"),
              dict(_open("rijbaan"), nr="KR-003"),
              {"soort": "water", "techniek": TECHNIEK_HDD, "breedte_m": 9.0, "nr": "KR-004"}]
        for c in kr:
            markeer_bijzonder_punt(c)
        items = build_vergunningen([], kr, "Heerenveen")
        per_kruising = [i for i in items if i.get("kruising")]
        self.assertEqual([i["kruising"] for i in per_kruising], ["KR-004"])
        verzamel = [i for i in items if "verzamelpost" in i["item"]]
        self.assertEqual(len(verzamel), 2)
        water = next(i for i in verzamel if "sloten" in i["item"])
        self.assertIn("KR-001, KR-002", water["trigger"])
        weg = next(i for i in verzamel if "wegen" in i["item"])
        self.assertIn("KR-003", weg["trigger"])


if __name__ == "__main__":
    unittest.main()
