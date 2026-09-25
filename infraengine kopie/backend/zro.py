"""ZRO-dossiers: status en vestigingsgegevens per gekruist perceel (FO §6.3).

Het register uit ``registers.build_zro`` wordt hier verrijkt met een
persistent dossier per perceel (data/zro/<slug>/dossier.json):
eigenaar, aard van het recht, vergoeding, status, bijlagen. Daarnaast:

- ZRO-tekening: automatisch gegenereerde situatietekening (PDF) op basis van
  tracé, kadastrale ondergrond (DKK-WMS of luchtfoto), perceel en werkstrook.
- ZRO-overeenkomst: concept-overeenkomst (Word/.docx) zodra het dossier
  compleet is.
"""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
from datetime import date
from pathlib import Path

import requests
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
ZRO_DIR = ROOT / "data" / "zro"
ZRO_DIR.mkdir(parents=True, exist_ok=True)

WERKSTROOK_M = 3.0  # zelfde aanname als registers.build_zro

ZRO_STATUSSEN = [
    "contact te leggen",
    "contact gelegd",
    "in onderhandeling",
    "akkoord bereikt",
    "overeenkomst opgesteld",
    "bij notaris",
    "gevestigd",
    "geweigerd / gedoogplicht (BP2024)",
]

AARD_OPTIES = [
    "opstalrecht",
    "erfdienstbaarheid",
    "kwalitatieve verplichting",
    "gedoogplicht (Belemmeringenwet / Ow)",
    "huur / gebruiksovereenkomst",
]

LEEG_DOSSIER = {
    "status": ZRO_STATUSSEN[0],
    "eigenaar_naam": "",
    "eigenaar_adres": "",
    "eigenaar_postcode_plaats": "",
    "eigenaar_email": "",
    "eigenaar_telefoon": "",
    "netbeheerder": "",
    "aard_recht": "",
    "vergoeding_eenmalig_eur": None,
    "vergoeding_jaarlijks_eur": None,
    "vergoeding_grondslag": "",  # gevuld wanneer het richtlijnvoorstel is overgenomen
    "notaris": "",
    "opmerkingen": "",
    "bijlagen": [],  # {bestand, soort: tekening|overeenkomst|overig, datum}
}

DOSSIER_VELDEN = [k for k in LEEG_DOSSIER if k != "bijlagen"]


def slug_van(perceel: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", perceel.strip()).strip("_")[:80]
    return s or "onbekend_perceel"


def _dossier_pad(slug: str) -> Path:
    return ZRO_DIR / slug / "dossier.json"


def laad_dossier(slug: str) -> dict:
    pad = _dossier_pad(slug)
    dossier = {**LEEG_DOSSIER, "bijlagen": []}
    if pad.exists():
        try:
            dossier.update(json.loads(pad.read_text()))
        except Exception:
            pass
    # bijlagen waarvan het bestand verdwenen is niet tonen
    map_ = pad.parent
    dossier["bijlagen"] = [b for b in dossier["bijlagen"]
                           if (map_ / b.get("bestand", "")).exists()]
    return dossier


def bewaar_dossier(slug: str, dossier: dict) -> None:
    pad = _dossier_pad(slug)
    pad.parent.mkdir(parents=True, exist_ok=True)
    pad.write_text(json.dumps(dossier, ensure_ascii=False, indent=1))


def update_dossier(slug: str, velden: dict) -> dict:
    dossier = laad_dossier(slug)
    for k in DOSSIER_VELDEN:
        if k in velden:
            w = velden[k]
            # bedragen kunnen als tekst binnenkomen (registerpagina)
            if k.startswith("vergoeding_") and k.endswith("_eur") and isinstance(w, str):
                s = w.replace("€", "").strip()
                try:
                    w = float(s)
                except ValueError:
                    try:  # Nederlandse notatie: 1.234,56
                        w = float(s.replace(".", "").replace(",", "."))
                    except ValueError:
                        w = None
            dossier[k] = w
    # bedrag gewijzigd zonder meegestuurde grondslag (registerpagina):
    # dan geldt het richtlijnvoorstel niet meer als grondslag
    if (("vergoeding_eenmalig_eur" in velden or "vergoeding_jaarlijks_eur" in velden)
            and "vergoeding_grondslag" not in velden):
        dossier["vergoeding_grondslag"] = ""
    if dossier["status"] not in ZRO_STATUSSEN:
        dossier["status"] = ZRO_STATUSSEN[0]
    bewaar_dossier(slug, dossier)
    return dossier


def voeg_bijlage_toe(slug: str, bestandsnaam: str, inhoud: bytes, soort: str) -> dict:
    map_ = ZRO_DIR / slug
    map_.mkdir(parents=True, exist_ok=True)
    naam = re.sub(r"[^\w.\-]+", "_", bestandsnaam) or "bijlage"
    # niet overschrijven: nummer ophogen
    pad = map_ / naam
    stam, punt, ext = naam.partition(".")
    n = 1
    while pad.exists():
        n += 1
        pad = map_ / f"{stam}_{n}{punt}{ext}"
    pad.write_bytes(inhoud)
    dossier = laad_dossier(slug)
    dossier["bijlagen"].append({
        "bestand": pad.name, "soort": soort,
        "datum": date.today().strftime("%d-%m-%Y"),
    })
    bewaar_dossier(slug, dossier)
    return dossier


def verwijder_bijlage(slug: str, bestandsnaam: str) -> dict:
    map_ = ZRO_DIR / slug
    pad = (map_ / bestandsnaam).resolve()
    if map_.resolve() in pad.parents and pad.exists():
        pad.unlink()
    dossier = laad_dossier(slug)
    dossier["bijlagen"] = [b for b in dossier["bijlagen"] if b["bestand"] != bestandsnaam]
    bewaar_dossier(slug, dossier)
    return dossier


def bijlage_pad(slug: str, bestandsnaam: str) -> Path | None:
    map_ = (ZRO_DIR / slug).resolve()
    pad = (map_ / bestandsnaam).resolve()
    if map_ in pad.parents and pad.exists():
        return pad
    return None


def checklist(dossier: dict) -> list:
    """Vestigingsvereisten; alles ✓ = overeenkomst kan worden opgesteld."""
    heeft_tekening = any(b["soort"] == "tekening" for b in dossier["bijlagen"])
    verg = dossier.get("vergoeding_eenmalig_eur")
    return [
        {"eis": "Eigenaar / rechthebbende bekend",
         "ok": bool(dossier.get("eigenaar_naam", "").strip()),
         "hint": "BRK-eigendom is niet gekoppeld (licentie); handmatig invullen."},
        {"eis": "Netbeheerder / verkrijger ingevuld",
         "ok": bool(dossier.get("netbeheerder", "").strip()),
         "hint": "Partij die het recht verkrijgt."},
        {"eis": "Aard van het recht bepaald",
         "ok": dossier.get("aard_recht", "") in AARD_OPTIES,
         "hint": "Opstalrecht is gebruikelijk voor MS-kabels."},
        {"eis": "Vergoeding overeengekomen",
         "ok": isinstance(verg, (int, float)),
         "hint": "Eenmalige vergoeding invullen (0 mag); het richtlijnvoorstel "
                 "kan als startpunt worden overgenomen."},
        {"eis": "ZRO-tekening als bijlage aanwezig",
         "ok": heeft_tekening,
         "hint": "Genereer de tekening; deze hoort als bijlage bij de akte."},
    ]


def is_compleet(dossier: dict) -> bool:
    return all(c["ok"] for c in checklist(dossier))


# ---------------------------------------------------------------------------
# Vergoeding: voorstel volgens de gangbare richtlijnen en afspraken
# ---------------------------------------------------------------------------
# Systematiek naar de landelijke afspraken tussen LTO Nederland en de
# gezamenlijke netbeheerders voor kabels en leidingen in landelijk gebied:
# - eenmalige afkoopvergoeding voor het zakelijk recht als percentage van de
#   agrarische grondwaarde van de belaste strook (werkstrook op het perceel);
# - een vaste afsluitvergoeding per te vestigen recht;
# - een meewerkvergoeding bij medewerking binnen de gestelde termijn;
# - gewassen- en structuurschade apart, op basis van werkelijke schade
#   (gewassen- en structuurschaderegeling; artikel 4 van de overeenkomst).
# Bij huur/gebruik geldt een jaarlijkse vergoeding in plaats van afkoop; bij
# een gedoogplicht (BP2024/Ow) is het bedrag een indicatie van de wettelijke
# schadeloosstelling. Bedragen zijn indicatieve startwaarden, per organisatie
# en regio instelbaar (vgl. TARIEVEN in registers.py; README "tarieven").

VERGOEDING_RICHTLIJN = {
    "grondwaarde_eur_m2": 8.50,      # agrarische grondwaarde, regionaal verschillend
    "afkoop_pct": 30,                # % van de grondwaarde van de belaste strook
    "afsluitvergoeding_eur": 500.0,  # vast, per te vestigen recht
    "meewerkvergoeding_eur": 250.0,  # bij ondertekening binnen de gestelde termijn
    "minimum_eenmalig_eur": 750.0,   # ondergrens voor kleine percelen
    "huur_pct_per_jaar": 4,          # huur/gebruik: jaarlijks % van de grondwaarde
}

GRONDSLAG_TEKST = ("richtlijnsystematiek LTO Nederland / gezamenlijke "
                   "netbeheerders (indicatieve startwaarden)")


def vergoeding_voorstel(zro_item: dict, dossier: dict) -> dict:
    """Vergoedingsvoorstel voor dit perceel volgens de richtlijn.

    Retourneert opbouwregels, het eenmalige en jaarlijkse bedrag en de
    p.m.-posten die buiten de afkoop blijven (werkelijke schade).
    """
    r = VERGOEDING_RICHTLIJN
    aard = dossier.get("aard_recht") or "opstalrecht"
    strook_m2 = float(zro_item.get("werkstrook_m2") or 0)
    grondwaarde = strook_m2 * r["grondwaarde_eur_m2"]
    basis = (f"{strook_m2:.0f} m² belaste strook × "
             f"€ {r['grondwaarde_eur_m2']:.2f}/m² = € {grondwaarde:,.0f}"
             .replace(",", "."))

    regels = []
    jaarlijks = None
    if aard == "huur / gebruiksovereenkomst":
        jaarlijks = round(grondwaarde * r["huur_pct_per_jaar"] / 100, 2)
        regels.append({"post": f"Jaarlijkse gebruiksvergoeding "
                               f"({r['huur_pct_per_jaar']}% van de grondwaarde)",
                       "grondslag": basis,
                       "bedrag_eur": jaarlijks, "periodiek": "per jaar"})
        regels.append({"post": "Afsluitvergoeding", "grondslag": "vast, per overeenkomst",
                       "bedrag_eur": r["afsluitvergoeding_eur"]})
        eenmalig = r["afsluitvergoeding_eur"]
    else:
        afkoop = round(grondwaarde * r["afkoop_pct"] / 100, 2)
        regels.append({"post": f"Afkoop zakelijk recht "
                               f"({r['afkoop_pct']}% van de grondwaarde belaste strook)",
                       "grondslag": basis, "bedrag_eur": afkoop})
        regels.append({"post": "Afsluitvergoeding", "grondslag": "vast, per te vestigen recht",
                       "bedrag_eur": r["afsluitvergoeding_eur"]})
        regels.append({"post": "Meewerkvergoeding",
                       "grondslag": "bij medewerking binnen de gestelde termijn",
                       "bedrag_eur": r["meewerkvergoeding_eur"]})
        eenmalig = afkoop + r["afsluitvergoeding_eur"] + r["meewerkvergoeding_eur"]
        if eenmalig < r["minimum_eenmalig_eur"]:
            regels.append({"post": "Aanvulling tot minimumvergoeding",
                           "grondslag": f"ondergrens € {r['minimum_eenmalig_eur']:,.0f}"
                                        .replace(",", "."),
                           "bedrag_eur": round(r["minimum_eenmalig_eur"] - eenmalig, 2)})
            eenmalig = r["minimum_eenmalig_eur"]

    toelichting = GRONDSLAG_TEKST
    if aard == "gedoogplicht (Belemmeringenwet / Ow)":
        toelichting += ("; bij een gedoogplicht geldt de wettelijke "
                        "schadeloosstelling — dit bedrag is een indicatie")

    return {
        "aard": aard,
        "regels": regels,
        "eenmalig_eur": round(eenmalig, 2),
        "jaarlijks_eur": jaarlijks,
        "pm": ["Gewassenschade — werkelijke schade na aanleg",
               "Structuurschade — werkelijke schade na aanleg"],
        "grondslag": GRONDSLAG_TEKST,
        "toelichting": toelichting,
    }


# ---------------------------------------------------------------------------
# ZRO-tekening (PDF): situatietekening perceel + tracé + werkstrook
# ---------------------------------------------------------------------------

DKK_WMS = "https://service.pdok.nl/kadaster/kadastralekaart/wms/v5_0"
LUFO_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"

# A4-liggend op 200 dpi
_PG_W, _PG_H = 2339, 1654
_MARGE = 40
_TITELBLOK_H = 300
_NICE_SCHALEN = [200, 250, 500, 750, 1000, 1250, 1500, 2000, 2500, 5000]

_FONT_KANDIDATEN = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(px: int, bold: bool = False):
    from PIL import ImageFont
    for pad in _FONT_KANDIDATEN:
        try:
            index = 1 if (bold and pad.endswith(".ttc")) else 0
            return ImageFont.truetype(pad, px, index=index)
        except Exception:
            continue
    return ImageFont.load_default()


def _wms_getmap(url: str, layers: str, bbox: tuple, w: int, h: int,
                transparant: bool = False):
    from PIL import Image
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": layers, "STYLES": "", "SRS": "EPSG:28992",
        "BBOX": ",".join(f"{v:.2f}" for v in bbox),
        "WIDTH": min(w, 4000), "HEIGHT": min(h, 4000),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE" if transparant else "FALSE",
    }
    r = requests.get(url, params=params, timeout=30,
                     headers={"User-Agent": "InfraEngine-prototype/0.1"})
    r.raise_for_status()
    if "image" not in r.headers.get("Content-Type", ""):
        raise ValueError("WMS gaf geen afbeelding terug")
    im = Image.open(io.BytesIO(r.content)).convert("RGBA")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    return im


def _vul_polygon(im, poly, kleur_rgba, transform):
    """Polygon (met eventuele gaten) semi-transparant vullen via masker."""
    from PIL import Image, ImageDraw
    masker = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(masker)
    polys = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    for p in polys:
        d.polygon([transform(*c) for c in p.exterior.coords], fill=255)
        for ring in p.interiors:
            d.polygon([transform(*c) for c in ring.coords], fill=0)
    laag = Image.new("RGBA", im.size, kleur_rgba)
    im.paste(laag, (0, 0), Image.composite(masker, Image.new("L", im.size, 0), masker))


def _teken_polygon_rand(draw, poly, kleur, breedte, transform, dash=None):
    polys = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    for p in polys:
        ringen = [p.exterior, *p.interiors]
        for ring in ringen:
            pts = [transform(*c) for c in ring.coords]
            if dash:
                _dash_lijn(draw, pts, kleur, breedte, dash)
            else:
                draw.line(pts, fill=kleur, width=breedte, joint="curve")


def _dash_lijn(draw, pts, kleur, breedte, dash):
    aan, uit = dash
    rest, tekenen = aan, True
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        pos = 0.0
        while pos < seg:
            stap = min(rest, seg - pos)
            t1, t2 = pos / seg, (pos + stap) / seg
            if tekenen:
                draw.line([(x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
                           (x1 + (x2 - x1) * t2, y1 + (y2 - y1) * t2)],
                          fill=kleur, width=breedte)
            pos += stap
            rest -= stap
            if rest <= 0:
                tekenen = not tekenen
                rest = aan if tekenen else uit


def maak_tekening(zro_item: dict, route_geojson: dict, stations: list,
                  percelen: list, gemeente: str | None, projectnaam: str,
                  achtergrond: str = "dkk") -> bytes:
    """ZRO-situatietekening als PDF (A4-liggend, RD New, met titelblok)."""
    from PIL import Image, ImageDraw

    route = shape(route_geojson)
    perceel = shape(zro_item["geometry"]) if zro_item.get("geometry") else None
    werkstrook = route.buffer(WERKSTROOK_M, cap_style=2)

    # kaartkader en schaal
    kaart_w = _PG_W - 2 * _MARGE
    kaart_h = _PG_H - 2 * _MARGE - _TITELBLOK_H
    focus = perceel if perceel is not None else route
    fxmin, fymin, fxmax, fymax = focus.buffer(20).bounds
    # tracé in de omgeving van het perceel mee in beeld
    omgeving = route.intersection(focus.buffer(60).envelope)
    if not omgeving.is_empty:
        oxmin, oymin, oxmax, oymax = omgeving.bounds
        fxmin, fymin = min(fxmin, oxmin), min(fymin, oymin)
        fxmax, fymax = max(fxmax, oxmax), max(fymax, oymax)
    nodig_mpp = max((fxmax - fxmin) / kaart_w, (fymax - fymin) / kaart_h)
    dpi = 200
    schaal = next((s for s in _NICE_SCHALEN if s * 0.0254 / dpi >= nodig_mpp),
                  _NICE_SCHALEN[-1])
    mpp = schaal * 0.0254 / dpi
    cx, cy = (fxmin + fxmax) / 2, (fymin + fymax) / 2
    x0, y1 = cx - kaart_w / 2 * mpp, cy + kaart_h / 2 * mpp
    bbox = (x0, y1 - kaart_h * mpp, x0 + kaart_w * mpp, y1)

    def tf(x, y):
        return ((x - x0) / mpp, (y1 - y) / mpp)

    # --- ondergrond ---
    kaart = Image.new("RGBA", (kaart_w, kaart_h), (255, 255, 255, 255))
    ondergrond_bron = "witte ondergrond"
    try:
        if achtergrond == "luchtfoto":
            lufo = _wms_getmap(LUFO_WMS, "Actueel_orthoHR", bbox, kaart_w, kaart_h)
            lufo = Image.blend(lufo, Image.new("RGBA", lufo.size, (255, 255, 255, 255)), 0.25)
            kaart.alpha_composite(lufo)
            ondergrond_bron = "luchtfoto (PDOK)"
        dkk = _wms_getmap(DKK_WMS, "Kadastralekaart", bbox, kaart_w, kaart_h,
                          transparant=True)
        kaart.alpha_composite(dkk)
        ondergrond_bron = ("luchtfoto + DKK (PDOK)" if achtergrond == "luchtfoto"
                           else "kadastrale kaart DKK (PDOK)")
    except Exception:
        # geen netverbinding: perceelsgrenzen uit de al opgehaalde DKK-data
        d0 = ImageDraw.Draw(kaart)
        for g, _props in percelen:
            if g.geom_type in ("Polygon", "MultiPolygon"):
                _teken_polygon_rand(d0, g, (150, 155, 150, 255), 2, tf)
        ondergrond_bron = "DKK-perceelsgrenzen (offline)"

    draw = ImageDraw.Draw(kaart)

    # --- werkstrook, belaste strook, perceel, tracé ---
    _vul_polygon(kaart, werkstrook, (200, 50, 43, 34), tf)
    if perceel is not None:
        belast = werkstrook.intersection(perceel)
        if not belast.is_empty and belast.geom_type in ("Polygon", "MultiPolygon"):
            _vul_polygon(kaart, belast, (200, 50, 43, 80), tf)
        _vul_polygon(kaart, perceel, (232, 185, 35, 36), tf)
        _teken_polygon_rand(draw, perceel, (198, 145, 10, 255), 5, tf, dash=(26, 14))
    _teken_polygon_rand(draw, werkstrook, (200, 50, 43, 140), 2, tf)

    routes = route.geoms if route.geom_type == "MultiLineString" else [route]
    for lijn in routes:
        draw.line([tf(*c) for c in lijn.coords], fill=(200, 50, 43, 255),
                  width=6, joint="curve")

    for i, s in enumerate(stations, 1):
        px, py = tf(*s)
        if -30 <= px <= kaart_w + 30 and -30 <= py <= kaart_h + 30:
            draw.ellipse([px - 12, py - 12, px + 12, py + 12],
                         fill=(200, 50, 43, 255), outline=(255, 255, 255, 255), width=3)
            draw.text((px, py - 18), f"MS{i}", font=_font(24, bold=True),
                      fill=(200, 50, 43, 255), anchor="ms",
                      stroke_width=3, stroke_fill=(255, 255, 255, 255))

    # perceellabel
    if perceel is not None:
        lx, ly = tf(perceel.representative_point().x, perceel.representative_point().y)
        draw.text((lx, ly), zro_item["perceel"], font=_font(30, bold=True),
                  fill=(120, 88, 0, 255), anchor="mm",
                  stroke_width=4, stroke_fill=(255, 255, 255, 230))

    # --- pagina samenstellen ---
    pagina = Image.new("RGB", (_PG_W, _PG_H), (255, 255, 255))
    pagina.paste(kaart.convert("RGB"), (_MARGE, _MARGE))
    d = ImageDraw.Draw(pagina)
    d.rectangle([_MARGE, _MARGE, _MARGE + kaart_w, _MARGE + kaart_h],
                outline=(30, 42, 51), width=3)

    # noordpijl
    nx, ny = _MARGE + kaart_w - 70, _MARGE + 90
    d.polygon([(nx, ny - 44), (nx - 17, ny + 18), (nx, ny + 6)], fill=(30, 42, 51))
    d.polygon([(nx, ny - 44), (nx + 17, ny + 18), (nx, ny + 6)], outline=(30, 42, 51),
              width=3)
    d.text((nx, ny + 26), "N", font=_font(30, bold=True), fill=(30, 42, 51), anchor="ma")

    # schaalbalk
    for lengte in (10, 20, 25, 50, 100, 200, 250, 500):
        if lengte / mpp >= 220:
            break
    balk_px = lengte / mpp
    bx, by = _MARGE + 30, _MARGE + kaart_h - 46
    for i in range(4):
        d.rectangle([bx + balk_px / 4 * i, by, bx + balk_px / 4 * (i + 1), by + 12],
                    fill=(30, 42, 51) if i % 2 == 0 else (255, 255, 255),
                    outline=(30, 42, 51), width=2)
    d.text((bx, by - 8), "0", font=_font(22), fill=(30, 42, 51), anchor="ls")
    d.text((bx + balk_px, by - 8), f"{lengte} m", font=_font(22),
           fill=(30, 42, 51), anchor="rs")

    # --- titelblok ---
    tb_y = _MARGE + kaart_h
    d.rectangle([_MARGE, tb_y, _PG_W - _MARGE, _PG_H - _MARGE],
                outline=(30, 42, 51), width=3)
    kol2 = _MARGE + 950
    kol3 = _MARGE + 1620
    d.line([kol2, tb_y, kol2, _PG_H - _MARGE], fill=(30, 42, 51), width=2)
    d.line([kol3, tb_y, kol3, _PG_H - _MARGE], fill=(30, 42, 51), width=2)

    d.text((_MARGE + 24, tb_y + 26), "ZRO-TEKENING — ZAKELIJK RECHT",
           font=_font(44, bold=True), fill=(30, 42, 51))
    d.text((_MARGE + 24, tb_y + 92),
           f"Tracé middenspanning · project {projectnaam or 'zonder naam'}",
           font=_font(28), fill=(75, 90, 102))
    d.text((_MARGE + 24, tb_y + 136),
           f"Gemeente {gemeente or 'onbekend'} · stelsel RD New (EPSG:28992)",
           font=_font(28), fill=(75, 90, 102))
    d.text((_MARGE + 24, tb_y + 180),
           f"Ondergrond: {ondergrond_bron}", font=_font(24), fill=(124, 137, 148))
    d.text((_MARGE + 24, tb_y + 220),
           "Automatisch gegenereerd door InfraEngine (fase 1-prototype) — indicatief, "
           "geen meetkundige grensreconstructie.",
           font=_font(22), fill=(124, 137, 148))

    regels = [
        ("Registratienr", zro_item["nr"]),
        ("Kadastraal perceel", zro_item["perceel"]),
        ("Ingenomen tracélengte", f"{zro_item['ingenomen_lengte_m']} m"),
        ("Werkstrook (breedte)", f"{WERKSTROOK_M * 2:.0f} m — {zro_item['werkstrook_m2']} m²"),
        ("Schaal", f"1 : {schaal}"),
        ("Datum", date.today().strftime("%d-%m-%Y")),
    ]
    for i, (k, w) in enumerate(regels):
        y = tb_y + 26 + i * 42
        d.text((kol2 + 22, y), k, font=_font(24), fill=(124, 137, 148))
        d.text((kol2 + 330, y), str(w), font=_font(26, bold=True), fill=(30, 42, 51))

    # legenda
    ly = tb_y + 30
    d.text((kol3 + 22, ly - 6), "LEGENDA", font=_font(24, bold=True), fill=(30, 42, 51))
    items = [
        ((200, 50, 43, 255), "lijn", "MS-tracé"),
        ((200, 50, 43, 90), "vlak", "werkstrook / belaste strook"),
        ((198, 145, 10, 255), "dash", "perceel waarop recht rust"),
    ]
    for i, (kleur, soort, tekst) in enumerate(items):
        y = ly + 44 + i * 52
        if soort == "lijn":
            d.line([kol3 + 22, y + 10, kol3 + 92, y + 10], fill=kleur[:3], width=6)
        elif soort == "vlak":
            d.rectangle([kol3 + 22, y, kol3 + 92, y + 22],
                        fill=(244, 213, 210), outline=(200, 50, 43), width=2)
        else:
            _dash_lijn(d, [(kol3 + 22, y + 10), (kol3 + 92, y + 10)], kleur[:3], 5, (16, 9))
        d.text((kol3 + 106, y - 4), tekst, font=_font(24), fill=(30, 42, 51))

    buf = io.BytesIO()
    pagina.save(buf, "PDF", resolution=dpi)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ZRO-overeenkomst (.docx, zonder externe libraries)
# ---------------------------------------------------------------------------

def _xml_escape(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _p(tekst: str, *, bold=False, size=22, align=None, space_after=140,
       space_before=0) -> str:
    """Eén Word-paragraaf; size in halve punten (22 = 11 pt)."""
    ppr = f'<w:spacing w:before="{space_before}" w:after="{space_after}"/>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/>' \
          f'<w:szCs w:val="{size}"/></w:rPr>'
    runs = ""
    for i, deel in enumerate(tekst.split("\n")):
        if i:
            runs += "<w:br/>"
        runs += f'<w:r>{rpr}<w:t xml:space="preserve">{_xml_escape(deel)}</w:t></w:r>'
    return f"<w:p><w:pPr>{ppr}{rpr}</w:pPr>{runs}</w:p>"


def _docx(paragrafen: list) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(paragrafen) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _eur(v) -> str:
    if not isinstance(v, (int, float)):
        return "€ —"
    return "€ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def maak_overeenkomst(zro_item: dict, dossier: dict, gemeente: str | None,
                      projectnaam: str) -> bytes:
    """Concept-ZRO-overeenkomst als bewerkbaar Word-document."""
    aard = dossier.get("aard_recht") or "opstalrecht"
    eigenaar = dossier.get("eigenaar_naam") or "[naam eigenaar]"
    adres = ", ".join(x for x in (dossier.get("eigenaar_adres"),
                                  dossier.get("eigenaar_postcode_plaats")) if x)
    netb = dossier.get("netbeheerder") or "[netbeheerder]"
    tekeningen = [b["bestand"] for b in dossier["bijlagen"] if b["soort"] == "tekening"]
    vandaag = date.today().strftime("%d-%m-%Y")
    jaarlijks = dossier.get("vergoeding_jaarlijks_eur")

    P = []
    P.append(_p("OVEREENKOMST TOT VESTIGING VAN EEN ZAKELIJK RECHT",
                bold=True, size=32, align="center", space_after=60))
    P.append(_p(f"({aard}) — {zro_item['nr']} · perceel {zro_item['perceel']}",
                size=24, align="center", space_after=300))

    P.append(_p("DE ONDERGETEKENDEN:", bold=True, space_before=120))
    P.append(_p(f"1. {eigenaar}{', ' + adres if adres else ''}, hierna te noemen: "
                f"“de Eigenaar”;"))
    P.append(_p(f"2. {netb}, hierna te noemen: “de Netbeheerder”;"))

    P.append(_p("IN AANMERKING NEMENDE DAT:", bold=True, space_before=160))
    P.append(_p(f"– de Netbeheerder in de gemeente {gemeente or '[gemeente]'} een "
                f"ondergrondse middenspanningsverbinding aanlegt en in stand houdt "
                f"(project “{projectnaam or 'zonder naam'}”);"))
    P.append(_p(f"– het tracé van deze verbinding het aan de Eigenaar toebehorende "
                f"kadastrale perceel {zro_item['perceel']} kruist over een lengte van "
                f"circa {zro_item['ingenomen_lengte_m']} m, met een werkstrook van "
                f"circa {zro_item['werkstrook_m2']} m², een en ander zoals aangegeven "
                f"op de aan deze overeenkomst gehechte ZRO-tekening;"))
    P.append(_p("– partijen de voorwaarden wensen vast te leggen waaronder ten laste "
                "van het perceel en ten behoeve van de Netbeheerder een zakelijk "
                "recht wordt gevestigd;"))

    P.append(_p("VERKLAREN TE ZIJN OVEREENGEKOMEN ALS VOLGT:", bold=True,
                space_before=160))

    art = [
        ("Artikel 1 — Vestiging en omschrijving van het recht",
         f"De Eigenaar verleent aan de Netbeheerder een {aard} tot het aanleggen, "
         f"hebben, houden, inspecteren, onderhouden, vervangen en verwijderen van "
         f"een ondergrondse elektriciteitsverbinding met toebehoren in een strook "
         f"grond met een breedte van {WERKSTROOK_M * 2:.0f} meter ({WERKSTROOK_M:.0f} "
         f"meter ter weerszijden van de hartlijn), zoals aangegeven op de als "
         f"bijlage gevoegde ZRO-tekening."),
        ("Artikel 2 — Vergoeding",
         f"De Netbeheerder betaalt aan de Eigenaar een eenmalige vergoeding van "
         f"{_eur(dossier.get('vergoeding_eenmalig_eur'))}"
         + (f", alsmede een jaarlijkse vergoeding van {_eur(jaarlijks)}"
            if isinstance(jaarlijks, (int, float)) and jaarlijks else "")
         + ", te voldoen binnen 30 dagen na het passeren van de notariële akte."
         + (f" De vergoeding is bepaald volgens de "
            f"{dossier['vergoeding_grondslag']}."
            if dossier.get("vergoeding_grondslag") else "")),
        ("Artikel 3 — Gebruik en gedogen",
         "De Eigenaar gedoogt de aanwezigheid van de verbinding en verleent de "
         "Netbeheerder en door haar aangewezen derden toegang tot de strook voor "
         "aanleg, beheer, onderhoud en verwijdering, na voorafgaande aankondiging "
         "behoudens spoedeisende gevallen. De Eigenaar zal binnen de strook geen "
         "bouwwerken oprichten, diepwortelende beplanting aanbrengen, ontgrondingen "
         "uitvoeren of de grondwaterstand wijzigen zonder voorafgaande schriftelijke "
         "toestemming van de Netbeheerder."),
        ("Artikel 4 — Schade",
         "Schade die het gevolg is van de aanleg of van werkzaamheden aan de "
         "verbinding wordt door de Netbeheerder vergoed dan wel hersteld volgens "
         "de gebruikelijke gewassen- en structuurschaderegelingen."),
        ("Artikel 5 — Notariële vestiging en kosten",
         f"Het recht wordt gevestigd bij notariële akte, te verlijden ten overstaan "
         f"van {dossier.get('notaris') or '[notaris]'}. De kosten van de akte, de "
         f"inschrijving in de openbare registers en het kadastraal recht komen voor "
         f"rekening van de Netbeheerder."),
        ("Artikel 6 — Bijlagen",
         "Van deze overeenkomst maakt deel uit: ZRO-tekening "
         + (", ".join(tekeningen) if tekeningen else "[nog te genereren]")
         + ". Bij strijdigheid tussen tekst en tekening prevaleert de tekening "
           "voor de ligging van de strook."),
    ]
    for kop, tekst in art:
        P.append(_p(kop, bold=True, space_before=160))
        P.append(_p(tekst))

    if dossier.get("opmerkingen", "").strip():
        P.append(_p("Artikel 7 — Bijzondere bepalingen", bold=True, space_before=160))
        P.append(_p(dossier["opmerkingen"].strip()))

    P.append(_p(f"Aldus overeengekomen en in tweevoud ondertekend, "
                f"d.d. {vandaag}.", space_before=260))
    P.append(_p("De Eigenaar,\n\n\n____________________________\n" + eigenaar,
                space_before=200))
    P.append(_p("De Netbeheerder,\n\n\n____________________________\n" + netb,
                space_before=200))
    P.append(_p("Concept — automatisch opgesteld door InfraEngine (fase 1-prototype) "
                "op basis van het ZRO-dossier; juridische toetsing vereist.",
                size=18, space_before=260))
    return _docx(P)
