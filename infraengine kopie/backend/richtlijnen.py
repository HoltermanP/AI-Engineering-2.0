"""Beheer van tracérichtlijnen: upload, AI-interpretatie en doorwerking.

In het beheerscherm (⚖ Richtlijnen in de kopbalk) uploadt de beheerder
richtlijndocumenten (PDF, Word, tekst of Markdown), bijvoorbeeld het eigen
ontwerphandboek, AVOI-voorschriften of een waterschapskeur. Claude leest het
document en vertaalt de voorschriften naar de instelbare rekenparameters van
de tracébepaling, met per parameter een motivering en het citaat uit de
richtlijn waarop de waarde is gebaseerd:

  - het wegingsprofiel (``engine.DEFAULT_WEIGHTS``): voorkeursligging,
    zonelagen, kruisingsprijzen;
  - de kruisingsbeslistabel (``WATER_OPEN_MAX_M``, ``RIJBAAN_OPEN_MAX_M``
    e.d.): tot welke breedte open ontgraving de standaard is;
  - kruisingsherkenning (``KRUISING_MIN_DWARS_M``, ``KRUISING_LANGS_FACTOR``);
  - boor-uitloop per techniek en de werkterrein-eisen.

Alleen parameters die de richtlijn daadwerkelijk onderbouwt worden gezet;
de rest houdt de standaardwaarde. Actieve richtlijnen gelden als nieuwe
standaard bij elke berekening (``pas_toe`` wordt aangeroepen aan het begin
van ``/api/compute``); expliciete aanpassingen in het wegingsprofiel-paneel
van de gebruiker gaan vóór de richtlijn. Bij meerdere actieve richtlijnen
wint de laatst geüploade per parameter.

Documenten en interpretaties staan persistent in ``data/richtlijnen/<id>/``
(origineel bestand + ``meta.json``). Vereist voor de interpretatie een
Anthropic API-key (``ANTHROPIC_API_KEY``, omgeving of ``infraengine/.env``);
zonder key blijft de upload bewaard en kan de interpretatie later opnieuw
worden gestart.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import engine

ROOT = Path(__file__).resolve().parent.parent

try:  # API-key uit infraengine/.env laden als die er is
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

RL_DIR = ROOT / "data" / "richtlijnen"

MODEL = "claude-opus-4-8"
MAX_TOKENS = 8000
MAX_BESTAND_MB = 20
MAX_TEKST_TEKENS = 150_000

TOEGESTANE_EXTENSIES = {".pdf", ".docx", ".txt", ".md"}


class RichtlijnError(Exception):
    pass


# ---------------------------------------------------------------------------
# Parametercatalogus: alles wat een richtlijn mag instellen. ``pad`` bepaalt
# waar de waarde landt: ("weights", sleutel) in het wegingsprofiel,
# ("engine", attribuut) als drempel in engine.py, ("boor", techniek) in
# BOOR_UITLOOP, ("werk", techniek, veld) in WERKTERREIN_EIS.
# ---------------------------------------------------------------------------

def _w(sleutel: str, omschrijving: str, lo=0.0, hi=200.0) -> dict:
    return {"pad": ("weights", sleutel), "omschrijving": omschrijving,
            "eenheid": "kosten/m (relatief)", "min": lo, "max": hi}


def _e(attr: str, omschrijving: str, eenheid: str, lo: float, hi: float) -> dict:
    return {"pad": ("engine", attr), "omschrijving": omschrijving,
            "eenheid": eenheid, "min": lo, "max": hi}


CATALOGUS: dict[str, dict] = {
    # --- wegingsprofiel: basiskosten per meter naar ligging -----------------
    "berm_groen": _w("berm_groen", "ligging in berm of groenstrook (voorkeursligging)"),
    "voetpad": _w("voetpad", "ligging onder voetpad/trottoir"),
    "fietspad": _w("fietspad", "ligging onder fietspad"),
    "parkeervlak": _w("parkeervlak", "ligging onder parkeervlak"),
    "rijbaan": _w("rijbaan", "ligging in de rijbaan (lengterichting)"),
    "erf_prive": _w("erf_prive", "ligging op privaat terrein (erf/agrarisch, ZRO nodig)"),
    "overig_onverhard": _w("overig_onverhard", "ligging in overig onverhard terrein"),
    "onbekend": _w("onbekend", "ligging op terrein zonder BGT-classificatie"),
    "natuur_groen": _w("natuur_groen", "ligging in bos/natuurlijk terrein"),
    # --- wegingsprofiel: vermenigvuldigers ---------------------------------
    "gesloten_verharding": _w("gesloten_verharding",
                              "vermenigvuldiger gesloten verharding (asfalt)", 0.1, 20),
    "natura2000_weg": _w("natura2000_weg",
                         "vermenigvuldiger binnen Natura 2000 via bestaande weg/berm", 0.1, 20),
    "nnn": _w("nnn", "vermenigvuldiger Natuurnetwerk Nederland", 0.1, 20),
    "grondwaterbescherming": _w("grondwaterbescherming",
                                "vermenigvuldiger grondwaterbeschermingsgebied", 0.1, 20),
    "bodem_verontreinigd": _w("bodem_verontreinigd",
                              "vermenigvuldiger vastgestelde bodemverontreiniging/nazorg", 0.1, 20),
    "bodem_verdacht": _w("bodem_verdacht",
                         "vermenigvuldiger bodemonderzoekslocatie (verdacht)", 0.1, 20),
    "archeologie": _w("archeologie", "vermenigvuldiger archeologisch monument (AMK)", 0.1, 20),
    "boom_wortelzone": _w("boom_wortelzone", "vermenigvuldiger wortelzone bomen", 0.1, 20),
    # --- wegingsprofiel: kruisingsprijzen ----------------------------------
    "water_kruising": _w("water_kruising",
                         "celprijs per meter voor het kruisen van een watergang "
                         "(hoger = sterker omlopen)", 1, 500),
    "spoor_kruising": _w("spoor_kruising",
                         "celprijs per meter voor het kruisen van spoor", 1, 1000),
    # --- kruisingsbeslistabel (open sleuf is de standaard; sleufloos alleen
    #     waar de beheerder open ontgraving niet toestaat) -------------------
    "water_open_max_m": _e("WATER_OPEN_MAX_M",
                           "watergang tot deze breedte: open ontgraving met afdamming "
                           "toegestaan", "m", 0, 30),
    "water_persing_max_m": _e("WATER_PERSING_MAX_M",
                              "watergang tot deze breedte: persing/nanodrill volstaat; "
                              "daarboven HDD", "m", 0, 60),
    "rijbaan_open_max_m": _e("RIJBAAN_OPEN_MAX_M",
                             "rijbaan tot deze breedte: open sleuf in halve rijbaan "
                             "(AVOI) toegestaan", "m", 0, 30),
    "rijbaan_persing_max_m": _e("RIJBAAN_PERSING_MAX_M",
                                "rijbaan tot deze breedte: persing volstaat; daarboven "
                                "HDD", "m", 0, 60),
    # --- kruisingsherkenning ------------------------------------------------
    "kruising_min_dwars_m": _e("KRUISING_MIN_DWARS_M",
                               "minimale haakse breedte om als kruising te tellen",
                               "m", 0.1, 10),
    "kruising_langs_factor": _e("KRUISING_LANGS_FACTOR",
                                "boven kruislengte > factor × breedte geldt het als "
                                "langsligging, geen kruising", "×", 1, 20),
    "raket_max_boorlengte_m": _e("RAKET_MAX_BOORLENGTE_M",
                                 "maximale boorlengte voor ongestuurde raketboring",
                                 "m", 5, 60),
    "boom_wortelzone_m": _e("BOOM_WORTELZONE_M",
                            "minimale wortelzone-straal rond een boom", "m", 0.5, 15),
    # --- boor-uitloop per techniek (intredehoek/opstelling) ------------------
    "boor_uitloop_hdd_m": {"pad": ("boor", engine.TECHNIEK_HDD),
                           "omschrijving": "uitloop vóór intrede/na uittrede bij HDD",
                           "eenheid": "m", "min": 0, "max": 60},
    "boor_uitloop_nanodrill_m": {"pad": ("boor", engine.TECHNIEK_NANO),
                                 "omschrijving": "uitloop bij nanodrill/mini-HDD",
                                 "eenheid": "m", "min": 0, "max": 30},
    "boor_uitloop_persing_m": {"pad": ("boor", engine.TECHNIEK_PERSING),
                               "omschrijving": "uitloop (kuip) bij persing",
                               "eenheid": "m", "min": 0, "max": 20},
    "boor_uitloop_raket_m": {"pad": ("boor", engine.TECHNIEK_RAKET),
                             "omschrijving": "uitloop (kuip) bij raketboring",
                             "eenheid": "m", "min": 0, "max": 20},
    # --- werkterrein-eisen per techniek -------------------------------------
    "werkterrein_hdd_intrede_m2": {"pad": ("werk", engine.TECHNIEK_HDD, "intrede_m2"),
                                   "omschrijving": "werkterrein intredezijde HDD "
                                   "(boorstelling)", "eenheid": "m²", "min": 10, "max": 2000},
    "werkterrein_hdd_uittrede_m2": {"pad": ("werk", engine.TECHNIEK_HDD, "uittrede_m2"),
                                    "omschrijving": "werkterrein uittredezijde HDD",
                                    "eenheid": "m²", "min": 5, "max": 1000},
    "werkterrein_persing_intrede_m2": {"pad": ("werk", engine.TECHNIEK_PERSING, "intrede_m2"),
                                       "omschrijving": "werkterrein intredezijde persing "
                                       "(perskuip)", "eenheid": "m²", "min": 5, "max": 500},
    "werkterrein_persing_uittrede_m2": {"pad": ("werk", engine.TECHNIEK_PERSING, "uittrede_m2"),
                                        "omschrijving": "werkterrein uittredezijde persing "
                                        "(ontvangstkuip)", "eenheid": "m²", "min": 5, "max": 500},
    "werkterrein_nanodrill_intrede_m2": {"pad": ("werk", engine.TECHNIEK_NANO, "intrede_m2"),
                                         "omschrijving": "werkterrein intredezijde nanodrill",
                                         "eenheid": "m²", "min": 5, "max": 500},
    "werkterrein_nanodrill_uittrede_m2": {"pad": ("werk", engine.TECHNIEK_NANO, "uittrede_m2"),
                                          "omschrijving": "werkterrein uittredezijde nanodrill",
                                          "eenheid": "m²", "min": 5, "max": 500},
    "werkterrein_raket_intrede_m2": {"pad": ("werk", engine.TECHNIEK_RAKET, "intrede_m2"),
                                     "omschrijving": "werkterrein intredezijde raketboring",
                                     "eenheid": "m²", "min": 2, "max": 200},
    "werkterrein_raket_uittrede_m2": {"pad": ("werk", engine.TECHNIEK_RAKET, "uittrede_m2"),
                                      "omschrijving": "werkterrein uittredezijde raketboring",
                                      "eenheid": "m²", "min": 2, "max": 200},
}


def _standaard(pad: tuple) -> float:
    """Fabrieksstandaard van een catalogusparameter (vóór richtlijn-overrides)."""
    doel = pad[0]
    if doel == "weights":
        return engine.DEFAULT_WEIGHTS[pad[1]]
    if doel == "engine":
        return _ENGINE_DEFAULTS[pad[1]]
    if doel == "boor":
        return _BOOR_DEFAULTS[pad[1]]
    return _WERK_DEFAULTS[pad[1]][pad[2]]


# Fabrieksinstellingen vastleggen bij import, zodat elke berekening vanaf een
# schone lei begint (pas_toe reset eerst en past daarna de overrides toe).
_ENGINE_ATTRS = ("WATER_OPEN_MAX_M", "WATER_PERSING_MAX_M", "RIJBAAN_OPEN_MAX_M",
                 "RIJBAAN_PERSING_MAX_M", "KRUISING_MIN_DWARS_M",
                 "KRUISING_LANGS_FACTOR", "RAKET_MAX_BOORLENGTE_M",
                 "BOOM_WORTELZONE_M")
_ENGINE_DEFAULTS = {a: getattr(engine, a) for a in _ENGINE_ATTRS}
_BOOR_DEFAULTS = dict(engine.BOOR_UITLOOP)
_WERK_DEFAULTS = {t: dict(v) for t, v in engine.WERKTERREIN_EIS.items()}


# ---------------------------------------------------------------------------
# Opslag: data/richtlijnen/<id>/ met origineel bestand + meta.json
# ---------------------------------------------------------------------------

def _meta_pad(rid: str) -> Path:
    return RL_DIR / rid / "meta.json"


def _laad_meta(rid: str) -> dict:
    pad = _meta_pad(rid)
    if not pad.exists():
        raise RichtlijnError(f"Richtlijn '{rid}' bestaat niet.")
    return json.loads(pad.read_text(encoding="utf-8"))


def _schrijf_meta(meta: dict) -> None:
    pad = _meta_pad(meta["id"])
    pad.parent.mkdir(parents=True, exist_ok=True)
    pad.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def lijst() -> list[dict]:
    """Alle richtlijnen, oudste eerst (uploadvolgorde = toepassingsvolgorde)."""
    if not RL_DIR.exists():
        return []
    metas = []
    for d in RL_DIR.iterdir():
        if (d / "meta.json").exists():
            try:
                metas.append(_laad_meta(d.name))
            except Exception:
                continue
    return sorted(metas, key=lambda m: m["geupload"])


def bewaar(bestandsnaam: str, inhoud: bytes) -> dict:
    """Nieuw richtlijndocument opslaan; interpretatie volgt apart."""
    ext = Path(bestandsnaam).suffix.lower()
    if ext not in TOEGESTANE_EXTENSIES:
        raise RichtlijnError("Alleen .pdf, .docx, .txt of .md wordt ondersteund.")
    if len(inhoud) > MAX_BESTAND_MB * 1024 * 1024:
        raise RichtlijnError(f"Bestand groter dan {MAX_BESTAND_MB} MB.")
    basis = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(bestandsnaam).stem).strip("_") or "richtlijn"
    rid = f"{time.strftime('%Y%m%d-%H%M%S')}-{basis}"[:80]
    map_ = RL_DIR / rid
    map_.mkdir(parents=True, exist_ok=True)
    (map_ / f"document{ext}").write_bytes(inhoud)
    meta = {
        "id": rid,
        "naam": Path(bestandsnaam).stem,
        "bestandsnaam": bestandsnaam,
        "geupload": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "actief": True,
        "interpretatie": {"status": "nog_niet"},
    }
    _schrijf_meta(meta)
    return meta


def verwijder(rid: str) -> None:
    _laad_meta(rid)  # bestaat-check
    shutil.rmtree(RL_DIR / rid)


def zet_actief(rid: str, actief: bool) -> dict:
    meta = _laad_meta(rid)
    meta["actief"] = bool(actief)
    _schrijf_meta(meta)
    return meta


# ---------------------------------------------------------------------------
# Tekstextractie (docx/txt/md); PDF gaat als document rechtstreeks naar Claude
# ---------------------------------------------------------------------------

def _docx_tekst(pad: Path) -> str:
    with zipfile.ZipFile(pad) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _document_blokken(rid: str) -> list[dict]:
    """Anthropic-contentblokken voor het brondocument van een richtlijn."""
    map_ = RL_DIR / rid
    bestand = next((p for p in map_.iterdir() if p.stem == "document"), None)
    if bestand is None:
        raise RichtlijnError("Brondocument ontbreekt op schijf.")
    if bestand.suffix == ".pdf":
        return [{"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(bestand.read_bytes()).decode()}}]
    tekst = (_docx_tekst(bestand) if bestand.suffix == ".docx"
             else bestand.read_text(encoding="utf-8", errors="ignore"))
    tekst = tekst[:MAX_TEKST_TEKENS]
    if not tekst.strip():
        raise RichtlijnError("Geen leesbare tekst in het document gevonden.")
    return [{"type": "text", "text": f"RICHTLIJNDOCUMENT:\n\n{tekst}"}]


# ---------------------------------------------------------------------------
# AI-interpretatie: richtlijn → parameterwaarden met motivering en citaat
# ---------------------------------------------------------------------------

_SYSTEEM = """Je bent ontwerpassistent voor middenspanningskabeltracés in Nederland.
Je krijgt een richtlijndocument (bijv. ontwerphandboek netbeheerder, AVOI/
verordening, waterschapskeur, ProRail-voorschrift). Vertaal de voorschriften
die over tracékeuze en kruisingstechniek gaan naar de rekenparameters van de
tracébepalings-engine.

Uitgangspunt van de engine (verander dit niet, tenzij de richtlijn expliciet
anders voorschrijft): open ontgraving is de standaard; sleufloze technieken
alleen waar dat verplicht of onvermijdelijk is.

Regels:
- Zet ALLEEN parameters waarvoor het document een concreet voorschrift of
  duidelijke voorkeur bevat. Geen voorschrift = parameter weglaten.
- Neem getallen letterlijk over waar het document maten noemt (bijv.
  "watergangen breder dan 5 m sleufloos kruisen" → water_open_max_m = 5).
- Kwalitatieve voorkeuren vertaal je proportioneel in het wegingsprofiel
  (bijv. "ligging in de rijbaan vermijden" → rijbaan duidelijk hoger dan de
  standaard; "voorkeursligging in de berm" → berm_groen iets lager).
- Elke gezette parameter krijgt een korte motivering én een letterlijk citaat
  (max ± 25 woorden) uit het document.
- Voorschriften die je niet op een parameter kunt afbeelden zet je in
  "niet_gemapt" (korte omschrijving per stuk), zodat de beheerder ze ziet.

Antwoord UITSLUITEND met geldige JSON, zonder toelichting eromheen:
{
 "samenvatting": "1-3 zinnen: wat regelt dit document en voor wie",
 "parameters": [
   {"sleutel": "<catalogussleutel>", "waarde": <getal>,
    "motivering": "<waarom deze waarde>", "citaat": "<letterlijk citaat>"}
 ],
 "niet_gemapt": ["<voorschrift dat niet in een parameter past>", ...]
}"""


def _catalogus_tekst() -> str:
    regels = []
    for sleutel, c in CATALOGUS.items():
        regels.append(f"- {sleutel}: {c['omschrijving']} "
                      f"[eenheid: {c['eenheid']}, standaard: {_standaard(c['pad']):g}, "
                      f"bereik: {c['min']:g}–{c['max']:g}]")
    return "\n".join(regels)


def _parse_json(tekst: str) -> dict:
    m = re.search(r"\{.*\}", tekst, re.DOTALL)
    if not m:
        raise RichtlijnError("Het model gaf geen JSON terug.")
    return json.loads(m.group(0))


def interpreteer(rid: str) -> dict:
    """Laat Claude de richtlijn vertalen naar parameters; slaat het resultaat op."""
    meta = _laad_meta(rid)
    try:
        import anthropic
    except ImportError:
        raise RichtlijnError("Python-pakket 'anthropic' is niet geïnstalleerd "
                             "(./.venv/bin/pip install anthropic).")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RichtlijnError("Geen Anthropic API-key gevonden. Zet ANTHROPIC_API_KEY "
                             "in de omgeving of in infraengine/.env en interpreteer opnieuw.")

    blokken = _document_blokken(rid)
    blokken.append({"type": "text", "text":
                    "Beschikbare parameters (alleen deze sleutels zijn geldig):\n"
                    + _catalogus_tekst()})
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=_SYSTEEM,
            messages=[{"role": "user", "content": blokken}])
        antwoord = "".join(b.text for b in msg.content if b.type == "text")
        data = _parse_json(antwoord)
    except anthropic.AuthenticationError:
        raise RichtlijnError("Anthropic API-key is ongeldig.")
    except anthropic.APIStatusError as e:
        raise RichtlijnError(f"Anthropic API-fout ({e.status_code}).")
    except anthropic.APIConnectionError:
        raise RichtlijnError("Geen verbinding met de Anthropic API.")
    except (json.JSONDecodeError, RichtlijnError):
        raise RichtlijnError("Antwoord van het model was geen bruikbare JSON; "
                             "probeer het opnieuw.")

    parameters, genegeerd = [], []
    for p in data.get("parameters", []):
        sleutel = p.get("sleutel")
        cat = CATALOGUS.get(sleutel)
        try:
            waarde = float(p.get("waarde"))
        except (TypeError, ValueError):
            cat = None
        if cat is None:
            genegeerd.append(str(sleutel))
            continue
        parameters.append({
            "sleutel": sleutel,
            "waarde": round(min(max(waarde, cat["min"]), cat["max"]), 3),
            "standaard": _standaard(cat["pad"]),
            "eenheid": cat["eenheid"],
            "motivering": str(p.get("motivering", ""))[:400],
            "citaat": str(p.get("citaat", ""))[:400],
        })

    meta["interpretatie"] = {
        "status": "ok",
        "model": MODEL,
        "datum": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "samenvatting": str(data.get("samenvatting", ""))[:1200],
        "parameters": parameters,
        "niet_gemapt": [str(x)[:300] for x in data.get("niet_gemapt", [])][:30],
        "genegeerd": genegeerd[:30],
    }
    _schrijf_meta(meta)
    return meta


def markeer_fout(rid: str, fout: str) -> dict:
    meta = _laad_meta(rid)
    meta["interpretatie"] = {"status": "fout", "fout": fout,
                             "datum": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _schrijf_meta(meta)
    return meta


# ---------------------------------------------------------------------------
# Doorwerking: actieve richtlijnen → overrides → engine/wegingsprofiel
# ---------------------------------------------------------------------------

def actieve_overrides() -> dict:
    """Parameters van alle actieve, geïnterpreteerde richtlijnen samengevoegd.

    Uploadvolgorde bepaalt de voorrang: de laatst geüploade richtlijn wint per
    parameter. ``bronnen`` noemt per sleutel de winnende richtlijn.
    """
    ov = {"weights": {}, "engine": {}, "boor": {}, "werk": {},
          "bronnen": {}, "actief": []}
    for meta in lijst():
        interp = meta.get("interpretatie", {})
        if not meta.get("actief") or interp.get("status") != "ok":
            continue
        n = 0
        for p in interp.get("parameters", []):
            cat = CATALOGUS.get(p["sleutel"])
            if cat is None:
                continue
            pad, waarde = cat["pad"], p["waarde"]
            if pad[0] == "weights":
                ov["weights"][pad[1]] = waarde
            elif pad[0] == "engine":
                ov["engine"][pad[1]] = waarde
            elif pad[0] == "boor":
                ov["boor"][pad[1]] = waarde
            else:
                ov["werk"].setdefault(pad[1], {})[pad[2]] = waarde
            ov["bronnen"][p["sleutel"]] = meta["naam"]
            n += 1
        if n:
            ov["actief"].append({"naam": meta["naam"], "parameters": n})
    return ov


def pas_toe() -> dict:
    """Engine terugzetten op fabrieksinstellingen en de actieve richtlijnen
    toepassen; aan te roepen aan het begin van elke berekening. De dicts
    (BOOR_UITLOOP, WERKTERREIN_EIS) worden in place gemuteerd omdat andere
    modules ze bij naam geïmporteerd hebben."""
    ov = actieve_overrides()
    for attr, waarde in _ENGINE_DEFAULTS.items():
        setattr(engine, attr, waarde)
    engine.BOOR_UITLOOP.clear()
    engine.BOOR_UITLOOP.update(_BOOR_DEFAULTS)
    engine.WERKTERREIN_EIS.clear()
    engine.WERKTERREIN_EIS.update({t: dict(v) for t, v in _WERK_DEFAULTS.items()})

    for attr, waarde in ov["engine"].items():
        setattr(engine, attr, waarde)
    for techniek, waarde in ov["boor"].items():
        engine.BOOR_UITLOOP[techniek] = waarde
    for techniek, velden in ov["werk"].items():
        engine.WERKTERREIN_EIS.setdefault(techniek, {}).update(velden)
    return ov


def overzicht() -> dict:
    """Alles wat het beheerscherm nodig heeft."""
    labels = {sleutel: {"omschrijving": c["omschrijving"], "eenheid": c["eenheid"],
                        "standaard": _standaard(c["pad"])}
              for sleutel, c in CATALOGUS.items()}
    return {"richtlijnen": lijst(), "overrides": actieve_overrides(),
            "catalogus": labels,
            "api_key": bool(os.getenv("ANTHROPIC_API_KEY"))}
