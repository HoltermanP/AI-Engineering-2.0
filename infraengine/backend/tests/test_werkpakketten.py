"""Werkpakketten per station-paar, met en zonder gesloten ring.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString  # noqa: E402

from registers import build_werkpakketten  # noqa: E402

X, Y = 155000.0, 463000.0
# drie stations op een driehoek; het ringtracé loopt langs de zijden terug
# naar station 1
STATIONS = [(X, Y), (X + 300, Y), (X + 300, Y + 400)]


class WerkpakkettenTest(unittest.TestCase):
    def test_open_streng(self):
        route = LineString(STATIONS)
        wps = build_werkpakketten(route, STATIONS)
        self.assertEqual([w["naam"] for w in wps],
                         ["Station 1 – Station 2", "Station 2 – Station 3"])
        self.assertEqual([w["lengte_m"] for w in wps], [300.0, 400.0])
        self.assertEqual(wps[-1]["chainage_tot_m"], round(route.length, 1))

    def test_gesloten_ring(self):
        route = LineString(STATIONS + [STATIONS[0]])
        wps = build_werkpakketten(route, STATIONS, ring=True)
        self.assertEqual([w["nr"] for w in wps], ["WP-01", "WP-02", "WP-03"])
        self.assertEqual(wps[2]["naam"], "Station 3 – Station 1")
        self.assertEqual((wps[2]["van_station"], wps[2]["tot_station"]), (3, 1))
        # sluitende verbinding = schuine zijde (500 m), tot het einde van het tracé
        self.assertEqual(wps[2]["lengte_m"], 500.0)
        self.assertEqual(wps[2]["chainage_tot_m"], round(route.length, 1))
        # chainages sluiten op elkaar aan
        for a, b in zip(wps[:-1], wps[1:]):
            self.assertEqual(a["chainage_tot_m"], b["chainage_van_m"])

    def test_ring_met_twee_stations_blijft_een_streng(self):
        # ring is met twee stations niet zinvol (backend weigert dat al);
        # de werkpakketindeling mag er in elk geval niet op omvallen
        twee = STATIONS[:2]
        wps = build_werkpakketten(LineString(twee), twee, ring=True)
        self.assertEqual([w["naam"] for w in wps],
                         ["Station 1 – Station 2", "Station 2 – Station 1"])


if __name__ == "__main__":
    unittest.main()
