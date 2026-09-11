"""Registers, basis-toets, kostenraming en variantenvergelijking (FO §5, §6).

De registers worden automatisch gevuld uit het tracé: elk segment, elke
kruising en elk gekruist perceel genereert de bijbehorende items. Statussen
en verantwoordelijken vult de ontwerper in de applicatie aan.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, mapping

from engine import (
    CL_BERM, CL_ERF, CL_FIETSPAD, CL_NATUURGROEN, CL_ONBEKEND, CL_ONVERHARD,
    CL_PAND, CL_PARKEER, CL_RIJBAAN, CL_SPOOR, CL_VERBODEN, CL_VOETPAD, CL_WATER,
    CLASS_NAMES, ZN_ARCHEO, ZN_BODEM, ZN_BODEM_ELDERS, ZN_BODEM_ONDERZOEK,
    ZN_BOOM, ZN_GWB, ZN_NATURA, ZN_NNN, ZONE_NAMES, BOOM_WORTELZONE_M,
    TECHNIEK_HDD, TECHNIEK_NANO, TECHNIEK_OPEN, TECHNIEK_PERSING, TECHNIEK_RAKET,
    BOOR_UITLOOP, BOOR_UITLOOP_DEFAULT, _substring,
)

# Indicatieve eenheidsprijzen (per organisatie instelbaar; FO §7 Kosten)
TARIEVEN = {
    "sleuf_per_m": {
        CL_BERM: 70, CL_VOETPAD: 95, CL_FIETSPAD: 120, CL_PARKEER: 130,
        CL_RIJBAAN: 220, CL_ERF: 100, CL_ONVERHARD: 80, CL_ONBEKEND: 90,
        CL_WATER: 0, CL_SPOOR: 0, CL_PAND: 0, CL_VERBODEN: 0,
    },
    "hdd_vast": 6000, "hdd_per_m": 350,
    "persing_vast": 4000, "persing_per_m": 250,
    "nanodrill_vast": 1500, "nanodrill_per_m": 120,
    "raket_vast": 1000, "raket_per_m": 90,
    "zro_per_perceel": 1500,
    "mof_per_stuk": 2500,
    # onderzoeken (indicatief, per stuk)
    "onderzoek_natuur_quickscan": 2500,
    "onderzoek_bodem": 4000,
    "onderzoek_archeologie": 3500,
    "onderzoek_sondering_per_boring": 800,
    "onderzoek_bea": 1500,
}

DOORLOOPTIJD_WK = {
    "AVOI": (4, 8), "Waterschap": (8, 8), "ProRail": (12, 26),
    "RWS": (8, 26), "Provincie": (8, 8), "Natuur": (13, 26), "Verkeer": (4, 8),
    "Melding": (1, 4), "KLIC": (1, 1),
}

LEGE_ZONES = {bit: 0.0 for bit in ZONE_NAMES}


def _basisitem(nr, register, geom=None):
    item = {
        "nr": nr,
        "status": "nog aan te vragen" if register == "vergunning" else "open",
        "verantwoordelijke": "",
        "documenten": [],
    }
    if geom is not None:
        item["geometry"] = geom
    return item


# ---------------------------------------------------------------------------
# 6.1 Vergunningen en meldingen
# ---------------------------------------------------------------------------

def build_vergunningen(segments: list, crossings: list, gemeente: str | None,
                       zones_m: dict | None = None) -> list:
    items = []
    gem = gemeente or "gemeente (naam onbekend)"
    zones_m = {**LEGE_ZONES, **(zones_m or {})}

    publiek = [s for s in segments if s["klasse"] in
               (CL_BERM, CL_VOETPAD, CL_FIETSPAD, CL_PARKEER, CL_RIJBAAN, CL_ONVERHARD)]
    if publiek:
        lengte = round(sum(s["lengte_m"] for s in publiek))
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Instemmingsbesluit / vergunning kabels en leidingen (AVOI)",
            "bevoegd_gezag": gem.capitalize() if gemeente else gem,
            "trigger": f"{lengte} m tracé in openbare grond",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["AVOI"],
        })

    for c in crossings:
        if c["soort"] == "water":
            zwaar = c["techniek"] == TECHNIEK_HDD
            items.append({
                **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
                "item": ("Omgevingsvergunning wateractiviteit" if zwaar
                         else "Melding waterschapsverordening"),
                "bevoegd_gezag": "Waterschap",
                "trigger": f"Kruising watergang {c['nr']} ({c['breedte_m']} m, {c['techniek']})",
                "doorlooptijd_wk": DOORLOOPTIJD_WK["Waterschap"],
                "kruising": c["nr"],
            })
        elif c["soort"] == "spoor":
            items.append({
                **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
                "item": "Toestemming kruising spoor",
                "bevoegd_gezag": "ProRail",
                "trigger": f"Kruising spoor {c['nr']}",
                "doorlooptijd_wk": DOORLOOPTIJD_WK["ProRail"],
                "kruising": c["nr"],
            })
        elif c["soort"] == "rijbaan":
            items.append({
                **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
                "item": "Tijdelijke verkeersmaatregelen / verkeersbesluit",
                "bevoegd_gezag": "Wegbeheerder",
                "trigger": f"Kruising rijbaan {c['nr']} ({c['techniek']}); CROW 96b",
                "doorlooptijd_wk": DOORLOOPTIJD_WK["Verkeer"],
                "kruising": c["nr"],
            })

    boringen = [c for c in crossings if c["techniek"] in (TECHNIEK_HDD, TECHNIEK_PERSING)]
    if boringen:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Melding bemaling / lozing bij boringen",
            "bevoegd_gezag": "Gemeente / waterschap",
            "trigger": f"{len(boringen)} boring(en) of persing(en)",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Melding"],
        })

    if zones_m[ZN_NATURA] > 0 or zones_m[ZN_NNN] > 0:
        delen = []
        if zones_m[ZN_NATURA] > 0:
            delen.append(f"{zones_m[ZN_NATURA]:.0f} m in Natura 2000")
        if zones_m[ZN_NNN] > 0:
            delen.append(f"{zones_m[ZN_NNN]:.0f} m in NNN")
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Natuur: flora- en fauna-activiteit / Natura 2000-activiteit",
            "bevoegd_gezag": "Provincie",
            "trigger": "; ".join(delen) + " — uitkomst quickscan bepaalt vervolg",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Natuur"],
        })
    if zones_m[ZN_GWB] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Melding/ontheffing grondwaterbeschermingsgebied "
                    "(boorvloeistof, bemaling)",
            "bevoegd_gezag": "Provincie / omgevingsdienst",
            "trigger": f"{zones_m[ZN_GWB]:.0f} m tracé in grondwaterbeschermingsgebied",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Melding"],
        })
    if zones_m[ZN_BODEM] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Melding graven in bodem met vastgestelde verontreiniging (Bal)",
            "bevoegd_gezag": "Gemeente",
            "trigger": f"{zones_m[ZN_BODEM]:.0f} m tracé door verontreinigd of "
                       f"nazorggebied (BRO SLD)",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Melding"],
        })
    if zones_m[ZN_BOOM] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Bomen: toestemming werken in wortelzone / evt. kapvergunning",
            "bevoegd_gezag": gem.capitalize() if gemeente else gem,
            "trigger": f"{zones_m[ZN_BOOM]:.0f} m tracé binnen de wortelzone van bomen "
                       f"(BGT/gemeentelijk register) — uitkomst BEA bepaalt kap of "
                       f"bescherming",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["AVOI"],
        })
    if zones_m[ZN_ARCHEO] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Archeologie: programma van eisen / begeleiding",
            "bevoegd_gezag": "Gemeente",
            "trigger": f"{zones_m[ZN_ARCHEO]:.0f} m tracé over AMK-terrein",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Provincie"],
        })

    items.append({
        **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
        "item": "KLIC oriëntatiemelding (ontwerpfase)",
        "bevoegd_gezag": "Kadaster",
        "trigger": "Ontwerpfase — verplicht vóór definitief ontwerp",
        "doorlooptijd_wk": DOORLOOPTIJD_WK["KLIC"],
    })
    items.append({
        **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
        "item": "KLIC graafmelding (max. 20 werkdagen vóór graven)",
        "bevoegd_gezag": "Kadaster",
        "trigger": "Vóór uitvoering",
        "doorlooptijd_wk": DOORLOOPTIJD_WK["KLIC"],
    })
    return items


# ---------------------------------------------------------------------------
# 6.2 Boringen, persingen en nanodrills
# ---------------------------------------------------------------------------

def build_boringen(route: LineString, crossings: list) -> list:
    items = []
    for c in crossings:
        if c["techniek"] not in (TECHNIEK_HDD, TECHNIEK_PERSING, TECHNIEK_NANO,
                                 TECHNIEK_RAKET):
            continue
        uitloop = BOOR_UITLOOP.get(c["techniek"], BOOR_UITLOOP_DEFAULT)
        m_in = max(0.0, c["chainage_van_m"] - uitloop)
        m_uit = min(route.length, c["chainage_tot_m"] + uitloop)
        p_in, p_uit = route.interpolate(m_in), route.interpolate(m_uit)
        # boorlijn exact op het tracé; waar het rechttrekken lukte is dit de
        # rechte lijn intrede→uittrede, anders volgt hij de (gebogen) route
        # en wordt de afwijking van de koorde getoetst (boring moet recht)
        boorlijn = _substring(route, m_in, m_uit)
        koorde = LineString([boorlijn.coords[0], boorlijn.coords[-1]])
        afwijking = max(koorde.distance(Point(xy)) for xy in boorlijn.coords)
        recht = afwijking <= 0.25
        dekking = {
            "water": "≥ 1,0–1,5 m onder leggerbodem (keur; NEN 3651)",
            "rijbaan": "≥ 1,2 m onder wegdek (AVOI wegbeheerder)",
            "spoor": "conform ProRail-voorschrift, stalen mantelbuis",
        }.get(c["soort"], "≥ 1,0 m")
        wt = c.get("werkterrein") or {}
        items.append({
            **_basisitem(f"BOR-{len(items) + 1:03d}", "boring"),
            "type": c["techniek"],
            "type_oorspronkelijk": c.get("techniek_oorspronkelijk", ""),
            "kruising": c["nr"],
            "obstakel": f"{c['soort']} ({c['breedte_m']} m)",
            "noodzaak": c.get("noodzaak", ""),
            "intredepunt_rd": (round(p_in.x, 2), round(p_in.y, 2)),
            "uittredepunt_rd": (round(p_uit.x, 2), round(p_uit.y, 2)),
            "uitloop_m": uitloop,
            "lengte_m": round(m_uit - m_in, 1),
            "recht": recht,
            "afwijking_recht_m": round(afwijking, 2),
            "geometry": mapping(boorlijn),
            "dekking_eis": dekking,
            "mantelbuis": "1× HDPE Ø160 SDR11 (voorstel)" if c["soort"] != "spoor"
                          else "Stalen mantelbuis (voorstel)",
            "bodemprofiel": "BRO-profiel nog op te halen (fase 2)",
            "berekeningen": "Sterkte / boorvloeistofdruk: nog niet uitgevoerd",
            "werkterrein_oordeel": wt.get("oordeel", "niet getoetst"),
            "werkterrein_intrede_m2": wt.get("intrede_m2"),
            "werkterrein_intrede_eis_m2": wt.get("intrede_eis_m2"),
            "werkterrein_uittrede_m2": wt.get("uittrede_m2"),
            "werkterrein_uittrede_eis_m2": wt.get("uittrede_eis_m2"),
            "werkterrein_opmerking": wt.get("opmerking", ""),
        })
    return items


# ---------------------------------------------------------------------------
# 6.3 Zakelijk recht (ZRO): gekruiste percelen
# ---------------------------------------------------------------------------

def build_zro(route: LineString, percelen: list, werkstrook_m: float = 3.0) -> list:
    from shapely.prepared import prep

    items = []
    buffer = route.buffer(0.5)
    strook = route.buffer(werkstrook_m)
    # prepared geometry: bij lange tracés (corridor-modus) zijn er tienduizenden
    # kandidaat-percelen; de voorselectie moet goedkoop zijn
    pbuffer = prep(buffer)
    for geom, props in percelen:
        if not pbuffer.intersects(geom):
            continue
        snede = route.intersection(geom)
        lengte = snede.length
        if lengte < 1.0:
            continue
        opp = geom.intersection(strook).area
        aanduiding = " ".join(str(props.get(k, "")) for k in
                              ("kadastraleGemeenteWaarde", "sectie", "perceelnummer"))
        items.append({
            **_basisitem(f"ZRO-{len(items) + 1:03d}", "zro",
                         mapping(geom) if geom.geom_type in ("Polygon", "MultiPolygon") else None),
            "perceel": aanduiding.strip() or props.get("identificatieLokaalID", "?"),
            "eigenaar": "onbekend — BRK-eigendom niet gekoppeld (licentie)",
            "ingenomen_lengte_m": round(lengte, 1),
            "chainage_m": round(route.project(snede.centroid), 1),
            "werkstrook_m2": round(opp),
            "aard_recht": "opstalrecht / gedoogplicht (te bepalen)",
            "workflow": "contact te leggen",
        })
    items.sort(key=lambda i: -i["ingenomen_lengte_m"])
    for i, item in enumerate(items, 1):
        item["nr"] = f"ZRO-{i:03d}"
    return items


# ---------------------------------------------------------------------------
# 6.4 Onderzoeken (FO): bodem, archeologie, natuur, grondonderzoek, NGE
# ---------------------------------------------------------------------------

def build_onderzoeken(zones_m: dict | None, boringen: list) -> list:
    zones_m = {**LEGE_ZONES, **(zones_m or {})}
    items = []

    def add(soort, aanleiding, conclusie="nog uit te voeren"):
        items.append({
            **_basisitem(f"OND-{len(items) + 1:03d}", "onderzoek"),
            "soort": soort, "aanleiding": aanleiding,
            "rapport": "", "conclusie": conclusie,
        })

    add("KLIC-oriëntatiemelding", "Ontwerpfase (WIBON); ligging bestaande netten")
    if zones_m[ZN_NATURA] > 0 or zones_m[ZN_NNN] > 0:
        add("Natuur-quickscan (flora en fauna)",
            f"{zones_m[ZN_NATURA] + zones_m[ZN_NNN]:.0f} m tracé in of nabij "
            f"beschermd natuurgebied")
    if zones_m[ZN_BODEM] > 0:
        add("Milieuhygiënisch bodemonderzoek + saneringsplan-check",
            f"{zones_m[ZN_BODEM]:.0f} m tracé door verontreinigd of nazorggebied "
            f"(BRO SLD); veiligheidsklasse CROW 400, raadpleeg het overheidsbesluit")
    if zones_m[ZN_BODEM_ONDERZOEK] > 0:
        add("Milieuhygiënisch bodemonderzoek (vooronderzoek NEN 5725)",
            f"{zones_m[ZN_BODEM_ONDERZOEK]:.0f} m tracé over bekende "
            f"onderzoekslocatie (BRO SAD / historisch Wbb); rapporten opvragen, "
            f"bepaalt veiligheidsklasse CROW 400")
    if (zones_m[ZN_BODEM] == 0 and zones_m[ZN_BODEM_ONDERZOEK] == 0
            and zones_m[ZN_BODEM_ELDERS] > 0):
        add("Bodeminformatie opvragen bij bevoegd gezag",
            f"{zones_m[ZN_BODEM_ELDERS]:.0f} m tracé in gebied waar het bevoegd "
            f"gezag bodemdata (nog) via een eigen loket publiceert; BRO SAD/SLD "
            f"en het landelijke Bodemloket zijn hier mogelijk niet dekkend")
    if zones_m[ZN_ARCHEO] > 0:
        add("Archeologisch bureauonderzoek / IVO",
            f"{zones_m[ZN_ARCHEO]:.0f} m tracé over AMK-terrein")
    if zones_m[ZN_BOOM] > 0:
        add("Bomen Effect Analyse (BEA)",
            f"{zones_m[ZN_BOOM]:.0f} m tracé binnen de wortelzone van bomen "
            f"(kroonprojectie, minimaal r = {BOOM_WORTELZONE_M:.1f} m; bron BGT/"
            f"gemeentelijk register); Handboek Bomen — dekking wisselt per "
            f"gemeente, veldcheck nodig")
    hdd = [b for b in boringen if b["type"] == TECHNIEK_HDD]
    if hdd:
        add("Grondonderzoek boringen (sonderingen, BRO-profielen)",
            f"{len(hdd)} HDD-kruising(en); bodemopbouw en boorbaarheid")
    add("NGE (niet gesprongen explosieven)",
        "Gemeentelijke bodembelastingkaart niet als open laag beschikbaar",
        "handmatig beoordelen (fase 2)")
    return items


# ---------------------------------------------------------------------------
# Basis-toets (FO §5, MVP-omvang)
# ---------------------------------------------------------------------------

def build_checks(route: LineString, segments: list, crossings: list, boringen: list,
                 zones_m: dict | None = None) -> list:
    checks = []
    zones_m = {**LEGE_ZONES, **(zones_m or {})}

    def add(ernst, toets, grondslag, melding, punt=None):
        checks.append({"ernst": ernst, "toets": toets, "grondslag": grondslag,
                       "melding": melding, "punt": punt})

    for s in segments:
        if s["klasse"] in (CL_PAND, CL_VERBODEN):
            add("kritiek", "Uitsluiting", "BGT/BAG; wegingsprofiel",
                f"Segment {s['nr']} loopt door een uitgesloten zone ({s['ligging']}).")
        if s["klasse"] == CL_RIJBAAN and s["lengte_m"] > 25:
            add("waarschuwing", "Ligging in rijbaan", "NEN 7171-1; AVOI",
                f"Segment {s['nr']} ligt {s['lengte_m']} m in de lengterichting van de "
                f"rijbaan; wegbeheerder weigert dit vaak. Overweeg trottoir of berm.")
        if s["klasse"] == CL_ERF:
            add("waarschuwing", "Privaat terrein (aanname)", "BRK niet gekoppeld",
                f"Segment {s['nr']} ({s['lengte_m']} m) ligt op erf; ZRO waarschijnlijk "
                f"nodig. Controleer eigendom via BRK.")

    dekking = {CL_BERM: "0,80 m", CL_VOETPAD: "0,80 m", CL_FIETSPAD: "0,80 m",
               CL_PARKEER: "0,80 m", CL_RIJBAAN: "≥ 1,00 m of mantelbuis"}
    liggingen = {s["klasse"] for s in segments if s["klasse"] in dekking}
    for k in sorted(liggingen):
        add("info", "Dekking op maaiveld", "Kabelleggingsnorm netbeheerder; NEN 7171-1",
            f"Ligging {CLASS_NAMES[k]}: aan te houden dekking {dekking[k]} (instelbare norm).")

    for b in boringen:
        if not b.get("recht", True):
            ernst = ("kritiek" if b["type"] in (TECHNIEK_PERSING, TECHNIEK_RAKET)
                     else "waarschuwing")
            add(ernst, "Boortracé niet recht", "NEN 3650; persing/raket boren recht",
                f"{b['nr']} ({b['type']}): tracé wijkt tot {b['afwijking_recht_m']} m "
                "af van de rechte lijn intrede–uittrede (rechttrekken strandde op "
                "een obstakel); in-/uittredepunt verschuiven of techniek "
                "heroverwegen.", b["intredepunt_rd"])

    for c in crossings:
        if not c.get("techniek"):
            add("kritiek", "Kruising zonder techniek", "Beslistabel §4",
                f"Kruising {c['nr']} heeft geen techniekvoorstel.", c["punt"])
        elif c["techniek"] == TECHNIEK_HDD:
            add("info", "Boorplan vereist", c["richtlijn"],
                f"{c['nr']}: HDD vraagt boorplan en lengteprofiel (fase 2); "
                f"bodemprofiel BRO nog op te halen.", c["punt"])

        # werkterrein-toets (globaal): ruim voldoende = akkoord, twijfel =
        # afhankelijk van type boorstelling, geen ruimte = alternatief of maatwerk
        wt = c.get("werkterrein")
        if wt:
            if c.get("techniek_oorspronkelijk"):
                add("waarschuwing", "Werkterrein boring",
                    "Richtlijn boortechnieken; CROW 308",
                    f"{c['nr']}: onvoldoende werkruimte voor "
                    f"{c['techniek_oorspronkelijk']}; sleufloos alternatief "
                    f"toegepast: {c['techniek']}.", c["punt"])
            if wt["oordeel"] == "onzeker":
                add("waarschuwing", "Werkterrein boring",
                    "Richtlijn boortechnieken; CROW 308",
                    f"{c['nr']}: werkruimte onzeker (intrede {wt['intrede_m2']} m² "
                    f"bij eis {wt['intrede_eis_m2']} m², uittrede {wt['uittrede_m2']} m² "
                    f"bij eis {wt['uittrede_eis_m2']} m²); uitvoerbaarheid afhankelijk "
                    f"van type boorstelling.", c["punt"])
            elif wt["oordeel"] == "onvoldoende":
                add("kritiek", "Werkterrein boring",
                    "Richtlijn boortechnieken; CROW 308",
                    f"{c['nr']}: geen sleufloze techniek met voldoende werkruimte "
                    f"binnen de richtlijnen; maatwerk vereist (in-/uittredepunt "
                    f"verplaatsen, langere boring of ander tracé).", c["punt"])

    # scherpe knikken (minimale buigradius, vereenvoudigde toets)
    coords = list(route.coords)
    for i in range(1, len(coords) - 1):
        ax, ay = coords[i - 1]; bx, by = coords[i]; cx, cy = coords[i + 1]
        v1 = (bx - ax, by - ay); v2 = (cx - bx, cy - by)
        l1 = math.hypot(*v1); l2 = math.hypot(*v2)
        if l1 < 0.1 or l2 < 0.1:
            continue
        cosa = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
        hoek = math.degrees(math.acos(cosa))
        if hoek > 80:
            chainage = route.project(Point(bx, by))
            add("waarschuwing", "Buigradius", "IEC-norm / kabelspecificatie (15 × D)",
                f"Scherpe richtingsverandering ({hoek:.0f}°) op chainage {chainage:.0f} m; "
                f"controleer minimale buigradius.", (round(bx, 2), round(by, 2)))

    # zonelagen (FO §5)
    if zones_m[ZN_NATURA] > 0:
        add("waarschuwing", "Natura 2000", "Omgevingswet; natuurvergunning provincie",
            f"{zones_m[ZN_NATURA]:.0f} m tracé binnen Natura 2000 (alleen via bestaande "
            f"weg of berm toegestaan); natuurtoets en mogelijk vergunning met lange "
            f"doorlooptijd.")
    if zones_m[ZN_NNN] > 0:
        add("waarschuwing", "Natuurnetwerk Nederland", "Provinciale omgevingsverordening",
            f"{zones_m[ZN_NNN]:.0f} m tracé in NNN; compensatieplicht mogelijk, "
            f"quickscan vereist.")
    if zones_m[ZN_GWB] > 0:
        add("waarschuwing", "Grondwaterbescherming", "Provinciale omgevingsverordening",
            f"{zones_m[ZN_GWB]:.0f} m tracé in grondwaterbeschermingsgebied; beperkingen "
            f"aan boorvloeistof (HDD) en bemaling.")
    if zones_m[ZN_BODEM] > 0:
        add("waarschuwing", "Bodemverontreiniging", "BRO SLD; Bal (graven in "
            "verontreinigde bodem); CROW 400",
            f"{zones_m[ZN_BODEM]:.0f} m tracé door gebied met een overheidsbesluit "
            f"bodemverontreiniging of nazorg (BRO SLD); melding Bal vereist, "
            f"veiligheidsklasse en afvoerkosten bepalen.")
    if zones_m[ZN_BODEM_ONDERZOEK] > 0:
        add("waarschuwing", "Bodemkwaliteit", "BRO SAD / Bodemloket; CROW 400",
            f"{zones_m[ZN_BODEM_ONDERZOEK]:.0f} m tracé over een bekende "
            f"bodemonderzoekslocatie; vooronderzoek (NEN 5725) bepaalt "
            f"veiligheidsklasse en afvoerkosten.")
    if zones_m[ZN_BODEM_ELDERS] > 0:
        add("info", "Bodemkwaliteit — dekking", "Bodemloket (beschikbaarheid gegevens)",
            f"{zones_m[ZN_BODEM_ELDERS]:.0f} m tracé in gebied waarvan het bevoegd gezag "
            f"bodemdata via een eigen loket publiceert; controleer of BRO SAD/SLD hier "
            f"al gevuld is en raadpleeg anders het gemeentelijke/provinciale loket.")
    if zones_m[ZN_BOOM] > 0:
        add("waarschuwing", "Bomen (wortelzone)",
            "Handboek Bomen; gemeentelijke bomenverordening / APV",
            f"{zones_m[ZN_BOOM]:.0f} m tracé binnen de wortelzone van bomen "
            f"(kroonprojectie, minimaal r = {BOOM_WORTELZONE_M:.1f} m); graven in de "
            f"wortelzone staat de gemeente vaak niet toe — sleufloze passage, tracé "
            f"verleggen of BEA met beschermingsmaatregelen. Bronnen: BGT-plustopografie "
            f"en gekoppelde gemeentelijke registers (BOMEN_REGIONAAL); dekking is niet "
            f"landsdekkend, veldcheck blijft nodig.")
    if zones_m[ZN_ARCHEO] > 0:
        add("waarschuwing", "Archeologie", "AMK / gemeentelijke waardenkaart; Erfgoedwet",
            f"{zones_m[ZN_ARCHEO]:.0f} m tracé over AMK-terrein; onderzoek en mogelijk "
            f"begeleiding onder de vrijstellingsdiepte.")

    add("info", "Graafveiligheid", "CROW 500; WIBON",
        "KLIC-oriëntatiemelding vereist in de ontwerpfase; deze prototype-versie "
        "rekent zonder KLIC-data (handmatige levering in fase 1, API in fase 2).")
    orde = {"kritiek": 0, "waarschuwing": 1, "info": 2}
    checks.sort(key=lambda c: orde[c["ernst"]])
    return checks


# ---------------------------------------------------------------------------
# Werkpakketten: elk tracédeel van station tot station is één genummerd
# werkpakket (WP-01, WP-02, …). Alle registerregels krijgen het werkpakket
# waarin ze vallen; regels zonder vaste plek op het tracé gelden tracébreed.
# ---------------------------------------------------------------------------

WP_TRACEBREED = "tracébreed"

# Indicatieve planningsparameters per werkpakket (per organisatie instelbaar)
PLANNING = {
    "tempo_m_per_wk": 600,       # open ontgraving incl. aanvullen en herstel
    "wk_per_boring": {TECHNIEK_HDD: 1.0, TECHNIEK_PERSING: 1.0,
                      TECHNIEK_NANO: 0.5, TECHNIEK_RAKET: 0.5},
    "voorbereiding_min_wk": 6,   # engineering en werkvoorbereiding
    "onderzoeken_wk": 8,
    "zro_wk": 16,
    "uitvoering_min_wk": 1,
}


def build_werkpakketten(route: LineString, stations: list) -> list:
    """Tracé opdelen in werkpakketten: van station tot station, in strengvolgorde."""
    ch = [route.project(Point(s)) for s in stations]
    ch[0], ch[-1] = 0.0, route.length
    for i in range(1, len(ch)):  # volgorde = streng; chainage mag niet dalen
        ch[i] = max(ch[i], ch[i - 1])
    items = []
    for i in range(len(stations) - 1):
        m0, m1 = ch[i], ch[i + 1]
        items.append({
            "nr": f"WP-{i + 1:02d}",
            "naam": f"Station {i + 1} – Station {i + 2}",
            "van_station": i + 1,
            "tot_station": i + 2,
            "chainage_van_m": round(m0, 1),
            "chainage_tot_m": round(m1, 1),
            "lengte_m": round(m1 - m0, 1),
            "geometry": mapping(_substring(route, m0, m1)),
        })
    return items


def _wp_van(werkpakketten: list, m: float) -> str:
    for wp in werkpakketten:
        if m <= wp["chainage_tot_m"] + 0.05:
            return wp["nr"]
    return werkpakketten[-1]["nr"] if werkpakketten else WP_TRACEBREED


def ken_werkpakketten_toe(werkpakketten: list, route: LineString,
                          segments: list, crossings: list, boringen: list,
                          moffen: list, zro: list, vergunningen: list,
                          onderzoeken: list, checks: list) -> None:
    """Elke registerregel het werkpakket geven waarin hij valt (op chainage);
    zo zijn alle registers per werkpakket te sorteren en te filteren."""
    if not werkpakketten:
        return
    for s in segments:
        s["werkpakket"] = _wp_van(werkpakketten, (s["van_m"] + s["tot_m"]) / 2)
    kruising_wp = {}
    for c in crossings:
        c["werkpakket"] = _wp_van(
            werkpakketten, (c["chainage_van_m"] + c["chainage_tot_m"]) / 2)
        kruising_wp[c["nr"]] = c["werkpakket"]
    for b in boringen:
        b["werkpakket"] = kruising_wp.get(b["kruising"], WP_TRACEBREED)
    for m in moffen:
        m["werkpakket"] = _wp_van(werkpakketten, m["chainage_m"])
    for z in zro:
        z["werkpakket"] = (_wp_van(werkpakketten, z["chainage_m"])
                           if z.get("chainage_m") is not None else WP_TRACEBREED)
    for v in vergunningen:
        v["werkpakket"] = kruising_wp.get(v.get("kruising"), WP_TRACEBREED)
    for o in onderzoeken:
        o["werkpakket"] = WP_TRACEBREED
    for c in checks:
        c["werkpakket"] = (_wp_van(werkpakketten, route.project(Point(c["punt"])))
                           if c.get("punt") else WP_TRACEBREED)


def build_planning(werkpakketten: list, vergunningen: list, onderzoeken: list,
                   boringen: list, zro: list) -> list:
    """Indicatieve planning per werkpakket, in weken vanaf projectstart.

    Model: de voorbereiding (onderzoeken, vergunningen, ZRO) start voor elk
    werkpakket in week 1 en loopt parallel; de uitvoering gebeurt met één
    ploeg in strengvolgorde en start zodra de eigen voorbereiding én de
    uitvoering van het vorige werkpakket klaar zijn. Parameters in PLANNING.
    """
    p = PLANNING
    rows: list = []

    def add(wp_nr, fase, start, duur_wk, toelichting):
        eind = start + max(1, int(math.ceil(duur_wk))) - 1
        rows.append({
            "nr": f"PLN-{len(rows) + 1:03d}",
            "werkpakket": wp_nr, "fase": fase,
            "start_wk": start, "eind_wk": eind, "duur_wk": eind - start + 1,
            "status": "gepland", "toelichting": toelichting,
        })
        return eind

    verg_breed = max((max(v["doorlooptijd_wk"]) for v in vergunningen
                      if v.get("werkpakket", WP_TRACEBREED) == WP_TRACEBREED),
                     default=0)
    uitvoer_klaar = 0
    for wp in werkpakketten:
        nr = wp["nr"]
        klaar = 0
        if onderzoeken:
            klaar = max(klaar, add(nr, "Onderzoeken", 1, p["onderzoeken_wk"],
                                   f"{len(onderzoeken)} onderzoek(en), tracébreed"))
        verg_wp = [v for v in vergunningen if v.get("werkpakket") == nr]
        verg_wk = max([max(v["doorlooptijd_wk"]) for v in verg_wp]
                      + [verg_breed, p["voorbereiding_min_wk"]])
        klaar = max(klaar, add(
            nr, "Vergunningen en werkvoorbereiding", 1, verg_wk,
            f"{len(verg_wp)} werkpakket-specifiek + tracébrede vergunningen; "
            f"langste doorlooptijd bepaalt"))
        percelen = sum(1 for z in zro if z.get("werkpakket") == nr)
        if percelen:
            klaar = max(klaar, add(nr, "Zakelijk recht (ZRO)", 1, p["zro_wk"],
                                   f"{percelen} perceel/percelen"))
        boringen_wp = [b for b in boringen if b.get("werkpakket") == nr]
        boor_wk = sum(p["wk_per_boring"].get(b["type"], 1.0) for b in boringen_wp)
        duur = max(p["uitvoering_min_wk"],
                   wp["lengte_m"] / p["tempo_m_per_wk"] + boor_wk)
        toel = f"{wp['lengte_m']:.0f} m sleufwerk"
        if boringen_wp:
            toel += f", {len(boringen_wp)} boring(en)"
        toel += " — één ploeg, in strengvolgorde"
        uitvoer_klaar = add(nr, "Uitvoering", max(klaar, uitvoer_klaar) + 1,
                            duur, toel)
    return rows


# ---------------------------------------------------------------------------
# Kosten en MCA
# ---------------------------------------------------------------------------

def build_kosten(segments: list, crossings: list, zro: list, moffen: list,
                 onderzoeken: list | None = None) -> dict:
    t = TARIEVEN
    sleuf = sum(t["sleuf_per_m"].get(s["klasse"], 90) * s["lengte_m"] for s in segments
                if s["klasse"] not in (CL_WATER, CL_SPOOR))
    boringen = 0.0
    for c in crossings:
        lengte = (c.get("kruislengte_m", c["breedte_m"])
                  + 2 * BOOR_UITLOOP.get(c["techniek"], BOOR_UITLOOP_DEFAULT))
        if c["techniek"] == TECHNIEK_HDD:
            boringen += t["hdd_vast"] + t["hdd_per_m"] * lengte
        elif c["techniek"] == TECHNIEK_PERSING:
            boringen += t["persing_vast"] + t["persing_per_m"] * lengte
        elif c["techniek"] == TECHNIEK_NANO:
            boringen += t["nanodrill_vast"] + t["nanodrill_per_m"] * lengte
        elif c["techniek"] == TECHNIEK_RAKET:
            boringen += t["raket_vast"] + t["raket_per_m"] * lengte
    zro_kosten = t["zro_per_perceel"] * len(zro)
    mof_kosten = t["mof_per_stuk"] * len(moffen)
    onderzoek_kosten = 0.0
    n_hdd = sum(1 for c in crossings if c["techniek"] == TECHNIEK_HDD)
    for o in onderzoeken or []:
        soort = o["soort"].lower()
        if "quickscan" in soort:
            onderzoek_kosten += t["onderzoek_natuur_quickscan"]
        elif "bodemonderzoek" in soort:
            onderzoek_kosten += t["onderzoek_bodem"]
        elif "archeologisch" in soort:
            onderzoek_kosten += t["onderzoek_archeologie"]
        elif "grondonderzoek" in soort:
            onderzoek_kosten += t["onderzoek_sondering_per_boring"] * max(1, n_hdd)
        elif "bomen effect" in soort:
            onderzoek_kosten += t["onderzoek_bea"]
    totaal = sleuf + boringen + zro_kosten + mof_kosten + onderzoek_kosten
    return {
        "sleufwerk": round(sleuf), "boringen": round(boringen),
        "zro": round(zro_kosten), "moffen": round(mof_kosten),
        "onderzoeken": round(onderzoek_kosten),
        "totaal": round(totaal),
        "toelichting": "Directe kosten, indicatief (TARIEVEN); de volledige "
                       "RAW-calculatie met staartkosten staat in het "
                       "calculatieregister.",
    }


def build_mca_row(naam: str, route: LineString, segments: list, crossings: list,
                  zro: list, vergunningen: list, kosten: dict) -> dict:
    per_techniek: dict = {}
    for c in crossings:
        per_techniek[c["techniek"]] = per_techniek.get(c["techniek"], 0) + 1
    privaat = sum(s["lengte_m"] for s in segments if s["klasse"] == CL_ERF)
    doorloop = max([max(v["doorlooptijd_wk"]) for v in vergunningen], default=0)
    return {
        "variant": naam,
        "lengte_m": round(route.length),
        "kruisingen": per_techniek,
        "meters_privaat_m": round(privaat),
        "aantal_percelen": len(zro),
        "aantal_vergunningen": len(vergunningen),
        "kosten_eur": kosten.get("aannemingssom_excl_btw", kosten["totaal"]),
        "doorlooptijd_wk": doorloop,
    }


# Variant-wegingsprofielen (FO §3.4)
VARIANT_PROFIELEN = {
    "Voorkeursvariant": {},
    "Kortste": {"berm_groen": 1.0, "voetpad": 1.0, "fietspad": 1.0, "parkeervlak": 1.0,
                "rijbaan": 1.4, "erf_prive": 1.2, "overig_onverhard": 1.0,
                "water_kruising": 8.0, "spoor_kruising": 40.0},
    "Maximaal publiek": {"erf_prive": 8.0},
    "Minimaal boringen": {"water_kruising": 45.0, "spoor_kruising": 150.0, "rijbaan": 6.0},
}
