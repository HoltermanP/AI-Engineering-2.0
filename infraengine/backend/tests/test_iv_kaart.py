"""IV → kaart: voorstel opbouwen uit een AI-extractie (zonder netwerk) en
doorzetten naar het procesdossier (mijlpalen, risico's, stations)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iv_kaart  # noqa: E402
import proces  # noqa: E402

PUNTEN = {
    "spannenburg": (176800.0, 548900.0),
    "woudsend": (172300.0, 553900.0),
    "koufurderrige": (174100.0, 555200.0),
    "sint nicolaasga": (181600.0, 551400.0),
}


def nep_geocoder(zoekterm, *, plaats="", gemeente="", provincie="",
                 alternatief="", adres=False):
    sleutel = (zoekterm or plaats).lower().split(",")[0].strip()
    if adres:
        if "de zwaan" in sleutel:
            return {"x": 172350.0, "y": 553950.0, "weergavenaam": f"{zoekterm}, {plaats}",
                    "type": "adres", "status": "gevonden", "opmerking": ""}
        return {"status": "niet_gevonden", "opmerking": "geen treffer"}
    for naam, xy in PUNTEN.items():
        if naam in sleutel or (plaats and naam in plaats.lower()):
            return {"x": xy[0], "y": xy[1], "weergavenaam": naam.title(),
                    "type": "woonplaats", "status": "gevonden", "opmerking": ""}
    return {"status": "niet_gevonden", "opmerking": "geen treffer"}


EXTRACTIE = {
    "project": {"titel": "IV E MS Uitbreiding 20 kV", "opdrachtgever": "Liander",
                "budget_keur": None, "gewenste_ibn": "Q4 2025"},
    "samenvatting": "Backbone 20 kV rond Spannenburg.",
    "knooppunten": [
        {"id": "RS Spannenburg", "soort": "RS", "zoekterm": "Spannenburg", "plaats": "Spannenburg"},
        {"id": "DR06", "soort": "DR", "zoekterm": "Woudsend", "plaats": "Woudsend"},
        {"id": "DR07", "soort": "DR", "zoekterm": "Woudsend", "plaats": "Woudsend"},
        {"id": "DR05", "soort": "DR", "zoekterm": "Koufurderrige", "plaats": "Koufurderrige"},
        {"id": "DR03", "soort": "DR", "zoekterm": "Sint Nicolaasga", "plaats": "Sint Nicolaasga"},
        {"id": "DR99", "soort": "DR", "zoekterm": "Onbekendhuizen", "plaats": ""},
    ],
    "verbindingen": [
        {"naam": "Westelijke subring", "soort": "ring", "kabel": "630 mm2 Al",
         "knooppunten": ["RS Spannenburg", "DR05", "DR06", "DR07", "DR99"]},
        {"naam": "Spaak", "soort": "spaak", "knooppunten": ["RS Spannenburg", "DR03"]},
    ],
    "klantlocaties": [
        {"nr": "2", "soort": "ODN", "adres": "De Zwaan 34", "plaats": "Woudsend",
         "vermogen": "0 -> 130 kW", "status": "GTV verhoging"},
        {"nr": "9", "soort": "ODN", "adres": "Bouwen 17", "plaats": "St. Nicolaasga"},
    ],
    "hoeveelheden": [
        {"omschrijving": "630 mm2 Al backbone", "aantal": 43225, "eenheid": "m"},
        {"omschrijving": "240 mm2 Al", "aantal": 7375, "eenheid": "m"},
        {"omschrijving": "DR", "aantal": 12, "eenheid": "stuks"},
    ],
    "mijlpalen": [{"naam": "IBN aanpassingen infrastructuur", "datum": "Q4 2025",
                   "veld": "ibn_datum"},
                  {"naam": "IV beoordelen MT", "datum": "Juli 2023", "veld": ""}],
    "uitgangspunten": ["Toekomstvast tot 2035"],
    "risicos": [{"omschrijving": "Meelegkansen telecom onbekend",
                 "oorzaak": "Geen terugkoppeling", "gevolg": "Latere IBN",
                 "intern_extern": "Extern", "maatregel": "Afstemmen"}],
    "ontbrekend": ["Geen coördinaten van de DR-en"],
}


class VoorstelTest(unittest.TestCase):
    def setUp(self):
        self.v = iv_kaart.bouw_voorstel(EXTRACTIE, geocoder=nep_geocoder)

    def test_knooppunten_status_en_dubbelen(self):
        per_id = {k["id"]: k for k in self.v["knooppunten"]}
        self.assertEqual(per_id["RS Spannenburg"]["status"], "gevonden")
        self.assertEqual(per_id["DR99"]["status"], "niet_gevonden")
        # DR06 en DR07 liggen in hetzelfde dorp: tweede punt verschoven + onzeker
        self.assertEqual(per_id["DR06"]["status"], "gevonden")
        self.assertEqual(per_id["DR07"]["status"], "onzeker")
        self.assertAlmostEqual(per_id["DR07"]["x"] - per_id["DR06"]["x"],
                               iv_kaart.DUBBEL_OFFSET_M)
        s = self.v["statistiek"]
        self.assertEqual((s["gevonden"], s["onzeker"], s["niet_gevonden"]), (4, 1, 1))

    def test_ring_sluit_en_ontbrekend(self):
        ring = self.v["verbindingen"][0]
        # RS, DR05, DR06, DR07 + terug naar RS; DR99 ontbreekt
        self.assertEqual(len(ring["stations"]), 5)
        self.assertEqual(ring["stations"][0], ring["stations"][-1])
        self.assertEqual(ring["labels"][0], "RS Spannenburg")
        self.assertEqual(ring["ontbrekend"], ["DR99"])
        self.assertGreater(ring["hemelsbreed_m"], 10000)
        spaak = self.v["verbindingen"][1]
        self.assertEqual(len(spaak["stations"]), 2)

    def test_klanten_en_hoeveelheden(self):
        self.assertEqual(self.v["statistiek"]["klanten"], 2)
        self.assertEqual(self.v["statistiek"]["klanten_gevonden"], 1)
        self.assertEqual(self.v["kabel_m_totaal"], 50600)
        self.assertEqual(self.v["mijlpalen"][0]["veld"], "ibn_datum")


class ToepassenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.oud = (proces.PROCES_DIR, proces.CONFIG_PAD)
        proces.PROCES_DIR = Path(self.tmp.name) / "proces"
        proces.CONFIG_PAD = Path(self.tmp.name) / "proces_config.json"
        state = proces.laad_state("iv-test")
        state["iv"] = iv_kaart.bouw_voorstel(EXTRACTIE, geocoder=nep_geocoder)
        proces.bewaar_state(state)

    def tearDown(self):
        proces.PROCES_DIR, proces.CONFIG_PAD = self.oud
        self.tmp.cleanup()

    def test_toepassen_vult_proces_en_geeft_stations(self):
        r = proces.iv_kaart_toepassen("iv-test", 0)
        self.assertEqual(len(r["stations"]), 5)
        self.assertEqual(len(r["klantlocaties"]), 1)
        self.assertEqual(r["risicos_toegevoegd"], 1)
        g = proces.laad_gegevens("iv-test")
        self.assertEqual(g["mijlpalen"]["ibn_datum"], "Q4 2025")
        self.assertEqual(g["verificatie"]["opdrachtgever"], "Liander")
        state = proces.laad_state("iv-test")
        self.assertEqual(state["risico"][0]["bron"], "IV")
        self.assertEqual(state["iv"]["toegepast"]["verbinding"], 0)
        # tweede keer: geen dubbele risico's, mijlpaal blijft staan
        r2 = proces.iv_kaart_toepassen("iv-test", 1)
        self.assertEqual(r2["risicos_toegevoegd"], 0)
        self.assertEqual(len(proces.laad_state("iv-test")["risico"]), 1)

    def test_referentie_in_raming(self):
        tekst = proces.iv_referentie_tekst("iv-test", 45000, 5_000_000)
        self.assertIn("50.600 m", tekst)
        self.assertIn("-11%", tekst)
        self.assertNotIn("IV-budget", tekst)  # budget ontbreekt in dit IV

    def test_overzicht_meldt_voorstel(self):
        s = proces.iv_voorstel_samenvatting(proces.laad_state("iv-test"))
        self.assertEqual(s["verbindingen"], ["Westelijke subring", "Spaak"])
        self.assertEqual(s["statistiek"]["knooppunten"], 6)


class LocatieserverParseTest(unittest.TestCase):
    def test_parse_rd(self):
        self.assertEqual(iv_kaart._parse_rd("POINT(176812.345 548901.2)"),
                         (176812.35, 548901.2))
        self.assertIsNone(iv_kaart._parse_rd("onzin"))


if __name__ == "__main__":
    unittest.main()
