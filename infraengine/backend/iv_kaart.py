"""IV → kaart: het investeringsvoorstel (IV) als startdocument van het proces.

Een IV bevat het tracé meestal niet als coördinaten maar als kaartfiguur
(screenshot) en als schematische netstructuur. Wat er wél hard in staat —
knooppunten met plaatsaanduiding (RS/OS/DR-en), de volgorde van de
verbinding(en), klantadressen, hoeveelheden, mijlpalen, risico's en
uitgangspunten — wordt hier in twee stappen naar de kaart en het proces
gebracht:

  1. ``extraheer``  — de AI leest het IV (inclusief figuren) en levert een
     gestructureerd JSON-antwoord via een geforceerde tool-aanroep (geen
     proza, geen vrije opmaak);
  2. ``bouw_voorstel`` — elke plaatsaanduiding en elk adres wordt via de
     PDOK Locatieserver naar een RD-punt vertaald, met een status per punt
     (gevonden / onzeker / niet gevonden), zodat de ontwerper ziet wat hij
     op de kaart moet nalopen. De knooppunten van een verbinding worden in
     de volgorde uit het IV als stations aangeboden; de rekenengine bepaalt
     daarna het tracé ertussen.

De kaartfiguur uit het IV wordt bewust niet "overgetrokken": een raster
bevat geen vectorpaden en een benadering daarvan zou schijnnauwkeurigheid
geven. Het resultaat is een kloppend netontwerp (juiste knooppunten,
juiste volgorde) dat de app zelf uitrekent en de mens bijstuurt.
"""
from __future__ import annotations

import re
import time

import requests

LOCATIESERVER_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
HTTP_TIMEOUT_S = 12
MAX_TOKENS = 16000

# knooppunten die op exact dezelfde plek uitkomen (twee DR-en in één dorp)
# worden iets uit elkaar gezet: een tracé met een verbinding van 0 m kan de
# rekenengine niet aan, en de ontwerper ziet zo dat er twee punten liggen
DUBBEL_OFFSET_M = 80.0


class IvKaartError(Exception):
    pass


# ---------------------------------------------------------------------------
# 1. Gestructureerde extractie (tool-schema = het contract met de AI)
# ---------------------------------------------------------------------------

EXTRACTIE_SYSTEM = (
    "Je bent netontwerper bij een Nederlandse netbeheerder en leest een "
    "investeringsvoorstel (IV) voor een middenspanningsnet. Je haalt er "
    "uitsluitend feiten uit die in het document staan — tekst, tabellen én "
    "figuren (kaartjes, schematische netstructuur). Verzin niets; wat "
    "onzeker is markeer je in 'toelichting'. Plaatsaanduidingen geef je "
    "als geocodeerbare zoekterm (dorp, buurtschap of straat met "
    "plaatsnaam), afgeleid van de labels in de kaartfiguren en de "
    "adressen in de tekst. De volgorde van knooppunten in een verbinding "
    "lees je uit de schematische netstructuur (bijlage) of, als die "
    "ontbreekt, uit de kaartfiguur; begin en eindig bij het voedende "
    "station. Een ring geef je als één verbinding die begint en eindigt bij "
    "hetzelfde station; een spaak of aftakking als aparte verbinding."
)

EXTRACTIE_TOOL = {
    "name": "iv_extractie",
    "description": "Gestructureerde inhoud van het investeringsvoorstel.",
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "object",
                "properties": {
                    "titel": {"type": "string"},
                    "opdrachtgever": {"type": "string",
                                      "description": "Organisatie/afdeling die het IV indient"},
                    "auteur": {"type": "string"},
                    "datum_versie": {"type": "string"},
                    "ums_nummer": {"type": "string"},
                    "risicoregister": {"type": "string",
                                       "description": "Risico-/knelpuntnummer(s), bv. R08793"},
                    "regio": {"type": "string"},
                    "voedend_station": {"type": "string"},
                    "gemeente": {"type": "string",
                                 "description": "Gemeente(n) van het gebied, indien af te leiden"},
                    "provincie": {"type": "string",
                                  "description": "Provincie van het gebied (bv. Fryslân); gebruikt als filter bij het geocoderen"},
                    "uitvoerende": {"type": "string"},
                    "budget_keur": {"type": ["number", "null"],
                                    "description": "Totale projectkosten in k€ als genoemd; null als weggelaten"},
                    "nauwkeurigheid": {"type": "string",
                                       "description": "Nauwkeurigheidsklasse van de raming, bv. +10/-10%"},
                    "gewenste_ibn": {"type": "string"},
                },
            },
            "samenvatting": {"type": "string",
                             "description": "Gevraagd besluit en doel in 3-6 zinnen"},
            "knooppunten": {
                "type": "array",
                "description": "Alle stations en distributieregelaars (RS/OS/DR/MSR) die het ontwerp vormen",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Label uit het IV, bv. RS Spannenburg, DR05"},
                        "soort": {"type": "string", "enum": ["RS", "OS", "DR", "MSR", "overig"]},
                        "naam": {"type": "string"},
                        "plaats": {"type": "string", "description": "Dorp/buurtschap waar het punt ligt"},
                        "zoekterm": {"type": "string",
                                     "description": "Geocodeerbare zoekterm voor de PDOK Locatieserver, bv. 'Koufurderrige' of 'Vosseleane, Woudsend'"},
                        "zoekterm_alternatief": {"type": "string",
                                                 "description": "Terugvaloptie als de zoekterm geen officiële plaats is (buurtschap, stationsnaam): dichtstbijzijnde woonplaats of straat, bv. 'Tjerkgaast'"},
                        "toelichting": {"type": "string"},
                        "bron": {"type": "string", "description": "bv. figuur 5, bijlage C"},
                    },
                    "required": ["id", "soort", "zoekterm"],
                },
            },
            "verbindingen": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "naam": {"type": "string"},
                        "soort": {"type": "string", "enum": ["ring", "streng", "spaak"]},
                        "spanning_kv": {"type": ["number", "null"]},
                        "kabel": {"type": "string", "description": "bv. 630 mm2 Al"},
                        "knooppunten": {"type": "array", "items": {"type": "string"},
                                        "description": "ids van knooppunten in volgorde"},
                        "bron": {"type": "string"},
                    },
                    "required": ["naam", "knooppunten"],
                },
            },
            "klantlocaties": {
                "type": "array",
                "description": "Adressen van klantontwikkelingen/transportbeperkingen",
                "items": {
                    "type": "object",
                    "properties": {
                        "nr": {"type": "string"},
                        "soort": {"type": "string", "description": "LDN/ODN e.d."},
                        "adres": {"type": "string", "description": "straat + huisnummer"},
                        "plaats": {"type": "string"},
                        "vermogen": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "required": ["adres"],
                },
            },
            "hoeveelheden": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "omschrijving": {"type": "string"},
                        "aantal": {"type": "number"},
                        "eenheid": {"type": "string", "description": "m, stuks, …"},
                    },
                    "required": ["omschrijving", "aantal", "eenheid"],
                },
            },
            "mijlpalen": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "naam": {"type": "string"},
                        "datum": {"type": "string", "description": "zoals in het IV, bv. 'Q4 2025'"},
                        "veld": {"type": "string",
                                 "enum": ["ibn_datum", "start_uitvoering", "contract_getekend",
                                          "vo_gereed", "do_gereed", "uo_gereed", ""],
                                 "description": "Koppeling met de projectmijlpalen, alleen bij een "
                                                "eenduidige match: ibn_datum = inbedrijfname; "
                                                "start_uitvoering = geplande start van de realisatie "
                                                "buiten (niet: opdracht uitzetten of IV beoordelen); "
                                                "contract_getekend = aannemingsovereenkomst; "
                                                "vo/do/uo_gereed = ontwerpfasen. Anders ''"},
                    },
                    "required": ["naam", "datum"],
                },
            },
            "uitgangspunten": {"type": "array", "items": {"type": "string"},
                               "description": "Randvoorwaarden, ontwerpkeuzes, normen (bv. toekomstvast tot 2035)"},
            "risicos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "omschrijving": {"type": "string"},
                        "oorzaak": {"type": "string"},
                        "gevolg": {"type": "string"},
                        "aspect": {"type": "string"},
                        "intern_extern": {"type": "string", "enum": ["Intern", "Extern"]},
                        "maatregel": {"type": "string"},
                    },
                    "required": ["omschrijving"],
                },
            },
            "ontbrekend": {"type": "array", "items": {"type": "string"},
                           "description": "Informatie die het IV niet geeft maar het ontwerp wel nodig heeft"},
        },
        "required": ["project", "samenvatting", "knooppunten", "verbindingen"],
    },
}


def extraheer(anthropic, model: str, blokken: list[dict], project: str) -> dict:
    """Eén AI-aanroep met geforceerde tool-keuze: het antwoord is altijd
    een object volgens EXTRACTIE_TOOL['input_schema']."""
    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=EXTRACTIE_SYSTEM,
            tools=[EXTRACTIE_TOOL],
            tool_choice={"type": "tool", "name": "iv_extractie"},
            messages=[{"role": "user", "content": blokken + [
                {"type": "text",
                 "text": f"Projectnaam in InfraEngine: {project}. Lees het "
                         "IV volledig, inclusief de kaartfiguren en de "
                         "schematische netstructuur, en vul de extractie in."}]}],
        )
    except anthropic.AuthenticationError:
        raise IvKaartError("Anthropic API-key is ongeldig.")
    except anthropic.APIConnectionError:
        raise IvKaartError("Verbinding met de Anthropic API mislukt.")
    except anthropic.APIStatusError as e:
        raise IvKaartError(f"Anthropic API-fout ({e.status_code}).")
    for blok in msg.content:
        if blok.type == "tool_use" and isinstance(blok.input, dict):
            return blok.input
    raise IvKaartError("AI gaf geen gestructureerde extractie terug; "
                       "probeer opnieuw.")


# ---------------------------------------------------------------------------
# 2. Geocoderen (PDOK Locatieserver, RD New)
# ---------------------------------------------------------------------------

_session = requests.Session()

_PUNT_RE = re.compile(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)")


def _parse_rd(wkt: str) -> tuple[float, float] | None:
    m = _PUNT_RE.search(wkt or "")
    if not m:
        return None
    return round(float(m.group(1)), 2), round(float(m.group(2)), 2)


def _zoek(q: str, types: str, rows: int = 3, provincie: str = "") -> list[dict]:
    fq = f"type:({types})"
    if provincie:
        fq += f' AND provincienaam:"{provincie}"'
    r = _session.get(LOCATIESERVER_URL, params={
        "q": q, "rows": rows, "fq": fq,
        "fl": "id,weergavenaam,type,centroide_rd,score",
    }, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    docs = (r.json().get("response") or {}).get("docs") or []
    uit = []
    for d in docs:
        xy = _parse_rd(d.get("centroide_rd", ""))
        if xy:
            uit.append({"x": xy[0], "y": xy[1],
                        "weergavenaam": d.get("weergavenaam", ""),
                        "type": d.get("type", ""),
                        "score": round(float(d.get("score", 0) or 0), 1)})
    return uit


def geocodeer(zoekterm: str, *, plaats: str = "", gemeente: str = "",
              provincie: str = "", alternatief: str = "",
              adres: bool = False) -> dict:
    """Zoekterm → {x, y, weergavenaam, type, status, opmerking}.

    status: 'gevonden' (adres/straat/plaats-treffer), 'onzeker' (alleen
    een ruimere treffer: de alternatieve zoekterm, de plaatsnaam terwijl
    een straat werd gezocht, of de gemeente) of 'niet_gevonden'. Met een
    ``provincie`` zoekt de Locatieserver alleen binnen die provincie — een
    buurtschap als 'Spannenburg' levert anders een gelijknamige straat elders
    in het land op. Netwerkfouten leveren 'niet_gevonden' met de reden in
    'opmerking' — nooit een exception."""
    zoekterm = (zoekterm or "").strip()
    alternatief = (alternatief or "").strip()
    if not zoekterm and not alternatief:
        return {"status": "niet_gevonden", "opmerking": "geen zoekterm"}
    pogingen = []
    if adres:
        pogingen.append((f"{zoekterm} {plaats}".strip(), "adres", "gevonden"))
        pogingen.append((zoekterm, "adres", "gevonden"))
        pogingen.append((zoekterm, "weg", "onzeker"))
    else:
        if zoekterm:
            pogingen.append((zoekterm, "woonplaats OR weg OR adres", "gevonden"))
            if plaats and plaats.lower() not in zoekterm.lower():
                pogingen.append((f"{zoekterm}, {plaats}", "woonplaats OR weg", "gevonden"))
        if alternatief:
            pogingen.append((alternatief, "woonplaats OR weg OR adres", "onzeker"))
        if plaats:
            pogingen.append((plaats, "woonplaats", "onzeker"))
        if zoekterm:
            pogingen.append((zoekterm, "gemeente OR buurt OR wijk", "onzeker"))
    if gemeente:
        pogingen.append((gemeente, "gemeente", "onzeker"))
    fout = ""
    for q, types, status in pogingen:
        try:
            treffers = _zoek(q, types, provincie=provincie)
        except Exception as e:  # netwerk/PDOK-storing: doorgaan, melden
            fout = f"Locatieserver niet bereikbaar ({type(e).__name__})"
            continue
        if treffers:
            t = treffers[0]
            opm = "" if status == "gevonden" else f"ruime treffer op '{q}'"
            return {**t, "status": status, "opmerking": opm, "zoekterm": q}
    return {"status": "niet_gevonden",
            "opmerking": fout or f"geen treffer voor '{zoekterm}'"}


# ---------------------------------------------------------------------------
# 3. Voorstel: extractie + geocodering → kaartpunten en procesgegevens
# ---------------------------------------------------------------------------

def _tekst(v, n: int = 300) -> str:
    return str(v if v is not None else "").strip()[:n]


# een knooppunt dat verder dan dit van het zwaartepunt van de overige
# knooppunten ligt is vrijwel zeker een naamgenoot elders in het land
UITSCHIETER_M = 30_000.0


def _markeer_uitschieters(knooppunten: list[dict]) -> None:
    """Plausibiliteit: knooppunten ver buiten het cluster van de rest worden
    'onzeker' (mediaan als zwaartepunt, zodat één uitschieter de maat niet
    verlegt)."""
    pts = [(k["x"], k["y"]) for k in knooppunten if k["x"] is not None]
    if len(pts) < 3:
        return
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
    for k in knooppunten:
        if k["x"] is None:
            continue
        d = ((k["x"] - mx) ** 2 + (k["y"] - my) ** 2) ** 0.5
        if d > UITSCHIETER_M and k["status"] != "niet_gevonden":
            k["status"] = "onzeker"
            k["opmerking"] = (f"ligt {d / 1000:.0f} km van de overige "
                              "knooppunten — waarschijnlijk een naamgenoot; "
                              "plaats handmatig")


def bouw_voorstel(extractie: dict, *, geocoder=geocodeer) -> dict:
    """Vertaalt de AI-extractie naar het kaart-/procesvoorstel. ``geocoder``
    is injecteerbaar voor tests (geen netwerk)."""
    proj = extractie.get("project") or {}
    gemeente = _tekst(proj.get("gemeente"), 80)
    provincie = _tekst(proj.get("provincie"), 40)

    # knooppunten → RD-punten
    knooppunten = []
    bezet: dict[tuple, int] = {}
    for k in extractie.get("knooppunten") or []:
        if not isinstance(k, dict) or not k.get("id"):
            continue
        g = geocoder(_tekst(k.get("zoekterm"), 120),
                     plaats=_tekst(k.get("plaats"), 80), gemeente=gemeente,
                     provincie=provincie,
                     alternatief=_tekst(k.get("zoekterm_alternatief"), 120))
        rij = {"id": _tekst(k.get("id"), 40),
               "soort": _tekst(k.get("soort"), 10) or "overig",
               "naam": _tekst(k.get("naam"), 120),
               "plaats": _tekst(k.get("plaats"), 80),
               "zoekterm": _tekst(k.get("zoekterm"), 120),
               "toelichting": _tekst(k.get("toelichting"), 300),
               "bron": _tekst(k.get("bron"), 60),
               "status": g["status"], "opmerking": g.get("opmerking", ""),
               "gevonden": g.get("weergavenaam", ""),
               "x": g.get("x"), "y": g.get("y")}
        if rij["x"] is not None:
            sleutel = (rij["x"], rij["y"])
            n = bezet.get(sleutel, 0)
            if n:
                rij["x"] = round(rij["x"] + DUBBEL_OFFSET_M * n, 2)
                rij["status"] = "onzeker"
                rij["opmerking"] = (f"zelfde locatie als een eerder knooppunt; "
                                    f"{DUBBEL_OFFSET_M * n:.0f} m verschoven — "
                                    "plaats op de kaart")
            bezet[sleutel] = n + 1
        knooppunten.append(rij)
    _markeer_uitschieters(knooppunten)
    per_id = {k["id"].lower(): k for k in knooppunten}

    # verbindingen → stationsreeksen (alleen gevonden knooppunten; wat
    # ontbreekt wordt gemeld zodat de ontwerper het handmatig toevoegt)
    verbindingen = []
    for vb in extractie.get("verbindingen") or []:
        if not isinstance(vb, dict):
            continue
        ids = [_tekst(i, 40) for i in (vb.get("knooppunten") or []) if _tekst(i, 40)]
        stations, ontbrekend, gebruikt = [], [], []
        for i in ids:
            k = per_id.get(i.lower())
            if k is None or k["x"] is None:
                ontbrekend.append(i)
                continue
            gebruikt.append(k["id"])
            stations.append([k["x"], k["y"]])
        # ring: sluit expliciet op het beginpunt zodat de streng rond loopt
        if _tekst(vb.get("soort")) == "ring" and len(stations) >= 2 \
                and stations[0] != stations[-1]:
            stations.append(stations[0])
            gebruikt.append(gebruikt[0])
        verbindingen.append({
            "naam": _tekst(vb.get("naam"), 120) or f"Verbinding {len(verbindingen) + 1}",
            "soort": _tekst(vb.get("soort"), 10) or "streng",
            "spanning_kv": vb.get("spanning_kv"),
            "kabel": _tekst(vb.get("kabel"), 60),
            "bron": _tekst(vb.get("bron"), 60),
            "knooppunten": ids, "labels": gebruikt,
            "stations": stations, "ontbrekend": ontbrekend,
            "hemelsbreed_m": round(sum(
                ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                for a, b in zip(stations[:-1], stations[1:])), 0),
        })

    # klantlocaties → adrespunten
    klanten = []
    for kl in extractie.get("klantlocaties") or []:
        if not isinstance(kl, dict) or not kl.get("adres"):
            continue
        g = geocoder(_tekst(kl.get("adres"), 120),
                     plaats=_tekst(kl.get("plaats"), 80), gemeente=gemeente,
                     provincie=provincie, adres=True)
        klanten.append({"nr": _tekst(kl.get("nr"), 10),
                        "soort": _tekst(kl.get("soort"), 20),
                        "adres": _tekst(kl.get("adres"), 120),
                        "plaats": _tekst(kl.get("plaats"), 80),
                        "vermogen": _tekst(kl.get("vermogen"), 60),
                        "klantstatus": _tekst(kl.get("status"), 60),
                        "status": g["status"], "opmerking": g.get("opmerking", ""),
                        "gevonden": g.get("weergavenaam", ""),
                        "x": g.get("x"), "y": g.get("y")})

    hoeveelheden = [{"omschrijving": _tekst(h.get("omschrijving"), 160),
                     "aantal": float(h.get("aantal") or 0),
                     "eenheid": _tekst(h.get("eenheid"), 12)}
                    for h in (extractie.get("hoeveelheden") or [])
                    if isinstance(h, dict) and h.get("omschrijving")]
    kabel_m = round(sum(h["aantal"] for h in hoeveelheden
                        if h["eenheid"].lower() in ("m", "m1", "meter")), 0)

    mijlpalen = [{"naam": _tekst(m.get("naam"), 120),
                  "datum": _tekst(m.get("datum"), 40),
                  "veld": _tekst(m.get("veld"), 24)}
                 for m in (extractie.get("mijlpalen") or [])
                 if isinstance(m, dict) and m.get("naam")]

    risicos = [{"omschrijving": _tekst(r.get("omschrijving"), 300),
                "oorzaak": _tekst(r.get("oorzaak"), 400),
                "gevolg": _tekst(r.get("gevolg"), 400),
                "aspect": _tekst(r.get("aspect"), 40) or "Omgeving",
                "intern_extern": (r.get("intern_extern")
                                  if r.get("intern_extern") in ("Intern", "Extern")
                                  else "Extern"),
                "maatregel": _tekst(r.get("maatregel"), 300)}
               for r in (extractie.get("risicos") or [])
               if isinstance(r, dict) and r.get("omschrijving")]

    return {
        "gegenereerd": time.strftime("%Y-%m-%d %H:%M"),
        "project": {k: (v if isinstance(v, (int, float)) or v is None
                        else _tekst(v, 200))
                    for k, v in proj.items()} if isinstance(proj, dict) else {},
        "samenvatting": _tekst(extractie.get("samenvatting"), 1500),
        "knooppunten": knooppunten,
        "verbindingen": verbindingen,
        "klantlocaties": klanten,
        "hoeveelheden": hoeveelheden,
        "kabel_m_totaal": kabel_m,
        "mijlpalen": mijlpalen,
        "uitgangspunten": [_tekst(u, 300) for u in (extractie.get("uitgangspunten") or [])
                           if _tekst(u, 300)],
        "risicos": risicos,
        "ontbrekend": [_tekst(u, 300) for u in (extractie.get("ontbrekend") or [])
                       if _tekst(u, 300)],
        "statistiek": {
            "knooppunten": len(knooppunten),
            "gevonden": sum(1 for k in knooppunten if k["status"] == "gevonden"),
            "onzeker": sum(1 for k in knooppunten if k["status"] == "onzeker"),
            "niet_gevonden": sum(1 for k in knooppunten if k["status"] == "niet_gevonden"),
            "klanten": len(klanten),
            "klanten_gevonden": sum(1 for k in klanten if k["x"] is not None),
        },
    }
