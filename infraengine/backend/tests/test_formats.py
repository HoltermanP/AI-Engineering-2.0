"""Documentformats: upload per documentsoort; met format volgt het document
die opbouw, zonder format bepaalt de AI de opbouw.

Draaien met::

    ./.venv/bin/python -m unittest discover -s backend/tests -v
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import formats  # noqa: E402


def _docx(tekst: str) -> bytes:
    """Minimaal .docx met één alinea per regel."""
    body = "".join(f"<w:p><w:r><w:t>{r}</w:t></w:r></w:p>" for r in tekst.splitlines())
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


class FormatsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._oud = formats.FM_DIR
        formats.FM_DIR = Path(self._tmp.name)

    def tearDown(self):
        formats.FM_DIR = self._oud
        self._tmp.cleanup()

    def test_catalogus_dekt_alle_documentsoorten(self):
        keys = {it["key"] for g in formats.catalogus() for it in g["items"]}
        self.assertIn("nota:VO", keys)
        self.assertIn("nota:UO", keys)
        self.assertIn("proces:intake", keys)
        self.assertIn("proces:verkeersplan", keys)
        self.assertTrue(any(k.startswith("bureau:") for k in keys))
        self.assertEqual(formats.bureau_sleutel("Natuur-quickscan"),
                         "bureau:natuur-quickscan")
        self.assertIsNone(formats.bureau_sleutel("KLIC-oriëntatiemelding"))

    def test_zonder_format_geen_blokken(self):
        self.assertIsNone(formats.blokken("nota:VO"))
        self.assertFalse(formats.heeft_format("nota:VO"))

    def test_upload_md_en_docx_geeft_formatblokken(self):
        formats.bewaar("nota:VO", "VO-sjabloon.md", b"# Nota\n## 1. Doel\n## 2. Scope\n")
        b = formats.blokken("nota:VO")
        self.assertEqual(len(b), 1)
        self.assertIn("## 2. Scope", b[0]["text"])
        self.assertIn("FORMAT", b[0]["text"])
        formats.bewaar("proces:intake", "intake.docx", _docx("Intakeverslag\n1 Aanleiding\n2 Scope"))
        b = formats.blokken("proces:intake")
        self.assertIn("2 Scope", b[0]["text"])
        o = formats.overzicht()
        self.assertEqual(o["aantal"], 2)
        met = {it["key"]: it["format"] for g in o["groepen"] for it in g["items"]}
        self.assertEqual(met["nota:VO"]["bestandsnaam"], "VO-sjabloon.md")
        self.assertIsNone(met["nota:DO"])

    def test_pdf_als_documentblok(self):
        formats.bewaar("nota:DO", "DO.pdf", b"%PDF-1.4 fake")
        b = formats.blokken("nota:DO")
        self.assertEqual(b[0]["type"], "document")
        self.assertEqual(b[1]["type"], "text")

    def test_vervangen_en_verwijderen(self):
        formats.bewaar("nota:VO", "a.md", b"# A")
        formats.bewaar("nota:VO", "b.txt", b"B-format")
        self.assertEqual(formats.lijst()["nota:VO"]["bestandsnaam"], "b.txt")
        self.assertIn("B-format", formats.blokken("nota:VO")[0]["text"])
        formats.verwijder("nota:VO")
        self.assertIsNone(formats.blokken("nota:VO"))
        with self.assertRaises(formats.FormatError):
            formats.verwijder("nota:VO")

    def test_afwijzingen(self):
        with self.assertRaises(formats.FormatError):
            formats.bewaar("nota:XX", "a.md", b"# A")
        with self.assertRaises(formats.FormatError):
            formats.bewaar("nota:VO", "a.exe", b"x")
        with self.assertRaises(formats.FormatError):
            formats.bewaar("nota:VO", "leeg.txt", b"   \n")
        self.assertFalse(formats.heeft_format("nota:VO"))


if __name__ == "__main__":
    unittest.main()
