# InfraEngine — MS-tracéontwerp (fase 1-prototype)

Werkend prototype van fase 1 uit het functioneel ontwerp *InfraEngine* (versie 0.1,
11-09-2026): ontwerpplatform voor middenspanningstracés met PDOK-datalagen,
automatische tracébepaling, kruisingsherkenning en registers.

## Starten

```bash
cd infraengine
./start.sh            # of: ./.venv/bin/python -m uvicorn main:app --app-dir backend --port 8000
```

Open <http://127.0.0.1:8000>. Voor een direct gevuld voorbeeld (Amersfoort-
centrum, twee stations): <http://127.0.0.1:8000/#demo>.

Eerste installatie (eenmalig):

```bash
python3 -m venv .venv
./.venv/bin/pip install fastapi "uvicorn[standard]" shapely scikit-image pillow openpyxl requests anthropic python-dotenv
```

Voor de AI-ontwerpnota's (VO/DO/UO) is een Anthropic API-key nodig: zet
`ANTHROPIC_API_KEY=sk-ant-…` in de omgeving of in `infraengine/.env`.

## Deployen

Meegeleverd: `Dockerfile`, `docker-compose.yml` en `deploy/Caddyfile` voor
een kleine server (VPS) met Docker — het profiel dat bij deze app past:
bestandsopslag in `data/`, rekenverzoeken van minuten en één gelijktijdige
gebruiker. Serverless platforms (Vercel/Netlify) passen daar niet op;
een PaaS met persistente schijf (Fly.io, Railway, Render betaald) kan wel,
met dezelfde Dockerfile.

Op de server (met Docker en een (sub)domein dat naar de server wijst):

```bash
git clone <repo> && cd infraengine
echo 'ANTHROPIC_API_KEY=sk-ant-…' > .env       # + evt. GOOGLE_MAPS_API_KEY
# deploy/Caddyfile: domein invullen en wachtwoord-hash zetten:
docker run --rm caddy:2 caddy hash-password --plaintext 'jouw-wachtwoord'
docker compose up -d --build
```

Caddy haalt automatisch een HTTPS-certificaat en zet **basic-auth** voor de
hele app — laat die niet weg, want de app heeft zelf geen authenticatie en
de API kan bestanden schrijven en (via de nota's) API-tegoed verbruiken.
Aandachtspunten:

- **Eén uvicorn-worker** (staat zo in de Dockerfile): resultaat, voortgang
  en caches leven in het procesgeheugen. Niet opschalen naar meerdere
  workers of replica's zonder die staat eerst extern te maken.
- **`data/` is de volledige staat** — een bind-mount naast de code;
  back-uppen is een kwestie van die map kopiëren.
- De server heeft **uitgaand internet** nodig (PDOK, RCE, regionale
  services, api.anthropic.com) en voor lange tracés enkele GB's geheugen.

### Render

Meegeleverd: `render.yaml` op de **repo-root** (Render leest blueprints
alleen daar; via `rootDir` wijst hij naar deze map). Repo koppelen via
**New → Blueprint** in het Render-dashboard; bij de eerste deploy vraagt
Render om
`ANTHROPIC_API_KEY` en `BASIC_AUTH_WACHTWOORD`. Die laatste activeert de
ingebouwde basic-auth van de app (`main.py`) — op Render staat er geen
eigen reverse proxy voor, dus dit is daar de slagboom (gebruikersnaam
standaard `infraengine`, aanpasbaar met `BASIC_AUTH_GEBRUIKER`).

De blueprint kiest bewust: **Docker-runtime** (zelfde Dockerfile als de
VPS-opzet), regio **Frankfurt** (dicht bij PDOK), plan **standard** (2 GB —
de rasters passen niet in de 512 MB van starter), **één instance** met een
**persistente schijf** op `/app/data` (Render maakt daar dagelijks
snapshots van). Renders proxy staat lange verzoeken ruim toe (~100 min),
dus ook corridor-berekeningen lopen gewoon door. Kanttekening: met een
schijf deployt Render niet zero-downtime — bij elke deploy is de app even
onbereikbaar (en een lopende berekening breekt af).


## Wat het prototype doet (FO §9, fase 1)

1. **Verkennen** — kaartviewer in RD New (EPSG:28992) met PDOK-lagen:
   BRT-achtergrondkaart, luchtfoto, BGT-visualisatie, kadastrale kaart en
   de **BRO Bodemkaart** (bodemsamenstelling — zand, klei, veen, moerig —
   tot ± 1,2 m diepte, 1:50.000; klik in navigeermodus op de kaart voor de
   bodemeenheid op dat punt; bebouwd gebied is niet gekarteerd).
   Projectgebied tekenen (max ± 3 km²), MS-stations plaatsen (volgorde =
   streng), via-punten en verboden zones. Met **↷ Tracé verslepen** pak je
   een berekend tracé op en sleep je het naar de gewenste ligging: op de
   losgelaten plek komt een via-punt en het tracé wordt automatisch
   herberekend (ook het verslepen van een bestaand via-punt rekent direct
   opnieuw; via-punten zijn met ✕ Verwijderen weer weg te halen). Met de tool **🚶 Street View**
   (beschikbaar zodra een tracé is berekend) loop je door het tracé:
   klik op (of naast) het tracé en stap met de knoppen, de schuif of
   "Lopen" langs de route; de kaart toont positie en kijkrichting
   (kijkrichting = looprichting). Het beeld is standaard ingebed zonder
   API-sleutel (Googles embed-iframe). Optioneel: met een
   `GOOGLE_MAPS_API_KEY` in `infraengine/.env` (Maps JavaScript API) wordt
   het interactieve panorama gebruikt — lopen kan dan ook met de pijlen
   ín het beeld en de kaartmarker volgt mee. "↗ Google Maps" opent
   hetzelfde punt met kijkrichting in een nieuw tabblad.
2. **Tracé bepalen** — kostenoppervlak op een raster van 0,5 m (1,0 m boven
   1 km²) uit live opgehaalde BGT-data; kortste gewogen pad (Dijkstra/MCP,
   scikit-image); nabewerking: vereenvoudigen en kruisingen rechttrekken,
   beide gevalideerd tegen uitsluitingszones. Lange tracés rekenen per
   deeltraject in een corridor van ± 160 m; vindt een deeltraject daar geen
   doorgang (Natura 2000, aaneengesloten bebouwing, verboden zone), dan
   verbreedt de app de corridor **automatisch** stapsgewijs (± 320 → 640 →
   1280 m, `BREEDTE_ESCALATIE` in `main.py`) tot er een begaanbare route is;
   de verbrede deeltrajecten worden in de statusregel gemeld. Wegingsprofiel zichtbaar en
   instelbaar (FO §3.1). Drie varianten + voorkeursvariant met MCA-tabel
   (FO §3.4).
   - **Lange tracés (corridor-modus, tot 70 km)** — boven ± 2,5 km
     hemelsbreed rekent de app niet één raster over het hele gebied, maar
     per deeltraject van ± 1,5 km een corridor van ± 160 m rond de rechte
     lijn tussen de stations (via-punten en stations blijven harde
     tussenpunten). Datalagen worden per deeltraject opgehaald en vooruit
     geladen terwijl het vorige deeltraject rekent; het naadpunt tussen twee
     deeltrajecten mag ± 60 m verschuiven naar de goedkoopst bereikbare cel.
     Deelroutes, kruisingen, segmenten en zonemetrages worden aaneengehecht;
     moffen, registers, ZRO, toetsing, kosten en MCA draaien daarna op het
     volledige tracé. De frontend toont de voortgang (`GET /api/progress`).
     Kanttekening: de route kan niet buiten de corridor zoeken — stuur bij
     grote obstakels (bijvoorbeeld een meer of Natura 2000-gebied dwars op
     de lijn) met via-punten. Reken op grofweg één à anderhalve minuut per
     10 km zonder varianten; met drie varianten ruwweg het dubbele.
3. **Kruisingen** — herkenning van kruisingen met watergangen, rijbanen en
   spoor; techniekvoorstel uit de beslistabel (FO §4): HDD, persing, open
   sleuf, met richtlijn en bevoegd gezag. Alleen een **échte dwarsing** telt
   als kruising: schampt het tracé enkel een rand, loopt het in de
   lengterichting door of langs het obstakel, of raakt het alleen de
   oeverzone (BGT ondersteunend waterdeel), dan is het geen kruising en dus
   geen boring (filter instelbaar in `engine.py`: `KRUISING_MIN_DWARS_M`,
   `KRUISING_LANGS_FACTOR`). De breedte wordt **haaks op het obstakel**
   gemeten (hoek-onafhankelijk; een schuine oversteek maakt een 6 m-weg niet
   9 m breed), naast de kruislengte langs het tracé die de boorlengte
   bepaalt. De beslistabel is **kritisch**:
   open kruising is de standaard waar de beheerder die toestaat (watergang
   ≤ 3 m met afdamming, rijbaan ≤ 7 m in halve rijbaan onder AVOI);
   sleufloos alleen waar dat verplicht of onvermijdelijk is (spoor, brede
   watergang, brede rijbaan) en dan de lichtste techniek die past. Elke
   keuze draagt een `noodzaak`-motivering (zichtbaar in register, Excel en
   GeoJSON; drempels instelbaar in `engine.py`: `WATER_OPEN_MAX_M`,
   `WATER_PERSING_MAX_M`, `RIJBAAN_OPEN_MAX_M`, `RIJBAAN_PERSING_MAX_M`).
   Boringen worden **exact ingetekend van intrede- tot uittredepunt**: het
   tracé wordt over de volledige boorlengte rechtgetrokken, de uitloop per
   techniek is realistisch (`BOOR_UITLOOP`: HDD 10 m, nanodrill 5 m,
   persing 3 m, raket 2 m) en de boorlijn met in-/uittredemarkers staat op
   de kaart en in de GeoJSON-export. **Klik op een boorlijn** (of een
   in-/uittredemarker, of een open-kruisingspunt) in de kaart voor een
   popup met alle gegevens: techniek, noodzaak, boorlengte en uitloop,
   in-/uittredepunt in RD, dekking-eis, mantelbuis, rechtheid,
   werkterrein-oordeel, richtlijn en bevoegd gezag. Per boring een globale
   **werkterrein-toets**: aaneengesloten inzetbare ruimte rond het in- en
   uittredepunt (flood-fill op het BGT-klasseraster, Natura 2000 telt niet
   mee) tegen een instelbare ruimtebehoefte per techniek
   (`WERKTERREIN_EIS`). Ruim voldoende → akkoord; twijfel → "onzeker,
   afhankelijk van type boorstelling"; onvoldoende → automatisch het eerste
   passende sleufloze alternatief binnen de richtlijnen (HDD → nanodrill/
   mini-HDD → persing → raketboring, per obstakeltype begrensd: onder spoor
   alleen HDD/persing, ongestuurd alleen bij korte kruisingen; open kruising
   als laatste uitweg waar de beheerder dat toestaat). Past niets, dan een
   kritieke melding in de toetsing (maatwerk vereist).
4. **Toetsen** — basis-toets: uitsluitingen, ligging in rijbaan, privaat
   terrein (proxy), dekking-informatie, buigradius-knikken, boorplan-plicht,
   graafveiligheid (CROW 500/WIBON-melding).
5. **Registers** — automatisch gevuld uit het tracé (FO §6): vergunningen en
   meldingen (AVOI, waterschap, ProRail, verkeer, KLIC), boringen (met in-/
   uittredepunten in RD, dekking-eis, mantelbuisvoorstel), ZRO (gekruiste
   DKK-percelen met ingenomen lengte en werkstrook), kruisingen, moffen op
   haspellengte, indicatieve kostenraming én een **complete RAW-calculatie**
   (tabblad *Calculatie*): bestekposten volgens de RAW-systematiek per
   hoofdstuk (sloopwerk, bemalingen, grondwerken, leidingwerk, kabelwerk,
   verhardingen, groen, verkeersmaatregelen, stelposten) met postnummer,
   eenheid, hoeveelheid uit het tracé, fictieve maar realistische
   eenheidsprijzen (prijspeil 2026, excl. btw) en de inschrijvingsstaat:
   eenmalige kosten (91.88), uitvoeringskosten uit een bouwtijdraming
   (92.88), algemene kosten (93.88), winst en risico (94.88) en korting
   (95.88) tot de aannemingssom in- en exclusief btw. Prijzen, sleufprofiel
   en staartpercentages zijn instelbaar in `backend/calculatie.py`
   (`EENHEIDSPRIJZEN`, `STAART`); de MCA vergelijkt varianten op de
   aannemingssom. Export naar **GeoJSON** (RD) en
   **Excel** (alle registers als werkbladen, incl. het werkblad
   *RAW-calculatie* met de volledige begroting en staart). **Projectbeheer** in de
   kopbalk: **+ Nieuw** (leeg project), **Opslaan** (invoer én het berekende
   tracé met alle registers, als JSON in `data/`) en **openen** via de
   keuzelijst — een geopend project herstelt kaart, paneel, exports en
   nota's direct, zonder herberekening.
   - **Werkpakketten** — elk tracédeel van station tot station wordt
     automatisch een genummerd werkpakket (WP-01, WP-02, …, naam
     "Station 1 – Station 2", bewerkbaar). Alle registerregels (segmenten,
     kruisingen, boringen, moffen, ZRO, vergunningen, toetsing) krijgen het
     werkpakket waarin ze vallen en zijn er in de registerpagina op te
     sorteren en te filteren; regels zonder vaste plek op het tracé staan op
     "tracébreed". De kaart toont WP-labels halverwege elk deel; Excel en
     GeoJSON exporteren de werkpakketten mee.
   - **Planning per werkpakket** — indicatieve planning in weken (tabblad
     *Planning*, met balkjes): onderzoeken, vergunningen/werkvoorbereiding
     en ZRO lopen per werkpakket parallel vanaf week 1 (langste
     doorlooptijd bepaalt); de uitvoering gebeurt met één ploeg in
     strengvolgorde en start zodra de eigen voorbereiding én het vorige
     werkpakket klaar zijn. Status en toelichting per fase bewerkbaar;
     parameters (graaftempo, weken per boring, ZRO-doorlooptijd) instelbaar
     in `registers.py` (`PLANNING`).
6. **ZRO-dossiers** — klik in het ZRO-register op een perceel voor de
   detailpagina: workflow-status (contact → onderhandeling → akkoord →
   notaris → gevestigd), eigenaar en contactgegevens, aard van het recht,
   vergoeding, notaris en opmerkingen. Per dossier:
   - **Vergoedingsvoorstel volgens de richtlijn**: opbouw naar de
     systematiek van de landelijke afspraken LTO Nederland / gezamenlijke
     netbeheerders — eenmalige afkoop als percentage van de agrarische
     grondwaarde van de belaste strook, plus afsluit- en meewerkvergoeding
     (met ondergrens); bij huur/gebruik een jaarlijkse vergoeding; bij een
     gedoogplicht als indicatie van de wettelijke schadeloosstelling.
     Gewassen- en structuurschade blijven p.m. (werkelijke schade,
     artikel 4). Eén klik neemt het voorstel over in het dossier; de
     grondslag wordt vastgelegd en genoemd in artikel 2 van de
     overeenkomst. Startwaarden instelbaar in `zro.py`
     (`VERGOEDING_RICHTLIJN`).
   - **ZRO-tekening** automatisch genereren (PDF, A4-liggend): kadastrale
     kaart of luchtfoto als ondergrond (PDOK-WMS; offline terugval op de al
     opgehaalde perceelsgrenzen), tracé, werkstrook, belaste strook en
     gemarkeerd perceel, met titelblok, nette schaal, schaalbalk en
     noordpijl — opgeslagen als bijlage bij het dossier.
   - **ZRO-overeenkomst** (concept, bewerkbaar Word-document) opstellen
     zodra alle vestigingsvereisten zijn vervuld (eigenaar, verkrijger,
     aard recht, vergoeding, tekening); de dossierstatus springt dan naar
     "overeenkomst opgesteld".
   - Eigen bijlagen toevoegen (bijv. correspondentie of eigendomsbewijs).
   Dossiers zijn persistent per perceel (`data/zro/<perceel>/`) en de
   status loopt mee in het ZRO-register en de Excel-export.
7. **Ontwikkelnota's met AI (VO / DO / UO)** — knoppen in het Export-paneel.
   De backend bundelt alle beschikbare data van het berekende tracé
   (varianten + MCA, segmenten, kruisingen, boringen, vergunningen,
   onderzoeken, ZRO incl. dossierstatus, toetsing, moffen, kosten, het
   kans- en risicoregister uit het procesdossier én de bekende
   databeperkingen) en laat Claude (`claude-opus-4-8`) daar een
   fase-specifieke **ontwikkelnota in het vaste HVP/Liander-format** van
   schrijven (naar de aangeleverde UKZ DO-/UO-ontwikkelnota's; het
   VO-format is daarvan afgeleid): titelblok met verificatietabel,
   projectteam en versiebeheer, dan de vaste hoofdstukken 1 Inleiding
   (scope, werkgebied, werkpakkettentabel), 2 Gerelateerde documenten,
   3 Ontwerp (VO: variantenafweging + MCA en voorkeursvariant; DO:
   tracéverloop per verbinding met persingen/HDD-nummers en restpunten;
   UO: HDD-detailoverzicht en uitvoeringswijze per werkpakket; telkens
   incl. CAR-verzekeringstoets), 4 Omgeving (stakeholderstabellen per
   werkpakket, conditionerende onderzoeken, seizoensbeperkingen,
   vergunningen), 5 Randvoorwaarden (risico's RISMAN-geordend, eisen,
   mijlpalenplanning, V&G, raming, controle en review met de tollgate)
   en 6 Restpunten (tabel). Ontbrekende gegevens staan als gemarkeerde
   invulplek [IN TE VULLEN: …]. Hoofdstukindeling per fase instelbaar in
   `backend/nota.py` (`NOTA_FASEN`).
   - **Kaarten in de nota** — de AI plaatst `[AFBEELDING: …]`-markers op de
     voorgeschreven plekken; de app vervangt die door gerenderde kaarten
     (`backend/kaart.py`, PDOK-luchtfoto-ondergrond, RD New, schaalbalk en
     noordpijl): de **overzichtstekening** van het tracé met stations en
     WP-labels (§1.2/§3), het **variantenoverzicht** (alle varianten in
     kleur, §3.1) en in de VO-nota **Bijlage A** met per variant een eigen
     kaart plus toelichting. De kaarten staan in het Word-document
     (ingebed als JPEG) én in de live nota-preview
     (`GET /api/kaart/overzicht.jpg|varianten.jpg|variant.jpg`).
   - **Projectgegevens-invulscherm** (procespagina → 👥 Projectgegevens):
     namen voor de verificatietabel (opdrachtgever, opgesteld door,
     verificatie, autorisatie, vrijgave), het projectteam
     (naam/functie/organisatie) en de mijlpalen (VO/DO/UO gereed, contract,
     GSU, IBN). Opgeslagen per project (`data/proces/<project>.json`) en
     automatisch gebruikt in het titelblok en §5.3 van elke ontwikkelnota;
     alleen lege velden blijven [IN TE VULLEN]. De nota bouwt zich **live op in een
   voorbeeldvenster** (het model streamt begrensde Markdown die als nette
   HTML wordt gerenderd, `GET /api/nota/stream`); daarna is hij te
   downloaden als **opgemaakt Word-document** met titelpagina en
   documentgegevens, echte kopstijlen (zichtbaar in de Word-navigatie),
   rastertabellen voor de registeroverzichten en een voettekst met
   paginanummers (`POST /api/nota/docx`; in één keer zonder voorbeeld:
   `GET /api/export/nota`). Vereist `ANTHROPIC_API_KEY` (omgeving of
   `infraengine/.env`); fase-instructies, model en Word-stijlen in
   `backend/nota.py`.
8. **Procesondersteuning (◫ Proces in de kopbalk)** — het volledige proces
   van **intake (IV) → VO → DO → UO → overdracht naar realisatie**, met de
   tollgate-systematiek uit de aangeleverde procesdocumenten (Checklist VO
   GW-MS v1.5, WOL Checklist Tollgates T3/T4/T5, risico-overzichtstabel en
   de DO/UO-ontwikkelnota's). 130 processtappen per discipline
   (document-, scope-, technisch, omgevings-, financieel, plannings-,
   risico- en contractmanagement), elk met product/beheersdocument en
   review-markering.
   - **Uitvoering per stap instelbaar** (⚙ Admin op de procespagina): **AI**
     (voert uit en meldt gereed), **hybride** (AI maakt het concept, de mens
     keurt goed of af — met verplichte toelichting bij afkeuren) of **mens**
     (handmatig). Stappen, producten en hele fasen zijn aan/uit te zetten;
     de keuzes staan in `data/proces_config.json`.
   - **Triggers**: na elke tracéberekening (met projectnaam) werkt de app de
     data-gedreven stappen van de actieve fase automatisch bij (WBS,
     materiaallijst, raming, planning, stakeholders, vergunningen,
     onderzoeken, ZRO-stand, K&L derden, documentenregister) — uit te
     zetten met "AI-stappen automatisch uitvoeren" in de admin. De
     zijbalk toont **"Wacht op jou"** (goedkeuringen, handmatige stappen,
     tollgate-reviews) en een meldingenlog.
   - **AI-conceptdocumenten**: per stap stelt Claude een concept-
     beheersdocument op uit de projectdata (verificatieplan, VGM-O/VGM-U,
     bouw- en publiekscommunicatieplan, werkplannen civiel/warm werk,
     boorplannen per boring, bedienings-, keurings-, verkeers- en
     bemalingsplan, ecologisch werkprotocol, cultuurtechnisch advies,
     PRA/projectplan OOO, PvE archeologie, afwijkingen- en
     wijzigingenregister, oplegnotitie aanneming). Het document bouwt zich
     live op, wordt na "Opslaan" een opgemaakt **Word-artefact** bij de stap
     (`data/proces/<project>/`) en de stap springt naar "AI-concept —
     goedkeuren" (hybride) of "gereed" (AI). De ontwerpnota-stappen (VO/DO/
     UO) gebruiken de bestaande AI-ontwerpnota's.
   - **Intake vanuit het IV**: upload het investeringsvoorstel (pdf/docx/
     txt) bij stap IV-01; de AI leest het document en schrijft het
     **intakeverslag** (scope, stations, budget, randvoorwaarden, vragen,
     advies voor de eerste tracéverkenning).
   - **Tollgates (T1/TM2/T3/T4/T5)**: per fase een reviewtabel met alle
     ✔-producten (voldoet / voldoet niet + toelichting). Het
     tollgate-besluit is **altijd menselijk** (naam verplicht); zolang niet
     alle review-producten gereed zijn kan de gate alleen met een
     afwijkingsbesluit worden genomen. Een genomen gate activeert de
     volgende fase.
   - **Kans- en risicoregister** (tabblad Risico's): structuur naar de
     projectrisico-overzichtstabel — oorzaak/gevolg, aspect, allocatie
     OG/ON, kans × gevolgsom (geld/tijd/kwaliteit/veiligheid/omgeving) als
     score, beheersmaatregelen met soort en actiehouder. De AI genereert en
     actualiseert projectspecifieke risico's uit de registers (verwijst
     naar echte VRG-/OND-nummers, zones en boringen); rijen zijn inline te
     bewerken en te verwijderen. Export naar **Excel** (risico's +
     procesoverzicht met alle stapstatussen).
9. **Registerpagina** — knop **▤ Registers** in de kopbalk (of `#registers`
   in de URL) opent alle registers op een eigen pagina: tabel per register
   met sorteren (klik op de kolomkop), filteren per kolom, vrije zoektekst
   en inline bewerken (dubbelklik op een cel met ✎; statusvelden als
   keuzelijst). Rechts een meeschuivende kaart die naar de geselecteerde
   rij zoomt. Bewerkingen gaan naar `POST /api/register/update`
   (whitelist per register) en tellen mee in de Excel-export;
   ZRO-velden lopen via het dossier (`/api/zro/detail`), met een knop naar
   de volledige dossier-detailpagina. Na een herlaad haalt de pagina het
   laatste rekenresultaat terug via `GET /api/result`.

## Architectuur

```
infraengine/
├── backend/
│   ├── main.py        FastAPI: /api/compute, /api/export/*, /api/project/*
│   ├── pdok.py        PDOK-fetchers: BGT OGC API, DKK WFS, NWB, IMWA-legger/
│   │                  keringen, waterschapsnaam, RCE, CPT, NGE (met cache)
│   ├── ahn.py         AHN-WCS: hoogteprofielen langs boringen en tracé
│   ├── sonderingen.py BRO-uitgifteservice: CPT-kenmerken langs het tracé
│   ├── klic.py        KLIC-importkoppelvlak (data/klic/, voorbereid)
│   ├── brk.py         BRK-importkoppelvlak (data/brk/eigenaren.csv, voorbereid)
│   ├── engine.py      Kostenraster (PIL), routing (skimage MCP), nabewerking,
│   │                  kruisingsbeslistabel, segmentering, moffen
│   ├── registers.py   Vergunningen, boringen, ZRO, toetsing, kosten, MCA,
│   │                  variant-wegingsprofielen
│   ├── calculatie.py  RAW-calculatie: bestekposten, hoeveelheden en
│   │                  inschrijvingsstaat (fictieve, realistische prijzen)
│   ├── zro.py         ZRO-dossiers (status, eigenaar, recht, bijlagen),
│   │                  tekening-generator (PDF) en overeenkomst (.docx)
│   ├── nota.py        AI-ontwerpnota's VO/DO/UO (Claude API) → Word
│   └── proces.py      Procesondersteuning IV→VO→DO→UO→realisatie:
│                      stappencatalogus (tollgates TM2/T3/T4/T5), config
│                      (admin), triggers, AI-conceptdocumenten, intake-
│                      analyse, kans- en risicoregister
├── frontend/          OpenLayers 9 (CDN) + proj4, zonder buildstap
│                      (procespagina: proces.js + proces.css)
└── data/              Opgeslagen projecten (JSON), data/zro/ dossiers,
                       data/proces/ procesdossiers + AI-artefacten,
                       data/proces_config.json (admininstellingen)
```

Conform FO §8, met twee bewuste vereenvoudigingen voor het prototype:
frontend in plain JS in plaats van React (geen buildstap; zelfde
OpenLayers-kaartlaag), en projectopslag in JSON-bestanden in plaats van
PostGIS.

## Gebruikte databronnen (allemaal open)

| Laag | Bron | Gebruik |
|---|---|---|
| BGT (12 collecties) | PDOK OGC API Features | kostenoppervlak, kruisingen, segmentligging |
| DKK-percelen | PDOK WFS v5_0 | ZRO-register |
| Bestuurlijke gebieden | PDOK WFS | gemeentenaam bevoegd gezag |
| BRT / luchtfoto / BGT-visualisatie / DKK | PDOK WMTS+WMS | kaartbeelden |
| Natura 2000 | PDOK WFS (RVO) | harde uitsluiting buiten bestaande weg/berm; natuurvergunning; quickscan |
| Natuurnetwerk Nederland | PDOK WMS → rastermasker | strenge weging (×3); quickscan |
| Grondwaterbeschermingsgebieden | PDOK WMS (provincies) → rastermasker | weging; melding boorvloeistof/bemaling |
| BRO SLD: overheidsbesluit bodemverontreiniging/nazorg | PDOK WMS (TNO) → rastermasker | weging (×2,0); Bal-melding graven in verontreinigde bodem; saneringsplan-check |
| BRO SAD: milieuhygiënisch bodemonderzoek | PDOK WMS (TNO) → rastermasker | weging (×1,5); vooronderzoek NEN 5725 (CROW 400) |
| Bodemloket: Wbb-locaties (historisch) | GDN geoservices WMS → rastermasker | aanvulling op SAD zolang de BRO-migratie loopt (zelfde zone en weging) |
| Bodemloket: beschikbaarheid gegevens | GDN geoservices WMS → rastermasker | dekkingssignaal (geen kosteneffect): "raadpleeg eigen loket bevoegd gezag" |
| Regionale bodembronnen (register `BODEM_REGIONAAL`) | per bevoegd gezag (nu: Zuid-Holland — spoedlocaties, BSB-bedrijfsterreinen, stortplaatsen; Zaanstad/Nazca — verontreinigingen, activiteiten; Fryslân-bodematlas — saneringslocaties; Gelderland — stortplaatsen nazorg + gesloten stortplaatsen; Overijssel — stortplaatsen; Limburg — Wbb-overgangsrecht + voormalige stortplaatsen) | vullen de BRO/Bodemloket-gaten; welke bron geldt volgt automatisch uit het trace/projectgebied (`GET /api/regionale-bronnen`); spoed/verontreiniging telt als ×2,0, onderzoek als ×1,5; actieve bronnen worden per berekening gemeld |
| Archeologische Monumentenkaart 2014 | RCE WFS | weging (×1,5); PvE/begeleiding; bureauonderzoek |
| BRO Bodemkaart (SGM, 1:50.000): bodemsamenstelling tot ± 1,2 m | PDOK WMS (TNO) | informatieve kaartlaag (zand/klei/veen/moerig) met legenda en klik-info (bodemeenheid per vlak); weegt niet mee in het kostenoppervlak; bebouwd gebied is niet gekarteerd |
| Bomen: BGT vegetatieobject (punt) + gemeentelijke registers (`BOMEN_REGIONAAL`; nu: Amersfoort, Utrecht, Amsterdam, Groningen, Almere, Breda, Delft, Assen — het register dat bij het trace/projectgebied hoort wordt automatisch geraadpleegd) | PDOK OGC API Features + per gemeente | wortelzone-weging (×2,0; kroonprojectie waar bekend, anders r ≈ 2,5 m); BEA + kapvergunning-signaal; kaartlaag; ontdubbeld op ~2 m |
| AHN maaiveldhoogte (DTM 0,5 m) | PDOK WCS + WMS (RWS) | hoogteprofiel per boring (min/max/verval, in register, popup, Excel en GeoJSON) en langs korte tracés (≤ 5 km, `hoogteprofiel` in het resultaat); informatieve kaartlaag |
| NWB wegvakken (wegbeheerder) | PDOK WFS (RWS) | echte wegbeheerder (Rijk/provincie/gemeente/waterschap) + wegnaam als bevoegd gezag bij rijbaankruisingen |
| Waterschappen: leggerwatergangen (IMWA oppervlaktewaterlichaam) | PDOK OGC API Features (HWH, landelijk) | categorie primair/secundair/tertiair (≈ A/B/C) per waterkruising: bij een primaire (A-)watergang wordt open ontgraving kritisch uitgesloten en de lichtste passende sleufloze techniek voorgesteld; vervangt de breedte-aanname |
| Waterschappen: waterkeringen (IMWA) | PDOK OGC API Features (HWH, landelijk) + WMS | kering + beschermingszone (± 15 m) als weging (×3,0); waterschapsvergunning + toets-waarschuwing |
| Waterschapsgrenzen (IMSO) | PDOK WMS GetFeatureInfo (HWH) | naam van het bevoegde waterschap bij waterkruisingen (i.p.v. generiek "Waterschap") |
| BRO geotechnisch sondeeronderzoek (CPT) | BRO-uitgifteservice (characteristics, live) + PDOK WMS als kaartlaag | **sonderingenregister**: alle bestaande sonderingen binnen ± 50 m van het tracé of ± 100 m van een boring, met BRO-ID, chainage, einddiepte, maaiveld, kwaliteitsklasse, datum en link naar het BRO-loket; per boring gekoppeld in het boorregister (SON-nrs + loket-links) → opvragen i.p.v. nieuw ramen |
| Rijksmonument-contouren | RCE WFS/WMS | weging (×2,0); omgevingsvergunning rijksmonumentenactiviteit; kaartlaag |
| Stiltegebieden | PDOK WMS (provincies) → rastermasker | lichte weging (×1,2); melding/ontheffing + werkplan geluidsarme uitvoering |
| NGE-verdachte gebieden (register `NGE_REGIONAAL`) | per gemeente/regio (nu: Amsterdam — CE-bodembelastingkaart WMS) | weging (×1,5); NGE-vooronderzoek (WSCS-OCE); buiten gekoppelde gebieden blijft het onderzoeksitem "handmatig beoordelen" |
| Buisleidingen gevaarlijke stoffen (register `BUISLEIDING_REGIONAAL`) | nog leeg — REV publiceert (nog) geen open buisleidingenservice en de Risicokaart is afgeschermd | koppelvlak staat klaar (weging ×2,0, Bevb-afstemming + proefsleuven-onderzoek zodra een bron is toegevoegd) |
| KLIC (voorbereid koppelvlak, licentie) | import: GeoJSON in `data/klic/` | netdichtheid-weging (×1,5), toets "bestaande netten", melding per boring; zonder import blijft alles op "onbekend — KLIC niet gekoppeld" |
| BRK-eigendom (voorbereid koppelvlak, licentie) | import: `data/brk/eigenaren.csv` | echte eigenaren in het ZRO-register en de dossiers; zonder import "onbekend" |

De zonelagen wegen mee in het kostenoppervlak (instelbaar in het
wegingsprofiel), zijn als overlay op de kaart te tonen, en voeden de
toetsing, het vergunningenregister en het **onderzoeksregister** (natuur-
quickscan, bodemonderzoek, archeologie, sonderingen per HDD, NGE-placeholder).
Lagen zonder open WFS worden als WMS-beeld op rasterresolutie gemaskeerd —
een bewuste benadering; een falende dienst betekent minder scherpte, geen
fout (gemeld in `laag_fouten`).

## Bekende beperkingen (bewust buiten fase 1)

- **KLIC en BRK-eigendom zijn licentiebronnen** (FO §2-noot) en nog niet
  aangesloten, maar het koppelvlak staat klaar: een KLIC-levering (GeoJSON in
  `data/klic/`) weegt direct mee als netdichtheid en vult de melding
  "bestaande netten" per boring; een BRK-export (`data/brk/eigenaren.csv`)
  vult de echte eigenaren in het ZRO-register. Zonder import blijft alles
  eerlijk op "onbekend — niet gekoppeld". Het FO markeert precies deze twee
  bronnen als bepalend voor de kwaliteit — toegang vroeg regelen.
- Kruisingskosten zijn per meter benaderd in plaats van vast+variabel per
  techniek (FO §3.2); het omloop-versus-boren-gedrag werkt, de kalibratie is
  indicatief.
- **Inmiddels landelijk gekoppeld:** waterschapsleggers (watergangen met
  categorie én keringen, via de landelijke IMWA-datasets op PDOK),
  NWB-wegbeheerder, AHN-hoogteprofielen (per boring en langs korte tracés)
  en BRO-sonderingen (signaal per boring). Kanttekeningen: de IMWA-categorie
  (primair/secundair/tertiair) is een landelijke harmonisatie van de
  A/B/C-leggers en per waterschap kan het onderhoudsregime afwijken; het
  AHN-profiel is maaiveld (DTM) — een volledig HDD-boorplan (BRO-bodemopbouw,
  gestuurde diepteligging) blijft fase 2.
- **Zonder landelijke open service, dus per-bronhouder-koppelingen:**
  gemeentelijke bomenregisters (`BOMEN_REGIONAAL`, acht gemeenten gekoppeld;
  beschermwaardigheid ontbreekt), NGE-bodembelastingkaarten
  (`NGE_REGIONAAL`, nu Amsterdam — buiten gekoppelde gebieden blijft het
  onderzoeksitem "handmatig beoordelen") en buisleidingen Bevb
  (`BUISLEIDING_REGIONAAL`, nog leeg: het REV publiceert nog geen open
  buisleidingenservice en de Risicokaart is afgeschermd — bron toevoegen
  zodra een service-URL bekend is). Welke regionale bron bij een trace
  hoort bepaalt de app zelf: `GET /api/regionale-bronnen?bbox=…` filtert de
  registers op het projectgebied, de frontend bouwt daar de kaartlagen uit
  op en toont naast elk laag-vinkje welke bron geldt (of "geen bron voor
  dit gebied").
- **Bodemdata zit midden in de stelselwijziging Wbb → BRO; er bestaat géén
  volledige landelijke bron.** De app stapelt daarom vier sporen: BRO **SAD**
  (milieuhygiënisch bodemonderzoek; verplichte aanlevering sinds 1-7-2025,
  landelijke set ~3,6 GB maar per bronhouder nog wisselend gevuld — regio
  Utrecht bijv. nog leeg), BRO **SLD** (overheidsbesluit bodemverontreiniging/
  nazorg; in werking per 1-1-2026, register nog vrijwel leeg maar de zwaarste
  weging zodra gevuld), het **historische Wbb-Bodemloket** als overgangsbron,
  en **regionale open services per bevoegd gezag** via het uitbreidbare
  register `BODEM_REGIONAAL` in `pdok.py` (meegeleverd: Zuid-Holland,
  Zaanstad/Nazca, Fryslân-bodematlas, Gelderland, Overijssel en Limburg;
  Brabant-BIS e.d. zijn op dezelfde manier toe te voegen zodra hun
  service-URL bekend is — provincie Utrecht publiceert geen open
  Wbb-locatieservice). Waar alle sporen leeg zijn en de
  dekkingslaag "eigen website" aangeeft, volgt een info-melding en het
  onderzoeksitem "bodeminformatie opvragen bij bevoegd gezag" in plaats van
  een kostenweging. Het vooronderzoek (NEN 5725) doet altijd de echte
  uitspraak. Let op: de SAD/SLD-WMS'en renderen alleen ingezoomd (schaal
  < 1:50.000 resp. 1:100.000); de rekenmaskers vallen daar ruim binnen.
- Doorlooptijden, tarieven en dekkingsnormen zijn indicatieve startwaarden
  (instelbaar in `registers.py`; de vergoedingsrichtlijn voor ZRO in
  `zro.py`, `VERGOEDING_RICHTLIJN` — grondwaarde en percentages per
  organisatie en regio aan te passen). De eenheidsprijzen van de
  RAW-calculatie zijn fictief maar realistisch (prijspeil 2026) en de
  postnummers volgen de RAW-systematiek indicatief; vervang ze door de
  eigen bedrijfscatalogus (`calculatie.py`). Sleuf- en herstelposten
  rekenen over de volledige segmentlengte per ligging (korte overlap met
  sleufloze kruisingen niet in mindering gebracht — conservatief).
- In de corridor-modus (lange tracés) zoekt de route binnen ± 160 m van de
  rechte lijn tussen de stations; een grootschalige omleiding vindt hij niet
  zelf — via-punten sturen het tracé. Een kruising die precies op een naad
  tussen twee deeltrajecten valt wordt samengevoegd, maar verliest daarbij de
  werkterrein-toets (staat dan op "niet getoetst"). Instelbaar in `main.py`:
  `CHUNK_M`, `CORRIDOR_BREEDTE_M`, `NAAD_RADIUS_M`, `MAX_TRACE_KM`.
- Eén gelijktijdige gebruiker; geen authenticatie.
