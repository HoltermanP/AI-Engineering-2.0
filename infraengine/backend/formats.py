"""Documentformats: per AI-document een eigen sjabloon of voorbeeld.

De gebruiker kan per documentsoort (ontwikkelnota VO/DO/UO, elk
bureauonderzoek, elk procesdocument en het intakeverslag) een format
uploaden: een leeg sjabloon of een ingevuld voorbeeld (PDF, Word, tekst of
Markdown). Is er een format, dan wordt het betreffende document in die
opbouw opgesteld (hoofdstukken, paragrafen, volgorde, tabellen); is er
geen format, dan bepaalt de AI de opbouw zelf via de vaste instructies in
nota.py, bureau.py en proces.py.

Opslag: ``data/formats/<sleutel>/document.<ext>`` + ``meta.json``, één
format per documentsoort (een nieuwe upload vervangt de vorige).

Aanroep vanuit de generatoren::

    fm = formats.blokken("nota:VO")     # None of Anthropic-contentblokken
    if fm: messages = [{"role": "user", "content": fm + [{"type": "text", ...}]}]
"""
from __future__ import annotations

import base64
import html
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FM_DIR = ROOT / "data" / "formats"

MAX_BESTAND_MB = 20
MAX_TEKST_TEKENS = 120_000
TOEGESTANE_EXTENSIES = {".pdf", ".docx", ".txt", ".md"}


class FormatError(Exception):
    pass


# ---------------------------------------------------------------------------
# Catalogus: welke documenten een format kunnen krijgen
# ---------------------------------------------------------------------------

def _slug(tekst: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", tekst.lower()).strip("-")[:60]


def bureau_sleutel(soort: str) -> str | None:
    """Formatsleutel voor een onderzoekssoort uit het register (op profiel)."""
    import bureau
    p = bureau.profiel_voor(soort)
    if not p or not p.get("uitvoerbaar"):
        return None
    return "bureau:" + _slug(p["match"])


def catalogus() -> list[dict]:
    """Groepen met documentsoorten: [{groep, items: [{key, titel, hint}]}]."""
    import bureau
    import nota
    import proces
    groepen = [
        {"groep": "Ontwikkelnota's",
         "items": [{"key": f"nota:{fase}", "titel": spec["titel"],
                    "hint": "vaste ontwikkelnota-indeling van het bouwteam"}
                   for fase, spec in nota.NOTA_FASEN.items()]},
        {"groep": "Bureauonderzoeken",
         "items": [{"key": "bureau:" + _slug(p["match"]), "titel": p["match"],
                    "hint": "samenvatting, aanleiding, werkwijze, bevindingen, advies, status"}
                   for p in bureau.PROFIELEN if p.get("uitvoerbaar")]},
        {"groep": "Procesdocumenten",
         "items": [{"key": "proces:intake", "titel": "Intakeverslag (IV-02)",
                    "hint": "samenvatting, scope, budget/planning, randvoorwaarden, vragen, advies"}]
                  + [{"key": f"proces:{k}", "titel": spec["titel"],
                      "hint": "samenvatting en genummerde hoofdstukken"}
                     for k, spec in proces.AI_DOC.items()]},
    ]
    return groepen


def _titels() -> dict:
    return {it["key"]: it["titel"] for g in catalogus() for it in g["items"]}


def _map(key: str) -> Path:
    if not re.fullmatch(r"[a-z]+:[A-Za-z0-9_-]+", key or ""):
        raise FormatError(f"Ongeldige formatsleutel '{key}'.")
    return FM_DIR / key.replace(":", "__")


# ---------------------------------------------------------------------------
# Opslag
# ---------------------------------------------------------------------------

def _laad_meta(key: str) -> dict | None:
    pad = _map(key) / "meta.json"
    if not pad.exists():
        return None
    return json.loads(pad.read_text(encoding="utf-8"))


def lijst() -> dict:
    """{key: meta} van alle geüploade formats."""
    if not FM_DIR.exists():
        return {}
    uit = {}
    for d in FM_DIR.iterdir():
        if (d / "meta.json").exists():
            try:
                m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                uit[m["key"]] = m
            except Exception:
                continue
    return uit


def bewaar(key: str, bestandsnaam: str, inhoud: bytes) -> dict:
    """Format voor één documentsoort opslaan; vervangt een eerder format."""
    titels = _titels()
    if key not in titels:
        raise FormatError(f"Onbekende documentsoort '{key}'.")
    ext = Path(bestandsnaam).suffix.lower()
    if ext not in TOEGESTANE_EXTENSIES:
        raise FormatError("Alleen .pdf, .docx, .txt of .md wordt ondersteund.")
    if len(inhoud) > MAX_BESTAND_MB * 1024 * 1024:
        raise FormatError(f"Bestand groter dan {MAX_BESTAND_MB} MB.")
    map_ = _map(key)
    if map_.exists():
        shutil.rmtree(map_)
    map_.mkdir(parents=True, exist_ok=True)
    bestand = map_ / f"document{ext}"
    bestand.write_bytes(inhoud)
    # leesbaarheid direct toetsen, zodat een leeg/onleesbaar bestand niet
    # stilzwijgend als format blijft staan
    if ext != ".pdf":
        tekst = _tekst(bestand)
        if not tekst.strip():
            shutil.rmtree(map_)
            raise FormatError("Geen leesbare tekst in het document gevonden.")
        tekens = len(tekst)
    else:
        tekens = None
    meta = {
        "key": key,
        "titel": titels[key],
        "bestandsnaam": bestandsnaam,
        "geupload": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tekens": tekens,
    }
    (map_ / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return meta


def verwijder(key: str) -> None:
    map_ = _map(key)
    if not (map_ / "meta.json").exists():
        raise FormatError(f"Geen format voor '{key}'.")
    shutil.rmtree(map_)


def overzicht() -> dict:
    """Catalogus met per documentsoort het geüploade format (of None)."""
    formats = lijst()
    groepen = catalogus()
    for g in groepen:
        for it in g["items"]:
            it["format"] = formats.get(it["key"])
    return {"groepen": groepen, "aantal": len(formats)}


# ---------------------------------------------------------------------------
# Tekstextractie en promptblokken
# ---------------------------------------------------------------------------

def _docx_tekst(pad: Path) -> str:
    with zipfile.ZipFile(pad) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tc>", "\t", xml)  # tabelcellen gescheiden houden
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _tekst(bestand: Path) -> str:
    if bestand.suffix == ".docx":
        tekst = _docx_tekst(bestand)
    else:
        tekst = bestand.read_text(encoding="utf-8", errors="ignore")
    return tekst[:MAX_TEKST_TEKENS]


FORMAT_INSTRUCTIE = (
    "FORMAT — de organisatie heeft voor dit document een eigen format "
    "aangeleverd (hierboven/bijgevoegd): een leeg sjabloon of een ingevuld "
    "voorbeeld van een eerder document. Stel het document op in exact deze "
    "opbouw: dezelfde onderdelen, hoofdstukken en paragrafen, in dezelfde "
    "volgorde en met dezelfde kopnummering en titels; dezelfde tabellen met "
    "dezelfde kolommen; dezelfde vaste tekstblokken (bijv. leeswijzer, "
    "verificatie- of versietabel) waar het format die heeft. Is het format "
    "een ingevuld voorbeeld, neem dan alleen de opbouw, de toon en het "
    "detailniveau over — géén inhoud, namen, getallen, locaties of "
    "conclusies uit het voorbeeld: alles inhoudelijks komt uitsluitend uit "
    "de projectdata. Voor een onderdeel van het format waarvoor geen "
    "projectdata bestaat laat je de kop staan met een invulplek "
    "[IN TE VULLEN: …]. Voeg geen hoofdstukken toe die niet in het format "
    "staan. Dit format gaat vóór een eventuele standaardindeling die elders "
    "in deze opdracht of de systeeminstructie wordt genoemd; de opmaakregels "
    "(begrensde Markdown, tabellen met kopregel, geen HTML) blijven gelden."
)


def blokken(key: str) -> list[dict] | None:
    """Anthropic-contentblokken met het format voor deze documentsoort, of
    None als er geen format is geüpload (de AI bepaalt dan de opbouw)."""
    meta = _laad_meta(key)
    if meta is None:
        return None
    map_ = _map(key)
    bestand = next((p for p in map_.iterdir() if p.stem == "document"), None)
    if bestand is None:
        return None
    if bestand.suffix == ".pdf":
        return [{"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(bestand.read_bytes()).decode()}},
                {"type": "text", "text": FORMAT_INSTRUCTIE}]
    tekst = _tekst(bestand)
    if not tekst.strip():
        return None
    return [{"type": "text",
             "text": f"FORMATDOCUMENT ({meta['bestandsnaam']}):\n\n{tekst}\n\n"
                     + FORMAT_INSTRUCTIE}]


def heeft_format(key: str) -> bool:
    return _laad_meta(key) is not None
