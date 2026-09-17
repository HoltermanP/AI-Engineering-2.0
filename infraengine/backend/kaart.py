"""Kaartafbeeldingen voor de ontwikkelnota's (PNG, RD New).

Drie beelden, gerenderd op een PDOK-luchtfoto-ondergrond (verbleekt, met
falende dienst als witte terugval):

  - ``overzichtskaart``  — het volledige tracé van één variant met stations
    en werkpakketlabels (de "overzichtstekening" in hoofdstuk 1/3 van de
    ontwikkelnota);
  - ``variantenkaart``   — alle berekende varianten in kleur over elkaar,
    met legenda (bijlage variantenafweging);
  - ``variantkaart``     — één variant uitgelicht, de overige in lichtgrijs
    (per-variantbeeld in de bijlage).

De beelden worden ingebed in de Word-nota's (nota.py vervangt de
``[AFBEELDING: …]``-markers) en geserveerd via ``/api/kaart/*`` voor de
live nota-preview in de frontend.
"""
from __future__ import annotations

import io
import math

from shapely.geometry import LineString, shape

from zro import LUFO_WMS, _font, _wms_getmap

# beeldmaat (px) — past op de tekstbreedte van de Word-pagina (± 16 cm)
BEELD_W, BEELD_H = 1600, 1120
_RAND = 8          # kaderrand
_BALK_H = 64       # onderbalk met bron/schaal

# variantkleuren (RGBA) — voorkeursvariant altijd in het InfraEngine-rood
VARIANT_KLEUREN = [
    (200, 50, 43, 255),    # rood — voorkeursvariant
    (37, 99, 235, 255),    # blauw
    (13, 148, 136, 255),   # teal
    (124, 92, 240, 255),   # paars
    (199, 126, 20, 255),   # amber
    (90, 105, 120, 255),   # grijsblauw
]
_GRIJS = (150, 156, 165, 200)
_INK = (30, 42, 51, 255)


def _routes(result: dict) -> list[LineString]:
    return [shape(v["route"]) for v in result["varianten"]]


def _bbox_om(geoms, w: int, h: int, marge: float = 0.06) -> tuple:
    """Vierkant-passende bbox (RD) rond de geometrieën, met randmarge."""
    xmin = min(g.bounds[0] for g in geoms)
    ymin = min(g.bounds[1] for g in geoms)
    xmax = max(g.bounds[2] for g in geoms)
    ymax = max(g.bounds[3] for g in geoms)
    bx, by = xmax - xmin, ymax - ymin
    xmin -= bx * marge + 40
    xmax += bx * marge + 40
    ymin -= by * marge + 40
    ymax += by * marge + 40
    # aspectratio van het beeld aanhouden
    mpp = max((xmax - xmin) / w, (ymax - ymin) / h)
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    return (cx - w / 2 * mpp, cy - h / 2 * mpp,
            cx + w / 2 * mpp, cy + h / 2 * mpp), mpp


def _ondergrond(bbox: tuple, w: int, h: int):
    """Verbleekte luchtfoto; zonder netverbinding een lichte ondergrond."""
    from PIL import Image
    im = Image.new("RGBA", (w, h), (241, 243, 240, 255))
    try:
        lufo = _wms_getmap(LUFO_WMS, "Actueel_orthoHR", bbox, w, h)
        im = Image.blend(lufo, Image.new("RGBA", (w, h), (255, 255, 255, 255)),
                         0.42)
    except Exception:
        pass
    return im


def _teken_route(draw, route: LineString, tf, kleur, breedte: int,
                 rand: bool = True) -> None:
    pts = [tf(*c) for c in route.coords]
    if rand:
        draw.line(pts, fill=(255, 255, 255, 230), width=breedte + 4,
                  joint="curve")
    draw.line(pts, fill=kleur, width=breedte, joint="curve")


def _teken_stations(draw, stations: list, tf, w: int, h: int) -> None:
    for i, s in enumerate(stations, 1):
        px, py = tf(s[0], s[1])
        if not (-20 <= px <= w + 20 and -20 <= py <= h + 20):
            continue
        draw.ellipse([px - 11, py - 11, px + 11, py + 11], fill=_INK,
                     outline=(255, 255, 255, 255), width=3)
        draw.text((px, py - 17), f"MS{i}", font=_font(26, bold=True),
                  fill=_INK, anchor="ms", stroke_width=4,
                  stroke_fill=(255, 255, 255, 235))


def _teken_wp_labels(draw, result: dict, variant_idx: int,
                     route: LineString, tf) -> None:
    for wp in result["varianten"][variant_idx].get("werkpakketten", []):
        try:
            m = (wp["chainage_van_m"] + wp["chainage_tot_m"]) / 2
            p = route.interpolate(m)
            px, py = tf(p.x, p.y)
            draw.text((px, py), wp["nr"], font=_font(24, bold=True),
                      fill=(200, 50, 43, 255), anchor="mm", stroke_width=4,
                      stroke_fill=(255, 255, 255, 235))
        except Exception:
            continue


def _schaalbalk(draw, mpp: float, w: int, h: int) -> None:
    doel_px = w * 0.2
    stap = 10 ** math.floor(math.log10(doel_px * mpp))
    for f in (5, 2, 1):
        if stap * f <= doel_px * mpp:
            lengte_m = stap * f
            break
    else:
        lengte_m = stap
    px = lengte_m / mpp
    x1, y = w - _RAND - 30 - px, h - _BALK_H - 26
    draw.rectangle([x1 - 12, y - 26, x1 + px + 12, y + 14],
                   fill=(255, 255, 255, 210))
    draw.line([x1, y, x1 + px, y], fill=_INK, width=5)
    for fx in (x1, x1 + px):
        draw.line([fx, y - 7, fx, y + 7], fill=_INK, width=4)
    tekst = f"{lengte_m / 1000:.0f} km" if lengte_m >= 1000 else f"{lengte_m:.0f} m"
    draw.text((x1 + px / 2, y - 10), tekst, font=_font(22, bold=True),
              fill=_INK, anchor="ms")


def _noordpijl(draw, w: int) -> None:
    nx, ny = w - _RAND - 42, _RAND + 56
    draw.ellipse([nx - 30, ny - 42, nx + 30, ny + 40],
                 fill=(255, 255, 255, 200))
    draw.polygon([(nx, ny - 30), (nx - 12, ny + 12), (nx, ny + 4)], fill=_INK)
    draw.polygon([(nx, ny - 30), (nx + 12, ny + 12), (nx, ny + 4)],
                 outline=_INK, width=3)
    draw.text((nx, ny + 16), "N", font=_font(22, bold=True), fill=_INK,
              anchor="ma")


def _afronden(kaart, titel: str, ondertitel: str) -> bytes:
    """Kader + onderbalk met titel/bron; retourneert PNG-bytes."""
    from PIL import Image, ImageDraw
    w, h = kaart.size
    d = ImageDraw.Draw(kaart)
    d.rectangle([0, 0, w - 1, h - 1], outline=(30, 42, 51, 255), width=3)
    d.rectangle([0, h - _BALK_H, w, h], fill=(30, 42, 51, 235))
    d.text((18, h - _BALK_H / 2), titel, font=_font(28, bold=True),
           fill=(255, 255, 255, 255), anchor="lm")
    d.text((w - 18, h - _BALK_H / 2), ondertitel, font=_font(22),
           fill=(200, 207, 203, 255), anchor="rm")
    buf = io.BytesIO()
    # JPEG: de luchtfoto-ondergrond maakt PNG onnodig groot (± 2 MB → ± 350 kB)
    kaart.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def _mpp_tekst(mpp: float) -> str:
    return (f"ondergrond: luchtfoto (PDOK) · RD New · "
            f"± 1:{max(1, round(mpp / 0.0254 * 96)):,}".replace(",", "."))


def overzichtskaart(result: dict, variant_idx: int = 0,
                    projectnaam: str = "") -> bytes:
    """Overzichtstekening: tracé + stations + werkpakketlabels."""
    from PIL import ImageDraw
    route = shape(result["varianten"][variant_idx]["route"])
    bbox, mpp = _bbox_om([route], BEELD_W, BEELD_H)
    kaart = _ondergrond(bbox, BEELD_W, BEELD_H)
    x0, _, _, y1 = bbox[0], bbox[1], bbox[2], bbox[3]

    def tf(x, y):
        return ((x - x0) / mpp, (y1 - y) / mpp)

    draw = ImageDraw.Draw(kaart)
    _teken_route(draw, route, tf, VARIANT_KLEUREN[0], 7)
    _teken_wp_labels(draw, result, variant_idx, route, tf)
    _teken_stations(draw, result.get("stations", []), tf, BEELD_W, BEELD_H)
    _schaalbalk(draw, mpp, BEELD_W, BEELD_H)
    _noordpijl(draw, BEELD_W)
    naam = result["varianten"][variant_idx]["naam"]
    lengte = result["varianten"][variant_idx]["lengte_m"]
    titel = (f"Overzichtstekening tracé — {naam} ({lengte:.0f} m)"
             + (f" · {projectnaam}" if projectnaam else ""))
    return _afronden(kaart, titel, _mpp_tekst(mpp))


def variantenkaart(result: dict, projectnaam: str = "") -> bytes:
    """Alle varianten in kleur over elkaar, met legenda."""
    from PIL import ImageDraw
    routes = _routes(result)
    bbox, mpp = _bbox_om(routes, BEELD_W, BEELD_H)
    kaart = _ondergrond(bbox, BEELD_W, BEELD_H)
    x0, y1 = bbox[0], bbox[3]

    def tf(x, y):
        return ((x - x0) / mpp, (y1 - y) / mpp)

    draw = ImageDraw.Draw(kaart)
    # voorkeursvariant (index 0) als laatste, zodat die bovenop ligt
    for i in range(len(routes) - 1, -1, -1):
        kleur = VARIANT_KLEUREN[i % len(VARIANT_KLEUREN)]
        _teken_route(draw, routes[i], tf, kleur, 6 if i else 8)
    _teken_stations(draw, result.get("stations", []), tf, BEELD_W, BEELD_H)

    # legenda
    regels = [(VARIANT_KLEUREN[i % len(VARIANT_KLEUREN)],
               f"{v['naam']} — {v['lengte_m']:.0f} m")
              for i, v in enumerate(result["varianten"])]
    lh, pad = 40, 16
    lw = max(draw.textlength(t, font=_font(24, bold=True))
             for _, t in regels) + 70
    draw.rectangle([_RAND + 8, _RAND + 8,
                    _RAND + 8 + lw, _RAND + 8 + pad * 2 + lh * len(regels)],
                   fill=(255, 255, 255, 225), outline=_INK, width=2)
    for i, (kleur, tekst) in enumerate(regels):
        y = _RAND + 8 + pad + lh * i + lh / 2
        draw.line([_RAND + 24, y, _RAND + 60, y], fill=kleur, width=8)
        draw.text((_RAND + 72, y), tekst, font=_font(24, bold=True),
                  fill=_INK, anchor="lm")

    _schaalbalk(draw, mpp, BEELD_W, BEELD_H)
    _noordpijl(draw, BEELD_W)
    titel = ("Variantenoverzicht tracéafweging"
             + (f" · {projectnaam}" if projectnaam else ""))
    return _afronden(kaart, titel, _mpp_tekst(mpp))


def variantkaart(result: dict, variant_idx: int,
                 projectnaam: str = "") -> bytes:
    """Eén variant uitgelicht; de overige varianten in lichtgrijs."""
    from PIL import ImageDraw
    routes = _routes(result)
    bbox, mpp = _bbox_om(routes, BEELD_W, BEELD_H)
    kaart = _ondergrond(bbox, BEELD_W, BEELD_H)
    x0, y1 = bbox[0], bbox[3]

    def tf(x, y):
        return ((x - x0) / mpp, (y1 - y) / mpp)

    draw = ImageDraw.Draw(kaart)
    for i, r in enumerate(routes):
        if i != variant_idx:
            _teken_route(draw, r, tf, _GRIJS, 5, rand=False)
    kleur = VARIANT_KLEUREN[variant_idx % len(VARIANT_KLEUREN)]
    _teken_route(draw, routes[variant_idx], tf, kleur, 8)
    _teken_stations(draw, result.get("stations", []), tf, BEELD_W, BEELD_H)
    _schaalbalk(draw, mpp, BEELD_W, BEELD_H)
    _noordpijl(draw, BEELD_W)
    v = result["varianten"][variant_idx]
    titel = (f"Variant: {v['naam']} ({v['lengte_m']:.0f} m)"
             + (f" · {projectnaam}" if projectnaam else ""))
    return _afronden(kaart, titel, _mpp_tekst(mpp))


def variant_index(result: dict, naam: str) -> int | None:
    """Variantindex op (deel van de) naam, hoofdletterongevoelig."""
    naam = naam.strip().lower()
    for i, v in enumerate(result.get("varianten", [])):
        if v["naam"].strip().lower() == naam:
            return i
    for i, v in enumerate(result.get("varianten", [])):
        if naam in v["naam"].strip().lower():
            return i
    return None
