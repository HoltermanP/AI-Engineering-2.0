"""RDNAPTRANS™2018 — ETRS89 ↔ RD New (EPSG:28992) en NAP, zonder pyproj.

Implementatievariant 2 van NSGI (zoals PROJ die standaard gebruikt): één
correctiegrid ``nl_nsgi_rdtrans2018.tif`` verschuift geografische
coördinaten direct tussen RD Bessel (EPSG:4289) en ETRS89 (EPSG:4258);
daarna volgt de RD-projectie (Oblique Stereographic, EPSG-methode 9809).
De hoogte gaat via het quasi-geoïde ``nl_nsgi_nlgeo2018.tif``:
NAP = h(ETRS89) − N.

Beide grids staan in ``backend/grids/`` (overgenomen van cdn.proj.org,
bron NSGI). De GeoTIFF's zijn deflate-gecomprimeerd met floating-point-
predictor; die worden hier met alleen ``zlib``/``struct`` gelezen.

Binnen het grid (Nederland + 12-mijlszone) is dit gelijk aan PROJ/
RDNAPTRANS op de millimeter; daarbuiten valt de module terug op de
7-parametertransformatie (± 0,25 m) en meldt dat via ``nauwkeurig=False``.
"""
from __future__ import annotations

import math
import struct
import threading
import zlib
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parent / "grids"
RDTRANS = GRID_DIR / "nl_nsgi_rdtrans2018.tif"
NLGEO = GRID_DIR / "nl_nsgi_nlgeo2018.tif"

# RD New: Bessel 1841, oblique stereographic (EPSG:28992)
A_BESSEL = 6377397.155
F_BESSEL = 1 / 299.1528128
LAT0 = math.radians(52.15616055555555)
LON0 = math.radians(5.38763888888889)
K0 = 0.9999079
FE, FN = 155000.0, 463000.0

# geldigheidsgebied van RD (EPSG:28992), ruim genomen
RD_X = (-7000.0, 300000.0)
RD_Y = (289000.0, 629000.0)


def is_rd(x: float, y: float) -> bool:
    return RD_X[0] <= x <= RD_X[1] and RD_Y[0] <= y <= RD_Y[1]


# ---------------------------------------------------------------- projectie

def _stereo_constanten():
    e2 = F_BESSEL * (2 - F_BESSEL)
    e = math.sqrt(e2)
    s0 = math.sin(LAT0)
    rho0 = A_BESSEL * (1 - e2) / (1 - e2 * s0 * s0) ** 1.5
    nu0 = A_BESSEL / math.sqrt(1 - e2 * s0 * s0)
    r = math.sqrt(rho0 * nu0)
    n = math.sqrt(1 + e2 * math.cos(LAT0) ** 4 / (1 - e2))
    s1 = (1 + s0) / (1 - s0)
    s2 = (1 - e * s0) / (1 + e * s0)
    w1 = (s1 * s2 ** e) ** n
    sin_chi00 = (w1 - 1) / (w1 + 1)
    c = (n + s0) * (1 - sin_chi00) / ((n - s0) * (1 + sin_chi00))
    w2 = c * w1
    chi0 = math.asin((w2 - 1) / (w2 + 1))
    return e, e2, r, n, c, chi0


_E, _E2, _R, _N, _C, _CHI0 = _stereo_constanten()


def bessel_naar_rd(lat: float, lon: float) -> tuple[float, float]:
    """Geografisch RD Bessel (graden) → RD x/y (m)."""
    phi, lam = math.radians(lat), math.radians(lon)
    big_lam = _N * (lam - LON0) + LON0
    sp = math.sin(phi)
    sa = (1 + sp) / (1 - sp)
    sb = (1 - _E * sp) / (1 + _E * sp)
    w = _C * (sa * sb ** _E) ** _N
    chi = math.asin((w - 1) / (w + 1))
    d = big_lam - LON0
    b = 1 + math.sin(chi) * math.sin(_CHI0) + math.cos(chi) * math.cos(_CHI0) * math.cos(d)
    x = FE + 2 * _R * K0 * math.cos(chi) * math.sin(d) / b
    y = FN + 2 * _R * K0 * (math.sin(chi) * math.cos(_CHI0)
                            - math.cos(chi) * math.sin(_CHI0) * math.cos(d)) / b
    return x, y


def rd_naar_bessel(x: float, y: float) -> tuple[float, float]:
    """RD x/y (m) → geografisch RD Bessel (graden)."""
    de, dn = x - FE, y - FN
    g = 2 * _R * K0 * math.tan(math.pi / 4 - _CHI0 / 2)
    h = 4 * _R * K0 * math.tan(_CHI0) + g
    i = math.atan(de / (h + dn))
    j = math.atan(de / (g - dn)) - i
    chi = _CHI0 + 2 * math.atan((dn - de * math.tan(j / 2)) / (2 * _R * K0))
    big_lam = j + 2 * i + LON0
    lam = (big_lam - LON0) / _N + LON0
    psi = 0.5 * math.log((1 + math.sin(chi)) / (_C * (1 - math.sin(chi)))) / _N
    phi = 2 * math.atan(math.exp(psi)) - math.pi / 2
    for _ in range(20):
        sp = math.sin(phi)
        psi_i = math.log(math.tan(phi / 2 + math.pi / 4)
                         * ((1 - _E * sp) / (1 + _E * sp)) ** (_E / 2))
        nieuw = phi - (psi_i - psi) * math.cos(phi) * (1 - _E2 * sp * sp) / (1 - _E2)
        if abs(nieuw - phi) < 1e-14:
            phi = nieuw
            break
        phi = nieuw
    return math.degrees(phi), math.degrees(lam)


# ------------------------------------------------------------- GeoTIFF-grid

class _Grid:
    """Eén deelgrid uit een PROJ-GeoTIFF: banden als lijsten van rijen."""

    def __init__(self, lon0, lat0, dlon, dlat, breedte, hoogte, banden):
        self.lon0, self.lat0 = lon0, lat0
        self.dlon, self.dlat = dlon, dlat
        self.w, self.h = breedte, hoogte
        self.banden = banden  # [band][rij][kolom], rij 0 = noordrand

    def bevat(self, lat, lon) -> bool:
        kx = (lon - self.lon0) / self.dlon
        ky = (self.lat0 - lat) / self.dlat
        return 0 <= kx <= self.w - 1 and 0 <= ky <= self.h - 1

    def waarde(self, band: int, lat: float, lon: float) -> float:
        kx = (lon - self.lon0) / self.dlon
        ky = (self.lat0 - lat) / self.dlat
        i0 = min(int(kx), self.w - 2)
        j0 = min(int(ky), self.h - 2)
        fx, fy = kx - i0, ky - j0
        b = self.banden[band]
        return ((b[j0][i0] * (1 - fx) + b[j0][i0 + 1] * fx) * (1 - fy)
                + (b[j0 + 1][i0] * (1 - fx) + b[j0 + 1][i0 + 1] * fx) * fy)


def _lees_ifds(data: bytes) -> list[dict]:
    bo = "<" if data[:2] == b"II" else ">"
    grootte = {1: 1, 2: 1, 3: 2, 4: 4, 11: 4, 12: 8, 16: 8}
    fmt = {1: "B", 2: "c", 3: "H", 4: "I", 11: "f", 12: "d", 16: "Q"}
    off = struct.unpack(bo + "I", data[4:8])[0]
    ifds = []
    while off:
        n = struct.unpack(bo + "H", data[off:off + 2])[0]
        tags = {}
        for k in range(n):
            e = data[off + 2 + 12 * k: off + 14 + 12 * k]
            tag, typ, cnt = struct.unpack(bo + "HHI", e[:8])
            if typ not in fmt:
                continue
            nb = grootte[typ] * cnt
            ruw = e[8:8 + nb] if nb <= 4 else data[
                struct.unpack(bo + "I", e[8:12])[0]:struct.unpack(bo + "I", e[8:12])[0] + nb]
            tags[tag] = ruw if typ == 2 else struct.unpack(bo + fmt[typ] * cnt, ruw)
        ifds.append(tags)
        off = struct.unpack(bo + "I", data[off + 2 + 12 * n: off + 6 + 12 * n])[0]
    return ifds


def _ontpak_blok(ruw: bytes, breedte: int, rijen: int, predictor: int) -> list[list[float]]:
    """Deflate + (optioneel) floating-point-predictor terugdraaien → float32-rijen."""
    buf = zlib.decompress(ruw)
    rijbytes = breedte * 4
    uit = []
    for r in range(rijen):
        rij = bytearray(buf[r * rijbytes:(r + 1) * rijbytes])
        if len(rij) < rijbytes:
            rij.extend(b"\0" * (rijbytes - len(rij)))
        if predictor == 3:
            for k in range(1, rijbytes):  # horizontale differentie per byte
                rij[k] = (rij[k] + rij[k - 1]) & 0xFF
            # bytevlakken (MSB eerst) terug naar big-endian floats
            be = bytearray(rijbytes)
            for i in range(breedte):
                for b in range(4):
                    be[i * 4 + b] = rij[b * breedte + i]
            uit.append(list(struct.unpack(">" + "f" * breedte, be)))
        else:
            uit.append(list(struct.unpack("<" + "f" * breedte, bytes(rij))))
    return uit


def _lees_grids(pad: Path) -> list[_Grid]:
    data = pad.read_bytes()
    grids = []
    for t in _lees_ifds(data):
        w, h = t[256][0], t[257][0]
        nb = t.get(277, (1,))[0]
        pred = t.get(317, (1,))[0]
        planar = t.get(284, (1,))[0]
        if t.get(259, (1,))[0] != 8 or (nb > 1 and planar != 2):
            raise ValueError(f"{pad.name}: niet-ondersteunde TIFF-indeling")
        dlon, dlat = t[33550][0], t[33550][1]
        lon0, lat0 = t[33922][3], t[33922][4]
        banden = [[[0.0] * w for _ in range(h)] for _ in range(nb)]
        if 322 in t:  # getegeld
            tw, th = t[322][0], t[323][0]
            nx, ny = math.ceil(w / tw), math.ceil(h / th)
            offs, cnts = t[324], t[325]
            for k, (o, c) in enumerate(zip(offs, cnts)):
                band, rest = divmod(k, nx * ny)
                ty, tx = divmod(rest, nx)
                blok = _ontpak_blok(data[o:o + c], tw, th, pred)
                for r in range(th):
                    yy = ty * th + r
                    if yy >= h:
                        break
                    rij = banden[band][yy]
                    for i in range(tw):
                        xx = tx * tw + i
                        if xx >= w:
                            break
                        rij[xx] = blok[r][i]
        else:  # strips: één strip per band (rowsperstrip = hoogte)
            rps = t.get(278, (h,))[0]
            per_band = math.ceil(h / rps)
            for k, (o, c) in enumerate(zip(t[273], t[279])):
                band, s = divmod(k, per_band)
                rijen = min(rps, h - s * rps)
                blok = _ontpak_blok(data[o:o + c], w, rijen, pred)
                for r in range(rijen):
                    banden[band][s * rps + r] = blok[r]
        grids.append(_Grid(lon0, lat0, dlon, dlat, w, h, banden))
    # fijnste grid eerst: dat gaat voor waar het geldt
    return sorted(grids, key=lambda g: g.dlon * g.dlat)


_cache: dict = {}
_lock = threading.Lock()


def _grids(pad: Path) -> list[_Grid] | None:
    with _lock:
        if pad not in _cache:
            try:
                _cache[pad] = _lees_grids(pad)
            except (OSError, ValueError, KeyError, zlib.error):
                _cache[pad] = None
        return _cache[pad]


def _grid_bij(pad: Path, lat: float, lon: float) -> _Grid | None:
    for g in _grids(pad) or []:
        if g.bevat(lat, lon):
            return g
    return None


# ------------------------------------------- terugval: 7-parametertransformatie

_GRS80_A, _GRS80_F = 6378137.0, 1 / 298.257222101
# RD Bessel → ETRS89 (EPSG:15739 "Amersfoort to ETRS89 (8)", coordinate frame)
_HELMERT = dict(tx=565.7381, ty=50.4018, tz=465.2904,
                rx=math.radians(0.395025981036064 / 3600),
                ry=math.radians(-0.330772431242031 / 3600),
                rz=math.radians(1.87607329462821 / 3600), s=4.07244e-6)


def _naar_cart(lat, lon, h, a, f):
    e2 = f * (2 - f)
    p, l = math.radians(lat), math.radians(lon)
    nu = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    return ((nu + h) * math.cos(p) * math.cos(l), (nu + h) * math.cos(p) * math.sin(l),
            (nu * (1 - e2) + h) * math.sin(p))


def _van_cart(x, y, z, a, f):
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(z + e2 * nu * math.sin(lat), p)
    return math.degrees(lat), math.degrees(lon)


def _helmert(x, y, z, inverse=False):
    k = -1 if inverse else 1
    hp = _HELMERT
    rx, ry, rz, s = k * hp["rx"], k * hp["ry"], k * hp["rz"], k * hp["s"]
    if inverse:
        x, y, z = x - hp["tx"], y - hp["ty"], z - hp["tz"]
    # coordinate frame rotation (EPSG 1032), lineair in de kleine hoeken
    x2 = (1 + s) * (x + rz * y - ry * z)
    y2 = (1 + s) * (-rz * x + y + rx * z)
    z2 = (1 + s) * (ry * x - rx * y + z)
    if not inverse:
        x2, y2, z2 = x2 + hp["tx"], y2 + hp["ty"], z2 + hp["tz"]
    return x2, y2, z2


def _bessel_naar_etrs_helmert(lat, lon):
    c = _naar_cart(lat, lon, 0.0, A_BESSEL, F_BESSEL)
    return _van_cart(*_helmert(*c), _GRS80_A, _GRS80_F)


def _etrs_naar_bessel_helmert(lat, lon):
    c = _naar_cart(lat, lon, 0.0, _GRS80_A, _GRS80_F)
    return _van_cart(*_helmert(*c, inverse=True), A_BESSEL, F_BESSEL)


# ------------------------------------------------------------- publieke API

def _verschuif(lat, lon, g: _Grid):
    """RD Bessel → ETRS89 met het rdtrans-grid (verschuiving in boogseconden)."""
    return (lat + g.waarde(0, lat, lon) / 3600.0,
            lon + g.waarde(1, lat, lon) / 3600.0)


def rd_naar_etrs89(x: float, y: float) -> dict:
    """RD x/y → ETRS89 lat/lon (graden). ``nauwkeurig`` = grid gebruikt."""
    lat_b, lon_b = rd_naar_bessel(x, y)
    g = _grid_bij(RDTRANS, lat_b, lon_b)
    if g:
        lat, lon = _verschuif(lat_b, lon_b, g)
        return {"lat": lat, "lon": lon, "nauwkeurig": True}
    lat, lon = _bessel_naar_etrs_helmert(lat_b, lon_b)
    return {"lat": lat, "lon": lon, "nauwkeurig": False}


def etrs89_naar_rd(lat: float, lon: float, h: float | None = None) -> dict:
    """ETRS89 lat/lon (graden, h optioneel in m) → RD x/y (+ NAP-hoogte).

    De gridverschuiving is gedefinieerd op RD Bessel; de inverse wordt
    iteratief opgelost (zoals PROJ ``+inv +proj=hgridshift``)."""
    lat_b, lon_b = lat, lon
    nauwkeurig = False
    g = _grid_bij(RDTRANS, lat, lon)
    if g:
        for _ in range(10):
            la, lo = _verschuif(lat_b, lon_b, g)
            d_lat, d_lon = lat - la, lon - lo
            lat_b, lon_b = lat_b + d_lat, lon_b + d_lon
            if abs(d_lat) < 1e-12 and abs(d_lon) < 1e-12:
                break
        nauwkeurig = True
    else:
        lat_b, lon_b = _etrs_naar_bessel_helmert(lat, lon)
    x, y = bessel_naar_rd(lat_b, lon_b)
    uit = {"x": x, "y": y, "nauwkeurig": nauwkeurig}
    if h is not None:
        uit["nap"] = nap_hoogte(lat, lon, h)
    return uit


def nap_hoogte(lat: float, lon: float, h: float) -> float | None:
    """Ellipsoïdische hoogte ETRS89 → NAP (quasi-geoïde NLGEO2018)."""
    g = _grid_bij(NLGEO, lat, lon)
    if not g:
        return None
    return h - g.waarde(0, lat, lon)


def status() -> dict:
    return {"rdtrans2018": bool(_grids(RDTRANS)), "nlgeo2018": bool(_grids(NLGEO))}
