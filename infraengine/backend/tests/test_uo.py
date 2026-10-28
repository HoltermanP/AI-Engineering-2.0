"""Tests voor het uitvoeringsontwerp: RDNAPTRANS, inmeting, bladindeling,
revisiewolken en de PDF/DXF-werktekeningen (zonder netwerk).

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ezdxf  # noqa: E402
from shapely.geometry import LineString, Polygon, mapping  # noqa: E402

import dxf_export  # noqa: E402
import inmeting  # noqa: E402
import rdnap  # noqa: E402
import werktekening as wt  # noqa: E402

X, Y = 155000.0, 463000.0


class TestRdnap(unittest.TestCase):
    # referentie: PROJ 9 (cct) met nl_nsgi_rdtrans2018.tif, zie rdnap.py
    REF = [((52.155174, 5.387203), (154999.9555, 463000.1238)),
           ((53.2, 5.8), (182585.1740, 579340.4151)),
           ((51.4, 3.6), (30638.5937, 380507.0969)),
           ((52.5, 6.9), (257720.7110, 502440.5036))]

    def test_etrs89_naar_rd_gelijk_aan_proj(self):
        for (lat, lon), (x, y) in self.REF:
            r = rdnap.etrs89_naar_rd(lat, lon)
            self.assertTrue(r["nauwkeurig"])
            self.assertAlmostEqual(r["x"], x, delta=0.001)
            self.assertAlmostEqual(r["y"], y, delta=0.001)

    def test_heen_en_terug(self):
        for (lat, lon), _ in self.REF:
            r = rdnap.etrs89_naar_rd(lat, lon)
            t = rdnap.rd_naar_etrs89(r["x"], r["y"])
            self.assertAlmostEqual(t["lat"], lat, delta=1e-8)
            self.assertAlmostEqual(t["lon"], lon, delta=1e-8)

    def test_nap(self):
        # PROJ vgridshift nlgeo2018: N = 43,2772 m bij Amersfoort
        self.assertAlmostEqual(rdnap.nap_hoogte(52.155174, 5.387203, 43.2), -0.0772,
                               delta=0.001)

    def test_buiten_grid_terugval(self):
        r = rdnap.etrs89_naar_rd(50.0, 1.0)
        self.assertFalse(r["nauwkeurig"])


class TestInmeting(unittest.TestCase):
    def test_dxf_met_hoogte_tekst_en_blok(self):
        d = ezdxf.new("R2010")
        m = d.modelspace()
        m.add_polyline3d([(X, Y, 3.1), (X + 20, Y, 3.2)], dxfattribs={"layer": "BAND"})
        m.add_text("PUT", height=0.3).set_placement((X + 5, Y + 2))
        blk = d.blocks.new("BOOM")
        blk.add_circle((0, 0), 0.5)
        m.add_blockref("BOOM", (X + 8, Y + 4, 2.9))
        buf = io.StringIO()
        d.write(buf)
        feats = inmeting._lees_dxf(buf.getvalue().encode())
        band = next(f for f in feats if f["l"] == "BAND")
        self.assertEqual(band["g"]["coordinates"][0], [X, Y, 3.1])
        self.assertTrue(any(f.get("t") == "PUT" for f in feats))
        self.assertTrue(any(f.get("b") == "BOOM" and f.get("z") == 2.9 for f in feats))

    def test_punten_rd_zonder_kop(self):
        feats = inmeting._lees_punten(f"1;{X};{Y};2.50;put\n2;{X + 1};{Y};2.6;band".encode())
        self.assertEqual(feats[0]["g"]["coordinates"], [X, Y])
        self.assertEqual(feats[0]["z"], 2.5)
        self.assertEqual(feats[1]["l"], "band")

    def test_geen_rd_geweigerd(self):
        feats = inmeting._lees_punten(b"1;100;200;0;x")
        with self.assertRaises(inmeting.InmetingError):
            inmeting._controleer_rd(feats)


def _variant(punten):
    route = LineString([(X + dx, Y + dy) for dx, dy in punten])
    return {"naam": "test", "route": mapping(route), "lengte_m": route.length,
            "boringen": [], "kruisingen": [], "moffen": [], "segmenten": [
                {"ligging": "berm / groenstrook"}, {"ligging": "rijbaan"}],
            "werkpakketten": [{"nr": "WP-01", "chainage_van_m": 0,
                               "chainage_tot_m": route.length}]}


class TestBladindeling(unittest.TestCase):
    def test_hele_route_gedekt(self):
        v = _variant([(0, 0), (300, 0), (300, 200), (700, 250)])
        bladen = wt.bladindeling(v)
        self.assertGreater(len(bladen), 2)
        self.assertEqual(bladen[0]["van_m"], 0)
        self.assertAlmostEqual(bladen[-1]["tot_m"], LineString(
            v["route"]["coordinates"]).length, delta=0.05)
        route = LineString(v["route"]["coordinates"])
        for b in bladen:  # elk stuk ligt binnen zijn blad
            stuk = wt._substring(route, b["van_m"], b["tot_m"])
            self.assertTrue(Polygon(b["polygoon"]).buffer(0.01).contains(stuk))
        for a, b in zip(bladen, bladen[1:]):  # overlap, geen gat
            self.assertLessEqual(b["van_m"], a["tot_m"])

    def test_schaal_bepaalt_aantal(self):
        v = _variant([(0, 0), (1000, 0)])
        v["tekeningen"] = {"instellingen": {"schaal": 250}}
        n250 = len(wt.bladindeling(v))
        v["tekeningen"] = {"instellingen": {"schaal": 1000}}
        n1000 = len(wt.bladindeling(v))
        self.assertGreater(n250, n1000)

    def test_transform_draait_tracé_horizontaal(self):
        v = _variant([(0, 0), (100, 100)])
        b = wt.bladindeling(v)[0]
        T = wt.Transform(b, wt.kaartvak("A3"), 500)
        a, c = T(X, Y), T(X + 100, Y + 100)
        self.assertAlmostEqual(a[1], c[1], delta=1e-6)


class TestRevisies(unittest.TestCase):
    def test_afronding_geeft_geen_wolk(self):
        v1 = _variant([(0, 0), (50.004, 0), (100, 0)])
        v2 = _variant([(0, 0), (50.0, 0), (100, 0)])
        self.assertEqual(wt.wijzigingen(wt._snapshot(v1), wt._snapshot(v2)), [])

    def test_verlegd_punt_geeft_wolk_ter_plaatse(self):
        v = _variant([(0, 0), (50, 0), (100, 0), (400, 0)])
        wt.nieuwe_revisie(v, "eerste uitgave", "PH")
        v2 = _variant([(0, 0), (50, 1.5), (100, 0), (400, 0)])
        v2["tekeningen"] = v["tekeningen"]
        self.assertTrue(wt.gewijzigd_na_revisie(v2))
        r = wt.nieuwe_revisie(v2, "verlegd", "PH")
        self.assertEqual(r["rev"], "B")
        self.assertEqual(len(r["wolken"]), 1)
        self.assertTrue(Polygon(r["wolken"][0]).contains(
            LineString([(X + 50, Y + 1.5), (X + 50, Y + 0.5)])))
        self.assertNotIn(len(wt.bladindeling(v2)), r["bladen_gewijzigd"][1:])

    def test_status_akkoord(self):
        v = _variant([(0, 0), (100, 0)])
        wt.nieuwe_revisie(v, "", "", definitief=True)
        r = wt.zet_status(v, "0", "akkoord", "J. Jansen")
        self.assertEqual(r["akkoord_door"], "J. Jansen")
        with self.assertRaises(ValueError):
            wt.zet_status(v, "0", "onzin")


class TestUitvoer(unittest.TestCase):
    def test_pdf_set(self):
        v = _variant([(0, 0), (200, 0), (200, 150)])
        data = wt.maak_pdf(v, [[X, Y]], "Test", lambda bb: {}, {"datum": "2026-10-28"})
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        n = len(wt.bladindeling(v))
        self.assertEqual(data.count(b"/Type /Page "), 1 + n + 1 + 1)  # overzicht, situatie, DP, UZ

    def test_dxf_layouts_en_laagnamen(self):
        v = _variant([(0, 0), (200, 0)])
        v["segmenten"] = []
        inst = wt.instellingen(v)
        bladen = wt.bladindeling(v, inst)
        data = dxf_export.maak_werktekening_dxf(v, [[X, Y]], [], "Test", bladen, inst,
                                                lambda bb: {}, [], [], wt.km_tekst)
        doc = ezdxf.read(io.StringIO(data.decode()))
        self.assertEqual(len(doc.audit().errors), 0)
        self.assertIn("MS_TRACE", doc.layers)
        self.assertIn("Blad 001", doc.layouts.names())
        vp = [e for e in doc.layouts.get("Blad 001") if e.dxftype() == "VIEWPORT"
              and e.dxf.id != 1]
        self.assertEqual(len(vp), 1)

    def test_uitzetpunten(self):
        v = _variant([(0, 0), (30, 40), (60, 40)])
        p = wt.uitzetpunten(v)
        self.assertEqual(p[1]["metrering_m"], 50.0)
        self.assertEqual(p[1]["knik_gr"], 53.1)


if __name__ == "__main__":
    unittest.main()
