"""AI-bureauonderzoeken per onderzoek uit het onderzoekenregister.

Voor onderzoeken waarvan de benodigde data al in het rekenresultaat zit
(zonelagen, registers, BRO, bomen) kan Claude een bureauonderzoek uitvoeren
en daar een rapport van schrijven, in dezelfde streaming-flow als de
ontwerpnota's (nota.py).

Per onderzoekssoort is vastgelegd wat de juridische status van zo'n
AI-bureauonderzoek is:

- ``zelf``               — bureaustudie zonder wettelijke certificeringsplicht;
                           het AI-rapport is bruikbaar als concept, na
                           vakinhoudelijke toetsing.
- ``bureau_aanbevolen``  — geen wettelijke certificeringsplicht, maar het
                           bevoegd gezag eist doorgaans een deskundige en/of
                           het onderzoek vereist veldwerk; het AI-rapport is
                           een voorbereidende bureaustudie.
- ``bureau_verplicht``   — wettelijk voorbehouden aan een gecertificeerde of
                           erkende organisatie; het AI-rapport dient alleen
                           als interne voorbereiding en scopingdocument.

De teksten (grondslag + toelichting) worden zowel in de frontend getoond als
letterlijk in het rapport verwerkt (hoofdstuk "Status en validiteit").
"""
from __future__ import annotations

import json
import os
from datetime import date

import engine
import nota as nota_mod
from nota import FOUT_MARK, MODEL, NotaError, _cap, _zonder_geometrie

MAX_TOKENS = 20000

VALIDITEIT_LABELS = {
    "zelf": "AI-bureauonderzoek bruikbaar (toetsing vereist)",
    "bureau_aanbevolen": "Voorbereidend — deskundige/veldwerk vereist",
    "bureau_verplicht": "Gecertificeerd bureau wettelijk vereist",
}

# Profiel per onderzoekssoort; gematcht op prefix van het `soort`-veld uit
# registers.build_onderzoeken. Volgorde is betekenisvol (langste prefix eerst
# waar soorten elkaar overlappen, zoals de twee bodemonderzoeken en NGE).
PROFIELEN = [
    {"match": "KLIC-oriëntatiemelding",
     "uitvoerbaar": False,
     "reden": "Administratieve melding bij het Kadaster (WIBON); "
              "geen onderzoeksrapport om op te stellen."},

    {"match": "Natuur-quickscan",
     "uitvoerbaar": True,
     "validiteit": "bureau_aanbevolen",
     "grondslag": "Omgevingswet / Besluit activiteiten leefomgeving "
                  "(soortenbescherming); geen wettelijke certificeringsplicht, "
                  "maar het bevoegd gezag eist een rapport van een deskundig "
                  "ecoloog en doorgaans een veldbezoek.",
     "toelichting": "Deze bureaustudie beoordeelt de ligging ten opzichte van "
                    "beschermde gebieden (Natura 2000, NNN) op basis van de "
                    "gekoppelde zonelagen. Verspreidingsdata van beschermde "
                    "soorten (NDFF) en een veldbezoek ontbreken; de formele "
                    "quickscan flora en fauna moet door een ecoloog worden "
                    "uitgevoerd.",
     "instructie": "Voer een bureaustudie natuur uit: beoordeel per zone "
                   "(Natura 2000, NNN, stiltegebied) de raakvlakken met het "
                   "tracé, de kans op vergunningplicht (natuurvergunning, "
                   "compensatie NNN) en welke mitigerende maatregelen in de "
                   "uitvoering voor de hand liggen (werkperiode, werkbreedte, "
                   "sleufloze passage). Benoem expliciet welke soortendata "
                   "ontbreekt en wat de ecoloog in het veld moet vaststellen.",
     "data": ("zones", "segmenten_ligging", "kruisingen", "boringen",
              "toetsing", "vergunningen")},

    {"match": "Milieuhygiënisch bodemonderzoek (vooronderzoek NEN 5725)",
     "uitvoerbaar": True,
     "validiteit": "zelf",
     "grondslag": "NEN 5725 (vooronderzoek bodem); het vooronderzoek zelf is "
                  "een bureaustudie zonder Kwalibo-erkenningsplicht. Eventueel "
                  "vervolgend veldonderzoek (NEN 5740) valt wél onder Kwalibo "
                  "(BRL SIKB 2000).",
     "toelichting": "De structuur van NEN 5725 (aanleiding, deelgebieden, "
                    "geraadpleegde bronnen, verwachtingsmodel) leent zich voor "
                    "een bureaustudie op de gekoppelde BRO-data (SAD/SLD) en "
                    "het Bodemloket. Het rapport kan als concept-vooronderzoek "
                    "dienen en moet door een bodemdeskundige worden "
                    "vastgesteld.",
     "instructie": "Stel een vooronderzoek conform NEN 5725 op: aanleiding en "
                   "aanpak, afbakening onderzoekslocatie (het tracé met "
                   "werkstrook), geraadpleegde bronnen (BRO SAD/SLD, "
                   "Bodemloket, regionale bronnen uit de data), bekende "
                   "onderzoeks- en verontreinigingslocaties op of nabij het "
                   "tracé, een verwachtingsmodel per tracédeel (verdacht/"
                   "onverdacht met motivering) en de consequenties: "
                   "veiligheidsklasse-indicatie CROW 400, waar verkennend "
                   "veldonderzoek (NEN 5740) nodig is en waar niet.",
     "data": ("zones", "segmenten_ligging", "bodem_bronnen", "toetsing",
              "databeperkingen")},

    {"match": "Milieuhygiënisch bodemonderzoek + saneringsplan-check",
     "uitvoerbaar": True,
     "validiteit": "bureau_verplicht",
     "grondslag": "Kwalibo (Besluit bodemkwaliteit / Omgevingswet): veldwerk "
                  "(BRL SIKB 2000), analyses (AS3000) en milieukundige "
                  "begeleiding van saneringen (BRL SIKB 6000) zijn "
                  "erkenningsplichtig; CROW 400 voor veilig werken.",
     "toelichting": "Het tracé raakt een locatie met een overheidsbesluit "
                    "bodemverontreiniging of nazorg (BRO SLD). De bureaustudie "
                    "brengt besluiten en consequenties in kaart, maar nader "
                    "onderzoek, saneringsplan/BUS-melding en begeleiding "
                    "moeten door een erkende bodemintermediair worden gedaan.",
     "instructie": "Voer een bureaustudie uit naar de bekende "
                   "bodemverontreiniging op het tracé: welke tracédelen "
                   "raken besluit- of nazorggebieden, wat betekent dat voor "
                   "graven (melding Bal, veiligheidsklasse CROW 400, "
                   "afvoerkosten), waar is sleufloze passage het overwegen "
                   "waard, en welke stappen moet het erkende bodembureau "
                   "vervolgens zetten (opvragen besluiten, nader onderzoek, "
                   "saneringsplan of BUS-melding).",
     "data": ("zones", "segmenten_ligging", "bodem_bronnen", "toetsing",
              "kosten", "databeperkingen")},

    {"match": "Bodeminformatie opvragen",
     "uitvoerbaar": False,
     "reden": "De benodigde bodemdata staat bij een gemeentelijk of "
              "provinciaal loket en is niet gekoppeld; zonder die data is "
              "geen bureaustudie mogelijk."},

    {"match": "Archeologisch bureauonderzoek",
     "uitvoerbaar": True,
     "validiteit": "bureau_verplicht",
     "grondslag": "Erfgoedwet; Kwaliteitsnorm Nederlandse Archeologie (KNA) / "
                  "BRL SIKB 4000: archeologisch (voor)onderzoek dat het "
                  "bevoegd gezag accepteert moet door een gecertificeerde "
                  "organisatie worden uitgevoerd.",
     "toelichting": "Deze bureaustudie signaleert de raakvlakken met "
                    "AMK-terreinen en de te verwachten onderzoekseisen; het "
                    "formele KNA-bureauonderzoek en eventueel IVO moeten door "
                    "een gecertificeerd archeologisch bureau worden gedaan.",
     "instructie": "Voer een archeologische bureaustudie uit: welke tracédelen "
                   "raken AMK-terrein, wat is de verstoringsdiepte en -breedte "
                   "van de aanleg (open sleuf vs. boring, boringen onder "
                   "monumentale zones door als alternatief), welke "
                   "vrijstellingsgrenzen en dubbelbestemmingen zijn te "
                   "verwachten, en wat is de logische onderzoeksstrategie "
                   "(KNA-bureauonderzoek, IVO, begeleiding) per raakvlak.",
     "data": ("zones", "segmenten_ligging", "boringen", "toetsing",
              "vergunningen")},

    {"match": "Bomen Effect Analyse",
     "uitvoerbaar": True,
     "validiteit": "bureau_aanbevolen",
     "grondslag": "Geen wettelijke certificeringsplicht; gemeenten eisen "
                  "doorgaans een boomtechnisch deskundige (bijv. European "
                  "Tree Technician) en een veldopname per boom conform het "
                  "Handboek Bomen (Norminstituut Bomen).",
     "toelichting": "De boompunten, kroon-/wortelzones en de tracéligging "
                    "zijn gekoppeld; conditie, soort en werkelijke "
                    "kroonprojectie vergen een veldopname. Het AI-rapport is "
                    "een voor-BEA die de knelpunten prioriteert.",
     "instructie": "Voer een voor-BEA uit: hoeveel bomen liggen met hun "
                   "wortelzone op of nabij het tracé, waar zijn de grootste "
                   "conflicten (aantal meters tracé in wortelzones per "
                   "tracédeel), welke maatregelen liggen per knelpunt voor de "
                   "hand (tracé verleggen, sleufloos passeren, handmatig "
                   "graven/wortelscherm) en welke bomen de boomtechnisch "
                   "deskundige in het veld moet opnemen.",
     "data": ("zones", "bomen", "segmenten_ligging", "boringen", "toetsing")},

    {"match": "Grondonderzoek boringen",
     "uitvoerbaar": True,
     "validiteit": "bureau_aanbevolen",
     "grondslag": "NEN-EN-ISO 22476 / NEN 9997 (Eurocode 7): sonderingen en "
                  "boringen zijn veldwerk door een geotechnisch bedrijf; de "
                  "bureaustudie op bestaande BRO-data kent geen "
                  "certificeringsplicht.",
     "toelichting": "Bestaande sonderingen uit de BRO nabij de boringen zijn "
                    "gekoppeld. De bureaustudie beoordeelt of die volstaan en "
                    "raamt het aanvullende veldonderzoek; nieuwe sonderingen "
                    "zijn veldwerk.",
     "instructie": "Voer een geotechnisch vooronderzoek uit voor de "
                   "voorgenomen boringen: beoordeel per boring de nabijgelegen "
                   "bestaande BRO-sonderingen (afstand, diepte, kwaliteits"
                   "klasse, relevantie), concludeer waar bestaande data "
                   "volstaat en waar nieuwe sonderingen nodig zijn (met "
                   "aantal/locatie-indicatie per intrede- en uittredepunt), "
                   "en benoem aandachtspunten voor de boorbaarheid en het "
                   "boorplan (dekking, verval, bestaande netten).",
     "data": ("boringen", "sonderingen", "kruisingen", "toetsing")},

    {"match": "Werkplan stiltegebied",
     "uitvoerbaar": True,
     "validiteit": "zelf",
     "grondslag": "Provinciale omgevingsverordening (stiltegebieden); geen "
                  "certificeringsplicht — het werkplan wordt door de "
                  "uitvoerende partij opgesteld en door de provincie "
                  "beoordeeld.",
     "toelichting": "Alle benodigde tracé- en uitvoeringsdata is beschikbaar; "
                    "het concept-werkplan kan na toetsing door de uitvoerder "
                    "bij de provincie worden ingediend.",
     "instructie": "Stel een concept-werkplan geluidsarme uitvoering op voor "
                   "de tracédelen in het stiltegebied: welke werkzaamheden "
                   "vinden daar plaats (sleufwerk per ligging, boringen, "
                   "moffen), welk materieel met welke geluidsbeperkende "
                   "maatregelen, werktijden, routering en de borging "
                   "(toolbox, monitoring).",
     "data": ("zones", "segmenten_ligging", "boringen", "kruisingen",
              "werkpakketten")},

    {"match": "Afstemming monumentenzorg",
     "uitvoerbaar": True,
     "validiteit": "zelf",
     "grondslag": "Omgevingswet / Erfgoedwet; de afstemmingsnotitie zelf kent "
                  "geen certificeringsplicht — het besluit ligt bij RCE en "
                  "gemeente.",
     "toelichting": "De notitie bereidt het overleg met RCE/gemeente voor op "
                    "basis van de gekoppelde monumentcontouren en het "
                    "tracéontwerp.",
     "instructie": "Stel een afstemmingsnotitie monumentenzorg op: welke "
                   "tracédelen raken een rijksmonument-contour, wat is daar "
                   "de voorgenomen uitvoeringswijze, welke effecten zijn te "
                   "verwachten (graafwerk, trillingen, tijdelijk "
                   "werkterrein), welke alternatieven bestaan en welke "
                   "vragen aan RCE/gemeente voorgelegd moeten worden.",
     "data": ("zones", "segmenten_ligging", "boringen", "vergunningen",
              "toetsing")},

    {"match": "Proefsleuven",
     "uitvoerbaar": False,
     "reden": "Liggingbepaling van de buisleiding is fysiek veldwerk; er is "
              "geen data om een bureaustudie op uit te voeren."},

    {"match": "NGE-vooronderzoek",
     "uitvoerbaar": True,
     "validiteit": "bureau_verplicht",
     "grondslag": "Certificatieschema Vooronderzoek en Risicoanalyse "
                  "Ontplofbare Oorlogsresten (CS-VROO) en CS-OOO/WSCS-OCE "
                  "voor opsporing: het formele vooronderzoek en de opsporing "
                  "zijn voorbehouden aan gecertificeerde bedrijven.",
     "toelichting": "De gekoppelde bodembelastingkaart geeft alleen de "
                    "verdachte zones; het historisch bronnenonderzoek dat "
                    "CS-VROO vereist zit niet in de data. Het AI-rapport "
                    "scoopt het formele vooronderzoek.",
     "instructie": "Stel een scopingdocument NGE op: welke tracédelen liggen "
                   "in verdacht gebied (met meters en ligging), wat betekent "
                   "dat voor de uitvoeringswijze (detectie vooraf, begeleide "
                   "ontgraving, sleufloos als alternatief), en formuleer de "
                   "onderzoeksvraag en afbakening voor het CS-VROO-"
                   "vooronderzoek door het gecertificeerde bureau.",
     "data": ("zones", "segmenten_ligging", "boringen", "toetsing",
              "databeperkingen")},

    {"match": "NGE (",
     "uitvoerbaar": False,
     "reden": "Geen gekoppelde bodembelastingkaart voor dit gebied; geen "
              "data voor een bureaustudie."},
]

SYSTEM = (
    "Je bent een senior adviseur ondergrondse infrastructuur bij een "
    "Nederlandse netbeheerder. Je voert bureauonderzoeken uit voor "
    "middenspanningstracés en schrijft daar beknopte, professionele "
    "rapporten van. Je schrijft in het Nederlands, zakelijk en concreet, en "
    "baseert je uitsluitend op de aangeleverde projectdata. Noem concrete "
    "aantallen, lengtes en registratienummers uit de data. Verzin niets: "
    "waar data ontbreekt benoem je dat expliciet en maak je duidelijk wat "
    "in het veld of bij een bureau belegd moet worden.\n\n"
)

# vaste opbouw; vervalt als voor deze onderzoekssoort een format is geüpload
SYSTEM_STRUCTUUR = (
    "STRUCTUUR — schrijf het rapport in deze opbouw:\n"
    "- `# <titel van het rapport>` (één regel).\n"
    "- `## Samenvatting` (1-2 alinea's met de hoofdconclusie).\n"
    "- `## 1. Aanleiding en doel`\n"
    "- `## 2. Werkwijze en geraadpleegde bronnen` — benoem dat dit een "
    "geautomatiseerd bureauonderzoek is op basis van de gekoppelde open "
    "datasets (noem ze) en wat daar niet in zit.\n"
    "- `## 3. Bevindingen` — de kern, met tabellen waar zinvol.\n"
    "- `## 4. Beoordeling en advies` — conclusies per bevinding en concrete "
    "vervolgstappen.\n"
    "- `## 5. Status en validiteit` — neem de aangeleverde validiteitstekst "
    "inhoudelijk over: wat is de juridische status van dit rapport en wat "
    "moet door wie formeel worden uitgevoerd.\n\n"
)
SYSTEM_STRUCTUUR_FORMAT = (
    "STRUCTUUR — de opdracht bevat een FORMAT van de organisatie (leeg "
    "sjabloon of voorbeeld): schrijf het rapport in exact die opbouw. Neem "
    "de aangeleverde validiteitstekst inhoudelijk op bij het onderdeel van "
    "het format dat over status, geldigheid of vervolg gaat.\n\n"
)
SYSTEM_OPMAAK = (
    "OPMAAK — begrensde Markdown: koppen met `#`/`##`/`###`, opsommingen "
    "met `- `, kernoordelen **vet**, Markdown-tabellen (max 6 kolommen). "
    "Geen code-blokken, links, afbeeldingen, voetnoten of HTML."
)
SYSTEM = SYSTEM + SYSTEM_STRUCTUUR + SYSTEM_OPMAAK
SYSTEM_MET_FORMAT = SYSTEM.replace(SYSTEM_STRUCTUUR, SYSTEM_STRUCTUUR_FORMAT)


def profiel_voor(soort: str) -> dict | None:
    for p in PROFIELEN:
        if soort.startswith(p["match"]):
            return p
    return None


def _validiteit_info(profiel: dict) -> dict:
    status = profiel["validiteit"]
    return {"status": status,
            "label": VALIDITEIT_LABELS[status],
            "grondslag": profiel["grondslag"],
            "toelichting": profiel["toelichting"]}


def opties(result: dict, variant_idx: int) -> list:
    """Per onderzoek uit het register: kan de AI dit als bureauonderzoek
    uitvoeren, en wat is dan de juridische status van het rapport?"""
    v = result["varianten"][variant_idx]
    uit = []
    for o in v.get("onderzoeken", []):
        p = profiel_voor(o["soort"])
        rij = {"nr": o["nr"], "soort": o["soort"]}
        if p is None:
            rij.update({"uitvoerbaar": False,
                        "reden": "Geen bureauonderzoek-profiel voor dit "
                                 "onderzoekstype."})
        elif not p["uitvoerbaar"]:
            rij.update({"uitvoerbaar": False, "reden": p["reden"]})
        elif (p["match"] == "Bomen Effect Analyse"
              and not result.get("bomen")):
            rij.update({"uitvoerbaar": False,
                        "reden": "Geen boompunten in de gekoppelde data."})
        else:
            rij.update({"uitvoerbaar": True,
                        "validiteit": _validiteit_info(p)})
        uit.append(rij)
    return uit


# ---------------------------------------------------------------------------
# Context: alleen de data die voor dít onderzoek relevant is
# ---------------------------------------------------------------------------

def _context(result: dict, variant_idx: int, onderzoek: dict, profiel: dict,
             projectnaam: str) -> dict:
    v = result["varianten"][variant_idx]

    segmenten = _zonder_geometrie(v.get("segmenten", []))
    ligging_totalen: dict = {}
    for s in segmenten:
        ligging_totalen[s["ligging"]] = round(
            ligging_totalen.get(s["ligging"], 0) + s["lengte_m"], 1)

    kandidaten = {
        "zones": v.get("zones", {}),
        "segmenten_ligging": {"totalen_m": ligging_totalen,
                              "segmenten": _cap(segmenten, 60, "segmenten")},
        # alles is open, tenzij: alleen bijzondere punten (sleufloos of
        # afwijking), open ontgravingen alleen als aantal
        "kruisingen": _cap(_zonder_geometrie(
            [c for c in v.get("kruisingen", []) if engine.is_bijzonder_punt(c)]),
            80, "kruisingen"),
        "open_ontgravingen_standaard_n": sum(
            1 for c in v.get("kruisingen", []) if not engine.is_bijzonder_punt(c)),
        "boringen": _cap(_zonder_geometrie(v.get("boringen", [])), 60,
                         "boringen"),
        "sonderingen": _cap(_zonder_geometrie(v.get("sonderingen", [])), 60,
                            "sonderingen"),
        "bomen": {"aantal": len(result.get("bomen", [])),
                  "bronnen": result.get("bomen_bronnen", []),
                  "rivm_bomenkaart_fractie": result.get("bomen_rivm_fractie"),
                  "wortelzones_xyr": _cap(result.get("bomen", []), 200,
                                          "bomen")},
        "toetsing": v.get("toetsing", []),
        "vergunningen": v.get("vergunningen", []),
        "werkpakketten": _zonder_geometrie(v.get("werkpakketten", [])),
        "kosten": v.get("kosten", {}),
        "bodem_bronnen": result.get("bodem_bronnen_regionaal", []),
        "databeperkingen": {"bgt_fouten": result.get("bgt_fouten", []),
                            "laag_fouten": result.get("laag_fouten", [])},
    }

    return {
        "project": {"naam": projectnaam or "zonder naam",
                    "gemeente": result.get("gemeente"),
                    "datum": date.today().strftime("%d-%m-%Y"),
                    "coordinatenstelsel": "RD New (EPSG:28992)"},
        "variant": {"naam": v["naam"], "lengte_m": v["lengte_m"]},
        "onderzoek": {k: onderzoek.get(k) for k in
                      ("nr", "soort", "aanleiding", "conclusie", "status")},
        "validiteit": _validiteit_info(profiel),
        "data": {k: kandidaten[k] for k in profiel["data"]},
    }


# ---------------------------------------------------------------------------
# Generatie (zelfde streaming-patroon als nota.stream_nota)
# ---------------------------------------------------------------------------

def _vind_onderzoek(result: dict, variant_idx: int, nr: str) -> dict:
    v = result["varianten"][variant_idx]
    for o in v.get("onderzoeken", []):
        if o["nr"] == nr:
            return o
    raise NotaError(f"Onderzoek {nr} niet gevonden in het register.")


def _check_profiel(result: dict, variant_idx: int, nr: str) -> tuple:
    onderzoek = _vind_onderzoek(result, variant_idx, nr)
    p = profiel_voor(onderzoek["soort"])
    if p is None or not p["uitvoerbaar"]:
        raise NotaError(
            f"'{onderzoek['soort']}' is niet als AI-bureauonderzoek uit te "
            f"voeren: {(p or {}).get('reden', 'geen profiel')}")
    return onderzoek, p


def stream_bureau(result: dict, variant_idx: int, nr: str, projectnaam: str):
    """Generator van Markdown-tekstdelen voor het bureauonderzoek-rapport.
    Fouten tijdens het streamen worden als FOUT_MARK-regel gemeld."""
    onderzoek, profiel = _check_profiel(result, variant_idx, nr)
    try:
        import anthropic
    except ImportError:
        raise NotaError("Python-pakket 'anthropic' is niet geïnstalleerd "
                        "(./.venv/bin/pip install anthropic).")
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise NotaError(
            "Geen Anthropic API-key gevonden. Zet ANTHROPIC_API_KEY in de "
            "omgeving of in infraengine/.env en herstart de server.")

    context = _context(result, variant_idx, onderzoek, profiel, projectnaam)
    import formats
    fm = formats.blokken("bureau:" + formats._slug(profiel["match"]))
    prompt = (
        f"Voer het volgende bureauonderzoek uit en schrijf het rapport: "
        f"{onderzoek['soort']} ({onderzoek['nr']}).\n\n"
        f"Aanleiding uit het register: {onderzoek['aanleiding']}\n\n"
        f"Opdracht: {profiel['instructie']}\n\n"
        "Projectdata (JSON, automatisch gegenereerd door het "
        "InfraEngine-ontwerpplatform):\n" +
        json.dumps(context, ensure_ascii=False)
    )

    def _stream():
        client = anthropic.Anthropic()
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_MET_FORMAT if fm else SYSTEM,
                thinking={"type": "adaptive"},
                messages=[{"role": "user",
                           "content": (fm or []) + [{"type": "text", "text": prompt}]}],
            ) as stream:
                for tekst in stream.text_stream:
                    yield tekst
                slot = stream.get_final_message()
            if slot.stop_reason == "max_tokens":
                yield FOUT_MARK + ("Rapport is afgekapt (max_tokens); "
                                   "probeer opnieuw.")
        except anthropic.AuthenticationError:
            yield FOUT_MARK + "Anthropic API-key is ongeldig."
        except anthropic.APIConnectionError:
            yield FOUT_MARK + "Verbinding met de Anthropic API verbroken."
        except anthropic.APIStatusError as e:
            yield FOUT_MARK + f"Anthropic API-fout ({e.status_code})."

    return _stream()


# ---------------------------------------------------------------------------
# Word-export (hergebruikt de docx-machinerie van nota.py)
# ---------------------------------------------------------------------------

def rapport_docx(result: dict, variant_idx: int, nr: str, projectnaam: str,
                 markdown: str) -> tuple[str, bytes]:
    """Rapport-Markdown naar een opgemaakt Word-document.

    Retourneert (bestandsnaam, docx-bytes)."""
    onderzoek, profiel = _check_profiel(result, variant_idx, nr)
    if len(markdown) > 2_000_000:
        raise NotaError("Rapport-inhoud is onwaarschijnlijk groot; geweigerd.")
    v = result["varianten"][variant_idx]
    validiteit = _validiteit_info(profiel)
    blocks = nota_mod._md_blocks(markdown.split(FOUT_MARK)[0])

    # eerste h1 = rapporttitel op de titelpagina, rest is inhoud
    titel = onderzoek["soort"]
    inhoud = []
    for soort, data in blocks:
        if soort == "h1" and titel == onderzoek["soort"]:
            titel = data
        else:
            inhoud.append((soort, data))

    datum = date.today().strftime("%d-%m-%Y")
    P = []
    P.append(nota_mod._para("InfraEngine · Bureauonderzoek",
                            style="NotaKicker"))
    P.append(nota_mod._para(onderzoek["soort"], style="NotaTitel"))
    P.append(nota_mod._para(titel, style="NotaSubtitel"))
    P.append(nota_mod._tabel([
        ["Project", projectnaam or "zonder naam"],
        ["Gemeente", result.get("gemeente") or "onbekend"],
        ["Variant", f"{v['naam']} — {v['lengte_m']:.0f} m"],
        ["Onderzoek", f"{onderzoek['nr']} — {onderzoek['soort']}"],
        ["Datum", datum],
        ["Validiteit", validiteit["label"]],
        ["Status", "Concept — AI-gegenereerd, toetsing vereist"],
    ], kopregel=False))
    P.append(nota_mod._para(
        f"Automatisch uitgevoerd bureauonderzoek (model {MODEL}) op basis "
        f"van de gekoppelde open data van het InfraEngine-ontwerpplatform. "
        f"{validiteit['grondslag']}",
        size=18, color=nota_mod._INK3,
        ppr_extra='<w:spacing w:before="4000"/>'))
    P.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    for soort, data in inhoud:
        if soort in ("h1", "h2"):
            P.append(nota_mod._para(data, style="Kop1"))
        elif soort == "h3":
            P.append(nota_mod._para(data, style="Kop2"))
        elif soort == "ul":
            for item in data:
                P.append(nota_mod._para("–  " + item, style="Opsomming"))
        elif soort == "table":
            P.append(nota_mod._tabel(data))
        else:
            P.append(nota_mod._para(data))

    footer = (f"{projectnaam or 'InfraEngine'} · {onderzoek['nr']} "
              f"bureauonderzoek · concept {datum}")
    docx = nota_mod._docx_pakket("".join(P), footer)
    slug = (projectnaam or "infraengine").strip().replace(" ", "_")[:40] \
        or "infraengine"
    naam = (f"bureauonderzoek_{onderzoek['nr']}_{slug}_"
            f"{date.today().strftime('%Y%m%d')}.docx")
    return naam, docx
