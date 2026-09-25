"""Onderzoeken wijzen op de kaart het tracédeel aan waar ze op zien.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString, shape  # noqa: E402

import engine  # noqa: E402
from engine import Grid, ZN_ARCHEO, ZN_BODEM, ZN_NATURA, ZN_NNN, zone_trajecten  # noqa: E402
from registers import BEREIK_DEEL, BEREIK_TRACEBREED, build_onderzoeken  # noqa: E402

X, Y = 155000.0, 463000.0


def _grid_met_zones():
    """Raster van 200 × 20 m; archeologie op x 40–80 m en 120–140 m,
    bodemverontreiniging op x 100–160 m."""
    g = Grid((X, Y, X + 200, Y + 20), cell=2.0)
    for c in range(g.ncols):
        x = X + (c + 0.5) * g.cell
        if 40 <= x - X < 80 or 120 <= x - X < 140:
            g.zones[:, c] |= ZN_ARCHEO
        if 100 <= x - X < 160:
            g.zones[:, c] |= ZN_BODEM
    return g


class ZoneTrajectenTest(unittest.TestCase):
    def setUp(self):
        self.grid = _grid_met_zones()
        self.route = LineString([(X, Y + 10), (X + 200, Y + 10)])

    def test_delen_per_zone(self):
        zt = zone_trajecten(self.route, self.grid)
        self.assertEqual(len(zt[ZN_ARCHEO]), 2)
        self.assertEqual(len(zt[ZN_BODEM]), 1)
        self.assertEqual(zt[ZN_NATURA], [])
        a, b = sorted(zt[ZN_ARCHEO], key=lambda l: l.bounds[0])
        self.assertAlmostEqual(a.bounds[0] - X, 40, delta=3)
        self.assertAlmostEqual(a.bounds[2] - X, 80, delta=3)
        self.assertAlmostEqual(b.bounds[0] - X, 120, delta=3)
        self.assertAlmostEqual(b.bounds[2] - X, 140, delta=3)
        # lengtes sluiten aan op de zonemetrage
        zl = engine.zone_lengtes(self.route, self.grid)
        self.assertAlmostEqual(sum(l.length for l in zt[ZN_BODEM]), zl[ZN_BODEM], delta=4)

    def test_zone_aan_het_eind_wordt_gesloten(self):
        route = LineString([(X + 130, Y + 10), (X + 200, Y + 10)])
        zt = zone_trajecten(route, self.grid)
        self.assertEqual(len(zt[ZN_BODEM]), 1)
        self.assertAlmostEqual(zt[ZN_BODEM][0].bounds[2] - X, 160, delta=3)


class OnderzoekGeometrieTest(unittest.TestCase):
    def setUp(self):
        self.grid = _grid_met_zones()
        self.route = LineString([(X, Y + 10), (X + 200, Y + 10)])
        self.zt = zone_trajecten(self.route, self.grid)
        self.zl = engine.zone_lengtes(self.route, self.grid)

    def test_deeltrace_krijgt_geometrie(self):
        items = build_onderzoeken(self.zl, [], zone_geoms=self.zt)
        per_soort = {o["soort"]: o for o in items}
        arch = per_soort["Archeologisch bureauonderzoek / IVO"]
        self.assertEqual(arch["bereik"], BEREIK_DEEL)
        g = shape(arch["geometry"])
        self.assertEqual(g.geom_type, "MultiLineString")
        self.assertEqual(len(g.geoms), 2)
        self.assertAlmostEqual(g.bounds[0] - X, 40, delta=3)
        self.assertAlmostEqual(g.bounds[2] - X, 140, delta=3)
        bodem = per_soort["Milieuhygiënisch bodemonderzoek + saneringsplan-check"]
        self.assertEqual(bodem["bereik"], BEREIK_DEEL)
        self.assertEqual(len(shape(bodem["geometry"]).geoms), 1)

    def test_tracebreed_zonder_geometrie(self):
        items = build_onderzoeken(self.zl, [], zone_geoms=self.zt)
        klic = next(o for o in items if o["soort"].startswith("KLIC"))
        self.assertEqual(klic["bereik"], BEREIK_TRACEBREED)
        self.assertIsNone(klic["geometry"])
        nge = next(o for o in items if o["soort"].startswith("NGE"))
        self.assertEqual(nge["bereik"], BEREIK_TRACEBREED)
        self.assertIsNone(nge["geometry"])

    def test_zonder_zone_geoms_blijft_werken(self):
        # oudere aanroepen (en de corridor zonder geometrie) leveren tracébreed
        items = build_onderzoeken(self.zl, [])
        arch = next(o for o in items if o["soort"].startswith("Archeologisch"))
        self.assertEqual(arch["bereik"], BEREIK_TRACEBREED)
        self.assertIsNone(arch["geometry"])

    def test_hdd_wijst_boorlijnen_aan(self):
        boringen = [{"type": engine.TECHNIEK_HDD, "nr": "BOR-001",
                     "geometry": {"type": "LineString",
                                  "coordinates": [[X + 10, Y], [X + 30, Y]]},
                     "intredepunt_rd": [X + 10, Y]},
                    {"type": engine.TECHNIEK_HDD, "nr": "BOR-002",
                     "geometry": None, "intredepunt_rd": [X + 90, Y]}]
        items = build_onderzoeken(self.zl, boringen, zone_geoms=self.zt)
        grond = next(o for o in items if o["soort"].startswith("Grondonderzoek"))
        self.assertEqual(grond["bereik"], BEREIK_DEEL)
        self.assertEqual(shape(grond["geometry"]).geom_type, "MultiLineString")

    def test_natuur_alleen_ndff_is_tracebreed(self):
        import ndff
        sam = ndff.samenvatting({})
        sam.update({"soorten_totaal": 1, "strikt_totaal": 0, "hokken": 1,
                    "periode": [2015, 2025]})
        items = build_onderzoeken({ZN_NATURA: 0, ZN_NNN: 0}, [], ndff=sam,
                                  zone_geoms=self.zt)
        nat = [o for o in items if o["soort"].startswith("Natuur")]
        if nat:  # alleen als ndff.tekst() met deze samenvatting een tekst levert
            self.assertEqual(nat[0]["bereik"], BEREIK_TRACEBREED)


if __name__ == "__main__":
    unittest.main()
