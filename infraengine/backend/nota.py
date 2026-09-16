"""Ontwerpnota's (VO / DO / UO) met AI.

Bundelt alle beschikbare data van het berekende tracé — varianten, MCA,
segmenten, kruisingen, boringen, vergunningen, onderzoeken, ZRO (incl.
dossierstatus), toetsing, moffen en kosten — en laat Claude daar een
fase-specifieke ontwerpnota van schrijven:

- VO  (Voorlopig Ontwerp): variantenafweging en onderbouwde voorkeursvariant.
- DO  (Definitief Ontwerp): definitieve tracékeuze, technieken, strategie
  vergunningen/ZRO, resterende onderzoeken.
- UO  (Uitvoeringsgereed Ontwerp): uitvoeringsaspecten, boringen en
  werkterreinen, meldingen, graafveiligheid, restpunten.

Het model schrijft de nota als begrensde Markdown (koppen, opsommingen,
tabellen). Die stroom wordt live naar de frontend gestreamd voor de
HTML-weergave, en met ``markdown_naar_docx`` omgezet naar een opgemaakt
Word-document: titelpagina met documentgegevens, echte kopstijlen (zichtbaar
in de Word-navigatie), rastertabellen en een voettekst met paginanummers.

Vereist een Anthropic API-key: omgevingsvariabele ``ANTHROPIC_API_KEY`` of
een regel in ``infraengine/.env``.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # API-key uit infraengine/.env laden als die er is
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

MODEL = "claude-opus-4-8"
MAX_TOKENS = 32000

# Huisstijlkleuren (gelijk aan frontend/styles.css), als hex zonder '#'
_INK = "1E2A33"
_INK2 = "4B5A66"
_INK3 = "7C8994"
_ACCENT = "C8322B"
_RULE = "C9CFCB"
_PAPER2 = "EAECE7"

FASE_NAMEN = {
    "VO": "Voorlopig Ontwerp",
    "DO": "Definitief Ontwerp",
    "UO": "Uitvoeringsgereed Ontwerp",
}

# Vast hoofdstukkenformat naar de HVP/Liander-ontwikkelnota's (UKZ DO-ApD2
# en UO-ApD1): 1 Inleiding (scope, werkgebied, werkpakkettentabel,
# afwijkingen, leeswijzer) · 2 Gerelateerde documenten · 3 Ontwerp (per
# verbinding/werkpakket + CAR-verzekering) · 4 Omgeving (stakeholders per
# werkpakket, onderzoeken, seizoensbeperkingen, vergunningen) ·
# 5 Randvoorwaarden (risico's, eisen, planning met mijlpalen, V&G, raming,
# controle en review) · 6 Restpunten (tabel). Het VO-format is hiervan
# afgeleid, met de variantenafweging (MCA) in hoofdstuk 3.
_FMT_KOP = (
    "Houd exact het vaste ontwikkelnota-format van de netbeheerder aan:\n"
    "Begin met `# {fase} Ontwikkelnota | <projectnaam>` en direct daarna, "
    "zonder kop, drie kleine tabellen:\n"
    "1) Verificatietabel (rijen: Opdrachtgever; Opgesteld door; Verificatie; "
    "Autorisatie; Vrijgave; Datum; Versie) — vul onbekende namen als "
    "[IN TE VULLEN: naam];\n"
    "2) Projectteam (kolommen Naam | Functie | Organisatie) met de rollen "
    "ontwerpleider, projectbeheerser, omgevingsmanager, projectleider, "
    "planner en technisch manager als invulregels;\n"
    "3) Versiebeheer (kolommen Versie | Datum | Status | Toelichting) met "
    "één regel: 0.1, vandaag, 'Concept ter review', 'AI-gegenereerd concept'.\n"
    "Daarna deze hoofdstukken, met exact deze nummers en titels:\n"
)

_FMT_STAART = (
    "\n`## 6. Restpunten` — tabel (Nummer | Omschrijving | Status) met de "
    "concrete openstaande punten uit de data: knelpunten uit de toetsing, "
    "niet-gevestigde ZRO's, openstaande onderzoeken, boringen die nog "
    "definitief moeten worden, ontbrekende koppelingen (KLIC/BRK). Status "
    "per punt: 'Nog opstarten' of 'Lopend'.\n\n"
    "Invulregels: gebruik overal de echte aantallen, lengtes, nummers "
    "(WP-, HDD-/BOR-, VRG-, OND-, ZRO-, RIS-) en bedragen uit de "
    "projectdata. Documentverwijzingen die de applicatie niet kent schrijf "
    "je als generieke verwijzing tussen rechte haken, bijvoorbeeld "
    "[verificatieplan] of [IN TE VULLEN: link raming]."
)

NOTA_FASEN = {
    "VO": {
        "titel": "VO Ontwikkelnota",
        "doel": _FMT_KOP.format(fase="VO") + (
            "`## 1. Inleiding` — doelzin (ontwerpscope als input voor de "
            "VO-fase); `### 1.1 Scope van de opdracht` (wat wordt ontworpen: "
            "MS-tracé tussen de stations, aantal verbindingen); `### 1.2 Scope "
            "werkgebied` (gebied, gemeente(n), totale tracélengte, verwijzing "
            "[overzichtstekening]); `### 1.3 Werkpakketten` — tabel "
            "(Werkpakket | Van | Naar | Meters) uit de werkpakketten-data met "
            "totaalregel; `### 1.4 Afwijkingsregister/wijzigingen` (procedure + "
            "verwijzing [afwijkingenregister]); `### 1.5 Leeswijzer`.\n"
            "`## 2. Gerelateerde documenten` — documentenlijst-verwijzing; "
            "`### 2.1 Generieke ontwerprichtlijnen` — tabel met de toegepaste "
            "richtlijndocumenten uit de data (richtlijnen_toegepast) en "
            "invulregels voor de S- en W-normbladen van de netbeheerder.\n"
            "`## 3. Ontwerp` — het VO-hart: `### 3.1 Variantenafweging` met "
            "de MCA-tabel over alle varianten (lengte, kruisingen, boringen, "
            "privaat terrein, kosten) en de onderbouwde keuze voor de "
            "**voorkeursvariant**; `### 3.2 Beschrijving voorkeurstracé` — "
            "per verbinding/werkpakket het tracéverloop op hoofdlijnen "
            "(ligging berm/voetpad/rijbaan, belangrijkste kruisingen met "
            "voorgestelde techniek en HDD-/boringnummers); `### 3.3 "
            "CAR-verzekering` — toets: aanneemsom > €20.000.000 of een boring "
            "> €250.000 vereist aanvullende CAR; toets op de RAW-calculatie.\n"
            "`## 4. Omgeving` — `### 4.1 Stakeholders per werkpakket`: per "
            "werkpakket een tabel (Stakeholder | Vergunning, toestemming | "
            "Perceel (indien privaat)) uit het vergunningen- en ZRO-register; "
            "`### 4.2 Stakeholders` (bevoegde gezagen, ZRO-eigenaren, "
            "omwonenden); `### 4.3 Conditionerende onderzoeken` — de in de "
            "VO-fase uit te voeren quickscans/bureauonderzoeken uit het "
            "onderzoeksregister met per onderzoek aanleiding en status; "
            "`### 4.4 Seizoensbeperkingen` (broedseizoen, gesloten seizoen "
            "keringen); `### 4.5 Vergunningen` (eerste beeld + strategie, "
            "verwijzing [vergunningenregister]).\n"
            "`## 5. Randvoorwaarden` — `### 5.1 Risico's en "
            "beheersmaatregelen`: top-risico's uit het risicoregister als "
            "tabel (ID | Risico | Score | Belangrijkste beheersmaatregel), "
            "RISMAN-ordening hoog→laag; `### 5.2 Eisen` (verificatie-aanpak, "
            "toetsing); `### 5.3 Planning` — mijlpalentabel (VO gereed, DO "
            "gereed, UO gereed, geplande start uitvoering, IBN-datum) — "
            "afleiden uit de planning-weken of [IN TE VULLEN]; `### 5.4 V&G "
            "plan ontwerp`; `### 5.5 Raming` (indicatieve aannemingssom uit "
            "de RAW-calculatie); `### 5.6 Controle en review` (tollgate TM2, "
            "reviewproces bouwteam).")
        + _FMT_STAART,
    },
    "DO": {
        "titel": "DO Ontwikkelnota",
        "doel": _FMT_KOP.format(fase="DO") + (
            "`## 1. Inleiding` — doelzin (ontwerpscope als input voor de "
            "DO-fase); `### 1.1 Scope van de opdracht`; `### 1.2 Scope "
            "werkgebied` (gebied, gemeente(n), totale tracélengte); "
            "`### 1.3 Werkpakketten` — tabel (Werkpakket | Van | Naar | "
            "Meters) met totaalregel; `### 1.4 Afwijkingsregister/"
            "wijzigingen`; `### 1.5 Leeswijzer`.\n"
            "`## 2. Gerelateerde documenten` — `### 2.1 Generieke "
            "ontwerprichtlijnen` — tabel met de toegepaste "
            "richtlijndocumenten (richtlijnen_toegepast) en invulregels voor "
            "de S- en W-normbladen.\n"
            "`## 3. Ontwerp` — de kern van het DO: groepeer het tracé in "
            "logische verbindingen (reeksen werkpakketten) en beschrijf per "
            "verbinding `### 3.x Verbinding x – WPxx t/m WPyy – <meters> "
            "meters` het tracéverloop als lopende tekst, zoals een "
            "ontwerpleider dat doet: aan welke zijde van welke weg, waar "
            "wordt overgestoken (persing), welke watergangen/wegen/spoor met "
            "welke techniek en welk boringnummer worden gekruist, moffen aan "
            "begin- en eindpunt, en sluit elke verbinding af met "
            "**Restpunten:** (bijv. boringen nog in concept, definitief in "
            "UO). Afsluitend `### 3.x CAR-verzekering` — toets aanneemsom "
            "> €20.000.000 / boring > €250.000 op de RAW-calculatie.\n"
            "`## 4. Omgeving` — `### 4.1 Stakeholders per werkpakket`: per "
            "werkpakket een tabel (Stakeholder | Vergunning, toestemming | "
            "Perceel (indien privaat)) uit vergunningen- en ZRO-register; "
            "`### 4.2 Stakeholders`; `### 4.3 Conditionerende onderzoeken` — "
            "per onderzoek uit het onderzoeksregister de stand en conclusie; "
            "`### 4.4 Aanvullende conditionerende onderzoeken` (vervolg op "
            "de quickscans, wat loopt nog); `### 4.5 Seizoensbeperkingen`; "
            "`### 4.6 Vergunningen` (aanvraag na akkoord DO, verwijzing "
            "[vergunningenregister]).\n"
            "`## 5. Randvoorwaarden` — `### 5.1 Risico's en "
            "beheersmaatregelen` (top-risico's uit het risicoregister als "
            "tabel, RISMAN hoog→laag); `### 5.2 Eisen` (eisenverificatie, "
            "toetsingsresultaten); `### 5.3 Planning` — mijlpalentabel (DO "
            "gereed, UO gereed, contract getekend, geplande start uitvoering "
            "(GSU), IBN-datum) uit de planning-weken of [IN TE VULLEN], plus "
            "de planning per werkpakket; `### 5.4 V&G plan ontwerp` (VGM-O, "
            "GO!-doelstelling); `### 5.5 Raming` (aannemingssom uit de "
            "RAW-calculatie); `### 5.6 Controle en review` (tollgate T3 als "
            "basis voor de review, indienen in [VISI]).")
        + _FMT_STAART,
    },
    "UO": {
        "titel": "UO Ontwikkelnota",
        "doel": _FMT_KOP.format(fase="UO") + (
            "`## 1. Inleiding` — `### 1.1 Scope van de opdracht`; `### 1.2 "
            "Scope werkgebied`; `### 1.3 Werkpakketten` — tabel (Werkpakket "
            "| Van | Naar | Meters) met totaalregel; `### 1.4 "
            "Afwijkingsregister/wijzigingen` (afwijkingenregister + "
            "VTW-overzicht); `### 1.5 Leeswijzer`.\n"
            "`## 2. Gerelateerde documenten` — `### 2.1 Generieke "
            "ontwerprichtlijnen` — tabel toegepaste richtlijnen + "
            "invulregels S-/W-bladen.\n"
            "`## 3. Ontwerp` — uitvoeringsgereed: `### 3.1 HDD-boringen "
            "detailoverzicht` — tabel van alle boringen (Nr | Techniek | "
            "Lengte m | Intredepunt RD | Uittredepunt RD | Status) uit het "
            "boorregister; daarna per werkpakket `### 3.x Werkpakket WPxx – "
            "(<van> – <naar>) – <meters> meters` met de uitvoeringswijze "
            "(sleufwerk per ligging, boringen definitief, moffen op "
            "haspellengte) en verwijzingen naar [werkplan civiel werk], "
            "[werkplan warm werk], [bedieningsplan] en [keuringsplan]; "
            "afsluitend `### 3.x CAR-verzekering` — toets aanneemsom "
            "> €20.000.000 / boring > €250.000.\n"
            "`## 4. Omgeving` — `### 4.1 Stakeholders per werkpakket` "
            "(tabellen Stakeholder | Vergunning, toestemming | Perceel); "
            "`### 4.2 Stakeholders`; `### 4.3 Communicatie` "
            "(bewonersbrieven, BouwApp, omgevingsmanager realisatie); "
            "`### 4.4 Conditionerende onderzoeken` — stand van alle "
            "onderzoeken met conclusies en doorwerking naar de uitvoering "
            "(MKB-begeleiding, ecologische vrijgave, werkprotocollen); "
            "`### 4.5 Seizoensbeperkingen` (broedseizoen medio maart–"
            "augustus, ecologische vrijgave vóór start); `### 4.6 "
            "Vergunningen` (status aangevraagd/verleend, verwijzing "
            "[vergunningenregister]); `### 4.7 Verkeersmaatregelenplan` "
            "(borging veilige uitvoering, verwijzing [verkeersplan]).\n"
            "`## 5. Randvoorwaarden` — het geaccepteerde DO geldt als "
            "vertrekpunt; `### 5.1 Risico's en beheersmaatregelen` "
            "(top-risico's uit het risicoregister, RISMAN hoog→laag); "
            "`### 5.2 Eisen` (eisenverificatie UO); `### 5.3 Planning` — "
            "mijlpalentabel (UO gereed, contract getekend, GSU, IBN-datum) "
            "uit de planning-weken of [IN TE VULLEN]; `### 5.4 V&G plan "
            "uitvoering` (VGM-U, GO!-doelstelling); `### 5.5 Raming` "
            "(aannemingssom uit de RAW-calculatie, opgebouwd per "
            "werkpakket); `### 5.6 Controle en review` (tollgate T4 als "
            "Stop/GO, review kernteam, indienen in [VISI]).")
        + _FMT_STAART,
    },
}

SYSTEM = (
    "Je bent een senior ontwerpleider kabelinfrastructuur bij een Nederlandse "
    "netbeheerder. Je schrijft ontwikkelnota's voor middenspanningstracés "
    "volgens de fasering VO (Voorlopig Ontwerp), DO (Definitief Ontwerp) en "
    "UO (Uitvoeringsgereed Ontwerp), in het vaste ontwikkelnota-format van "
    "het bouwteam (hoofdstukken Inleiding, Gerelateerde documenten, Ontwerp, "
    "Omgeving, Randvoorwaarden, Restpunten). Je schrijft in het Nederlands, "
    "zakelijk en concreet, en baseert je uitsluitend op de aangeleverde "
    "projectdata. Noem concrete aantallen, lengtes, bedragen en "
    "registratienummers uit de data (bedragen als € 1.234). Verzin geen "
    "feiten: waar data ontbreekt of indicatief is (bijv. geen KLIC- of "
    "BRK-koppeling, indicatieve tarieven, falende datalagen) benoem je dat "
    "expliciet, of zet je een gemarkeerde invulplek [IN TE VULLEN: …]. "
    "Verwijzingen naar externe documenten die de applicatie niet kent "
    "schrijf je als generieke verwijzing tussen rechte haken, zoals in het "
    "format: zie [LINK].\n\n"
    "OPMAAK — schrijf de nota in deze begrensde Markdown en niets anders:\n"
    "- Begin direct met de titelregel `# …` en volg daarna exact de "
    "hoofdstukindeling die in de opdracht wordt voorgeschreven "
    "(`## 1. <titel>` … `### 1.1 <titel>`); geen extra hoofdstukken op "
    "topniveau, geen samenvatting vooraf.\n"
    "- Opsommingen met `- `; kernbegrippen of oordelen **vet**.\n"
    "- Gebruik Markdown-tabellen (met kopregel en scheidingsregel `|---|`) "
    "voor de voorgeschreven tabellen en registeroverzichten (werkpakketten, "
    "MCA, stakeholders per werkpakket, boringen, mijlpalen, risico's, "
    "restpunten). Houd tabellen op maximaal 6 kolommen en vat lange "
    "registers samen tot de relevante rijen.\n"
    "- Geen code-blokken, links, afbeeldingen, voetnoten of HTML."
)


class NotaError(Exception):
    """Nota kon niet worden gegenereerd (configuratie of API)."""


# Markering waarmee een fout midden in de stream aan de frontend wordt gemeld
FOUT_MARK = "\n\n[NOTA-FOUT] "


# ---------------------------------------------------------------------------
# Context: alle beschikbare data compact samenvatten voor het model
# ---------------------------------------------------------------------------

def _cap(rows: list, n: int, naam: str) -> list | dict:
    """Lange registers begrenzen; meld hoeveel rijen zijn weggelaten."""
    if len(rows) <= n:
        return rows
    return {"eerste_rijen": rows[:n],
            "weggelaten": f"{len(rows) - n} van {len(rows)} rijen weggelaten "
                          f"({naam}); gebruik de totalen"}


def _zonder_geometrie(rows: list, extra_weg: tuple = ()) -> list:
    weg = {"geometry", "punt", "route"} | set(extra_weg)
    return [{k: v for k, v in r.items() if k not in weg} for r in rows]


def bouw_context(result: dict, variant_idx: int, projectnaam: str) -> dict:
    """Compacte projectcontext uit het rekenresultaat + ZRO-dossiers."""
    import zro as zro_mod

    v = result["varianten"][variant_idx]

    segmenten = _zonder_geometrie(v.get("segmenten", []))
    ligging_totalen: dict = {}
    for s in segmenten:
        ligging_totalen[s["ligging"]] = round(
            ligging_totalen.get(s["ligging"], 0) + s["lengte_m"], 1)

    kruisingen = _zonder_geometrie(v.get("kruisingen", []))
    kruising_totalen: dict = {}
    for c in kruisingen:
        sleutel = f"{c['soort']} — {c['techniek']}"
        kruising_totalen[sleutel] = kruising_totalen.get(sleutel, 0) + 1

    zro_rijen = []
    for z in v.get("zro", []):
        d = zro_mod.laad_dossier(zro_mod.slug_van(z["perceel"]))
        zro_rijen.append({
            "nr": z["nr"], "perceel": z["perceel"],
            "eigenaar": d["eigenaar_naam"] or z.get("eigenaar", "onbekend"),
            "ingenomen_lengte_m": z["ingenomen_lengte_m"],
            "werkstrook_m2": z["werkstrook_m2"],
            "aard_recht": d["aard_recht"] or z.get("aard_recht", ""),
            "vergoeding_eenmalig_eur": d["vergoeding_eenmalig_eur"],
            "vergoeding_jaarlijks_eur": d["vergoeding_jaarlijks_eur"],
            "status_dossier": d["status"],
        })
    zro_status_totalen: dict = {}
    for z in zro_rijen:
        zro_status_totalen[z["status_dossier"]] = \
            zro_status_totalen.get(z["status_dossier"], 0) + 1

    mca = [var["mca"] for var in result["varianten"]]

    return {
        "project": {
            "naam": projectnaam or "zonder naam",
            "gemeente": result.get("gemeente"),
            "datum": date.today().strftime("%d-%m-%Y"),
            "modus": result.get("modus"),
            "aantal_stations": len(result.get("stations", [])),
            "coordinatenstelsel": "RD New (EPSG:28992)",
            # richtlijndocumenten (beheerscherm) die als parameter-overrides
            # in deze berekening zijn toegepast
            "richtlijnen_toegepast": result.get("richtlijnen_toegepast", []),
        },
        "variant": {
            "naam": v["naam"],
            "lengte_m": v["lengte_m"],
            "wegingsprofiel": v.get("wegingsprofiel"),
            "zones_m": v.get("zones", {}),
        },
        "mca_alle_varianten": mca,
        "variant_fouten": result.get("variant_fouten", []),
        "ligging_totalen_m": ligging_totalen,
        "segmenten": _cap(segmenten, 80, "segmenten"),
        "kruising_totalen": kruising_totalen,
        "kruisingen": _cap(kruisingen, 120, "kruisingen"),
        "boringen": _cap(_zonder_geometrie(v.get("boringen", [])), 80, "boringen"),
        "bestaande_sonderingen_bro": _cap(
            _zonder_geometrie(v.get("sonderingen", [])), 60, "sonderingen"),
        "vergunningen": v.get("vergunningen", []),
        "onderzoeken": v.get("onderzoeken", []),
        "zro_status_totalen": zro_status_totalen,
        "zro": _cap(zro_rijen, 100, "ZRO-percelen"),
        "toetsing": v.get("toetsing", []),
        # kans- en risicoregister uit het procesdossier (procespagina),
        # RISMAN-geordend — voedt hoofdstuk 5.1 van de ontwikkelnota
        "risicoregister": _risicoregister(projectnaam),
        "moffen_aantal": len(v.get("moffen", [])),
        "moffen": _cap(_zonder_geometrie(v.get("moffen", [])), 40, "moffen"),
        "werkpakketten": _zonder_geometrie(v.get("werkpakketten", [])),
        "planning_per_werkpakket_wk": v.get("planning", []),
        "kosten": v.get("kosten", {}),
        "raw_calculatie": {
            "systematiek": (v.get("calculatie") or {}).get("systematiek"),
            "prijspeil": (v.get("calculatie") or {}).get("prijspeil"),
            "hoofdstuk_totalen_eur": (v.get("calculatie") or {}).get("hoofdstukken", []),
            "inschrijvingsstaat": (v.get("calculatie") or {}).get("staart", []),
            "aannemingssom_excl_btw": (v.get("calculatie") or {}).get("aannemingssom_excl_btw"),
            "uitvoeringsduur_wk": (v.get("calculatie") or {}).get("uitvoeringsduur_wk"),
            "uitgangspunten": (v.get("calculatie") or {}).get("uitgangspunten", []),
        } if v.get("calculatie") else None,
        "databeperkingen": {
            "bgt_fouten": result.get("bgt_fouten", []),
            "laag_fouten": result.get("laag_fouten", []),
            "bodem_bronnen_regionaal": result.get("bodem_bronnen_regionaal", []),
            "bomen_bronnen": result.get("bomen_bronnen", []),
            "bekend": [
                "geen KLIC-koppeling (netdichtheid onbekend)",
                "geen BRK-eigendom (eigenaren proxy/handmatig)",
                "tarieven en doorlooptijden zijn indicatieve startwaarden",
            ],
        },
    }


# ---------------------------------------------------------------------------
# Generatie: Markdown-stroom uit het model
# ---------------------------------------------------------------------------

def _risicoregister(projectnaam: str) -> list:
    """Top-risico's uit het procesdossier (data/proces/<project>.json)."""
    if not projectnaam:
        return []
    try:
        import proces as proces_mod
        rijen = proces_mod.laad_state(projectnaam).get("risico", [])
    except Exception:
        return []
    return [{k: r.get(k) for k in
             ("nr", "omschrijving", "oorzaak", "gevolg", "aspect", "stadium",
              "allocatie", "werkpakket", "kans", "score", "status",
              "maatregelen")}
            for r in rijen[:25]]


def _check_fase(fase: str) -> str:
    fase = fase.upper()
    if fase not in NOTA_FASEN:
        raise NotaError(f"Onbekende fase '{fase}'; kies VO, DO of UO.")
    return fase


def stream_nota(result: dict, fase: str, variant_idx: int, projectnaam: str):
    """Controleert de configuratie en geeft een generator van tekstdelen
    (Markdown) terug. Fouten tijdens het streamen worden als FOUT_MARK-regel
    in de stroom gemeld (de HTTP-status is dan al verstuurd)."""
    fase = _check_fase(fase)
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

    context = bouw_context(result, variant_idx, projectnaam)
    prompt = (
        f"Schrijf een ontwerpnota voor de fase {fase} "
        f"({FASE_NAMEN[fase]}).\n\n"
        f"Opdracht: {NOTA_FASEN[fase]['doel']}\n\n"
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
                system=SYSTEM,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for tekst in stream.text_stream:
                    yield tekst
                slot = stream.get_final_message()
            if slot.stop_reason == "max_tokens":
                yield FOUT_MARK + "Nota is afgekapt (max_tokens); probeer opnieuw."
        except anthropic.AuthenticationError:
            yield FOUT_MARK + "Anthropic API-key is ongeldig."
        except anthropic.APIConnectionError:
            yield FOUT_MARK + "Verbinding met de Anthropic API verbroken."
        except anthropic.APIStatusError as e:
            yield FOUT_MARK + f"Anthropic API-fout ({e.status_code})."

    return _stream()


# ---------------------------------------------------------------------------
# Markdown → blokken (gedeeld met de frontend-weergave, zelfde subset)
# ---------------------------------------------------------------------------

_LIJST_RE = re.compile(r"^([-*–]|\d+[.)])\s+")
_SEP_CEL_RE = re.compile(r"^:?-{2,}:?$")


def _md_blocks(md: str) -> list:
    """Begrensde Markdown naar blokken: (soort, inhoud).

    Soorten: h1/h2/h3 (str), p (str), ul (list[str]), table (list[list[str]]).
    """
    blocks: list = []
    lines = md.replace("\r", "").split("\n")
    para: list = []

    def flush():
        if para:
            blocks.append(("p", " ".join(para)))
            para.clear()

    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            level = min(len(s) - len(s.lstrip("#")), 3)
            blocks.append((f"h{level}", s.lstrip("#").strip()))
        elif _LIJST_RE.match(s):
            flush()
            items = []
            while i < len(lines) and _LIJST_RE.match(lines[i].strip()):
                items.append(_LIJST_RE.sub("", lines[i].strip()))
                i += 1
            blocks.append(("ul", items))
            continue
        elif s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in
                         lines[i].strip().strip("|").split("|")]
                if not all(_SEP_CEL_RE.match(c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                breedte = max(len(r) for r in rows)
                blocks.append(("table",
                               [r + [""] * (breedte - len(r)) for r in rows]))
            continue
        else:
            para.append(s)
        i += 1
    flush()
    return blocks


# ---------------------------------------------------------------------------
# Word-document met stijlen, tabellen, titelpagina en voettekst
# ---------------------------------------------------------------------------

def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")


def _runs(tekst: str, *, size: int | None = None, bold=False, italic=False,
          color: str | None = None) -> str:
    """Tekst met inline **vet** / *cursief* naar Word-runs."""
    uit = []
    for deel in _INLINE_RE.split(tekst):
        if not deel:
            continue
        b, it, t = bold, italic, deel
        if deel.startswith("**") and deel.endswith("**") and len(deel) > 4:
            b, t = True, deel[2:-2]
        elif deel.startswith("*") and deel.endswith("*") and len(deel) > 2:
            it, t = True, deel[1:-1]
        rpr = ""
        if b:
            rpr += "<w:b/>"
        if it:
            rpr += "<w:i/>"
        if size:
            rpr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        if color:
            rpr += f'<w:color w:val="{color}"/>'
        rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        uit.append(f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(t)}</w:t></w:r>')
    return "".join(uit)


def _para(tekst: str, *, style: str | None = None, ppr_extra: str = "",
          **runkw) -> str:
    stijl = f'<w:pStyle w:val="{style}"/>' if style else ""
    return f"<w:p><w:pPr>{stijl}{ppr_extra}</w:pPr>{_runs(tekst, **runkw)}</w:p>"


def _tabel(rows: list, kopregel: bool = True) -> str:
    trs = []
    for ri, row in enumerate(rows):
        cellen = []
        for cel in row:
            shd = (f'<w:shd w:val="clear" w:color="auto" w:fill="{_PAPER2}"/>'
                   if kopregel and ri == 0 else "")
            tcpr = f'<w:tcPr>{shd}<w:vAlign w:val="center"/></w:tcPr>'
            runs = _runs(cel, size=18, bold=(kopregel and ri == 0))
            cellen.append(
                f'<w:tc>{tcpr}<w:p><w:pPr><w:spacing w:before="30" '
                f'w:after="30"/></w:pPr>{runs}</w:p></w:tc>')
        trs.append(f"<w:tr>{''.join(cellen)}</w:tr>")
    tblpr = ('<w:tblPr><w:tblStyle w:val="NotaTabel"/>'
             '<w:tblW w:w="5000" w:type="pct"/>'
             '<w:tblLayout w:type="autofit"/></w:tblPr>')
    # spacer-paragraaf na de tabel (Word-eis, en nette witruimte)
    return (f"<w:tbl>{tblpr}{''.join(trs)}</w:tbl>"
            '<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')


_STYLES_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults>
 <w:rPrDefault><w:rPr>
  <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>
  <w:sz w:val="21"/><w:szCs w:val="21"/><w:color w:val="{_INK}"/>
 </w:rPr></w:rPrDefault>
 <w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="288" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Standaard">
 <w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="NotaKicker">
 <w:name w:val="Nota Kicker"/>
 <w:pPr><w:spacing w:before="2400" w:after="80"/></w:pPr>
 <w:rPr><w:b/><w:caps/><w:sz w:val="20"/><w:color w:val="{_ACCENT}"/>
  <w:spacing w:val="30"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="NotaTitel">
 <w:name w:val="Nota Titel"/>
 <w:pPr><w:spacing w:after="60"/></w:pPr>
 <w:rPr><w:b/><w:sz w:val="60"/><w:szCs w:val="60"/><w:color w:val="{_INK}"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="NotaSubtitel">
 <w:name w:val="Nota Subtitel"/>
 <w:pPr><w:spacing w:after="500"/>
  <w:pBdr><w:bottom w:val="single" w:sz="12" w:space="10" w:color="{_ACCENT}"/></w:pBdr>
 </w:pPr>
 <w:rPr><w:sz w:val="30"/><w:szCs w:val="30"/><w:color w:val="{_INK2}"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Kop1">
 <w:name w:val="heading 1"/>
 <w:pPr><w:keepNext/><w:spacing w:before="380" w:after="140"/>
  <w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="{_ACCENT}"/></w:pBdr>
  <w:outlineLvl w:val="0"/></w:pPr>
 <w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/><w:color w:val="{_INK}"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Kop2">
 <w:name w:val="heading 2"/>
 <w:pPr><w:keepNext/><w:spacing w:before="260" w:after="100"/>
  <w:outlineLvl w:val="1"/></w:pPr>
 <w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/><w:color w:val="{_INK2}"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Opsomming">
 <w:name w:val="List Paragraph"/>
 <w:pPr><w:spacing w:after="60"/>
  <w:ind w:left="425" w:hanging="227"/></w:pPr>
</w:style>
<w:style w:type="table" w:styleId="NotaTabel">
 <w:name w:val="Nota Tabel"/>
 <w:tblPr>
  <w:tblBorders>
   <w:top w:val="single" w:sz="4" w:color="{_RULE}"/>
   <w:left w:val="single" w:sz="4" w:color="{_RULE}"/>
   <w:bottom w:val="single" w:sz="4" w:color="{_RULE}"/>
   <w:right w:val="single" w:sz="4" w:color="{_RULE}"/>
   <w:insideH w:val="single" w:sz="4" w:color="{_RULE}"/>
   <w:insideV w:val="single" w:sz="4" w:color="{_RULE}"/>
  </w:tblBorders>
  <w:tblCellMar>
   <w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>
  </w:tblCellMar>
 </w:tblPr>
</w:style>
</w:styles>"""


def _footer_xml(tekst: str) -> str:
    fld = lambda instr: (  # noqa: E731 — paginanummer-veld
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {instr} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    rpr = (f'<w:rPr><w:sz w:val="16"/><w:szCs w:val="16"/>'
           f'<w:color w:val="{_INK3}"/></w:rPr>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:pPr>'
        f'<w:pBdr><w:top w:val="single" w:sz="4" w:space="6" w:color="{_RULE}"/></w:pBdr>'
        f'<w:jc w:val="center"/><w:rPr><w:sz w:val="16"/><w:color w:val="{_INK3}"/></w:rPr></w:pPr>'
        f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(tekst)} · pagina </w:t></w:r>'
        + fld("PAGE") +
        f'<w:r>{rpr}<w:t xml:space="preserve"> van </w:t></w:r>'
        + fld("NUMPAGES") +
        "</w:p></w:ftr>")


def _docx_pakket(body: str, footer_tekst: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{body}"
        '<w:sectPr><w:footerReference w:type="default" r:id="rId2"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" '
        'w:footer="708"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.footer+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/footer" Target="footer1.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("word/footer1.xml", _footer_xml(footer_tekst))
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


def markdown_naar_docx(result: dict, fase: str, variant_idx: int,
                       projectnaam: str, markdown: str) -> tuple[str, bytes]:
    """Nota-Markdown naar een opgemaakt Word-document.

    Retourneert (bestandsnaam, docx-bytes)."""
    fase = _check_fase(fase)
    if len(markdown) > 2_000_000:
        raise NotaError("Nota-inhoud is onwaarschijnlijk groot; geweigerd.")
    context = bouw_context(result, variant_idx, projectnaam)
    proj = context["project"]
    blocks = _md_blocks(markdown.split(FOUT_MARK)[0])

    # eerste h1 = documenttitel (op de titelpagina), rest is inhoud
    titel = NOTA_FASEN[fase]["titel"]
    inhoud = []
    for soort, data in blocks:
        if soort == "h1" and titel == NOTA_FASEN[fase]["titel"]:
            titel = data
        else:
            inhoud.append((soort, data))

    P = []
    # --- titelpagina ---
    P.append(_para("InfraEngine · Ontwikkelnota middenspanningstracé",
                   style="NotaKicker"))
    P.append(_para(FASE_NAMEN[fase] + f" ({fase})", style="NotaTitel"))
    P.append(_para(titel, style="NotaSubtitel"))
    P.append(_tabel([
        ["Project", proj["naam"]],
        ["Gemeente", proj["gemeente"] or "onbekend"],
        ["Variant", f"{context['variant']['naam']} — "
                    f"{context['variant']['lengte_m']:.0f} m"],
        ["Coördinatenstelsel", proj["coordinatenstelsel"]],
        ["Datum", proj["datum"]],
        ["Status", "Concept — AI-gegenereerd, toetsing vereist"],
    ], kopregel=False))
    P.append(_para("Automatisch opgesteld met AI op basis van het berekende "
                   "tracé en de registers van het InfraEngine-ontwerpplatform "
                   f"(model {MODEL}). Inhoudelijke en juridische toetsing "
                   "door de ontwerpverantwoordelijke is vereist.",
                   size=18, color=_INK3,
                   ppr_extra='<w:spacing w:before="4400"/>'))
    P.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # --- inhoud ---
    for soort, data in inhoud:
        if soort == "h1" or soort == "h2":
            P.append(_para(data, style="Kop1"))
        elif soort == "h3":
            P.append(_para(data, style="Kop2"))
        elif soort == "ul":
            for item in data:
                P.append(_para("–  " + item, style="Opsomming"))
        elif soort == "table":
            P.append(_tabel(data))
        else:
            P.append(_para(data))

    footer = f"{proj['naam']} · {FASE_NAMEN[fase]} ({fase}) · concept {proj['datum']}"
    docx = _docx_pakket("".join(P), footer)
    slug = (projectnaam or "infraengine").strip().replace(" ", "_")[:40] or "infraengine"
    naam = f"{fase}-nota_{slug}_{date.today().strftime('%Y%m%d')}.docx"
    return naam, docx


def genereer_nota(result: dict, fase: str, variant_idx: int,
                  projectnaam: str) -> tuple[str, bytes]:
    """Volledige flow zonder streaming: nota genereren → Word-document."""
    fase = _check_fase(fase)
    md = "".join(stream_nota(result, fase, variant_idx, projectnaam))
    if FOUT_MARK.strip() in md:
        raise NotaError(md.split(FOUT_MARK)[-1].strip())
    return markdown_naar_docx(result, fase, variant_idx, projectnaam, md)
