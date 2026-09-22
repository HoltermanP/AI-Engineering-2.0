"""Gantt-afbeeldingen (JPEG) voor de ontwerp- en uitvoeringsplanning in de
ontwikkelnota's, naar analogie van ``kaart.py`` (dat de tracékaarten
rendert). Geen kaartondergrond/WMS nodig: x-as = weeknummer, y-as =
fase/discipline- resp. werkpakketrijen.

Twee beelden:

  - ``ontwerpplanning_kaart``    — de fasen IV/VO/DO/UO/NAO met hun
    tollgate-mijlpaal, en per fase een dunnere rij per discipline
    (``proces.bouw_ontwerpplanning``).
  - ``uitvoeringsplanning_kaart`` — per werkpakket de aaneengesloten
    subfasen van de bouw (``registers.build_uitvoeringsplanning``).

Ingebed in de Word-nota's (``nota.py`` vervangt de ``[AFBEELDING: …]``
-markers) en geserveerd via ``/api/kaart/planning-*.jpg`` voor de live
nota-preview in de frontend.
"""
from __future__ import annotations

from kaart import BEELD_W, _BALK_H, _INK, _RAND, _afronden
from zro import _font

_ACHTERGROND = (247, 248, 246, 255)
_RASTER = (221, 225, 222, 255)
_WIT = (255, 255, 255, 235)

# fase-/rijstatus -> kleur (ontwerpplanning)
_STATUS_KLEUR = {
    "afgerond": (13, 148, 136, 255),    # teal
    "actief": (200, 50, 43, 255),       # InfraEngine-rood
    "wachtend": (150, 156, 165, 220),   # grijs
    "uit": (196, 201, 206, 180),
    "gepland": (37, 99, 235, 255),      # milestone-rijen (GSU/IBN)
}

# subfase -> kleur (uitvoeringsplanning)
_SUBFASE_KLEUR = {
    "Mobilisatie/inrichting werkterrein": (150, 156, 165, 255),
    "Boring/persing": (124, 92, 240, 255),
    "Grondwerk (open sleuf)": (199, 126, 20, 255),
    "Kabelwerk/montage": (200, 50, 43, 255),
    "Herstelwerk": (37, 99, 235, 255),
    "Oplevering/keuring": (13, 148, 136, 255),
}

_MARGE_L, _MARGE_R = 320, 50
_MARGE_T, _MARGE_B = 40, 60
_RIJ_H_HOOFD = 44
_RIJ_H_SUB = 26


def _canvas(h: int):
    from PIL import Image
    return Image.new("RGBA", (BEELD_W, h), _ACHTERGROND)


def _kort(draw, tekst: str, max_w: float, font) -> str:
    """Tekst inkorten met '…' tot hij binnen max_w breedte past."""
    if draw.textlength(tekst, font=font) <= max_w:
        return tekst
    while tekst and draw.textlength(tekst + "…", font=font) > max_w:
        tekst = tekst[:-1]
    return tekst + "…"


def _x(wk: int, max_wk: int) -> float:
    breedte = BEELD_W - _MARGE_L - _MARGE_R
    return _MARGE_L + breedte * (wk / max_wk if max_wk else 0)


def _weekas(draw, y_top: float, y_bot: float, max_wk: int) -> None:
    stap = max(1, round(max_wk / 10 / 5) * 5) if max_wk > 10 else max(1, max_wk // 6 or 1)
    wk = 0
    while wk <= max_wk:
        x = _x(wk, max_wk)
        draw.line([x, y_top, x, y_bot], fill=_RASTER, width=1)
        draw.text((x, y_bot + 8), f"wk {wk}", font=_font(15), fill=_INK,
                  anchor="ma")
        wk += stap


def _balk(draw, y: float, h: float, wk0: int, wk1: int, max_wk: int, kleur) -> None:
    x0, x1 = _x(wk0 - 1, max_wk), _x(wk1, max_wk)
    draw.rounded_rectangle([x0, y, max(x1, x0 + 4), y + h], radius=min(6, h / 2),
                           fill=kleur, outline=_WIT, width=1)


def _ruit(draw, x: float, y: float, r: float, kleur) -> None:
    draw.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)],
                 fill=kleur, outline=(255, 255, 255, 255), width=2)


def ontwerpplanning_kaart(rijen: list[dict], projectnaam: str = "") -> bytes:
    """Fasebalken IV→VO→DO→UO→NAO met tollgate-mijlpaal, en per fase een
    dunnere rij per discipline; GSU/IBN als losse mijlpalen onderaan."""
    from PIL import ImageDraw

    fase_rijen = [r for r in rijen if r["fase"] and not r["discipline"]]
    detail_rijen = [r for r in rijen if r["fase"] and r["discipline"]]
    mijlpaal_rijen = [r for r in rijen if not r["fase"]]
    max_wk = max([r["eind_wk"] for r in rijen] + [1])

    legenda_h = 3 * 24 + 16
    hoogte = _MARGE_T + legenda_h
    layout = []  # (rij, y, h, hoofd)
    for f in fase_rijen:
        layout.append((f, hoogte, _RIJ_H_HOOFD, True))
        hoogte += _RIJ_H_HOOFD + 6
        for d in detail_rijen:
            if d["fase"] != f["fase"]:
                continue
            layout.append((d, hoogte, _RIJ_H_SUB, False))
            hoogte += _RIJ_H_SUB + 4
        hoogte += 8
    if mijlpaal_rijen:
        hoogte += 10
    y_as_bot = hoogte + (24 if mijlpaal_rijen else 0)
    hoogte = y_as_bot + _MARGE_B + _BALK_H

    kaart = _canvas(int(hoogte))
    draw = ImageDraw.Draw(kaart)
    _weekas(draw, _MARGE_T + legenda_h - 10, y_as_bot, max_wk)

    for rij, y, h, hoofd in layout:
        kleur = _STATUS_KLEUR.get(rij["status"], _STATUS_KLEUR["wachtend"])
        _balk(draw, y, h, rij["start_wk"], rij["eind_wk"], max_wk, kleur)
        label = rij["fase_naam"] if hoofd else f"› {rij['discipline']}"
        lfont = _font(17 if hoofd else 14, bold=hoofd)
        label = _kort(draw, label, _MARGE_L - 24, lfont)
        draw.text((_MARGE_L - 12, y + h / 2), label, font=lfont, fill=_INK,
                  anchor="rm")
        if hoofd and rij.get("tollgate"):
            xr = _x(rij["eind_wk"], max_wk)
            _ruit(draw, xr, y + h / 2, 9, _INK)
            draw.text((xr + 14, y + h / 2), rij["tollgate"], font=_font(16, bold=True),
                      fill=_INK, anchor="lm")

    for i, r in enumerate(mijlpaal_rijen):
        x = _x(r["start_wk"], max_wk)
        y = y_as_bot - 10
        draw.line([x, _MARGE_T + legenda_h - 10, x, y],
                 fill=_STATUS_KLEUR["gepland"], width=2)
        _ruit(draw, x, y, 8, _STATUS_KLEUR["gepland"])
        draw.text((x, y - 14), f"{r['toelichting']}: {r['mijlpaal_waarde']}",
                  font=_font(15, bold=True), fill=_INK, anchor="ms")

    # legenda statuskleuren (gereserveerde balk bovenaan, boven de fasebalken)
    legenda = [("afgerond", "Afgerond"), ("actief", "Actief/lopend"),
              ("wachtend", "Wachtend op vorige fase")]
    lx, ly = _RAND + 8, _RAND + 8
    draw.rectangle([lx - 4, ly - 4, lx + 260, ly + len(legenda) * 24],
                   fill=(255, 255, 255, 210))
    for i, (k, tekst) in enumerate(legenda):
        y = ly + i * 24
        draw.rectangle([lx, y, lx + 22, y + 14], fill=_STATUS_KLEUR[k])
        draw.text((lx + 30, y + 7), tekst, font=_font(15), fill=_INK, anchor="lm")

    titel = "Ontwerpplanning — IV t/m NAO" + (f" · {projectnaam}" if projectnaam else "")
    ondertitel = "indicatief, in weken vanaf projectstart"
    return _afronden(kaart, titel, ondertitel)


def uitvoeringsplanning_kaart(planning: list[dict], werkpakketten: list[dict],
                              projectnaam: str = "") -> bytes:
    """Per werkpakket één rij met de aaneengesloten subfasen van de bouw."""
    from PIL import ImageDraw

    volgorde = [w["nr"] for w in werkpakketten] or sorted(
        {r["werkpakket"] for r in planning})
    per_wp: dict = {}
    for r in planning:
        per_wp.setdefault(r["werkpakket"], []).append(r)
    max_wk = max([r["eind_wk"] for r in planning] + [1])

    legenda_h = len(_SUBFASE_KLEUR) * 22 + 12
    hoogte = _MARGE_T + legenda_h
    layout = []
    for wp in volgorde:
        layout.append((wp, hoogte))
        hoogte += _RIJ_H_HOOFD + 10
    y_as_bot = hoogte
    hoogte = y_as_bot + _MARGE_B + _BALK_H

    kaart = _canvas(int(hoogte))
    draw = ImageDraw.Draw(kaart)
    _weekas(draw, _MARGE_T + legenda_h - 10, y_as_bot, max_wk)

    for wp, y in layout:
        naam = next((w["naam"] for w in werkpakketten if w["nr"] == wp), "")
        nfont = _font(13)
        draw.text((_MARGE_L - 12, y + _RIJ_H_HOOFD / 2 - 9), wp,
                  font=_font(18, bold=True), fill=_INK, anchor="rm")
        if naam:
            draw.text((_MARGE_L - 12, y + _RIJ_H_HOOFD / 2 + 10),
                      _kort(draw, naam, _MARGE_L - 24, nfont), font=nfont,
                      fill=_INK, anchor="rm")
        for r in per_wp.get(wp, []):
            kleur = _SUBFASE_KLEUR.get(r["subfase"], _STATUS_KLEUR["wachtend"])
            _balk(draw, y, _RIJ_H_HOOFD, r["start_wk"], r["eind_wk"], max_wk, kleur)

    # legenda subfasen (gereserveerde balk bovenaan)
    lx, ly = _RAND + 8, _RAND + 8
    draw.rectangle([lx - 4, ly - 4, lx + 260, ly + len(_SUBFASE_KLEUR) * 22],
                   fill=(255, 255, 255, 210))
    for i, (subfase, kleur) in enumerate(_SUBFASE_KLEUR.items()):
        y = ly + i * 22
        draw.rectangle([lx, y, lx + 22, y + 13], fill=kleur)
        draw.text((lx + 30, y + 6), subfase, font=_font(14), fill=_INK, anchor="lm")

    titel = "Uitvoeringsplanning" + (f" · {projectnaam}" if projectnaam else "")
    ondertitel = "indicatief, in weken vanaf start uitvoering — één ploeg, in strengvolgorde"
    return _afronden(kaart, titel, ondertitel)
