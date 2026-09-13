# Token Approval / Ice Phishing Analysis — analiza pre implementacije

Ovaj dokument je **priprema**, ne opis gotove funkcije (uporedi sa DEX-SWAP-ANALIZA.md,
BEHAVIORAL-ANALIZA.md, TAINT-ANALIZA.md, PATHFINDING-ANALIZA.md, koji opisuju već
implementirane analize). Ništa iz njega još nije implementirano — ovo je istraživanje
postojećeg projekta plus predlog arhitekture, koji ćemo koristiti kao referencu kroz ceo
proces implementacije Token Approval / Ice Phishing analize.

**Metodologija ovog dokumenta:** svaki backend fajl naveden ispod je stvarno pročitan
(ne pretpostavljen iz naziva). Svaka tvrdnja o tome šta postoji/ne postoji u podacima je
proverena u kodu, ne pretpostavljena. Gde nešto NE postoji, to je eksplicitno rečeno — po
zahtevu, ništa se ne izmišlja.

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Pregled arhitekture projekta](#1-pregled-arhitekture-projekta) | struktura backend/frontend |
| [2. Kako rade postojeće analize](#2-kako-rade-postojeće-analize) | Graph/Taint/Pathfinding/Behavioral/DEX Swap |
| [3. Lanac dokaza (Chain of Evidence)](#3-lanac-dokaza-chain-of-evidence) | kako trenutno funkcioniše |
| [4. Generisanje izveštaja](#4-generisanje-izveštaja) | kako trenutno funkcioniše |
| [5. Log stranica / audit log](#5-log-stranica--audit-log) | kako trenutno funkcioniše |
| [6. Node/edge detalji i prikaz rezultata](#6-nodeedge-detalji-i-prikaz-rezultata) | postojeći sistem prikaza |
| [7. Šta STVARNO postoji u dostupnim podacima](#7-šta-stvarno-postoji-u-dostupnim-podacima) | **ključni deo — poštena inventura** |
| [8. Šta možemo izvući za Token Approval Analysis](#8-šta-možemo-izvući-za-token-approval-analysis) | mapiranje zahteva na stvarno dostupne podatke |
| [9. Predložena arhitektura implementacije](#9-predložena-arhitektura-implementacije) | modul, ruta, frontend, custody, izveštaj |
| [10. Gde bi šta bilo u kodu](#10-gde-bi-šta-bilo-u-kodu) | putanje (predlog) |
| [11. Otvorena pitanja za usaglašavanje](#11-otvorena-pitanja-za-usaglašavanje) | odluke pre pisanja koda |

---

## 1. Pregled arhitekture projekta

```
master_rad_lusi_blockchain_forensics/
├── backend/app/
│   ├── analytics/          # čisti analitički moduli (graf, taint, pathfinding, dex swap...)
│   │   └── plugins/        # plugin pipeline (taint, blacklist, peel chains, chain hopping...)
│   ├── api/routes/         # FastAPI rute (cases.py je najveći — sve analize su tu)
│   ├── evidence/           # lanac dokaza, audit log, identitet transakcije
│   ├── exports/            # PDF izveštaji (lanac dokaza + izveštaj aktivnosti), registar izveštaja
│   ├── investigations/     # beleške, pinovi, veze (istražiteljski sloj, van obima ovog rada)
│   └── services/           # case_management, onchain_ingestion, report_registry...
├── backend/scripts/        # seed_demo_*.py skripte za demo podatke
├── backend/tests/          # pytest, po jedan fajl po modulu/analizi
├── frontend/src/app/
│   ├── features/           # jedna stranica = jedan folder (isti obrazac za sve)
│   ├── core/services/      # api.service.ts (svi HTTP pozivi), analysis-state.service.ts
│   └── models/blockchain-forensics.models.ts  # svi TypeScript tipovi na jednom mestu
└── *.md                    # dokumentacija po analizi (ovaj fajl prati taj obrazac)
```

Svaka analiza je **fizički odvojen modul** (`app/analytics/<naziv>.py`) sa svojom rutom u
`cases.py`, svojom frontend stranicom u `features/<naziv>/`, i (po pravilu) svojim MD
dokumentom u korenu projekta. Nijedna analiza ne deli mutable stanje sa drugom — sve čitaju
istu, već očišćenu evidenciju slučaja, ali svaka je samostalna.

## 2. Kako rade postojeće analize

### 2.1 Zajednička osnova — evidencija i graf

- **`backend/app/analytics/ingestion.py`** (`clean_transaction_csv`) — čita CSV, normalizuje
  nazive kolona preko `COLUMN_ALIASES` (npr. `from`/`sender`/`from_address` →
  `sender_address`), konvertuje tipove, izbacuje redove bez obaveznih polja
  (`sender_address, recipient_address, amount, timestamp`). **Bitno:** `_normalize_columns`
  samo preimenuje POZNATE kolone — sve OSTALE kolone iz CSV-a (bilo koje ime koje nije u
  `COLUMN_ALIASES`) **ostaju u DataFrame-u, netaknute**, sve do kraja `clean_transaction_csv`
  (vidi §7.3 — ovo je ključan mehanizam za Token Approval Analysis).
- **`backend/app/analytics/case_graph.py`** — `clean_evidence_frames` čisti svaki evidencijski
  fajl slučaja pojedinačno, `combine_frames` ih spaja u jedan DataFrame (`combined_frame`).
- **`backend/app/analytics/graph_building.py`** (`build_transaction_graph`) — gradi
  `networkx.DiGraph` iz `combined_frame`: čvor = adresa, grana = agregat svih transakcija
  između dve adrese (`{amount, timestamp, metadata}` po transakciji, plus `total_amount`,
  `transaction_count`, `first_seen/last_seen` na nivou grane). **Namerno ne čuva `currency`
  ni bilo koju drugu dodatnu kolonu** — graf zna samo za sender/recipient/amount/timestamp/
  metadata. Ovo je već bio problem za DEX Swap Analysis (§2 u DEX-SWAP-ANALIZA.md) i biće
  isti problem za Token Approval Analysis (vidi §9.2 niže).

### 2.2 Graph Analysis (`/graph`)

Sirovi graf (`GET /cases/{id}/graph`) se učitava odmah, bez custody upisa — samo poziva
`build_transaction_graph` + `transaction_graph_to_node_link_json`. Klik na **„Analiziraj
graf"** pokreće `run_plugin_pipeline` (bojenje po riziku/crnoj listi — isti endpoint kao
Taint Analysis, `POST /cases/{id}/analytics/run`, bez `seed_addresses` sa fronta znači
„samo oboj graf", ne „pokreni taint od X").

### 2.3 Taint Analysis (`app/analytics/plugins/taint_analysis.py`)

`TaintAnalysisPlugin` — deo `run_plugin_pipeline`. Propagira **procentualnu** ("haircut")
zaprljanost od seed adresa (ručno zadate + sve sa `blacklist_flag`) kroz graf, procesuirajući
SVAKU transakciju hronološki (flatten preko svih grana, sortirano po vremenu). Radi sa jednim
bezjediničnim `balance`/`tainted_balance` brojem po čvoru — **model ne zna za valutu/token**,
što je razlog zašto ni DEX Swap ni (predložena) Token Approval analiza ne mogu jednostavno
"ubaciti" svoje događaje u taint model bez posebnog tretmana (vidi §9.2). Vraća `results`
(taint % po adresi), `tainted_hops` (svaki transfer koji je preneo nenulti taint, sa
`taint_by_source` raspodelom), `node_taint_series` (istorija po čvoru za scrubber
vremenske trake), `timeline_events`.

### 2.4 Pathfinding (`app/analytics/path_finding.py`)

Dve stvari u istom fajlu:
- `find_transaction_paths` — bogatija verzija (tri strategije: `shortest`/`all_simple_paths`/
  `most_likely`, koristi `nx.shortest_path`/`nx.all_simple_paths`, cost = `1/total_amount`
  za "most_likely").
- `bfs_shortest_path` / `find_path_to_nearest_of` — jednostavan usmeren BFS (koristi ih
  `POST /cases/{id}/pathfinding` ruta), `find_path_to_nearest_of` prima proizvoljan skup
  ciljnih adresa (npr. sve poznate CEX adrese) i vraća put do NAJBLIŽE, sa deterministic
  tie-break (alfabetski) kad je više kandidata na istoj udaljenosti.

### 2.5 Behavioral Analysis (`app/analytics/behavioral_analysis.py`)

`analyze_time_of_day(graph, address)` — čita sve transakcije jedne adrese direktno iz
GRAFA (`graph.edges(data=True)`, ne iz DataFrame-a), raspoređuje ih po UTC satu/danu.
Vraća `hourly_distribution`, `day_of_week_distribution`, `hour_by_day_distribution`,
i `stats` (najaktivniji sat/dan, peak period). **Nema timezone inferencu** — čisto UTC
brojanje (postoji odvojen `timezone_heuristics.py` za procenu vremenske zone, van obima
ovog pregleda).

### 2.6 DEX Swap Analysis (`app/analytics/dex_swap_analysis.py`) — implementiran, najbolji obrazac za ugled

Ovo je **najsvežije dodata, samostalna** analiza — arhitektonski najbliža onome što bi
Token Approval Analysis trebalo da bude, i zato je detaljno razrađena u
`DEX-SWAP-ANALIZA.md` (14 sekcija, pun vodič). Ključne tačke relevantne za nas:

- **Ne koristi deljeni graf** — čita `combined_frame` (cleaned, ali NE agregiran u graf)
  direktno, jer graf ne čuva `currency`. Isti problem će imati i Token Approval Analysis
  (treba `event_type`/`token_address`, koje graf takođe ne bi čuvao).
- **`currency` je opciona kolona** dodata u `COLUMN_ALIASES` — kad postoji, koristi se; kad
  ne postoji, modul i dalje radi (sa `input_token`/`output_token: null`) i **eksplicitno
  kaže da ne zna** (`data_completeness.note`). Ovo je TAČAN obrazac koji Token Approval
  Analysis treba da prati za svoja nova, opciona polja (vidi §8).
- **Dva nivoa pouzdanosti** (`Detected`/`Potential`) + `dex_match_basis` string koji kaže
  TAČNO na osnovu čega je nešto klasifikovano (poznata adresa / brand keyword / generic
  keyword) — heuristika je uvek objašnjena, nikad "crna kutija".
- **Dva endpointa**: `GET .../dex-swap-analysis` (pasivan, bez custody, koristi ga i Graf
  overlay) i `POST .../dex-swap-analysis/run` (deliberatan, uvek custody kad je poslat) —
  isti obrazac koriste i Behavioral (`GET`/`POST .../behavioral-analysis[/run]`).
- **Disclaimer je uvek u odgovoru** — heuristika, ne dokaz.
- Ima frontend stranicu, PDF izveštaj (frontend jsPDF, isti mehanizam kao Taint/Pathfinding),
  graph overlay (dodatne isprekidane grane preko postojećeg grafa, BEZ diranja
  `graph_building.py`), i potpunu custody/log integraciju.

**Ovo je predložen template za Token Approval Analysis — isti oblik u svih 8 dimenzija
(modul → ruta (GET+POST) → frontend stranica → graph overlay (opciono) → PDF izveštaj →
custody → audit log → testovi).**

## 3. Lanac dokaza (Chain of Evidence)

Pun opis: `LANAC-DOKAZA.md`. Sažetak relevantan za novu analizu:

- **Dva nivoa** vode se **istovremeno** iz jednog potpisivanja: po transakciji
  (`custody_log.jsonl`, preko `backend/app/evidence/custody_log.py`) i po dokaznom fajlu
  (`custody_evidence_log.jsonl`, `custody_evidence_log.py`).
- **Identitet transakcije** (`backend/app/evidence/tx_identity.py`,
  `transaction_id(row, evidence_stored_name)`): pravi tx hash ako postoji (`metadata`
  kolona), inače `sha256(sender|recipient|amount|timestamp|evidence_file)[:16]` sa
  prefiksom `row-`. **Radi nezavisno od toga koje dodatne kolone red ima** — čita samo
  bazna polja, pa će raditi i za redove Token Approval evidencije bez ikakve izmene.
- **Okidač**: svako **deliberatno pokretanje analize** (ne pasivan prikaz). Danas 5
  stranica to rade (Taint, Graf, Pathfinding, DEX Swaps, Behavioral) — svih 5 zovu isti
  `CustodyAccessDialogComponent`, i svih 5 na backendu prolaze kroz isti deljeni helper
  `_record_custody_access()` u `backend/app/api/routes/cases.py:498`.
- **Opseg upisa = CELA evidencija u kojoj je analiza tražila** (kombinovana ili jedan
  fajl), ne samo redovi koji su ušli u konačan rezultat — isti princip važi i za DEX Swap
  (upisuje se cela evidencija u kojoj se tražio obrazac, ne samo detektovani parovi).
  **Token Approval Analysis bi pratila identičan princip.**
- `_record_custody_access()` prima `per_evidence_frames: list[tuple[dict, pd.DataFrame]]`
  (dict = evidence entry iz `case.evidence[]`, DataFrame = očišćen sadržaj tog fajla) i
  `TransactionCustodyEntry` (ime/prezime, opis radnje, potpis, opciono
  identifikator predmeta/dokaznog materijala/proizvođač/model/serijski broj — sve sa
  razumnim podrazumevanim vrednostima, `N/A` za fizička polja). **Ništa u ovom helperu ne
  zavisi od TIPA analize** — potpuno spreman za ponovnu upotrebu bez ijedne izmene.

## 4. Generisanje izveštaja

Dva potpuno odvojena mehanizma u projektu — bitno ih razlikovati:

**A) PDF izveštaji po analizi (Taint/Pathfinding/DEX Swap)** — potpuno **frontend**
mehanizam (jsPDF + jspdf-autotable), nema posebne backend rute po analizi. Tok:
1. Frontend sastavi sadržaj (case ID, adresa, parametri, tabele nalaza, metodologija/
   ograničenja pasus, zaključak).
2. `POST /api/v1/reports/register` (`backend/app/api/routes/reports.py`) — prima
   `case_id`, `content` (rečnik koji se heš-uje), `summary` (slobodan rečnik za log),
   `report_type` (`'taint'|'pathfinding'|'dex_swap'|...`, čisto opisno). Backend računa
   SHA-256 otisak preko `compute_content_hash` (`backend/app/services/report_registry.py`
   — sortirani ključevi, fiksni separatori, da je otisak reproduktibilan), generiše
   kontrolni broj oblika `LUSI-2026-XXXX-XXXX`, upisuje u `data/report_registry.json`
   **i** piše `report_signed` red u audit log (sa `report_type` + celim `summary`
   rečnikom).
3. Frontend nacrta potpis (`SignaturePadComponent`), ubaci kontrolni broj + otisak +
   potpis + pečat "LUSI" na poslednju stranu, i pokrene preuzimanje.
4. `GET /api/v1/reports/verify?code=...&content_hash=...` — bilo ko sa pristupom app-u
   kasnije može proveriti da li se otisak poklapa sa registrovanim (detektuje naknadnu
   izmenu SADRŽAJA, ne bajt-za-bajt PDF fajla — granica eksplicitno navedena u docstringu
   modula).

**B) Lanac dokaza PDF (custody izveštaj)** — potpuno **backend** mehanizam (fpdf2,
ćirilični fontovi), `backend/app/exports/custody_report.py` (po transakciji) i
`custody_evidence_report.py` (po dokaznom fajlu), obe dele `custody_pdf_common.py` +
`pdf_fonts.py`. Ovo je odvojeno od (A) — prati tačno obrazac fizičkog dokaznog obrasca
(Идентификатор предмета/доказног материјала/..., pa hronološka tabela).

Za Token Approval Analysis: PDF izveštaj bi pratio **mehanizam (A)** — nova
`report_type: 'token_approval'` vrednost, isti `registerReport()`/potpis/kontrolni broj
tok kao DEX Swap, bez ijedne izmene `reports.py`/`report_registry.py`.

## 5. Log stranica / audit log

- **`backend/app/evidence/audit_log.py`** — jedan append-only JSONL fajl
  (`logs/audit_log.jsonl`), jedan red po akciji: `{timestamp, action, user, case_id,
  case_name, file_name, sha256, details}`. `details` je slobodan rečnik — svaka akcija
  upisuje šta joj je relevantno (za DEX Swap: `address, evidence_scope, max_gap_seconds,
  total_events, detected_count, potential_count, custody_recorded,
  custody_transaction_rows, custody_evidence_files`).
- **`backend/app/exports/activity_report.py`** (774 linije) — servisira i stranicu
  "Log aktivnosti" i njen PDF/CSV izvoz. Ključne mape koje bi trebalo proširiti za novu
  akciju:
  - `ACTION_LABELS: dict[str, tuple[str, str]]` (linija ~50) — `{'akcija': ('Srpski
    naziv', 'English label')}`. Nepoznata akcija se ne skriva ni ne prevodi pogrešno —
    pada nazad na sirovo ime akcije (`action_label()`), pa dodavanje nove akcije NIKAD ne
    kvari postojeći prikaz, samo poboljšava ga kad je dodata.
  - `_REPORT_TYPE_LABELS` (linija ~77) — mapira `report_type` (iz §4A) na čitljiv naziv za
    `report_signed` redove — trenutno već ima više tipova nego što DEX-SWAP-ANALIZA.md
    pominje (`'case_triage'`, `'graph_analysis'`, `'activity_log'` su već tu) — treba
    dodati `'token_approval'`.
  - `ACTION_HUE_ORDER` (linija ~107) — fiksni redosled koji dodeljuje svakoj akciji svoju
    boju (jednako raspoređene nijanse) — dodavanje nove akcije na kraj liste ne pomera
    boje postojećih.
  - Posebna logika po akciji za sažetak u proširenom prikazu (npr. `if action ==
    'dex_swap_analysis_run':` na liniji ~229, `if action == 'report_signed':` na ~268) —
    Token Approval bi dobio svoju granu ovde, isti obrazac.
- **Frontend blizanac**: `frontend/src/app/features/activity-log/activity-log.component.ts`
  drži sopstvenu kopiju istih mapa (`ACTION_PRESENTATION`, `ACTION_HUE`,
  `REPORT_TYPE_LABELS`) — **moraju se ažurirati na oba mesta** (isto upozorenje već stoji
  u DEX-SWAP-ANALIZA.md §12.5, gde je ovo bilo uzgredno popravljeno za sve tri postojeće
  akcije odjednom).
- Stranica **„Lanac dokaza"** (`/lanac-dokaza`) je odvojena od Log aktivnosti stranice —
  pokriveno u §3 iznad.

## 6. Node/edge detalji i prikaz rezultata

- **Graf stranica** (`frontend/src/app/features/graph-visualization/`, 2252 linije u
  `.component.ts`) je centralno mesto za "klik na čvor/granu → detalji" obrazac. Svaka nova
  analiza koja želi da se prikaže NA grafu (opciono, ne obavezno) dodaje sopstveni,
  nezavisan overlay — DEX Swap primer (`DEX-SWAP-ANALIZA.md` §9): poseban paralelan poziv
  (`loadDexSwapOverlay`) koji NE blokira glavni `/graph` poziv ako padne, dodaje dodatne
  cytoscape elemente (`buildSwapEdgeElements`) BEZ ponovnog pokretanja layout-a
  (`renderSwapOverlay` samo doda/ukloni elemente), sopstveni toggle dugme, i klik-panel
  koji zamenjuje standardni "Detalji čvora" panel dok je overlay element izabran.
- **Rezultati analiza se NE čuvaju trajno na serveru** (nema tabele "poslednji rezultat
  analize X za slučaj Y") — svaka stranica (Taint/Pathfinding/Behavioral/DEX Swap) ih drži
  samo u memoriji komponente dok je stranica otvorena; napuštanje stranice gubi prikaz
  (eksplicitno napomenuto i u DEX-SWAP-ANALIZA.md §14.4: "izbor evidencije i rezultat... se
  NE pamte kad odeš na drugu stranicu"). Ono što JESTE trajno: (a) sama evidencija
  (CSV-ovi na disku + `case.json`), (b) audit log, (c) lanac dokaza, (d) registar
  izveštaja. Token Approval Analysis bi pratila isti obrazac — bez novog "sačuvaj
  rezultat" mehanizma, osim ako se eksplicitno zatraži.
- **`app/services/address_enrichment.py`** (`get_known_entity`) — lokalni registar poznatih
  adresa (npr. CEX-ova) korišćen u Pathfinding-u (`find_path_to_nearest_of`) za "najbliži
  poznati cash-out". Potencijalno koristan i za Token Approval (npr. da li je `spender`
  poznat kao legitimna DEX/protokol adresa) — ali danas pokriva CEX/enrichment kontekst,
  ne DEX/drainer kontekst; `known_dex_contracts.json` (§2.6) je poseban, uži kuriran spisak
  napravljen baš za DEX Swap Analysis, iste ideje bi trebalo primeniti na Token Approval
  (vidi §8.4/§9 — nov, zaseban kuriran spisak, ne deljenje/menjanje postojeća dva).

## 7. Šta STVARNO postoji u dostupnim podacima

Ovo je najvažniji deo dokumenta — direktan odgovor na zahtev "ne izmišljaj podatke koje
projekat trenutno nema".

### 7.1 On-chain uvoz danas NE povlači nijedan ERC-20/EIP-2612 event

`backend/app/services/onchain_ingestion.py` je **jedini** izvor pravih (ne ručno unetih)
blockchain podataka u projektu. Poziva isključivo:

- Etherscan V2 `module=account&action=txlist` (`fetch_address_transactions`) — **samo
  native ETH transakcije** (`tx.value`, `tx.from`, `tx.to`, `tx.hash`, `tx.timeStamp`).
  Eksplicitno filtrira `isError == '1'` (revert), ali ne dira ni jedan drugi Etherscan
  modul.
- `module=proxy&action=eth_getTransactionByHash` (`fetch_transaction_by_hash`) — pojedinačna
  transakcija, isti native-only oblik.

**Nikad se ne poziva:**
- `module=account&action=tokentx` (Etherscan-ov endpoint za ERC-20 **Transfer** događaje) —
  DEX-SWAP-ANALIZA.md §7 ovo već konstatuje kao razlog zašto DEX modul ne može sam da
  proizvede par "ETH out / USDC in".
- `module=logs&action=getLogs` sa filterom po topic0 (jedini način da se povuku **ERC-20
  `Approval` eventi**, ili EIP-2612 `permit` pozivi — permit je poziv FUNKCIJE ugovora, ne
  event sam po sebi; ono što bi se videlo u logovima je ipak sam `Approval` event koji
  `permit()` interno emituje, isto kao i `approve()`).
- Bilo šta iz `module=contract` (ABI, decode poziva funkcije) — projekat nema dekodiranje
  ulaznih podataka transakcije (`input`/`data` polje) NIGDE u kodu; `fetch_transaction_by_hash`
  čak ni ne čita `tx.get('input')`.

**Zaključak:** Projekat danas **nema nijedan mehanizam** da sam, automatski, sa lanca
povuče approve/permit/transferFrom podatke. Ovo nije nedostatak parsiranja — podaci se
NIKAD i ne traže od Etherscan-a. Potvrđeno i odsustvom bilo kakve keccak256-sposobne
biblioteke u projektu (`backend/requirements.txt` nema `web3`, `eth-abi`, `eth-hash`,
`pycryptodome` niti bilo šta slično — provereno i odsustvom u instaliranom venv-u), što bi
inače bilo potrebno da se izračuna/proveri topic0 potpis za `Approval(address,address,
uint256)` event.

### 7.2 Šta bi bilo potrebno da se ovo doda kao PRAVI on-chain izvor (van obima za sada)

Da bi se approve/permit/transferFrom podaci povlačili automatski sa lanca (a ne samo iz
ručno pripremljenog CSV-a), trebalo bi:
1. Nov poziv `module=logs&action=getLogs` (Etherscan V2, ista baza kao postojeći pozivi) sa
   `topics[0]` = topic0 za `Approval(address,address,uint256)` (standardan, javno poznat
   potpis — ali njegovu tačnu heks vrednost treba izračunati/proveriti u trenutku
   implementacije, ne prepisati napamet u ovaj dokument bez alata da se to ovde potvrdi).
2. Dekodiranje topics[1]/topics[2] (owner/spender, 32-bajtni topic sa levim paddingom do
   adrese) i `data` polja (uint256 allowance) — ručna hex/int konverzija je izvodljiva bez
   nove zavisnosti (slično kako `_hex_wei_to_eth` već radi hex→decimal konverziju), ALI bi
   trebalo dodati testove za edge slučajeve (npr. netačna dužina topic-a).
3. Da bi se prepoznao `permit()` poziv (za razliku od običnog `approve()`), trebalo bi ili
   dekodirati `input` poziva transakcije (funkcijski selektor `0xd505accf` za standardni
   EIP-2612 `permit`), ili se osloniti samo na Approval event i NE tvrditi da je u pitanju
   permit specifično (pošteniji izbor bez dodatnog dekodiranja).

**Ovo NIJE predlog za sada** — navedeno je da bi se znalo tačno koliko je posla razlika
između "čita CSV kolone koje neko ručno pripremi" (izvodljivo odmah, §8) i "sam povlači sa
lanca" (novi obim posla, zahteva odluku da li se uopšte radi u ovoj fazi — vidi §11).

### 7.3 CSV evidencija VEĆ nosi dodatne kolone kroz ceo pipeline — ovo je pravi resurs

Ključno otkriće za realnu implementaciju: `ingestion._normalize_columns()`
(`backend/app/analytics/ingestion.py:39`) **ne baca nepoznate kolone** — samo preimenuje
poznate (`COLUMN_ALIASES`) i garantuje da obavezne kolone postoje; sve ostalo iz uploadovanog
CSV-a ostaje u DataFrame-u nepromenjeno, sve do `combined_frame` koji analitički moduli
čitaju. Ovo je TAČNO mehanizam kojim `currency` kolona danas stiže do DEX Swap Analysis
(dodata u `COLUMN_ALIASES` kao poznati alias, ali suštinski isti mehanizam bi radio i za
POTPUNO nepoznatu kolonu — samo bez normalizacije naziva/tipa).

**Praktična posledica:** analitičar već danas MOŽE da uploaduje CSV sa dodatnim kolonama
(npr. `event_type`, `token_address`, `spender_address`...) preko postojeće `POST
/upload/csv` rute — ništa u `upload.py`/`ingestion.py` to ne bi odbilo niti obrisalo. Jedini
rizik: `detect_currencies()`/`split_by_currency()` (RAZDVAJANJE-VALUTA.md) reaguju SAMO na
`currency`/`valuta`/`token`/`symbol`/`asset` kolonu — treba paziti da nov naziv kolone (npr.
`token_address`) ne kolidira sa aliasom `token` → `currency` (KOLIDIRA! `'token':
'currency'` je već u `COLUMN_ALIASES` — vidi §11, tačka koju treba rešiti pre imenovanja
kolona).

### 7.4 Šta postoji za identitet/pouzdanost, a šta ne

- **Tx hash**: postoji generički (`metadata` kolona, ista za sve analize) — Approval i
  transferFrom eventi bi ga koristili identično kao i danas.
- **Block number**: NE postoji nigde u trenutnom modelu podataka (ni `ingestion.py` ni
  `onchain_ingestion.py` ga ne čuvaju za native transakcije, iako ga Etherscan `txlist`
  odgovor SADRŽI — `tx.get('blockNumber')` se prosto ne čita). Za Token Approval Analysis
  bi ovo bila potpuno nova, opciona kolona — nikad izvedena, samo prenesena ako je u
  izvornom CSV-u.
- **Poznati DEX/legit protokol spisak**: postoji (`known_dex_contracts.json`, 10 adresa,
  §2.6) — ali je to spisak DEX router/pool adresa, NE spisak poznatih phishing/drainer
  ugovora. Ne postoji nikakav kuriran spisak poznatih ice-phishing/drainer kontrakata u
  projektu danas — ne treba izmišljati jedan bez stvarnih, proverljivih podataka (vidi
  §8.4/§11).
- **Cena/USD vrednost transfera**: ne postoji nigde u projektu (nema price oracle/API
  poziva) — bilo kakav "USD izloženost" izveden iz `amount` bi bio izmišljen. Ako se
  `amount` odnosi na allowance, ne postoji način da se pouzdano kaže "toliko dolara je bilo
  izloženo" bez cene tokena — ovo NEĆE biti deo analize.

## 8. Šta možemo izvući za Token Approval Analysis

Mapiranje **tačno traženih** podataka iz zahteva na ono što je stvarno izvodljivo, poštujući
§7. Predlog šeme (nove opcione kolone) je namerno **minimalan** — isti duh kao DEX Swap
Analysis, koja je dodala samo JEDNU novu kolonu (`currency`) i ponovo iskoristila postojeće
(`sender_address`, `recipient_address`, `amount`, `timestamp`, `metadata`).

### 8.1 Ponovna upotreba postojeće šeme (bez ijedne nove kolone)

| Polje iz zahteva | Izvor | Napomena |
|---|---|---|
| **owner** | `sender_address` | Za `approve`/`permit`: onaj ko potpisuje/poziva jeste vlasnik tokena u standardnom slučaju. Za `permit` ovo NIJE uvek tačno (relayer može poslati tx u ime potpisnika) — zato §8.2 dodaje eksplicitnu, opcionu `owner_address` kolonu kao override kad postoji, sa `sender_address` kao fallback kad ne postoji. |
| **spender** | `recipient_address` | Kod `approve(spender, amount)`/`permit(...)`, ništa se ne transferiše — "primalac" ovog reda semantički postaje odobreni trošilac. Isti trik kao DEX Swap koji `sender_address`/`recipient_address` ponovo tumači po kontekstu (wallet/DEX), ne po doslovnom značenju "transfer". |
| **allowance** | `amount` | Već numerička, već obavezna kolona — za `approve`/`permit` red, njena vrednost JESTE odobrena allowance. |
| **approval timestamp** | `timestamp` | Postojeća obavezna kolona. |
| **transaction hash** | `metadata` | Postojeća kolona, isti `tx_identity.transaction_id()` fallback kad nedostaje. |
| **kasnije korišćenje (transferFrom)** | isti red-oblik, drugi `event_type` | `transferFrom(owner, spender=caller, to)` red: `sender_address` = owner (izvor sredstava), `recipient_address` = STVARNI primalac (ne mora biti spender!), `amount` = preneta količina. Vidi §8.2 za to KOJI spender je iskoristio allowance. |

### 8.2 Nove, opcione kolone (predlog, analogно `currency`)

| Nova kolona | Obavezna? | Značenje | Šta ako nedostaje |
|---|---|---|---|
| `event_type` | **Da, za ovu analizu** (ali opciona na nivou celog projekta — CSV bez nje i dalje radi za SVE ostale analize nepromenjeno) | `approve` \| `permit` \| `transferFrom` | Red se ne razmatra za Token Approval Analysis uopšte (isti tretman kao redovi bez `currency` u DEX Swap — ne bacaju grešku, samo ih modul ignoriše jer ne zna šta predstavljaju) |
| `token_address` | Preporučeno, ne strogo obavezno | Adresa ERC-20 ugovora na koji se allowance odnosi | Bez nje, allowance se ne može pouzdano razlikovati po tokenu ako isti (owner, spender) par ima allowance na više tokena — analiza bi to eksplicitno prijavila kao ograničenje u `data_completeness`, isti obrazac kao DEX Swap §7 |
| `owner_address` | Ne (fallback na `sender_address`) | Eksplicitan vlasnik kad se razlikuje od pošiljaoca tx-a (permit relayer slučaj) | Koristi se `sender_address` |
| `spender_address` | Preporučeno za `transferFrom` redove | Za `approve`/`permit`: isto što i `recipient_address` (redudantno, ali eksplicitno čitljivo). Za `transferFrom`: KOJI spender/pozivalac je iskoristio allowance (pošto stvarni primalac sredstava — `recipient_address` — može biti treća adresa, ne sam spender) | Bez nje, `transferFrom` red se ne može pouzdano povezati sa TAČNO jednim ranijim approval-om ako je isti owner odobrio više spender-a za isti token — analiza bi to prijavila kao "nejednoznačno" umesto da nagađa |
| `is_unlimited` | Ne | Eksplicitna oznaka da je allowance "neograničen" (npr. `true`/`1`/`unlimited`) | Izvodi se heuristikom (vidi §8.3) kad nedostaje, uvek jasno obeleženo kao izvedeno, ne deklarisano |
| `block_number` | Ne | Broj bloka (čisto prikazno polje) | `null`, prikazano kao nedostupno |
| `permit_deadline` / `permit_nonce` | Ne | Samo za `event_type=permit` redove, čisto informativno | `null` |

**Kolizija imena koju treba rešiti pre pisanja koda:** `COLUMN_ALIASES` već mapira
`'token': 'currency'` (linija 32, `ingestion.py`). Kolona nazvana doslovno `token` bi se
tiho pretvorila u `currency`, ne u novu `token_address` semantiku. Predlog: koristiti
isključivo pun naziv `token_address` (i ne dodavati `token` kao alias za njega) — vidi §11.

### 8.3 Izvedeni podaci (računaju se, ne čitaju direktno)

Sve iz ove liste je **heuristika nad podacima koje CSV stvarno sadrži** — svaki nalaz mora
nositi objašnjenje na osnovu čega je izveden (isti obrazac kao `dex_match_basis`/`reasons[]`
kod DEX Swap Analysis), nikad tih tvrdnja bez osnove.

| Traženo iz zahteva | Kako se izvodi | Preduslov |
|---|---|---|
| **unlimited allowance** | (a) `is_unlimited` kolona ako postoji, ILI (b) `amount` >= konfigurabilan prag (npr. blizu 2^256-1 u raw jedinicama, ili apsurdno velik broj u human-readable jedinicama — prag mora biti **podesiv**, jer CSV ne garantuje raw/human jedinice) | `amount` (uvek postoji) |
| **approval status** (aktivna/opozvana/nadjačana) | Hronološka grupa po `(owner, spender, token)`: poslednji `approve`/`permit` red u toj grupi je "trenutno stanje"; ako je `amount == 0`, status = opozvana | `event_type`, `token_address` (preporučeno) |
| **revocation / approve(0)** | Red sa `event_type=approve` i `amount == 0` za par koji je ranije imao `amount > 0` | isto |
| **kasnije korišćenje allowance-a** | `transferFrom` redovi sa istim `(owner, spender[, token])` posle approval timestamp-a | `event_type`, `spender_address` (za pouzdano uparivanje kad ima više spender-a) |
| **vreme između approval-a i prvog korišćenja** | `min(transferFrom.timestamp) - approval.timestamp` za uparene redove | isto |
| **broj povezanih transferFrom transakcija** | `count()` uparenih `transferFrom` redova | isto |
| **ukupno povučena količina** | `sum(transferFrom.amount)` za uparene redove | isto |
| **povezane adrese** | `owner`, `spender`, `token_address`, i skup `recipient_address` iz uparenih `transferFrom` redova (gde je spender preusmerio sredstva — može biti treća adresa, ne sam spender) | isto |
| **risk indikatori** | Vidi §8.4 | kombinacija gorenavedenog |

### 8.4 Risk indikatori — samo iz stvarno prisutnih podataka, uvek objašnjeni

Isti disciplinovan obrazac kao DEX Swap (`Detected`/`Potential` + `reasons[]`) i
chain_hopping/peel_chains plugin-i (`match_basis` string) — svaki indikator mora reći TAČNO
zašto je upaljen:

- **"Unlimited + nikad iskorišćeno"** — `is_unlimited` (deklarisano ili izvedeno) i nula
  uparenih `transferFrom` redova do trenutka analize. Potencijalno "uspavan" rizik.
- **"Unlimited + povučeno ubrzo posle odobrenja"** — klasičan ice-phishing obrazac: prvi
  uparen `transferFrom` u kratkom vremenskom prozoru posle approval-a (podesiv prag, isti
  princip kao `max_gap_seconds` kod DEX Swap), i taj transfer čini veliki deo/celu
  allowance odjednom.
  * *Napomena o pouzdanosti:* ovaj prag NIJE forenzički standard, već konfigurabilna
    heuristika — mora biti jasno obeležena kao takva u odgovoru i izveštaju, isto kao
    DEX Swap-ov `max_gap_seconds`.
- **"Spender odobren od više različitih owner-a"** — `spender_address` koji se pojavljuje
  kao spender u approval redovima za **više različitih `owner_address` vrednosti** u istom
  slučaju — strukturni signal (mnogo žrtava odobrilo isti ugovor), računa se direktno
  grupisanjem po `spender_address` preko cele evidencije, bez potrebe za spoljnim spiskom.
  Ovo je isti tip signala kao `betweenness`/in-degree ideje iz IDEJE-NOVE-ANALIZE.md #3, ali
  ovde primenjeno specifično na spender-e, ne na graf uopšte.
- **"Opozvano tek posle korišćenja"** — postoji i uparen `transferFrom` PRE revocation
  reda — informativno, ne nužno "rizik" (revocation se ipak desio), ali korisno za
  vremensku liniju.
- **"Nikad opozvano, a već korišćeno"** — postoji uparen `transferFrom`, a nijedan kasniji
  `approve(0)` red za isti par — allowance (ako nije potrošena do nule) ostaje otvorena.

**Namerno IZOSTAVLJENO** (da ne bi bilo izmišljeno): bilo kakav kuriran spisak "poznatih
drainer/phishing ugovora" (nema izvora tih podataka u projektu — različito od
`known_dex_contracts.json`, koji su realne, javno poznate DEX router adrese), i bilo kakva
"USD vrednost izloženosti" (nema cenovnog izvora — §7.4).

## 9. Predložena arhitektura implementacije

Sledi **isti oblik u 8 tačaka** koji DEX Swap Analysis već dokazano koristi (§2.6) — namerno
konzistentno sa ostatkom projekta, bez ijedne izmene postojećih Graph/Taint/Pathfinding/
Behavioral/DEX Swap algoritama.

### 9.1 Backend modul

`backend/app/analytics/token_approval_analysis.py` — nova, samostalna funkcija (radni naziv
`analyze_token_approvals(transactions: pd.DataFrame, target_address: str | None = None, ...)
-> dict[str, Any]`), čita `combined_frame` direktno (ISTI razlog kao DEX Swap §2 — graf ne
čuva dodatne kolone), filtrira na redove gde `event_type` nije prazan, grupiše po
`(owner, spender, token)`, primenjuje §8.3/§8.4 logiku. Vraća `disclaimer` +
`data_completeness` (koje opcione kolone su stvarno prisutne u ovoj evidenciji) na isti
način kao DEX Swap.

### 9.2 Zašto NE ide kroz deljeni graf/taint model (isto obrazloženje kao DEX Swap §2/§10)

`build_transaction_graph` ne bi čuvao `event_type`/`token_address`/`spender_address` —
isti arhitektonski razlog kao za `currency`. Modul zato **ne dira** `graph_building.py`.

Ako se kasnije poželi prikaz na `/graph` stranici (opciono, po uzoru na DEX Swap §9), to bi
bio čisto **frontend overlay** (dodatne cytoscape grane `owner → spender`, stil vizuelno
različit od transakcionih i od SWAP grana), bez ijedne izmene taint modela — i taint kroz
approval granu se **ne bi** propuštao (approve/permit ne pomera sredstva, pa "prenos
taint-a" kroz nju nema smisla kao kod DEX swap-a, gde se realno menja token). Ovo bi trebalo
eksplicitno odlučiti pre implementacije (vidi §11).

### 9.3 Ruta — dva endpointa, isti obrazac

```
GET  /api/v1/cases/{case_id}/token-approval-analysis        (pasivno, bez custody)
POST /api/v1/cases/{case_id}/token-approval-analysis/run    (deliberatno, custody opciono u telu)
```

U `backend/app/api/routes/cases.py`, odmah pored `get_case_dex_swap_analysis`/
`run_case_dex_swap_analysis` (linije 353–464) — identičan sklop: `_filter_evidence_paths`,
`clean_evidence_frames`/`combine_frames`, `ValueError` → 404 za nepostojeću adresu,
`write_audit_log(action='token_approval_analysis_run', ...)`, i `_record_custody_access()`
pozvan BEZ IZMENE (već generički, §3) kad je `custody` prisutan.

### 9.4 Frontend stranica

`frontend/src/app/features/token-approval-analysis/` — isti obrazac kao
`dex-swap-analysis/` (evidence picker + Address + ANALYZE dugme → custody dijalog → lista
kartica/tabela nalaza sa jasno obeleženim nivoom pouzdanosti + disclaimer). Nov unos u
glavni meni.

### 9.5 PDF izveštaj

Isti mehanizam kao §4A — `registerReport()` sa `report_type: 'token_approval'`, izgrađen po
istom obrascu kao `dex-swap-analysis.component.ts`-ov PDF (zaglavlje + disclaimer, rezime,
tabela approval/permit nalaza, tabela transferFrom korišćenja, risk indikatori sekcija,
metodologija/ograničenja — direktno iz §7/§8 ovog dokumenta, potpis + kontrolni broj).

### 9.6 Custody / Lanac dokaza

Bez izmene — `_record_custody_access()` je već generički (§3). Nova ruta samo prosleđuje
`per_evidence_frames` i `custody` kao i DEX Swap.

### 9.7 Log aktivnosti

- `ACTION_LABELS['token_approval_analysis_run'] = (...)` u `activity_report.py` **i** u
  `activity-log.component.ts` (oba mesta, §5).
- `_REPORT_TYPE_LABELS['token_approval'] = (...)` na oba mesta.
- `ACTION_HUE_ORDER` — dodati na kraj liste (ne remetiti postojeće boje).
- Poseban `if action == 'token_approval_analysis_run':` blok za sažetak u proširenom
  prikazu (isti obrazac kao DEX Swap na liniji ~229).

### 9.8 Testovi

`backend/tests/test_token_approval_analysis.py` — isti stil kao
`test_dex_swap_analysis.py` (19 testova tamo): klasifikacija event tipova, uparivanje
approval↔transferFrom, revocation detekcija, unlimited heuristika (deklarisana i izvedena),
risk indikatori pojedinačno, nedostatak opcionih kolona i dalje radi (samo bez tih nalaza),
nepostojeća adresa → `ValueError`/404, `data_completeness` tačan.

## 10. Gde bi šta bilo u kodu

| Šta | Fajl (predlog, prati DEX Swap raspored) |
|---|---|
| Algoritam | `backend/app/analytics/token_approval_analysis.py` |
| Nove kolone u CSV normalizaciji | `backend/app/analytics/ingestion.py` — `COLUMN_ALIASES` (samo ako se nešto od novih polja odluči da MORA imati alias; `event_type`/`token_address`/`spender_address`/`owner_address` mogu ostati bez aliasa i i dalje proći kroz §7.3 mehanizam) |
| Ruta | `backend/app/api/routes/cases.py` (`get_case_token_approval_analysis`, `run_case_token_approval_analysis`, `TokenApprovalAnalysisRunRequest`) |
| Testovi | `backend/tests/test_token_approval_analysis.py` |
| Demo podaci | `backend/scripts/seed_demo_token_approval_evidence.py` (po uzoru na `seed_demo_dex_swap_evidence.py`) |
| Frontend stranica | `frontend/src/app/features/token-approval-analysis/` |
| PDF izveštaj | isti fajl kao gore (`buildTokenApprovalPdf`), koristi postojeći `/reports/register` |
| Log aktivnosti oznake | `backend/app/exports/activity_report.py` + `frontend/.../activity-log/activity-log.component.ts` |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`getTokenApprovalAnalysis`, `runTokenApprovalAnalysis`) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`TokenApprovalEvent`, `TokenApprovalAnalysisResult`, ...) |
| Dokumentacija gotove funkcije (posle implementacije) | `TOKEN-APPROVAL-ANALIZA.md` (novi fajl, ovaj dokument ostaje kao istorijska priprema) |

## 11. Otvorena pitanja za usaglašavanje

Namerno navedena OVDE, pre pisanja koda — svaka od njih menja konkretan detalj arhitekture
iz §8/§9:

1. **Naziv kolone za token ugovor.** `token` je već zauzet aliasom ka `currency`
   (§8.2/§7.3). Predlog: `token_address` bez ikakvog aliasa. Treba potvrditi da ovo ne
   koliduje sa planovima za neku drugu analizu.
2. **Da li `event_type` postaje deo `COLUMN_ALIASES`** (npr. prihvatiti i `type`/`action`
   kao alias) ili ostaje striktno `event_type`, bez alternativa — manje "magije", lakše za
   objasniti u dokumentaciji, ali manje fleksibilno za CSV-ove sa drugačijim nazivom kolone.
3. **Prag za "unlimited" heuristiku** (§8.3) kad `is_unlimited` kolona nije data — fiksna
   konstanta u kodu (rizik: pogrešna pretpostavka o raw/human jedinicama) ili obavezan query
   parametar (isti stil kao `max_gap_seconds` kod DEX Swap, korisnik/frontend ga postavlja
   eksplicitno)?
4. **Da li se radi i graph overlay** (§9.2, opciono) u prvoj verziji, ili tek posle osnovnog
   modula + PDF-a (kao što je DEX Swap prvo imao osnovnu analizu, overlay je dodat kasnije
   kao §9 njegovog dokumenta)?
5. **Da li se u ovoj fazi dira `onchain_ingestion.py`** (§7.2 — pravi `getLogs` poziv za
   Approval event) ili ostaje isključivo na CSV/ručno pripremljenoj evidenciji za prvu
   verziju? Ovo je najveća odluka po obimu posla — §7.2 postoji baš da bi ova odluka bila
   informisana, ne nagađana.
6. **Demo podaci** — treba li novi CSV u POSTOJEĆI demo slučaj (`46ae7f91db9b`, isti kao
   DEX Swap/Taint/Behavioral demo) radi konzistentnosti kroz sve analize, ili zaseban demo
   slučaj posvećen ice-phishing scenariju?

Ovaj dokument se ažurira kako se ova pitanja reše i kako implementacija napreduje.
