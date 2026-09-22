"""RAW-calculatie: complete inschrijvingsbegroting volgens de RAW-systematiek.

Uit het berekende tracé wordt een volledig uitgewerkte calculatie opgebouwd
naar het model van een RAW-bestek (Standaard RAW Bepalingen, CROW):
bestekposten per hoofdstuk met postnummer, omschrijving, eenheid, hoeveelheid
(resultaatsverplichting), eenheidsprijs en totaal, gevolgd door de
inschrijvingsstaat (staart): eenmalige kosten (91.88), uitvoeringskosten
(92.88), algemene kosten (93.88), winst en risico (94.88) en korting (95.88).

De eenheidsprijzen zijn FICTIEF maar realistisch (prijspeil 2026, excl. btw)
en per organisatie instelbaar in EENHEIDSPRIJZEN en STAART hieronder. De
hoeveelheden volgen uit de registers: segmenten (ligging per BGT-klasse),
boringen, moffen, zonelagen en stelposten uit de kostenraming.
"""
from __future__ import annotations

import math

from engine import (
    CL_BERM, CL_ERF, CL_FIETSPAD, CL_NATUURGROEN, CL_ONBEKEND, CL_ONVERHARD,
    CL_PARKEER, CL_RIJBAAN, CL_SPOOR, CL_VOETPAD, CL_WATER,
    TECHNIEK_HDD, TECHNIEK_NANO, TECHNIEK_OPEN, TECHNIEK_PERSING, TECHNIEK_RAKET,
    ZN_BODEM, ZN_BODEM_ONDERZOEK, ZN_BOOM,
)

# ---------------------------------------------------------------------------
# Uitgangspunten sleufprofiel en herstel (instelbaar)
# ---------------------------------------------------------------------------
SLEUF_BREEDTE_M = 0.60          # bodembreedte kabelsleuf MS-circuit
SLEUF_DIEPTE_M = 1.20           # ontgravingsdiepte (dekking ± 1,0 m)
ZANDBED_DIKTE_M = 0.40          # kabelbed + omhulling in schoon zand
HERSTEL_ELEMENTEN_M = 0.80      # herstelbreedte elementenverharding
HERSTEL_ASFALT_M = 1.00         # herstelbreedte asfalt (incl. zaagranden)
HERSTEL_BERM_M = 1.50           # herstelbreedte berm / groen (werkstrook)
# overlengte-toeslag (snij-/legverlies) komt uit het normenkader
# (normen.waarde("overlengte_pct"), instelbaar via data/normen.json)


def _kabel_toeslag() -> float:
    import normen
    return 1.0 + float(normen.waarde("overlengte_pct")) / 100.0


# Productienormen voor de bouwtijdraming (uitvoeringskosten, verkeersmaatregelen)
PRODUCTIE_SLEUF_M_DAG = 60.0
BOORDAGEN = {TECHNIEK_HDD: 3.0, TECHNIEK_PERSING: 2.0,
             TECHNIEK_NANO: 1.0, TECHNIEK_RAKET: 1.0}
OPSTART_DAGEN = 2.0
MIN_WEKEN = 2

# Fictieve maar realistische eenheidsprijzen (EUR, prijspeil 2026, excl. btw)
EENHEIDSPRIJZEN = {
    # 11 Sloopwerk
    "opbreken_elementen_m2": 8.50,
    "zagen_asfalt_m": 4.50,
    "opbreken_asfalt_m2": 11.00,
    # 21 Bemalingen
    "bemaling_boorlocatie_st": 1850.00,
    # 22 Grondwerken
    "ontgraven_sleuf_m3": 15.00,
    "aanvullen_grond_m3": 7.50,
    "kabelzand_m3": 38.00,
    "afvoer_grond_m3": 12.50,
    "toeslag_handwerk_wortelzone_m": 32.00,
    "toeslag_verontreinigd_m3": 65.00,
    "veiligheidsklasse_crow400_m": 9.00,
    # 25 Leidingwerk (sleufloze technieken en mantelbuizen)
    "hdd_inrichten_st": 5500.00,
    "hdd_boren_m": 160.00,
    "mantelbuis_hdpe160_m": 30.00,
    "persing_inrichten_st": 7500.00,
    "persing_m": 425.00,
    "nano_inrichten_st": 1250.00,
    "nano_m": 95.00,
    "raket_inrichten_st": 850.00,
    "raket_m": 70.00,
    "zinker_inrichten_st": 2250.00,
    "zinker_m": 95.00,
    # 26 Kabelwerk
    "kabel_leveren_m": 98.00,
    "kabel_leggen_sleuf_m": 12.50,
    "kabel_intrekken_buis_m": 18.00,
    "afdekband_m": 1.80,
    "beschermplaten_m": 9.50,
    "mofgat_st": 475.00,
    "mof_monteren_st": 2400.00,
    "eindsluiting_set_st": 1950.00,
    # 31 Verhardingen (herstel)
    "herstraten_m2": 13.50,
    "fundering_menggranulaat_m2": 12.00,
    "asfalt_herstel_m2": 48.00,
    # 51 Groenvoorzieningen
    "bermherstel_m2": 3.20,
    # 62 Verkeersmaatregelen
    "verkeersmaatregelen_wk": 1450.00,
    "rijplaten_kruising_st": 950.00,
}

# Staart van de inschrijvingsstaat (instelbaar)
STAART = {
    "inrichten_werkterrein_eur": 6500.00,   # 91.88 eenmalige kosten
    "opruimen_werkterrein_eur": 4500.00,
    "uitvoeringskosten_wk_eur": 3850.00,    # 92.88 uitvoeringskosten (per week)
    "ak_pct": 8.0,                          # 93.88 algemene kosten
    "wr_pct": 5.0,                          # 94.88 winst en risico
    "korting_eur": 0.0,                     # 95.88 korting
    "btw_pct": 21.0,
}

HOOFDSTUKKEN = {
    "01": "Algemeen (stelposten)",
    "11": "Sloopwerk",
    "21": "Bemalingen",
    "22": "Grondwerken",
    "25": "Leidingwerk (sleufloze technieken)",
    "26": "Kabelwerk",
    "31": "Verhardingen (herstel)",
    "51": "Groenvoorzieningen",
    "62": "Verkeersmaatregelen",
}

# Liggingsklassen → hersteltype van de verharding boven de sleuf
ELEMENTEN_KLASSEN = (CL_VOETPAD, CL_PARKEER)
ASFALT_KLASSEN = (CL_FIETSPAD, CL_RIJBAAN)
GROEN_KLASSEN = (CL_BERM, CL_ONVERHARD, CL_NATUURGROEN, CL_ONBEKEND, CL_ERF)


def _f(x: float, dec: int = 1) -> float:
    return round(float(x), dec)


def build_raw_calculatie(route_lengte_m: float, segments: list, crossings: list,
                         boringen: list, zro: list, moffen: list,
                         onderzoeken: list, zones_m: dict, kosten: dict,
                         n_stations: int = 2) -> dict:
    """Volledige RAW-calculatie voor één variant.

    `zones_m` is de zonemetrage per ZN_-bit (zoals zone_lengtes die levert),
    `kosten` de indicatieve kostenraming (voor de stelposten onderzoeken en
    zakelijk recht, zodat die bedragen op één plek worden bepaald)."""
    p = EENHEIDSPRIJZEN
    zones_m = zones_m or {}
    posten: list = []

    def post(nr, omschrijving, eenheid, hoeveelheid, prijs, herkomst="",
             soort="V", dec=1):
        if hoeveelheid <= 0 or prijs < 0:
            return
        hoeveelheid = _f(hoeveelheid, dec)
        posten.append({
            "nr": nr, "hoofdstuk": nr[:2],
            "omschrijving": omschrijving, "eenheid": eenheid,
            "hoeveelheid": hoeveelheid, "soort": soort,
            "eenheidsprijs_eur": _f(prijs, 2),
            "totaal_eur": round(hoeveelheid * prijs),
            "herkomst": herkomst,
        })

    # --- metrages per ligging (segmenten; water/spoor lopen via kruisingen) ---
    per_klasse: dict = {}
    for s in segments:
        per_klasse[s["klasse"]] = per_klasse.get(s["klasse"], 0.0) + s["lengte_m"]
    m_elementen = sum(per_klasse.get(k, 0.0) for k in ELEMENTEN_KLASSEN)
    m_asfalt = sum(per_klasse.get(k, 0.0) for k in ASFALT_KLASSEN)
    m_groen = sum(per_klasse.get(k, 0.0) for k in GROEN_KLASSEN)
    m_sleuf = m_elementen + m_asfalt + m_groen

    # --- sleufloze kruisingen en zinkers ---
    boor = {TECHNIEK_HDD: {"n": 0, "m": 0.0}, TECHNIEK_PERSING: {"n": 0, "m": 0.0},
            TECHNIEK_NANO: {"n": 0, "m": 0.0}, TECHNIEK_RAKET: {"n": 0, "m": 0.0}}
    for b in boringen:
        if b["type"] in boor:
            boor[b["type"]]["n"] += 1
            boor[b["type"]]["m"] += b["lengte_m"]
    m_boringen = sum(t["m"] for t in boor.values())
    m_buis = boor[TECHNIEK_HDD]["m"] + boor[TECHNIEK_NANO]["m"] + boor[TECHNIEK_RAKET]["m"]
    n_zinker = sum(1 for c in crossings
                   if c["soort"] == "water" and c["techniek"] == TECHNIEK_OPEN)
    m_zinker = per_klasse.get(CL_WATER, 0.0)
    n_rijbaan_open = sum(1 for c in crossings
                         if c["soort"] == "rijbaan" and c["techniek"] == TECHNIEK_OPEN)

    # --- bouwtijdraming (voert de uitvoeringskosten en verkeersmaatregelen) ---
    dagen = (m_sleuf / PRODUCTIE_SLEUF_M_DAG + OPSTART_DAGEN
             + sum(t["n"] * BOORDAGEN[naam] for naam, t in boor.items()))
    weken = max(MIN_WEKEN, math.ceil(dagen / 5.0))

    # ------------------------------------------------------------- 11 Sloopwerk
    post("110110", "Opbreken elementenverharding (tegels/klinkers), incl. "
         "sorteren en depot voor hergebruik", "m2",
         m_elementen * HERSTEL_ELEMENTEN_M, p["opbreken_elementen_m2"],
         f"{m_elementen:.0f} m voetpad/parkeervlak × {HERSTEL_ELEMENTEN_M:.2f} m")
    post("110120", "Zagen asfaltverharding (beide zaagsneden)", "m",
         m_asfalt * 2, p["zagen_asfalt_m"],
         f"{m_asfalt:.0f} m fietspad/rijbaan × 2 sneden")
    post("110130", "Opbreken/frezen asfaltverharding, incl. afvoer",
         "m2", m_asfalt * HERSTEL_ASFALT_M, p["opbreken_asfalt_m2"],
         f"{m_asfalt:.0f} m fietspad/rijbaan × {HERSTEL_ASFALT_M:.2f} m")

    # ------------------------------------------------------------ 21 Bemalingen
    n_bemaling = boor[TECHNIEK_HDD]["n"] + boor[TECHNIEK_PERSING]["n"]
    post("210110", "Bemaling in-/uittredepunten sleufloze kruisingen "
         "(opstellen, in bedrijf houden, lozen)", "st",
         n_bemaling, p["bemaling_boorlocatie_st"],
         f"{n_bemaling} HDD/persing(en)", dec=0)

    # ----------------------------------------------------------- 22 Grondwerken
    v_ontgraven = m_sleuf * SLEUF_BREEDTE_M * SLEUF_DIEPTE_M
    v_zand = m_sleuf * SLEUF_BREEDTE_M * ZANDBED_DIKTE_M
    v_aanvullen = v_ontgraven - v_zand
    profiel = (f"sleuf {SLEUF_BREEDTE_M:.2f} × {SLEUF_DIEPTE_M:.2f} m over "
               f"{m_sleuf:.0f} m open ontgraving")
    post("220110", "Ontgraven kabelsleuf, bodemklasse onverdacht, in den droge",
         "m3", v_ontgraven, p["ontgraven_sleuf_m3"], profiel)
    post("220210", "Leveren en aanbrengen kabelzand (kabelbed en omhulling)",
         "m3", v_zand, p["kabelzand_m3"],
         f"zandbed {ZANDBED_DIKTE_M:.2f} m dik")
    post("220310", "Aanvullen sleuf met uitkomende grond, verdichten", "m3",
         v_aanvullen, p["aanvullen_grond_m3"], "ontgraving minus zandbed")
    post("220410", "Afvoeren overtollige grond (schoon, incl. stort)", "m3",
         v_zand, p["afvoer_grond_m3"], "volume ingebracht kabelzand")
    m_boom = zones_m.get(ZN_BOOM, 0.0)
    post("220510", "Toeslag handmatig ontgraven binnen wortelzone bomen "
         "(Handboek Bomen)", "m", m_boom, p["toeslag_handwerk_wortelzone_m"],
         f"{m_boom:.0f} m tracé in wortelzone")
    m_bodem = zones_m.get(ZN_BODEM, 0.0)
    post("220610", "Toeslag ontgraven en afvoeren verontreinigde grond naar "
         "erkend verwerker (BRO SLD-gebied)", "m3",
         m_bodem * SLEUF_BREEDTE_M * SLEUF_DIEPTE_M, p["toeslag_verontreinigd_m3"],
         f"{m_bodem:.0f} m tracé door vastgestelde verontreiniging/nazorg")
    m_crow = m_bodem + zones_m.get(ZN_BODEM_ONDERZOEK, 0.0)
    post("220620", "Toeslag veiligheidsmaatregelen CROW 400 (voorlopige "
         "veiligheidsklasse)", "m", m_crow, p["veiligheidsklasse_crow400_m"],
         "meters in SLD- en SAD/Wbb-zones; definitief na vooronderzoek NEN 5725")

    # ---------------------------------------------- 25 Leidingwerk (sleufloos)
    post("250110", "Inrichten en verplaatsen boorstelling gestuurde boring (HDD)",
         "st", boor[TECHNIEK_HDD]["n"], p["hdd_inrichten_st"], dec=0)
    post("250120", "Uitvoeren gestuurde boring (HDD), incl. boorvloeistof en "
         "ruimen", "m", boor[TECHNIEK_HDD]["m"], p["hdd_boren_m"],
         f"{boor[TECHNIEK_HDD]['n']} boring(en), lengte incl. uitloop")
    post("250130", "Leveren en intrekken mantelbuis HDPE Ø160 SDR11", "m",
         m_buis, p["mantelbuis_hdpe160_m"], "HDD-, nanodrill- en raketlengtes")
    post("250210", "Persing stalen mantelbuis, incl. pers- en ontvangstput",
         "st", boor[TECHNIEK_PERSING]["n"], p["persing_inrichten_st"], dec=0)
    post("250220", "Persen stalen mantelbuis", "m",
         boor[TECHNIEK_PERSING]["m"], p["persing_m"])
    post("250310", "Inrichten nanodrill / mini-HDD", "st",
         boor[TECHNIEK_NANO]["n"], p["nano_inrichten_st"], dec=0)
    post("250320", "Uitvoeren nanodrill / mini-HDD", "m",
         boor[TECHNIEK_NANO]["m"], p["nano_m"])
    post("250410", "Inrichten raketboring (ongestuurd)", "st",
         boor[TECHNIEK_RAKET]["n"], p["raket_inrichten_st"], dec=0)
    post("250420", "Uitvoeren raketboring (ongestuurd)", "m",
         boor[TECHNIEK_RAKET]["m"], p["raket_m"])
    post("250510", "Open kruising watergang (zinker), incl. baggeren en "
         "taludherstel", "st", n_zinker, p["zinker_inrichten_st"], dec=0)
    post("250520", "Ontgraven en aanvullen zinkersleuf in watergang", "m",
         m_zinker, p["zinker_m"])

    # ------------------------------------------------------------ 26 Kabelwerk
    toeslag = _kabel_toeslag()
    m_kabel = (m_sleuf + m_boringen + m_zinker) * toeslag
    post("260110", "Leveren MS-kabel 3×1×630 mm² Al (per circuitmeter, "
         "3 fasen op haspels)", "m", m_kabel, p["kabel_leveren_m"],
         f"tracé × {toeslag:.1%} incl. overlengte-toeslag (normenkader)")
    post("260120", "Trekken en leggen MS-kabelcircuit in open sleuf", "m",
         m_sleuf + m_zinker, p["kabel_leggen_sleuf_m"])
    post("260130", "Intrekken MS-kabelcircuit in mantelbuis", "m",
         m_boringen, p["kabel_intrekken_buis_m"])
    post("260140", "Aanbrengen afdekband en waarschuwingslint", "m",
         m_sleuf, p["afdekband_m"])
    post("260150", "Aanbrengen kabelbeschermingsplaten (verharde liggingen)",
         "m", m_elementen + m_asfalt, p["beschermplaten_m"])
    post("260210", "Ontgraven en aanvullen mofgat, incl. bemaling", "st",
         len(moffen), p["mofgat_st"], dec=0)
    post("260220", "Monteren MS-verbindingsmof (3 fasen per set)", "st",
         len(moffen), p["mof_monteren_st"],
         "moffen op haspellengte (register Moffen)", dec=0)
    n_eind = max(0, (n_stations - 1)) * 2
    post("260310", "Monteren kabeleindsluitingen en aansluiten in MS-station "
         "(3 fasen per set)", "st", n_eind, p["eindsluiting_set_st"],
         f"{n_stations} station(s), per streng 2 einden", dec=0)

    # ------------------------------------------------ 31 Verhardingen (herstel)
    post("310110", "Herstraten elementenverharding uit depot, incl. "
         "aanvullen straatlaag", "m2", m_elementen * HERSTEL_ELEMENTEN_M,
         p["herstraten_m2"])
    post("310210", "Aanbrengen fundering menggranulaat 0/31,5, dik 0,25 m",
         "m2", m_asfalt * HERSTEL_ASFALT_M, p["fundering_menggranulaat_m2"])
    post("310220", "Herstel asfaltverharding (onder- en deklaag), incl. "
         "kleeflagen en naadafwerking", "m2", m_asfalt * HERSTEL_ASFALT_M,
         p["asfalt_herstel_m2"])

    # ------------------------------------------------- 51 Groenvoorzieningen
    post("510110", "Herstel bermen en groenstroken, incl. profileren en "
         "inzaaien", "m2", m_groen * HERSTEL_BERM_M, p["bermherstel_m2"],
         f"{m_groen:.0f} m in berm/groen × {HERSTEL_BERM_M:.1f} m werkstrook")

    # ------------------------------------------------ 62 Verkeersmaatregelen
    post("620110", "Verkeersmaatregelen conform CROW 96b, in stand houden",
         "week", weken, p["verkeersmaatregelen_wk"],
         f"bouwtijdraming {weken} weken", dec=0)
    post("620120", "Rijplaten / tijdelijke overkluizing bij open kruising "
         "rijbaan", "st", n_rijbaan_open, p["rijplaten_kruising_st"], dec=0)

    # --------------------------------------------------- 01 Stelposten (RAW)
    kosten = kosten or {}
    post("018010", "Stelpost: conditionerende onderzoeken (natuur, bodem, "
         "archeologie, BEA, sonderingen)", "EUR", kosten.get("onderzoeken", 0),
         1.0, "register Onderzoeken", soort="stelpost", dec=0)
    post("018020", "Stelpost: vestiging zakelijk recht en vergoedingen "
         "rechthebbenden", "EUR", kosten.get("zro", 0), 1.0,
         f"{len(zro)} perceel/percelen (register ZRO)", soort="stelpost", dec=0)

    posten.sort(key=lambda q: q["nr"])

    # --------------------------------------------------------- inschrijvingsstaat
    subtotaal = sum(q["totaal_eur"] for q in posten)
    st = STAART
    eenmalig = st["inrichten_werkterrein_eur"] + st["opruimen_werkterrein_eur"]
    uitvoering = weken * st["uitvoeringskosten_wk_eur"]
    grondslag_ak = subtotaal + eenmalig + uitvoering
    ak = grondslag_ak * st["ak_pct"] / 100.0
    wr = (grondslag_ak + ak) * st["wr_pct"] / 100.0
    aanneemsom = grondslag_ak + ak + wr - st["korting_eur"]
    btw = aanneemsom * st["btw_pct"] / 100.0

    def staartregel(nr, omschrijving, bedrag, grondslag=""):
        return {"nr": nr, "omschrijving": omschrijving,
                "grondslag": grondslag, "bedrag_eur": round(bedrag)}

    staart = [
        staartregel("", "Subtotaal bestekposten (resultaatsverplichtingen)",
                    subtotaal),
        staartregel("918810", "Eenmalige kosten: inrichten werkterrein",
                    st["inrichten_werkterrein_eur"]),
        staartregel("918820", "Eenmalige kosten: opruimen werkterrein",
                    st["opruimen_werkterrein_eur"]),
        staartregel("928810", "Uitvoeringskosten",
                    uitvoering, f"{weken} weken × "
                    f"€ {st['uitvoeringskosten_wk_eur']:,.0f}".replace(",", ".")),
        staartregel("", "Subtotaal", grondslag_ak),
        staartregel("938810", "Algemene kosten", ak, f"{st['ak_pct']:.0f}%"),
        staartregel("948810", "Winst en risico", wr, f"{st['wr_pct']:.0f}%"),
        staartregel("958810", "Korting", -st["korting_eur"]),
        staartregel("", "Aannemingssom (excl. btw)", aanneemsom),
        staartregel("", "btw", btw, f"{st['btw_pct']:.0f}%"),
        staartregel("", "Aannemingssom (incl. btw)", aanneemsom + btw),
    ]

    hoofdstuk_totalen = []
    for code, naam in HOOFDSTUKKEN.items():
        tot = sum(q["totaal_eur"] for q in posten if q["hoofdstuk"] == code)
        if tot > 0:
            hoofdstuk_totalen.append({"code": code, "naam": naam,
                                      "totaal_eur": round(tot)})

    return {
        "systematiek": "RAW-bestek (inschrijvingsbegroting)",
        "prijspeil": "2026 — fictieve maar realistische eenheidsprijzen, excl. btw",
        "posten": posten,
        "hoofdstukken": hoofdstuk_totalen,
        "staart": staart,
        "subtotaal_bestekposten_eur": round(subtotaal),
        "aannemingssom_excl_btw": round(aanneemsom),
        "btw_eur": round(btw),
        "aannemingssom_incl_btw": round(aanneemsom + btw),
        "uitvoeringsduur_wk": weken,
        "uitgangspunten": [
            f"Sleufprofiel {SLEUF_BREEDTE_M:.2f} × {SLEUF_DIEPTE_M:.2f} m "
            f"(dekking ± 1,0 m), kabelbed {ZANDBED_DIKTE_M:.2f} m schoon zand.",
            "Herstelbreedtes: elementenverharding "
            f"{HERSTEL_ELEMENTEN_M:.2f} m, asfalt {HERSTEL_ASFALT_M:.2f} m, "
            f"berm/groen {HERSTEL_BERM_M:.1f} m.",
            "Voetpad en parkeervlak gelden als elementenverharding, fietspad "
            "en rijbaan als asfalt (BGT kent het verhardingstype niet).",
            "Sleuf- en herstelposten zijn berekend over de volledige "
            "segmentlengte per ligging; korte overlap met sleufloze "
            "kruisingen is niet in mindering gebracht (conservatief).",
            f"Bouwtijdraming: {PRODUCTIE_SLEUF_M_DAG:.0f} m open sleuf per "
            f"dag plus boordagen per techniek → {weken} weken.",
            "Eenheidsprijzen zijn fictief maar realistisch (prijspeil 2026, "
            "excl. btw); instelbaar in calculatie.py (EENHEIDSPRIJZEN, STAART).",
            "Onderzoeken en zakelijk recht zijn als stelpost opgenomen; "
            "gewassen- en structuurschade blijven p.m.",
        ],
    }
