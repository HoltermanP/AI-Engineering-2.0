"""Open ontgravingen worden alleen bij bijzondere punten vermeld.

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


def _open(soort: str, **extra) -> dict:
    return {"soort": soort, "techniek": TECHNIEK_OPEN, "breedte_m": 2.5, **extra}


class BijzonderPuntTest(unittest.TestCase):
    def test_sleufloos_altijd_bijzonder(self):
        for techniek in (TECHNIEK_HDD, TECHNIEK_PERSING):
            c = {"soort": "water", "techniek": techniek, "breedte_m": 9.0}
            markeer_bijzonder_punt(c)
            self.assertTrue(c["bijzonder_punt"])
            self.assertEqual(c["bijzonder_reden"], engine.BIJZONDER_SLEUFLOOS)

    def test_sloot_buiten_legger_is_sleufwerk(self):
        c = _open("water")
        markeer_bijzonder_punt(c, legger_beschikbaar=True, nwb_beschikbaar=True)
        self.assertFalse(c["bijzonder_punt"])
        self.assertFalse(is_bijzonder_punt(c))

    def test_leggerwatergang_is_bijzonder(self):
        c = _open("water", legger_categorie="C (tertiair)")
        markeer_bijzonder_punt(c)
        self.assertTrue(c["bijzonder_punt"])
        self.assertIn("leggerwatergang", c["bijzonder_reden"])

    def test_erftoegang_zonder_wegbeheerder_is_sleufwerk(self):
        c = _open("rijbaan", verharding="open verharding")
        markeer_bijzonder_punt(c)
        self.assertFalse(c["bijzonder_punt"])

    def test_openbare_weg_nwb_is_bijzonder(self):
        c = _open("rijbaan", wegbeheerder_srt="G")
        markeer_bijzonder_punt(c)
        self.assertTrue(c["bijzonder_punt"])
        self.assertIn("gemeente", c["bijzonder_reden"])

    def test_bron_niet_geladen_blijft_vermeld(self):
        # zonder legger/NWB niet verzwijgen: liever één te veel
        w = _open("water")
        markeer_bijzonder_punt(w, legger_beschikbaar=False)
        self.assertTrue(w["bijzonder_punt"])
        r = _open("rijbaan")
        markeer_bijzonder_punt(r, nwb_beschikbaar=False)
        self.assertTrue(r["bijzonder_punt"])

    def test_afwijking_van_advies_is_bijzonder(self):
        c = _open("rijbaan", techniek_oorspronkelijk=TECHNIEK_PERSING)
        markeer_bijzonder_punt(c)
        self.assertTrue(c["bijzonder_punt"])
        self.assertEqual(c["bijzonder_reden"], engine.BIJZONDER_AFWIJKING)

    def test_oude_resultaten_zonder_markering_tellen_als_bijzonder(self):
        self.assertTrue(is_bijzonder_punt({"soort": "water", "techniek": TECHNIEK_OPEN}))


if __name__ == "__main__":
    unittest.main()
