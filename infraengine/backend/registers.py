"""Registers, basis-toets, kostenraming en variantenvergelijking (FO §5, §6).

De registers worden automatisch gevuld uit het tracé: elk segment, elke
kruising en elk gekruist perceel genereert de bijbehorende items. Statussen
en verantwoordelijken vult de ontwerper in de applicatie aan.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, mapping

import brk
import eigendom
from engine import (
    CL_BERM, CL_ERF, CL_FIETSPAD, CL_NATUURGROEN, CL_ONBEKEND, CL_ONVERHARD,
    CL_PAND, CL_PARKEER, CL_RIJBAAN, CL_SPOOR, CL_VERBODEN, CL_VOETPAD, CL_WATER,
    CLASS_NAMES, ZN_ARCHEO, ZN_BODEM, ZN_BODEM_ELDERS, ZN_BODEM_ONDERZOEK,
    ZN_BOOM, ZN_BUISLEIDING, ZN_GWB, ZN_KERING, ZN_KLIC, ZN_MONUMENT, ZN_NATURA,
    ZN_NGE, ZN_NNN, ZN_STILTE, ZONE_NAMES, BOOM_WORTELZONE_M,
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
    if zones_m[ZN_KERING] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Omgevingsvergunning wateractiviteit — kruising/nabijheid "
                    "waterkering",
            "bevoegd_gezag": "Waterschap",
            "trigger": f"{zones_m[ZN_KERING]:.0f} m tracé binnen de kering + "
                       f"beschermingszone (legger IMWA); ligging en diepte "
                       f"afstemmen met de keringbeheerder",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Waterschap"],
        })
    if zones_m[ZN_MONUMENT] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Omgevingsvergunning rijksmonumentenactiviteit",
            "bevoegd_gezag": "Gemeente (advies RCE)",
            "trigger": f"{zones_m[ZN_MONUMENT]:.0f} m tracé binnen een "
                       f"rijksmonument-contour (RCE)",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["RWS"],
        })
    if zones_m[ZN_STILTE] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Melding/ontheffing werken in stiltegebied",
            "bevoegd_gezag": "Provincie / omgevingsdienst",
            "trigger": f"{zones_m[ZN_STILTE]:.0f} m tracé in provinciaal "
                       f"stiltegebied; geluidseisen aan de uitvoering",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Melding"],
        })
    if zones_m[ZN_BUISLEIDING] > 0:
        items.append({
            **_basisitem(f"VRG-{len(items) + 1:03d}", "vergunning"),
            "item": "Afstemming buisleidingexploitant (Bevb; NEN 3651)",
            "bevoegd_gezag": "Leidingexploitant",
            "trigger": f"{zones_m[ZN_BUISLEIDING]:.0f} m tracé in of nabij de "
                       f"belemmeringenstrook van een buisleiding gevaarlijke "
                       f"stoffen; kruisings-/parallelligging-eisen exploitant",
            "doorlooptijd_wk": DOORLOOPTIJD_WK["Waterschap"],
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
# 6.2b Sonderingenregister: bestaande BRO-sonderingen die relevant zijn
# ---------------------------------------------------------------------------

# relevant = binnen SONDERING_TRACE_M van het tracé, of binnen
# SONDERING_BORING_M van een boorlijn (dan gekoppeld aan die boring)
SONDERING_TRACE_M = 50.0
SONDERING_BORING_M = 100.0


def build_sonderingen(route: LineString, cpts: list | None,
                      boringen: list) -> list:
    """Register van bestaande BRO-sonderingen die relevant zijn voor het tracé,
    en de koppeling naar het boorregister (in place op ``boringen``).

    ``cpts`` is de kenmerkenlijst uit ``sonderingen.langs_route`` of None als
    de BRO-dienst niet beschikbaar was — de boringen melden dat dan eerlijk.
    """
    import sonderingen as son_mod

    if cpts is None:
        for b in boringen:
            b["sonderingen"] = []
            b["sonderingen_bro"] = "onbekend (BRO-sondeerdienst niet beschikbaar)"
        return []

    boorlijnen = []
    for b in boringen:
        coords = (b.get("geometry") or {}).get("coordinates")
        lijn = (LineString([tuple(c) for c in coords])
                if coords and len(coords) >= 2
                else LineString([b["intredepunt_rd"], b["uittredepunt_rd"]]))
        boorlijnen.append((b, lijn))
        b["sonderingen"] = []

    items = []
    for cpt in cpts:
        p = Point(cpt["x"], cpt["y"])
        afstand_trace = route.distance(p)
        bij_boringen = [(b, lijn.distance(p)) for b, lijn in boorlijnen
                        if lijn.distance(p) <= SONDERING_BORING_M]
        if afstand_trace > SONDERING_TRACE_M and not bij_boringen:
            continue  # niet relevant voor dit tracé
        bij_boringen.sort(key=lambda ba: ba[1])
        relevantie = (f"nabij {', '.join(b['nr'] for b, _ in bij_boringen)}"
                      if bij_boringen else "langs tracé")
        items.append({
            **_basisitem(f"SON-{len(items) + 1:03d}", "sondering"),
            "bro_id": cpt["bro_id"],
            "punt": (round(cpt["x"], 1), round(cpt["y"], 1)),
            "chainage_m": round(route.project(p), 1),
            "afstand_trace_m": round(afstand_trace, 1),
            "einddiepte_m": cpt.get("einddiepte_m"),
            "maaiveld_nap": cpt.get("maaiveld_nap"),
            "kwaliteitsklasse": cpt.get("kwaliteitsklasse", ""),
            "norm": cpt.get("norm", ""),
            "datum": (cpt.get("datum") or "")[:10],
            "relevantie": relevantie,
            "boringen": [b["nr"] for b, _ in bij_boringen],
            "bro_loket": son_mod.loket_url(cpt["bro_id"]),
            "opmerking": "",
            "geometry": {"type": "Point", "coordinates": [cpt["x"], cpt["y"]]},
        })

    items.sort(key=lambda i: i["chainage_m"])
    for i, item in enumerate(items, 1):
        item["nr"] = f"SON-{i:03d}"

    # koppeling naar het boorregister: per boring de relevante sonderingen
    per_boring: dict = {}
    for item in items:
        for bnr in item["boringen"]:
            per_boring.setdefault(bnr, []).append(item)
    for b in boringen:
        gekoppeld = per_boring.get(b["nr"], [])
        b["sonderingen"] = [{"nr": s["nr"], "bro_id": s["bro_id"],
                             "bro_loket": s["bro_loket"]} for s in gekoppeld]
        if gekoppeld:
            b["sonderingen_bro"] = (
                "sondering(en) binnen ± 100 m bekend in de BRO: "
                + ", ".join(f"{s['nr']} ({s['bro_id']})" for s in gekoppeld))
        else:
            b["sonderingen_bro"] = (f"geen sondering binnen "
                                    f"± {SONDERING_BORING_M:.0f} m bekend — "
                                    f"nieuwe sondering ramen")
    return items


# ---------------------------------------------------------------------------
# 6.3 Zakelijk recht (ZRO): gekruiste percelen
# ---------------------------------------------------------------------------

def build_zro(route: LineString, percelen: list, werkstrook_m: float = 3.0,
              eigendom_signalen: "eigendom.Signalen | None" = None) -> list:
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
        perceel = aanduiding.strip() or props.get("identificatieLokaalID", "?")
        # eigenaar uit de BRK-import (voorbereid koppelvlak, data/brk/)
        brk_info = brk.eigenaar_van(perceel)
        eigenaar = ((brk_info["eigenaar"] + (f" — {brk_info['adres']}"
                                             if brk_info.get("adres") else ""))
                    if brk_info and brk_info.get("eigenaar")
                    else "onbekend — BRK-eigendom niet gekoppeld (licentie)")
        schatting = eigendom.schat(
            geom if geom.geom_type in ("Polygon", "MultiPolygon") else None,
            brk_info, eigendom_signalen)
        items.append({
            **_basisitem(f"ZRO-{len(items) + 1:03d}", "zro",
                         mapping(geom) if geom.geom_type in ("Polygon", "MultiPolygon") else None),
            "perceel": perceel,
            "eigenaar": eigenaar,
            "eigendom": schatting["eigendom"],
            "eigendom_toelichting": schatting["eigendom_toelichting"],
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
        bekend = sum(1 for b in hdd
                     if "bekend in de BRO" in (b.get("sonderingen_bro") or ""))
        aanleiding = f"{len(hdd)} HDD-kruising(en); bodemopbouw en boorbaarheid"
        if bekend:
            aanleiding += (f" — bij {bekend} boring(en) zijn al sonderingen "
                           f"binnen ± 100 m bekend in de BRO (opvragen i.p.v. "
                           f"nieuw ramen)")
        add("Grondonderzoek boringen (sonderingen, BRO-profielen)", aanleiding)
    if zones_m[ZN_STILTE] > 0:
        add("Werkplan stiltegebied (geluidsarme uitvoering)",
            f"{zones_m[ZN_STILTE]:.0f} m tracé in provinciaal stiltegebied")
    if zones_m[ZN_MONUMENT] > 0:
        add("Afstemming monumentenzorg (RCE/gemeente)",
            f"{zones_m[ZN_MONUMENT]:.0f} m tracé binnen een rijksmonument-contour")
    if zones_m[ZN_BUISLEIDING] > 0:
        add("Proefsleuven / liggingbepaling buisleiding (Bevb)",
            f"{zones_m[ZN_BUISLEIDING]:.0f} m tracé nabij een buisleiding "
            f"gevaarlijke stoffen; exacte ligging en eisen exploitant")
    if zones_m[ZN_NGE] > 0:
        add("NGE-vooronderzoek (niet gesprongen explosieven)",
            f"{zones_m[ZN_NGE]:.0f} m tracé in NGE-verdacht gebied "
            f"(gekoppelde bodembelastingkaart, NGE_REGIONAAL); "
            f"CS-VROO / opsporing conform WSCS-OCE")
    else:
        add("NGE (niet gesprongen explosieven)",
            "Geen gekoppelde bodembelastingkaart voor dit gebied "
            "(register NGE_REGIONAAL in pdok.py; geen landelijke open bron)",
            "handmatig beoordelen")
    return items


# ---------------------------------------------------------------------------
# Basis-toets (FO §5, MVP-omvang)
# ---------------------------------------------------------------------------

def build_checks(route: LineString, segments: list, crossings: list, boringen: list,
                 zones_m: dict | None = None,
                 bomen_rivm_fractie: float | None = None) -> list:
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

    # dekking-eisen uit het normenkader (normen.py / data/normen.json);
    # geen z-waarden in het datamodel, dus eis-rapportage i.p.v. meting
    import normen
    d_trot = f"{normen.waarde('dekking_trottoir_m'):.2f} m"
    dekking = {CL_BERM: f"{normen.waarde('dekking_berm_m'):.2f} m",
               CL_VOETPAD: d_trot, CL_FIETSPAD: d_trot, CL_PARKEER: d_trot,
               CL_RIJBAAN: f"≥ {normen.waarde('dekking_rijbaan_m'):.2f} m "
                           "of mantelbuis"}
    liggingen = {s["klasse"] for s in segments if s["klasse"] in dekking}
    for k in sorted(liggingen):
        add("info", "Dekking op maaiveld", normen.bron("dekking_berm_m"),
            f"Ligging {CLASS_NAMES[k]}: aan te houden dekking {dekking[k]} "
            "(instelbaar in het normenkader).")

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

    # de buigradiustoets (inpasbare boogstraal per knikpunt, factor × Ø uit
    # het normenkader) zit in maatvoering.toets — geen dubbeling hier

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
    elif (bomen_rivm_fractie or 0) > 0.05:
        # geen boompunten uit BGT/registers, maar het AHN-raster ziet hier wél
        # bomen: puntdekking ontbreekt, dus wortelzones zijn niet meegewogen
        add("info", "Bomen — dekking", "RIVM Bomenkaart (AHN); Handboek Bomen",
            f"Geen boompunten uit BGT of gemeentelijke registers in het zoekgebied, "
            f"terwijl de landelijke RIVM Bomenkaart hier wel boombedekking toont "
            f"(circa {bomen_rivm_fractie * 100:.0f}% van het gebied); wortelzones "
            f"zijn niet meegewogen in de route — inventariseer bomen langs het "
            f"tracé in het veld of koppel het gemeentelijke bomenregister.")
    if zones_m[ZN_ARCHEO] > 0:
        add("waarschuwing", "Archeologie", "AMK / gemeentelijke waardenkaart; Erfgoedwet",
            f"{zones_m[ZN_ARCHEO]:.0f} m tracé over AMK-terrein; onderzoek en mogelijk "
            f"begeleiding onder de vrijstellingsdiepte.")
    if zones_m[ZN_KERING] > 0:
        add("waarschuwing", "Waterkering", "Waterschapsverordening; legger keringen (IMWA)",
            f"{zones_m[ZN_KERING]:.0f} m tracé binnen een waterkering met "
            f"beschermingszone; kruising haaks en diep (of sleufloos) uitvoeren, "
            f"vergunning waterschap vereist.")
    if zones_m[ZN_BUISLEIDING] > 0:
        add("waarschuwing", "Buisleiding gevaarlijke stoffen", "Bevb; NEN 3651",
            f"{zones_m[ZN_BUISLEIDING]:.0f} m tracé in of nabij de "
            f"belemmeringenstrook van een buisleiding; kruisings- en "
            f"parallelligging-eisen van de exploitant gelden, proefsleuven vereist.")
    if zones_m[ZN_MONUMENT] > 0:
        add("waarschuwing", "Rijksmonument", "Omgevingswet; Erfgoedwet (RCE)",
            f"{zones_m[ZN_MONUMENT]:.0f} m tracé binnen een rijksmonument-contour; "
            f"vergunningplicht en mogelijk aangepaste uitvoeringswijze.")
    if zones_m[ZN_NGE] > 0:
        add("waarschuwing", "NGE-verdacht gebied", "WSCS-OCE; gemeentelijke "
            "bodembelastingkaart",
            f"{zones_m[ZN_NGE]:.0f} m tracé in NGE-verdacht gebied; "
            f"NGE-vooronderzoek vóór grondroering.")
    if zones_m[ZN_STILTE] > 0:
        add("info", "Stiltegebied", "Provinciale omgevingsverordening",
            f"{zones_m[ZN_STILTE]:.0f} m tracé in stiltegebied; geluidsarme "
            f"uitvoering en mogelijk ontheffing.")
    if zones_m[ZN_KLIC] > 0:
        add("waarschuwing", "Bestaande netten (KLIC-import)", "CROW 500; WIBON",
            f"{zones_m[ZN_KLIC]:.0f} m tracé binnen {1.5:g} m van bestaande "
            f"kabels of leidingen uit de ingelezen KLIC-levering; proefsleuven "
            f"en zorgvuldig graven (CROW 500).")

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

# Indicatieve planningsparameters voor de uitvoeringsplanning, per werkpakket
# (per organisatie instelbaar). De voorbereiding (onderzoeken, vergunningen,
# ZRO) plant de ontwerpplanning (proces.py, ONTWERP_PLANNING) — deze dict
# bevat uitsluitend de bouwfase.
UITVOERINGSPLANNING = {
    "mobilisatie_wk": 1,          # inrichten werkterrein, per werkpakket
    "wk_per_boring": {TECHNIEK_HDD: 1.0, TECHNIEK_PERSING: 1.0,
                      TECHNIEK_NANO: 0.5, TECHNIEK_RAKET: 0.5},
    "tempo_m_per_wk": 600,        # open ontgraving (graven, buis/mantel leggen)
    "kabelwerk_m_per_wk": 1200,   # kabel intrekken/leggen en aansluiten
    "wk_per_mof": 0.25,           # extra tijd per mof (lassen/meten/testen)
    "herstel_m_per_wk": 800,      # bestrating en terrein herstellen
    "oplevering_min_wk": 1,       # keuring en oplevering, per werkpakket
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
                          onderzoeken: list, checks: list,
                          sonderingen: list | None = None) -> None:
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
    for s in sonderingen or []:
        s["werkpakket"] = _wp_van(werkpakketten, s["chainage_m"])
    for c in checks:
        c["werkpakket"] = (_wp_van(werkpakketten, route.project(Point(c["punt"])))
                           if c.get("punt") else WP_TRACEBREED)


def build_uitvoeringsplanning(werkpakketten: list, boringen: list,
                              moffen: list) -> list:
    """Indicatieve uitvoeringsplanning per werkpakket, in weken vanaf de
    start van de bouw (GSU — geplande start uitvoering).

    De voorbereiding (onderzoeken, vergunningen, ZRO) is onderdeel van de
    ontwerpplanning (proces.py, ``bouw_ontwerpplanning``) en staat hier niet
    meer in. Model: één ploeg werkt de werkpakketten in strengvolgorde af;
    per werkpakket doorloopt de ploeg vaste subfasen: mobilisatie, boring/
    persing (indien van toepassing), grondwerk, kabelwerk/montage, herstel
    en oplevering. Parameters in UITVOERINGSPLANNING.
    """
    p = UITVOERINGSPLANNING
    rows: list = []

    def add(wp_nr, subfase, start, duur_wk, toelichting):
        eind = start + max(1, int(math.ceil(duur_wk))) - 1
        rows.append({
            "nr": f"PLN-{len(rows) + 1:03d}",
            "werkpakket": wp_nr, "fase": "Uitvoering", "subfase": subfase,
            "start_wk": start, "eind_wk": eind, "duur_wk": eind - start + 1,
            "status": "gepland", "toelichting": toelichting,
        })
        return eind

    klaar = 0
    for wp in werkpakketten:
        nr = wp["nr"]
        start = klaar + 1
        klaar = add(nr, "Mobilisatie/inrichting werkterrein", start,
                    p["mobilisatie_wk"], "Werkterrein inrichten en bereikbaar maken")

        boringen_wp = [b for b in boringen if b.get("werkpakket") == nr]
        if boringen_wp:
            boor_wk = sum(p["wk_per_boring"].get(b["type"], 1.0) for b in boringen_wp)
            klaar = add(nr, "Boring/persing", klaar + 1, boor_wk,
                       f"{len(boringen_wp)} boring(en)/persing(en)")

        klaar = add(nr, "Grondwerk (open sleuf)", klaar + 1,
                    wp["lengte_m"] / p["tempo_m_per_wk"],
                    f"{wp['lengte_m']:.0f} m graven, leggen en aanvullen")

        moffen_wp = [m for m in moffen if m.get("werkpakket") == nr]
        kabel_wk = (wp["lengte_m"] / p["kabelwerk_m_per_wk"]
                   + p["wk_per_mof"] * len(moffen_wp))
        toel = f"{wp['lengte_m']:.0f} m kabel intrekken en aansluiten"
        if moffen_wp:
            toel += f", {len(moffen_wp)} mof/moffen lassen en meten"
        klaar = add(nr, "Kabelwerk/montage", klaar + 1, kabel_wk, toel)

        klaar = add(nr, "Herstelwerk", klaar + 1,
                    wp["lengte_m"] / p["herstel_m_per_wk"],
                    f"{wp['lengte_m']:.0f} m bestrating en terrein herstellen")

        klaar = add(nr, "Oplevering/keuring", klaar + 1,
                    p["oplevering_min_wk"], "Keuring en oplevering werkpakket")
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
        # inschatting eigendom (eigendom.py): private percelen vragen een ZRO,
        # publieke lopen doorgaans via de AVOI-vergunning
        "percelen_privaat": sum(1 for z in zro if z.get("eigendom") == "privaat"),
        "percelen_publiek": sum(1 for z in zro if z.get("eigendom") == "publiek"),
        "percelen_eigendom_onbekend": sum(1 for z in zro
                                          if z.get("eigendom") == "onbekend"),
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
