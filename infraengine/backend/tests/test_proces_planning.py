"""Unit tests voor de ontwerpplanning: mijlpaaldatums, ankerdatum ('vandaag')
en de tijdsvolgordelijkheid tussen disciplines (``discipline_venster``).

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import proces  # noqa: E402


class TestMijlpaalDatum(unittest.TestCase):
    def test_herkent_gangbare_formaten(self):
        self.assertEqual(proces._parse_mijlpaal_datum("15-06-2026"),
                         date(2026, 6, 15))
        self.assertEqual(proces._parse_mijlpaal_datum("2026-06-15"),
                         date(2026, 6, 15))
        self.assertEqual(proces._parse_mijlpaal_datum("15/06/2026"),
                         date(2026, 6, 15))

    def test_vrije_tekst_geeft_none(self):
        self.assertIsNone(proces._parse_mijlpaal_datum("medio 2026"))
        self.assertIsNone(proces._parse_mijlpaal_datum(""))
        self.assertIsNone(proces._parse_mijlpaal_datum(None))


class TestProjectStartDatum(unittest.TestCase):
    def test_zonder_activiteit_geen_ankerdatum(self):
        self.assertIsNone(proces.project_start_datum(
            {"stappen": {}, "meldingen": [], "tollgates": {}}))

    def test_vroegste_tijdstempel_over_alle_bronnen(self):
        state = {
            "stappen": {"IV-01": {"log": [{"tijd": "2026-03-10 09:00"}]}},
            "meldingen": [{"tijd": "2026-02-20 08:00"}],
            "tollgates": {"IV": {"tijd": "2026-04-01 10:00"}},
        }
        self.assertEqual(proces.project_start_datum(state), date(2026, 2, 20))


class TestWeekVoorDatum(unittest.TestCase):
    def test_startdatum_is_week_1(self):
        d = date(2026, 1, 1)
        self.assertAlmostEqual(proces._week_voor_datum(d, d), 1.0)

    def test_een_week_later_is_week_2(self):
        start = date(2026, 1, 1)
        self.assertAlmostEqual(
            proces._week_voor_datum(date(2026, 1, 8), start), 2.0)


class TestDisciplineVenster(unittest.TestCase):
    """Regressie voor de tijdsvolgordelijkheid: vóór deze wijziging liepen
    bijna alle disciplines (0.0, 1.0), dus gelijktijdig van start tot eind
    van de fase."""

    def test_fasen_met_meerdere_disciplines_starten_gespreid(self):
        for fase, venster in proces.ONTWERP_PLANNING["discipline_venster"].items():
            starts = {v[0] for v in venster.values()}
            self.assertGreater(len(starts), 1,
                               f"fase {fase}: alle disciplines starten gelijktijdig")

    def test_financieel_start_niet_voor_scope(self):
        for fase in ("VO", "DO", "UO"):
            venster = proces.ONTWERP_PLANNING["discipline_venster"][fase]
            if proces.D_SCOPE in venster and proces.D_FIN in venster:
                self.assertLessEqual(venster[proces.D_SCOPE][0],
                                     venster[proces.D_FIN][0])

    def test_vensters_binnen_faseduur(self):
        for fase, venster in proces.ONTWERP_PLANNING["discipline_venster"].items():
            for discipline, (v0, v1) in venster.items():
                self.assertGreaterEqual(v0, 0.0, f"{fase}/{discipline}")
                self.assertLessEqual(v1, 1.0, f"{fase}/{discipline}")
                self.assertLess(v0, v1, f"{fase}/{discipline}")


if __name__ == "__main__":
    unittest.main()
