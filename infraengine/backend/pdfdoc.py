"""Minimale vector-PDF-schrijver voor werktekeningen (geen externe pakketten).

Alle maten in millimeter op het papier, oorsprong linksonder. Ondersteunt
lijnen/polylijnen/polygonen (lijndikte, kleur, streeppatroon, vulling),
cirkels, tekst in Helvetica/Helvetica-Bold (WinAnsi, dus é/ë/° werken) onder
elke hoek, uitlijning en clipping op een rechthoek.
"""
from __future__ import annotations

import math
import zlib

MM = 72.0 / 25.4

PAPIER = {  # liggend, mm
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

# Helvetica-breedtes (1/1000 em) voor ASCII 32–126
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
         1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
         333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def tekstbreedte(tekst: str, grootte_mm: float, vet: bool = False) -> float:
    """Breedte in mm van tekst met lettergrootte (kapitaalhoogte ≈ 0,72 em)."""
    tot = 0
    for ch in tekst:
        o = ord(ch)
        tot += _HELV[o - 32] if 32 <= o <= 126 else 556
    return tot / 1000.0 * grootte_mm * (1.06 if vet else 1.0)


def _kleur(c) -> tuple:
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return tuple(c)


_VERVANG = str.maketrans({"≥": ">=", "≤": "<=", "→": "->", "←": "<-", "≈": "~",
                          "−": "-", "·": "\u00b7", "✓": "v", "✕": "x", "²": "2", "³": "3"})


def _esc(tekst: str) -> str:
    b = tekst.translate(_VERVANG).encode("cp1252", errors="replace")
    uit = []
    for x in b:
        ch = chr(x)
        if ch in "()\\":
            uit.append("\\" + ch)
        elif x < 32 or x > 126:
            uit.append(f"\\{x:03o}")
        else:
            uit.append(ch)
    return "".join(uit)


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


class Pagina:
    def __init__(self, breedte_mm: float, hoogte_mm: float):
        self.w, self.h = breedte_mm, hoogte_mm
        self.ops: list[str] = []

    # ---- toestand
    def _stijl(self, dikte=0.25, kleur="#000000", streep=None, vul=None):
        r, g, b = _kleur(kleur)
        self.ops.append(f"{_f(dikte * MM)} w {_f(r)} {_f(g)} {_f(b)} RG")
        if streep:
            self.ops.append("[" + " ".join(_f(s * MM) for s in streep) + "] 0 d")
        else:
            self.ops.append("[] 0 d")
        if vul is not None:
            r, g, b = _kleur(vul)
            self.ops.append(f"{_f(r)} {_f(g)} {_f(b)} rg")

    def _pad(self, punten, sluit=False):
        if not punten:
            return False
        x, y = punten[0]
        self.ops.append(f"{_f(x * MM)} {_f(y * MM)} m")
        for x, y in punten[1:]:
            self.ops.append(f"{_f(x * MM)} {_f(y * MM)} l")
        if sluit:
            self.ops.append("h")
        return True

    def bewaar(self):
        self.ops.append("q")

    def herstel(self):
        self.ops.append("Q")

    def clip(self, x, y, w, h):
        self.ops.append(f"{_f(x * MM)} {_f(y * MM)} {_f(w * MM)} {_f(h * MM)} re W n")

    # ---- tekenen
    def lijn(self, punten, dikte=0.25, kleur="#000000", streep=None):
        if len(punten) < 2:
            return
        self._stijl(dikte, kleur, streep)
        self.ops.append("1 J 1 j")
        if self._pad(punten):
            self.ops.append("S")

    def vlak(self, punten, vul="#ffffff", rand=None, dikte=0.18, streep=None):
        if len(punten) < 3:
            return
        self._stijl(dikte, rand or vul, streep, vul)
        if self._pad(punten, sluit=True):
            self.ops.append("B" if rand else "f")

    def vlak_met_gaten(self, ringen, vul="#ffffff"):
        """Polygoon met binnenringen (even-odd vulling), zonder rand."""
        self._stijl(0.1, vul, None, vul)
        for ring in ringen:
            self._pad(ring, sluit=True)
        self.ops.append("f*")

    def rechthoek(self, x, y, w, h, dikte=0.25, kleur="#000000", vul=None):
        self._stijl(dikte, kleur, None, vul)
        self.ops.append(f"{_f(x * MM)} {_f(y * MM)} {_f(w * MM)} {_f(h * MM)} re "
                        + ("B" if vul is not None else "S"))

    def cirkel(self, cx, cy, r, dikte=0.25, kleur="#000000", vul=None, streep=None):
        k = 0.5523 * r
        self._stijl(dikte, kleur, streep, vul)
        p = lambda x, y: f"{_f((cx + x) * MM)} {_f((cy + y) * MM)}"
        self.ops.append(f"{p(r, 0)} m {p(r, k)} {p(k, r)} {p(0, r)} c "
                        f"{p(-k, r)} {p(-r, k)} {p(-r, 0)} c "
                        f"{p(-r, -k)} {p(-k, -r)} {p(0, -r)} c "
                        f"{p(k, -r)} {p(r, -k)} {p(r, 0)} c "
                        + ("B" if vul is not None else "S"))

    def tekst(self, x, y, tekst, grootte=2.5, hoek=0.0, uitlijn="links",
              vet=False, kleur="#000000", basis="onder"):
        """grootte = letterhoogte in mm (em); hoek in graden tegen de klok in.
        uitlijn: links/midden/rechts; basis: onder/midden/boven."""
        if not tekst:
            return
        breedte = tekstbreedte(tekst, grootte, vet)
        dx = {"links": 0.0, "midden": -breedte / 2, "rechts": -breedte}[uitlijn]
        dy = {"onder": 0.0, "midden": -grootte * 0.36, "boven": -grootte * 0.72}[basis]
        a = math.radians(hoek)
        ca, sa = math.cos(a), math.sin(a)
        tx = x + dx * ca - dy * sa
        ty = y + dx * sa + dy * ca
        r, g, b = _kleur(kleur)
        font = "/F2" if vet else "/F1"
        self.ops.append(
            f"BT {_f(r)} {_f(g)} {_f(b)} rg {font} {_f(grootte * MM)} Tf "
            f"{_f(ca)} {_f(sa)} {_f(-sa)} {_f(ca)} {_f(tx * MM)} {_f(ty * MM)} Tm "
            f"({_esc(tekst)}) Tj ET")

    def tekstblok(self, x, y, regels, grootte=2.2, regelafstand=1.35, **kw):
        for i, regel in enumerate(regels):
            self.tekst(x, y - i * grootte * regelafstand, regel, grootte, **kw)

    def pijl(self, a, b, dikte=0.25, kleur="#000000", kop=2.0):
        self.lijn([a, b], dikte, kleur)
        hoek = math.atan2(b[1] - a[1], b[0] - a[0])
        for d in (2.7, -2.7):
            self.lijn([b, (b[0] - kop * math.cos(hoek + d / 6), b[1] - kop * math.sin(hoek + d / 6))],
                      dikte, kleur)


class PdfDoc:
    def __init__(self, titel: str = "", auteur: str = "InfraEngine"):
        self.paginas: list[Pagina] = []
        self.titel, self.auteur = titel, auteur

    def pagina(self, papier: str = "A3") -> Pagina:
        w, h = PAPIER.get(papier, PAPIER["A3"])
        p = Pagina(w, h)
        self.paginas.append(p)
        return p

    def bytes(self) -> bytes:
        objs: list[bytes] = []

        def nieuw(inhoud: bytes) -> int:
            objs.append(inhoud)
            return len(objs)

        f1 = nieuw(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                   b"/Encoding /WinAnsiEncoding >>")
        f2 = nieuw(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                   b"/Encoding /WinAnsiEncoding >>")
        pages_id = len(objs) + 1
        objs.append(b"")  # placeholder Pages
        kids = []
        for p in self.paginas:
            stroom = zlib.compress("\n".join(p.ops).encode("latin-1"))
            c = nieuw(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stroom)
                      + stroom + b"\nendstream")
            pg = nieuw((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 "
                        f"{_f(p.w * MM)} {_f(p.h * MM)}] /Resources << /Font << /F1 {f1} 0 R "
                        f"/F2 {f2} 0 R >> >> /Contents {c} 0 R >>").encode())
            kids.append(pg)
        objs[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                              f"/Count {len(kids)} >>").encode()
        info = nieuw(f"<< /Title ({_esc(self.titel)}) /Producer ({_esc(self.auteur)}) >>".encode())
        cat = nieuw(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

        uit = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, 1):
            offsets.append(len(uit))
            uit += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(uit)
        uit += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            uit += f"{off:010d} 00000 n \n".encode()
        uit += (f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(uit)
