# Token Approval / Ice Phishing Analysis — dokumentacija implementacije

> **Status:** §1–§11 su izvorna priprema (istraživanje projekta + predlog arhitekture),
> napisana PRE ijedne linije koda. §12 dokumentuje šta je stvarno urađeno u **Fazi 1 —
> backend: prikupljanje i ekstrakcija podataka** (bez custody/PDF izveštaja/audit log
> integracije/frontend-a — te faze ostaju otvorene, vidi §12.6). Ništa iz §1–§11 nije
> naknadno izmenjeno da bi "ispalo tačno" — §12 na kraju kaže tačno gde se stvarna
> implementacija razlikuje od prvobitnog predloga i zašto.

Ovaj dokument prati ceo proces implementacije Token Approval / Ice Phishing analize, po
uzoru na DEX-SWAP-ANALIZA.md/BEHAVIORAL-ANALIZA.md/TAINT-ANALIZA.md/PATHFINDING-ANALIZA.md
koji opisuju već implementirane analize.

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
| [12. Implementacija — backend, Faza 1](#12-implementacija--backend-faza-1) | **✅ urađeno** — novi/izmenjeni fajlovi, API, metode, ograničenja, testovi |
| [13. Implementacija — Istorija odobrenja po adresi](#13-implementacija--istorija-odobrenja-po-adresi) | **✅ urađeno** — APPROVE → promena → REVOCATION → status, po odobrenju |
| [14. Implementacija — Korelacija Approval ↔ transferFrom](#14-implementacija--korelacija-approval--transferfrom) | **✅ urađeno** — OWNER → APPROVAL → SPENDER → transferFrom → transfer, kombinovan status |
| [15. Implementacija — Forenzički risk indikatori](#15-implementacija--forenzički-risk-indikatori) | **✅ urađeno** — LOW/MEDIUM/HIGH + lista razloga, nikad tvrdnja o zloupotrebi |

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

Navedena OVDE, pre pisanja koda. **Status posle §12 implementacije:** 1–3 su rešena (odluka
i obrazloženje u §12.2/§12.4); 4–6 ostaju otvorena za sledeću fazu (vidi §12.6).

1. ~~**Naziv kolone za token ugovor.**~~ **Rešeno:** `token_address`, bez aliasa u
   `COLUMN_ALIASES` (i bez potrebe da se ijedna alias-lista uopšte menja — vidi §12.2).
2. ~~**Da li `event_type` postaje deo `COLUMN_ALIASES`**~~ **Rešeno:** ostaje striktno
   `event_type`, bez alternativnih naziva — vidi §12.2 za obrazloženje (izbegava sporedne
   efekte na CSV-ove koje ne pišemo mi).
3. ~~**Prag za "unlimited" heuristiku**~~ **Rešeno:** query parametar
   (`unlimited_threshold`, podrazumevano `1e15`), isti stil kao `max_gap_seconds` — vidi
   §12.3/§12.4.
4. **Da li se radi i graph overlay** — i dalje otvoreno, namerno van obima Faze 1 (§12.6).
5. **Da li se u ovoj fazi dira `onchain_ingestion.py`** — **odlučeno za Fazu 1: NE.**
   Ništa u `onchain_ingestion.py` nije menjano ni dodavano — Faza 1 čita isključivo
   evidenciju koju analitičar uveze (CSV sa opcionim kolonama, §8.2), tačno kao što je
   zahtevano ("ne izmišljaj ABI, evente ili podatke"). §7.2 ostaje kao informisan opis
   koraka koji bi bio potreban za PRAVI on-chain izvor, van obima za sada.
6. **Demo podaci** — i dalje otvoreno; Faza 1 nema seed skriptu (nije traženo u ovom
   koraku) — vidi §12.6.

Ovaj dokument se dalje ažurira kako implementacija napreduje (Faza 2: custody, PDF
izveštaj, audit log, frontend).

---

## 12. Implementacija — backend, Faza 1

**Obim ove faze (po zahtevu):** samo backend prikupljanje i ekstrakcija Token Approval
podataka — analitički modul + jedna, pasivna (read-only) API ruta. **Nije** rađeno u ovoj
fazi: custody-gated `POST .../run` varijanta, PDF izveštaj, audit log/Log aktivnosti unos,
frontend stranica, graph overlay, izmena `onchain_ingestion.py`. Ništa od postojećih
analiza (Graph/Taint/Pathfinding/Behavioral/DEX Swap) nije dirano — potvrđeno i punim
pytest prolazom (§12.5).

### 12.1 Novi fajlovi

| Fajl | Sadržaj |
|---|---|
| `backend/app/analytics/token_approval_analysis.py` | Ceo algoritam — ekstrakcija, grupisanje, uparivanje, risk indikatori. Jedina javna ulazna tačka: `analyze_token_approvals()`. Nema pydantic modela (isti stil kao `dex_swap_analysis.py`/`path_finding.py` — analitički sloj vraća obične `dict`-ove, pydantic modeli se koriste samo na nivou rute za request/response tela sa telom zahteva, a ova ruta nema telo). |
| `backend/tests/test_token_approval_analysis.py` | 25 pytest testova (§12.5). |

### 12.2 Izmenjeni fajlovi — tačan obim izmene

| Fajl | Šta je izmenjeno | Šta NIJE dirano |
|---|---|---|
| `backend/app/api/routes/cases.py` | (a) Dodat import `from app.analytics.token_approval_analysis import (...)` odmah posle DEX Swap importa; (b) dodata JEDNA nova funkcija `get_case_token_approval_analysis`, ubačena između `run_case_dex_swap_analysis` i `get_seed_suggestions` — ni jedan postojeći red u fajlu nije obrisan niti izmenjen. | Sve postojeće rute (`get_case_graph`, `run_case_analytics`, `get_case_dex_swap_analysis`, `run_case_dex_swap_analysis`, `_record_custody_access`, ...) — nula izmena. |

**`backend/app/analytics/ingestion.py` NIJE menjan** — namerno, i to je ključna
arhitektonska odluka (rešava otvoreno pitanje §11.1/§11.2): `_normalize_columns` već
propušta svaku nepoznatu kolonu netaknutu (§7.3) — kolona nazvana tačno `event_type`,
`token_address`, `owner_address`, `spender_address`, `is_unlimited`, `block_number`,
`permit_deadline` ili `permit_nonce` stiže do `combined_frame` bez ijedne izmene u
`COLUMN_ALIASES`. Ovo je isti mehanizam kojim `currency` danas stiže do DEX Swap Analysis,
samo primenjen bez potrebe da se kolona uopšte doda u alias-mapu — pošto joj nije potreban
nijedan alternativni naziv (§11 tačka 2). Posledica: **nula rizika** da izmena
`ingestion.py` slučajno utiče na neku drugu analizu, jer fajl nije ni dotaknut.

`backend/app/analytics/graph_building.py`, `case_graph.py`, `path_finding.py`,
`behavioral_analysis.py`, `dex_swap_analysis.py`, `plugins/*.py` — **nula izmena**, u
potpunosti u skladu sa zahtevom.

### 12.3 Model podataka — kolone koje modul čita (implementirano tačno po §8.2)

Sve OPCIONE, sve sa **case-sensitive** tačnim nazivom kolone (bez aliasa):

| Kolona | Tip (posle parsiranja) | Koristi se za |
|---|---|---|
| `event_type` | `'approve'` \| `'permit'` \| `'transferFrom'` (case-insensitive vrednost, uski skup aliasa — `approve`/`erc20_approve`, `permit`/`eip2612_permit`/`eip-2612_permit`, `transferfrom`/`transfer_from`) | Da li se red uopšte razmatra za ovu analizu |
| `token_address` | tekst | Grupisanje po tokenu |
| `owner_address` | tekst, fallback na `sender_address` | Vlasnik tokena |
| `spender_address` | tekst; za approve/permit fallback na `recipient_address`, za transferFrom **bez fallback-a** (§12.4) | Odobreni trošilac / stvarni pozivalac transferFrom |
| `is_unlimited` | tri-state bool (`true/false/1/0/yes/no/unlimited/infinite/nepoznato`) | Deklarisana (najjača) osnova za "unlimited" |
| `block_number` | int (parsira i decimalne stringove kao `"18500000.0"`) | Čisto prikazno polje |
| `permit_deadline` | opaque tekst (nema pokušaja parsiranja formata — vidi §12.4) | Samo za `event_type=permit` redove |
| `permit_nonce` | int | Samo za `event_type=permit` redove |

Postojeće, ponovo iskorišćene kolone (bez izmene semantike za ostale analize):
`sender_address`, `recipient_address`, `amount` (allowance za approve/permit, preneta
količina za transferFrom), `timestamp`, `metadata` (tx hash).

### 12.4 Metode/servisna logika — šta tačno rade i zašto (uključujući odluke van §8 predloga)

Sve u `token_approval_analysis.py`, funkcija `analyze_token_approvals(transactions,
target_address=None, unlimited_threshold=1e15, rapid_use_seconds=3600)`:

- **`_extract_approval_rows`** — razdvaja evidenciju u `approval_events`/`transfer_events`/
  `skipped_rows`, i **eksplicitno prijavljuje** (`unrecognized_event_type_values`) svaku
  vrednost `event_type` koja ne pogodi poznat alias (npr. tipfeler `"aproove"`) — umesto da
  je ćutke ignoriše kao običan transfer.
- **Grupisanje** — `(owner, spender, token)` normalizovan (mala slova) ključ za grupisanje/
  uparivanje, ali **prikazna vrednost adrese ostaje onakva kakva se prvi put pojavila** u
  evidenciji — ista disciplina kao `dex_swap_analysis.classify_dex_node`.
- **Redosled odobrenja i status** — approve()/permit() u realnom ERC-20 ugovoru
  **PREPISUJE** prethodnu dozvolu, ne sabira se s njom. Implementacija to modelira
  eksplicitno: u svakoj grupi se odobrenja sortiraju hronološki; SVAKO osim POSLEDNJEG
  dobija `sequence_status: 'superseded'`, bez obzira na sopstveni iznos; samo poslednje
  određuje `current_status` (`'revoked'` ako mu je iznos 0, inače `'active'`). Ovo je
  odluka koja nije bila eksplicitna u §8.3 predlogu — dodata tokom implementacije jer je
  neophodna za tačnu semantiku approve().
- **`approve(spender, 0)` / opoziv** — tačna provera `amount == 0.0` (bezbedno, nema
  problema sa float preciznošću blizu nule) — posebno testirano (§12.5).
- **"Unlimited" — dva jasno razdvojena nivoa** (odluka doneta tokom implementacije, vidi
  §7.4/§8.3 obrazloženje): `'declared'` kad `is_unlimited` kolona to kaže direktno,
  `'potential_by_magnitude'` kad `amount >= unlimited_threshold` (podrazumevano `1e15`,
  podesivo kroz ceo opseg `[1.0, 1e40]`) — **nikad `'declared'` samo na osnovu veličine
  broja**, jer `amount` stiže kao `float64` (already parsed by `pd.to_numeric` upstream u
  `ingestion.py`) i ne može pouzdano potvrditi tačnu jednakost sa 78-cifrenim
  `2**256-1` sentinelom, a projekat nema registar decimala tokena da bi znao da li je
  `amount` u raw ili human-readable jedinicama (§7.4 — ograničenje, ne previd).
- **Uparivanje transferFrom → odobrenje, dva nivoa** (§8.3 predlog, implementiran tačno):
  1. **Tačno poklapanje** `(owner, spender, token)`.
  2. **Fallback**: ako tačnog poklapanja nema (npr. transferFrom red nema `token_address`),
     a za dati `(owner, spender)` postoji **TAČNO JEDNA** grupa odobrenja bez obzira na
     token — uparuje se, uz oznaku da je token **izveden**, ne potvrđen.
     Ako postoji **više** kandidata (isti owner/spender, različiti tokeni) — transfer
     ostaje **neatribuiran**, sa razlogom (`ambiguous_token_multiple_approvals`), umesto
     nagađanja koji token je u pitanju.
  3. transferFrom bez `spender_address` uopšte **nikad se ne uparuje** — razlog
     `no_spender_column` — jer se `recipient_address` (stvarni primalac sredstava) NE sme
     poistovetiti sa spender-om (oni mogu biti različiti — §8.1 tabela).
- **Vreme do prvog korišćenja** — računa se u odnosu na odobrenje koje je stvarno bilo **na
  snazi** u trenutku transfera (poslednje odobrenje sa `timestamp <= transfer.timestamp`),
  ne nužno prvo odobrenje u grupi — bitno kad grupa ima više uzastopnih odobrenja.
  Transfer koji prethodi SVAKOM odobrenju u evidenciji dobija `before_any_approval: true`
  (anomalija, prijavljena, ne skrivena).
- **Risk indikatori** (`risk_indicators`, lista `{code, label, reasons}` po grupi):
  - `unlimited_never_used` — neograničeno + aktivno + nula transferFrom.
  - `unlimited_rapid_drain` — transferFrom unutar `rapid_use_seconds` od odobrenja **koje
    je i samo bilo neograničeno** (ne "grupa je NEKAD imala neograničeno odobrenje" —
    namerno precizirano tokom implementacije, da rani mali approve + kasniji unlimited +
    brz transfer posle MALOG ne bi lažno pao pod ovaj indikator).
  - `revoked_after_use` — opozvano, a bar jedan transfer je već prošao pre opoziva.
  - `active_used_never_revoked` — aktivno i već korišćeno.
  - `spender_multi_owner` — spender adresa koju je odobrilo **≥2 različita owner-a** u
    CELOJ evidenciji (računa se PRE filtriranja po `target_address`, da rezultat ne zavisi
    od toga da li je adresa tražena).
- **`data_completeness.notes`** — lista rečenica na srpskom, jedna po opcionoj koloni koja
  nije deklarisana NI U JEDNOM redu, sa objašnjenjem šta tačno to ograničava (isti obrazac
  kao `dex_swap_analysis`'s `data_completeness.note`, samo prošireno na više polja).

### 12.5 API endpoint

```
GET /api/v1/cases/{case_id}/token-approval-analysis?address=<opciono>&evidence=<opciono>&unlimited_threshold=<opciono, 1.0-1e40>&rapid_use_seconds=<opciono, 0-2592000>
```

- **Read-only, bez custody upisa i bez audit log unosa** — isti tretman kao
  `GET .../dex-swap-analysis` i `GET .../behavioral-analysis` (pasivne varijante). Ovo je
  namerna, dokumentovana granica Faze 1 (§12.6), ne previd.
- `address` opciono — izostavljeno, vraća SVE grupe odobrenja u evidenciji; zadato, filtrira
  na grupe gde je ta adresa owner ILI spender (case-sensitive tačno poklapanje, ista
  konvencija kao Pathfinding/Behavioral/DEX Swap); `404` ako se adresa nigde ne pojavljuje
  (proveravano preko `sender_address`/`recipient_address`/`owner_address`/`spender_address`
  — šire od DEX Swap-ove provere, jer eksplicitan `owner_address` može biti adresa koja se
  NIGDE ne pojavljuje kao običan sender/recipient — vidi §12.4).
- Odgovor (skraćeno, pun oblik proveren stvarnim pozivom kroz `TestClient`, §12.5.2):
  ```json
  {
    "case_id": "...", "evidence": null, "address": null, "generated_at": "...",
    "total_approval_events": 1, "total_approve_events": 1, "total_permit_events": 0,
    "total_transferfrom_events": 1, "unattributed_transferfrom_count": 0,
    "unique_owners": 1, "unique_spenders": 1, "unique_tokens": 1,
    "groups": [{
      "owner": "0xOwner", "spender": "0xSpender", "token_address": "0xTokenA",
      "token_identified": true, "current_status": "active",
      "current_allowance_amount": 1e18, "current_unlimited_basis": "declared",
      "ever_unlimited": true, "approval_event_count": 1,
      "first_approval_timestamp": "...", "latest_approval_timestamp": "...",
      "approvals": [{"event_type": "approve", "amount": 1e18, "is_zero": false,
        "unlimited_basis": "declared", "is_unlimited_declared": true,
        "sequence_status": "active", "timestamp": "...", "transaction_hash": "0xapprovehash1",
        "block_number": null, "permit_deadline": null, "permit_nonce": null}],
      "transfer_from_count": 1, "total_transferred_amount": 5e17,
      "linked_transfers": [{"amount": 5e17, "timestamp": "...", "transaction_hash": "...",
        "block_number": null, "recipient": "0xThirdParty",
        "preceding_approval_timestamp": "...", "preceding_approval_unlimited_basis": "declared",
        "seconds_since_approval": 3600.0, "before_any_approval": false}],
      "first_use_timestamp": "...", "time_to_first_use_seconds": 3600.0,
      "time_to_first_use_anomaly": false, "related_addresses": ["0xThirdParty"],
      "spender_multi_owner": null,
      "risk_indicators": [{"code": "unlimited_rapid_drain", "label": "...", "reasons": ["...", "...", "..."]},
                           {"code": "active_used_never_revoked", "label": "...", "reasons": ["...", "..."]}]
    }],
    "unattributed_transfers": [], "skipped_row_count": 0, "unrecognized_event_type_values": [],
    "data_completeness": {"event_type_declared": true, "token_address_declared": true,
      "owner_address_declared": false, "spender_address_declared": true,
      "is_unlimited_declared": true, "block_number_declared": false,
      "permit_fields_declared": false, "notes": ["..."]},
    "unlimited_threshold": 1e15, "rapid_use_seconds": 3600,
    "disclaimer": "Token Approval Analysis čita isključivo polja koja evidencija stvarno deklariše - ..."
  }
  ```

#### 12.5.1 Testovi (pytest, `backend/tests/test_token_approval_analysis.py`)

25 testova, grupisano: `TestRequiredColumns` (2 — nedostatak obavezne kolone baca grešku,
nedostatak `event_type` vraća prazan-ali-validan rezultat), `TestOwnerSpenderExtraction` (3
— fallback na sender/recipient, eksplicitan `owner_address` override za permit,
neprepoznata `event_type` vrednost se prijavljuje), `TestUnlimitedDetection` (3 —
deklarisano/heuristika-po-veličini/običan iznos), `TestApprovalStatusSequence` (3 —
`approve(spender,0)` ⇒ revoked, `approve(spender,amount>0)` ⇒ active, ranije odobrenje ⇒
superseded), `TestTransferFromLinking` (5 — tačno poklapanje, nedostatak
`spender_address` ⇒ neatribuirano, nejednoznačan token uz više odobrenja ⇒ neatribuirano,
jedan nedvosmislen kandidat ⇒ izvedeno poklapanje, vreme do prvog korišćenja se računa
prema odobrenju na snazi), `TestRiskIndicators` (5 — sve pet indikatora, uključujući
namernu proveru da `unlimited_rapid_drain` NE opali kad je iskorišćeno odobrenje bilo malo
a neograničeno tek kasnije), `TestAddressFiltering` (2 — 404 za nepostojeću adresu,
filtriranje po owner adresi), `TestDataCompleteness` (2 — odsutne kolone se prijavljuju,
disclaimer uvek prisutan).

```bash
python -m pytest backend/tests/test_token_approval_analysis.py -v   # 25 passed
```

#### 12.5.2 Testirano i kroz pravu rutu (ne samo jedinični testovi)

End-to-end provera kroz `TestClient` (prijava → kreiranje slučaja → `POST /upload/csv` sa
CSV-om koji ima `event_type`/`token_address`/`spender_address`/`is_unlimited` kolone → `GET
.../token-approval-analysis`): stvaran HTTP 200 sa tačno očekivanim poljima (prikazano u
§12.5 iznad — svaka vrednost u tom primeru je stvarno vraćena od aplikacije, ne ručno
sastavljena), i stvaran HTTP 404 za adresu koja se ne pojavljuje u evidenciji. Potvrđuje da
`POST /upload/csv` (nepromenjen, §7.3) zaista pušta nove kolone kroz ceo lanac do ove nove
rute.

#### 12.5.3 Puna regresija

```bash
python -m pytest backend/ -q   # 327 passed (302 postojećih + 25 novih), 0 failed
```

Potvrđuje: nijedna postojeća analiza (Graph/Taint/Pathfinding/Behavioral/DEX Swap),
lanac dokaza, izveštaji, upload, niti bilo šta drugo nije pokvareno dodavanjem ovog modula
i ove rute.

### 12.6 Ograničenja — i šta OSTAJE otvoreno (van obima Faze 1)

**Ograničenja podataka (nasleđena iz §7, potvrđena implementacijom, ništa novo izmišljeno):**

- I dalje važi u potpunosti: nema automatskog on-chain izvora za approve/permit/
  transferFrom (§7.1) — Faza 1 svesno NE dodaje `getLogs`/`tokentx` poziv.
- **Novo, uočeno tokom implementacije:** `amount` je `float64` (posledica postojećeg
  `ingestion.clean_transaction_csv`-a, koji ovaj modul namerno ne menja) — svaka tvrdnja o
  "tačnoj" `2**256-1`/`2**96-1` vrednosti bi bila lažna preciznost, pa je "unlimited"
  ISKLJUČIVO deklarisano (`is_unlimited` kolona) ili heuristika-po-veličini praga, nikad
  "potvrđeno po tačnoj vrednosti sentinela".
- **Novo, uočeno tokom implementacije:** bez registra decimala po tokenu, modul ne zna da
  li je `amount` u raw baznim jedinicama ili već human-scaled — dodatan razlog zašto
  fiksni brojčani sentinel ne bi bio pouzdan (već pokriveno gore, ali eksplicitno vredno
  ponoviti kao ograničenje, ne kao previd).
- Grupisanje po `(owner, spender, token)` kad `token_address` nije deklarisan **spaja**
  sve tokene tog para u jednu grupu — `token_identified: false` to obeležava po grupi, ali
  ne razdvaja retroaktivno ono što evidencija sama nije razdvojila.
- transferFrom bez `spender_address` je **trajno neatribuiran** u ovoj verziji — nema
  fallback nagađanja (namerno, §12.4).
- `block_number` i `permit_deadline`/`permit_nonce` su čisti passthrough — nikad izvedeni,
  nikad validirani protiv nekog spoljnog izvora (nema ga).

**Van obima Faze 1 (planirano, ne urađeno — vidi §9/§11 za predlog kad se bude radilo):**

- `POST /cases/{id}/token-approval-analysis/run` — custody-gated deliberatna varijanta
  (§9.6) — `_record_custody_access` je već generički i ne treba mu nijedna izmena kad se
  ovo doda.
- PDF izveštaj (§9.5, `report_type: 'token_approval'`).
- Log aktivnosti unos (§9.7 — `ACTION_LABELS`, `_REPORT_TYPE_LABELS`, `ACTION_HUE_ORDER`
  na oba mesta, backend i frontend).
- Frontend stranica (§9.4) i graph overlay (§9.2, opciono).
- Demo seed skripta (`seed_demo_token_approval_evidence.py`) — nije tražena u ovom koraku;
  ručni test u §12.5.2 je odigrao tu ulogu za potrebe verifikacije.

---

## 13. Implementacija — Istorija odobrenja po adresi

**Zahtev:** rekonstruisati lanac `APPROVE → promena allowance-a → eventualni REVOCATION →
trenutni status` za analiziranu adresu, sa statusom **po pojedinačnom odobrenju**
(`ACTIVE`/`REVOKED`/`USED`/`UNKNOWN`), i (gde je moguće) trajanjem aktivnosti, vremenom do
opoziva, i da li je allowance korišćen pre opoziva.

**Obim:** i dalje backend-only (isti Faza-1 okvir kao §12) — nova, samostalna funkcija +
jedna nova pasivna (read-only) ruta, izgrađena **na vrhu** postojeće `analyze_token_approvals`
logike (§12), bez ijedne izmene Taint/Graph/Pathfinding/Behavioral/DEX Swap koda.

### 13.1 Novi/izmenjeni fajlovi

| Fajl | Šta je dodato |
|---|---|
| `backend/app/analytics/token_approval_analysis.py` (izmenjen — dodato, ništa obrisano) | Nova funkcija `build_token_approval_history()`; `_build_group_result()` prošireno da svaki pojedinačni `approvals[]` unos nosi i istorijska polja (§13.2); nove status konstante `STATUS_ACTIVE`/`STATUS_REVOKED`/`STATUS_USED`/`STATUS_UNKNOWN`. |
| `backend/app/api/routes/cases.py` (izmenjen — dodato, ništa obrisano) | Nova ruta `get_case_token_approval_history` (§13.4), dodat import `build_token_approval_history`. |
| `backend/tests/test_token_approval_analysis.py` (izmenjen — dodato) | 11 novih testova: `TestPerApprovalHistoryStatus` (7) + `TestApprovalHistory` (4) — ukupno sad **36** testova u fajlu. |

Ništa iz `graph_building.py`/`case_graph.py`/`path_finding.py`/`behavioral_analysis.py`/
`dex_swap_analysis.py`/`plugins/*.py`/`ingestion.py` nije dirano — nula izmena, isto kao §12.

### 13.2 Status po pojedinačnom odobrenju — tačna definicija (bitna odluka, obrazložena)

Svaki `approve()`/`permit()` red u grupi (već postojeće grupisanje iz §12 —
`(owner, spender, token)`, hronološki sortirano) sada dobija SVOJ **`status`**, odvojeno od
postojećeg `sequence_status` (koji ostaje nepromenjen — `superseded`/`revoked`/`active`,
koristi ga §12's grupni prikaz). Ovo NIJE bilo eksplicitno u prvobitnom §8.3 predlogu —
dizajnirano i obrazloženo sada, jer je zahtevan zatvoren skup od tačno četiri vrednosti:

```
event.amount == 0                              → REVOKED   (ovaj red JESTE čin opoziva)
transferFrom pouzdano povezan sa ovim odobrenjem → USED      (bez obzira da li je kasnije i superseded/revoked)
odobrenje NIJE poslednje u grupi (postoji kasniji approve/permit) → REVOKED
   (širа definicija — pokriva i eksplicitan approve(0) i "promenu" na drugi iznos;
    PRECIZNO vreme do opoziva, seconds_to_explicit_revocation, ostaje null osim kad je
    IMEDIATNO sledeći događaj baš approve(0) — vidi §13.2.1 zašto su namerno razdvojena
    dva različita pitanja pod jednim "REVOKED" labelom)
odobrenje JE poslednje, NEMA potvrđenog korišćenja, ALI postoji neatribuiran transferFrom
   istog vlasnika u njegovom vremenskom prozoru (§13.3)          → UNKNOWN
inače (poslednje, nekorišćeno, ništa sumnjivo neatribuirano)     → ACTIVE
```

**Redosled provere je gore odozgo naniže** (prvi uslov koji je tačan pobeđuje) — npr.
odobrenje koje je i korišćeno I kasnije zamenjeno novim iznosom se prijavljuje kao `USED`,
ne `REVOKED`, jer je korišćenje forenzički važnija činjenica.

#### 13.2.1 Zašto `status` i `seconds_to_explicit_revocation` namerno NE dele jedan uslov

Zahtevana su tačno četiri statusa — nema petog "CHANGED"/"SUPERSEDED" za slučaj kad je
odobrenje zamenjeno NOVIM (nenultim) iznosom, ne eksplicitnim `approve(spender, 0)`. Radije
nego da se ta dva različita događaja veštački razdvoje u status labelu (čime bi zahtevani
zatvoreni skup vrednosti bio narušen), odlučeno je:

- **`status: REVOKED`** — široko, "ovo odobrenje više nije na snazi", bilo da je razlog
  eksplicitan opoziv ILI promena iznosa. Odgovara koraku "promena allowance-a" I koraku
  "eventualni REVOCATION" iz zahtevanog dijagrama — oba завrešavaju život OVOG konkretnog
  odobrenja.
- **`seconds_to_explicit_revocation`** — usko, precizno, `None` osim kad je sledeći događaj
  BAŠ `approve(spender, 0)`. Ovo je polje koje odgovara na tačno postavljeno pitanje "koliko
  dugo je prošlo do revocation-a" — namerno strože od statusa, da bi ostalo tačno kad neko
  kasnije pita "koliko dugo JE OVO odobrenje trajalo do EKSPLICITNOG opoziva" nasuprot "koliko
  dugo do bilo koje sledeće promene".

Test koji ovo direktno pokriva:
`test_earlier_grant_superseded_by_change_is_revoked_but_has_no_explicit_revocation_time`.

### 13.3 `UNKNOWN` — konkretan, testiran slučaj (ne mrtav kod)

`UNKNOWN` se javlja **samo** za poslednje (trenutno "otvoreno") odobrenje u grupi, i samo
kad:
1. Nema nijednog **potvrđenog** (atribuiranog) `transferFrom`-a za to odobrenje, **i**
2. Postoji bar jedan **neatribuiran** `transferFrom` (iz §12.4 — `no_spender_column` ili
   `ambiguous_token_multiple_approvals`) **istog vlasnika** (owner), čiji `timestamp` pada
   u vremenski prozor ovog odobrenja (od njegovog `timestamp`-a do sledećeg događaja ili do
   "sada").

Drugim rečima: `ACTIVE` znači "nema TRAGA korišćenja, I nema ničeg neobjašnjenog u
evidenciji što bi moglo biti to korišćenje"; `UNKNOWN` znači "nema POTVRĐENOG korišćenja,
ALI postoji transferFrom u evidenciji koji bismo MOGLI propustiti zbog nedostatka
`spender_address` podatka — pošteno je reći 'ne znamo', ne 'nije korišćeno'". Ako
`spender_address` uopšte nije deklarisan u evidenciji (najčešći realan slučaj — §12.6),
SVAKO trenutno otvoreno odobrenje tog vlasnika će dobiti `UNKNOWN` umesto lažno-sigurnog
`ACTIVE`, čim postoji I JEDAN transferFrom red za tog vlasnika u odgovarajućem vremenskom
prozoru — testirano (`test_unattributed_transfer_for_same_owner_yields_unknown_not_active`),
uključujući i negativan slučaj kad je neatribuiran transfer PRE odobrenja pa ne sme da utiče
(`test_unattributed_transfer_before_approval_does_not_taint_status`).

### 13.4 Trajanje aktivnosti i vreme do opoziva

Za svako odobrenje (`approvals[]` unos, i u §12's grupnom prikazu i u §13's `history`):

| Polje | Značenje | Kad je `null` |
|---|---|---|
| `active_duration_seconds` | Koliko je OVO odobrenje bilo (ili je i dalje) na snazi | Samo za sam red opoziva (`is_zero=true`) — opoziv nema sopstveni "životni vek"; trajanje odobrenja KOJE JE ON opozvao je na TOM (prethodnom) redu. |
| `active_duration_ongoing` | `true` ako se trajanje i dalje računa "do sada" (odobrenje je i dalje poslednje u grupi i nije opoziv) | — (uvek `bool`) |
| `seconds_to_explicit_revocation` | Precizno vreme do EKSPLICITNOG `approve(spender, 0)` (§13.2.1) | Kad odobrenje nikad nije eksplicitno opozvano (samo promenjeno, ili je i dalje aktivno), ili je red sam opoziv. |
| `used_before_end` | Da li je OVO odobrenje korišćeno (transferFrom) pre nego što je prestalo da važi | `true` potvrđeno korišćeno; `false` potvrđeno nekorišćeno; `null` kad je nekorišćenje neizvesno (§13.3) ili red sam opoziv (nema šta da se "koristi"). |

`active_duration_seconds` za odobrenje koje je JOŠ na snazi (`active_duration_ongoing:
true`) računa se u odnosu na `now` — realno trenutno vreme servera, sa istom **closed-world
pretpostavkom** koju `taint_analysis.py` već eksplicitno koristi za sopstveni "zatvoreni
svet" model ("nema pristupa prilivima van uvezenih transakcija"): ovde to znači
pretpostavku da NIJE došlo do opoziva koji evidencija ne sadrži. `now` je parametrizovan
(`analyze_token_approvals(..., now=...)`/`build_token_approval_history(..., now=...)`,
podrazumevano `pd.Timestamp.now(tz='UTC')`) — isključivo radi determinističkog testiranja
(svi testovi u §13 prosleđuju fiksan `NOW`), ruta ga NE prosleđuje spolja (uvek stvarno
"sada").

### 13.5 `build_token_approval_history()` — nova funkcija

`backend/app/analytics/token_approval_analysis.py`:

```python
def build_token_approval_history(
    transactions: pd.DataFrame,
    address: str,                         # OBAVEZNO (za razliku od analyze_token_approvals)
    unlimited_threshold: float = DEFAULT_UNLIMITED_THRESHOLD,
    rapid_use_seconds: int = DEFAULT_RAPID_USE_SECONDS,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]
```

Namerno izgrađena **na vrhu** `analyze_token_approvals()` (poziva je internо sa
`target_address=address`), ne kao paralelna, nezavisna implementacija — isto grupisanje,
isto uparivanje, isti rizik-indikatori, garantovano bez rizika da se dve funkcije
"razminu" u tumačenju istih podataka. Doprinos ove funkcije je isključivo **preoblikovanje**:
spljoštava `groups[].approvals[]` u JEDAN hronološki (najstarije prvo) `history[]` niz preko
SVIH grupa te adrese (bez obzira da li se adresa u toj grupi javlja kao `owner` ili
`spender` — svaki unos nosi `role` polje koje to kaže), sa poljima tačno kako je zahtevano:
`token_address`, `spender`, `allowance_amount`, `approval_timestamp`, `transaction_hash`,
`block_number`, `status`, plus §13.4 trajanja — i `owner`/`event_type`/`unlimited_basis`/
`permit_deadline`/`permit_nonce`/`token_identified` za potpunost.

Prazna/nepostojeća adresa → `ValueError` (ista konvencija, ruta → 404/422 zavisno od uzroka
— vidi §13.6).

### 13.6 API endpoint

```
GET /api/v1/cases/{case_id}/token-approval-history?address=<OBAVEZNO>&evidence=<opciono>&unlimited_threshold=<opciono>&rapid_use_seconds=<opciono>
```

- `address` je **obavezan** query parametar (`Query(min_length=1)`, bez podrazumevane
  vrednosti) — izostavljen ⇒ FastAPI vraća `422` (validacija), pre nego što ruta uopšte
  izvrši ijednu liniju; adresa koja se nigde ne pojavljuje u evidenciji ⇒ `404` (isti
  `ValueError` mehanizam kao §12.5).
- Read-only, bez custody upisa i bez audit log unosa — isti namerni obim kao §12.6.
- Odgovor = `groups` (već filtrirano na tu adresu, identičan oblik kao §12.5, sada sa
  dodatnim §13.2/§13.4 poljima na svakom `approvals[]` unosu) **plus** flat `history[]`
  (§13.5) **plus** `entry_count`.

### 13.7 Testirano

**Jedinični testovi** (`backend/tests/test_token_approval_analysis.py`, sad 36 ukupno,
11 novih):

- `TestPerApprovalHistoryStatus` (7): jedino otvoreno odobrenje ⇒ `ACTIVE` + trajanje do
  `now`; potvrđeno korišćeno ⇒ `USED`; `approve(spender,0)` red ⇒ `REVOKED` bez trajanja;
  odobrenje neposredno pre eksplicitnog opoziva ⇒ tačno trajanje + tačno
  `seconds_to_explicit_revocation`; odobrenje zamenjeno NOVIM iznosom (ne eksplicitan
  opoziv) ⇒ i dalje `REVOKED` status, ali `seconds_to_explicit_revocation: null` (§13.2.1);
  neatribuiran transfer istog vlasnika u prozoru odobrenja ⇒ `UNKNOWN`, ne `ACTIVE`;
  neatribuiran transfer PRE odobrenja ⇒ ne utiče (i dalje `ACTIVE`).
- `TestApprovalHistory` (4): prazna adresa ⇒ `ValueError`; istorija sortirana hronološki
  preko više grupa (različiti spenderi); `role` tačno označava owner/spender; svaki unos
  nosi pun skup traženih polja.

```bash
python -m pytest backend/tests/test_token_approval_analysis.py -v   # 36 passed
python -m pytest backend/ -q                                        # 338 passed (302 + 36), 0 failed
```

**End-to-end kroz pravu rutu** (`TestClient`): CSV sa jednim odobrenjem, jednim
`transferFrom`, i jednim `approve(spender, 0)` opozivom → `GET .../token-approval-history
?address=0xOwner` vraća tačno: prvo odobrenje `status: "USED"`,
`active_duration_seconds: 86400.0` (razmak do opoziva), `seconds_to_explicit_revocation:
86400.0`, `used_before_end: true`; drugi (opoziv) red `status: "REVOKED"`,
`active_duration_seconds: null`. Bez `address` parametra → `422`. Sa nepostojećom adresom →
`404`. Grupni `current_status` (§12) ostaje `"revoked"`, u skladu sa §13's per-event
statusima — dva prikaza (grupni sažetak iz §12 i istorijski niz iz §13) se ne razilaze, jer
dele isti izvor podataka (§13.5).

### 13.8 Ograničenja (dodatna, specifična za istoriju)

- `UNKNOWN` je namerno **konzervativan** signal — javlja se i kada je stvarna verovatnoća
  da je neatribuiran transfer baš OVO odobrenje mala (npr. mnogo drugih grupa istog
  vlasnika bi podjednako moglo biti "osumnjičeno"). Bolje prijaviti neizvesnost nego lažnu
  sigurnost (`ACTIVE`), ali analitičar treba da zna da `UNKNOWN` NE znači "verovatno
  korišćeno", samo "ne može se isključiti".
- `active_duration_seconds` za `active_duration_ongoing: true` redove je vezano za realno
  vreme servera u trenutku poziva (closed-world pretpostavka, §13.4) — dva poziva iste rute
  minut razmaka će vratiti (blago) različit broj za isto, i dalje otvoreno odobrenje; ovo
  je očekivano ponašanje, ne nekonzistentnost.
- I dalje važi sve iz §12.6 (nema on-chain izvora, `amount` je float64, nema registra
  decimala, itd.) — §13 ne uvodi nijedan nov izvor podataka, samo novi način tumačenja
  već izvučenih polja.
- Van obima (nepromenjeno iz §12.6): custody/PDF izveštaj/audit log/frontend/graph overlay
  za ovu istoriju takođe nisu urađeni u ovom koraku.

---

## 14. Implementacija — Korelacija Approval ↔ transferFrom

**Zahtev:** za SVAKI approval pokušati pronaći povezane transferFrom aktivnosti duž lanca
`OWNER → APPROVAL → SPENDER → kasniji transferFrom → token transfer`, sa detaljima (approval
timestamp, first/last use, broj i ukupan iznos transferFrom transakcija, odredišta, tx
heševi) i **kombinovanim** statusom: `APPROVED` / `APPROVED + USED` / `APPROVED + REVOKED` /
`APPROVED + USED + REVOKED` / `UNKNOWN` — bez nagađanja kad podaci nisu dovoljni.

**Obim:** i dalje backend-only, Faza 1 okvir (isti kao §12/§13) — nova funkcija + nova
pasivna ruta, izgrađena na vrhu postojeće `analyze_token_approvals`/§13 logike. Nula izmena
Taint/Graph/Pathfinding/Behavioral/DEX Swap koda.

### 14.1 Novi/izmenjeni fajlovi

| Fajl | Šta je dodato |
|---|---|
| `backend/app/analytics/token_approval_analysis.py` (izmenjen — dodato, ništa obrisano) | Nova funkcija `correlate_approval_usage()` + helperi `_correlation_status()`/`_transfer_summary()`; `_build_group_result()` dodatno prošireno da svaki `approvals[]` unos nosi i `linked_transfers` (transferFrom redovi upareni baš sa TIM odobrenjem), `revocation_transaction_hash`, `revocation_timestamp`. |
| `backend/app/api/routes/cases.py` (izmenjen — dodato) | Nova ruta `get_case_token_approval_correlation` (§14.4), dodat import `correlate_approval_usage`. |
| `backend/tests/test_token_approval_analysis.py` (izmenjen — dodato) | 8 novih testova (`TestApprovalUsageCorrelation`) — ukupno sad **44** testa u fajlu. |

### 14.2 Zašto se ne ponavlja ekstrakcija — i zašto je status OVDE drugačiji od §13

`correlate_approval_usage()` poziva `analyze_token_approvals()` interno (isti obrazac kao
`build_token_approval_history()`, §13.5) — isto grupisanje, isto uparivanje, isti podaci,
garantovano bez razmimoilaženja između tri različita prikaza (§12 grupni sažetak, §13
istorija, §14 korelacija) nad istom evidencijom.

**Bitna, namerna razlika od §13:** §13's `status` (ACTIVE/REVOKED/USED/UNKNOWN) je
**jednorečan**, biran po prioritetu (USED pobeđuje REVOKED — "korišćeno pa opozvano" se u
§13 prikazuje samo kao `USED`, jer je korišćenje forenzički važnije za brz pregled).
Zahtev ovde traži **kombinovan** status koji SVE relevantne činjenice prikazuje ODJEDNOM
(`APPROVED + USED + REVOKED` je stvarno različito stanje od `APPROVED + REVOKED` — jedno
znači "iscrpljeno pa zatvoreno", drugo "zatvoreno a NIKAD dirano"). Zato `_correlation_status()`
gradi status iz DVA nezavisna, već izračunata polja (nijedna nova heuristika):

```
used_before_end (§13, tri-state True/False/None)         → REVOKED signal:
seconds_to_explicit_revocation (§13.2.1, precizno)         → is not None
```

```python
if used_before_end is None:
    return 'UNKNOWN'            # ne nagađa - vidi §14.3
parts = ['APPROVED']
if used_before_end: parts.append('USED')
if seconds_to_explicit_revocation is not None: parts.append('REVOKED')
return ' + '.join(parts)
```

`revoked` komponenta ovde koristi **strogu** definiciju iz §13.2.1 (samo eksplicitan
`approve(spender, 0)` koji NEPOSREDNO sledi) — namerno UŽE od §13's opšteg `REVOKED` statusa
(koji pokriva i "zamenjeno drugim iznosom"). Obrazloženje: `APPROVED + REVOKED` treba da
znači "vlasnik je svesno zatvorio ovaj pristup", ne "neko je slučajno odobrio veći/manji
iznos kasnije" — ta druga situacija nije "revocation" u smislu koji zahtev traži.

### 14.3 `UNKNOWN` — ista, već testirana osnova (§13.3), ne novi mehanizam

Korelacija NE uvodi novu neizvesnost — koristi identičan `used_before_end` tri-state signal
iz §13.3 (neatribuiran `transferFrom` istog vlasnika u vremenskom prozoru odobrenja ⇒ ne
može se pouzdano reći "nekorišćeno"). Kad je `used_before_end is None`, `status` je
**isključivo** `'UNKNOWN'` — nikad `'APPROVED + REVOKED'` ili bilo koja druga kombinacija
koja bi prećutno tvrdila "sigurno nekorišćeno". Ovo je direktna primena zahtevanog "ako
podaci nisu dovoljni da se nešto pouzdano poveže, nemoj nagađati" — testirano
(`test_unconfirmable_usage_is_unknown_not_guessed`).

### 14.4 Šta ulazi u korelaciju, šta ne

- **Jedan zapis po NENULTOM approve()/permit() redu** ("grant") — `approve(spender, 0)` red
  sam po sebi **nije** grant za korelaciju (nema šta da se "koristi"), pa se preskače
  (`test_revocation_row_itself_produces_no_correlation_entry`). Njegov uticaj se i dalje
  vidi — na PRETHODNOM grant-u, kroz `revoked`/`seconds_to_revocation`/
  `revocation_transaction_hash`/`revocation_timestamp`.
- Svako polje iz zahteva mapirano na već izvučene/izračunate podatke — ništa novo
  pogađano:

| Traženo polje | Izvor |
|---|---|
| `approval_timestamp`, `approval_transaction_hash`, `approval_block_number`, `approval_amount` | direktno sa approval reda (§12) |
| `first_use_timestamp` / `time_to_first_use_seconds` | prvi (hronološki) upareni `transferFrom` iz `linked_transfers` (§12.4's uparivanje) |
| `transfer_from_count` / `total_amount_transferred` | `len()`/`sum()` nad istim `linked_transfers` |
| `first_transfer_from` / `last_transfer_from` | prvi/poslednji element već hronološki sortiranog `linked_transfers` |
| `receiving_destinations` | skup različitih `recipient` vrednosti iz `linked_transfers` (isto polje koje §12 već koristi za `related_addresses`, ovde po pojedinačnom odobrenju umesto po celoj grupi) |
| `transaction_hashes` | `{approval, revocation, transfer_from: [...]}` — sve već poznati heševi, samo sabrani na jedno mesto |
| `status` | §14.2 |

Test `test_first_last_count_total_and_destinations_across_multiple_transfers` proverava
sve gorenavedeno odjednom, sa tri `transferFrom` reda na dva različita odredišta.

### 14.5 API endpoint

```
GET /api/v1/cases/{case_id}/token-approval-correlation?address=<opciono>&evidence=<opciono>&unlimited_threshold=<opciono>&rapid_use_seconds=<opciono>
```

- `address` je **opciono** (za razliku od §13's istorije, koja zahteva adresu) — izostavljeno,
  korelira SVAKO odobrenje u evidenciji; zadato, ograničava na odobrenja gde je ta adresa
  owner ili spender (isti obrazac kao `analyze_token_approvals`, §12.5) — `404` ako se
  adresa nigde ne pojavljuje.
- Read-only, bez custody upisa i bez audit log unosa — isti namerni obim kao §12.6/§13.8.
- Odgovor: `{address, correlation_count, correlations: [...], unattributed_transfers,
  data_completeness, unlimited_threshold, rapid_use_seconds, disclaimer, case_id, evidence,
  generated_at}`.

### 14.6 Testirano

**Jedinični testovi** (`TestApprovalUsageCorrelation`, 8, ukupno **44** u fajlu): sve pet
statusnih kombinacija pojedinačno (`APPROVED`, `APPROVED + USED`, `APPROVED + REVOKED`,
`APPROVED + USED + REVOKED`, `UNKNOWN`), revocation red ne pravi svoj zapis, first/last/
count/total/destinacije/heševi preko tri transferFrom transakcije na dva odredišta, i
opciono filtriranje po adresi (owner ili spender).

```bash
python -m pytest backend/tests/test_token_approval_analysis.py -v   # 44 passed
python -m pytest backend/ -q                                        # 346 passed (302 + 44), 0 failed
```

**End-to-end kroz pravu rutu** (`TestClient`): CSV sa jednim odobrenjem (`is_unlimited:
true`), dve `transferFrom` transakcije na dva različita odredišta, i eksplicitnim
`approve(spender, 0)` opozivom → `GET .../token-approval-correlation` vraća tačno JEDAN
korelacioni zapis: `status: "APPROVED + USED + REVOKED"`, `transfer_from_count: 2`,
`total_amount_transferred` = tačan zbir, `first_transfer_from`/`last_transfer_from` sa
tačnim tx hešem/odredištem svaki, `receiving_destinations: ["0xDestA", "0xDestB"]`,
`transaction_hashes` sa sva tri heša (approval + revocation + oba transferFrom-a),
`seconds_to_revocation` tačno izračunato. `unattributed_transfers: []` (oba transferFrom
reda su imala `spender_address`, pa su pouzdano uparena).

### 14.7 Ograničenja (dodatna, specifična za korelaciju)

- Isto kao §12.6/§13.8 (nema on-chain izvora, `amount` float64, nema registra decimala) —
  §14 ne dodaje nijedan nov izvor podataka, samo treći način tumačenja već izvučenih
  approval/transferFrom polja.
- `revoked` ovde namerno NE pokriva "zamenjeno novim iznosom" (§14.2) — analitičar koji želi
  taj širi signal treba §13's `status` polje (jednorečno, `REVOKED` pokriva oba slučaja) ili
  §12's grupni `sequence_status`.
- Kad `token_address` nije deklarisan a isti (owner, spender) par ima grantove za više
  (neidentifikovanih) tokena, `linked_transfers`/korelacija nasleđuje isto ograničenje kao
  §12.4/§12.6 — transferFrom redovi bez `token_address` se uparuju po istom
  "tačno-jedan-kandidat-inače-neatribuirano" pravilu, ne po nagađanju.
- Van obima (nepromenjeno): custody/PDF izveštaj/audit log/frontend/graph overlay za ovu
  korelaciju nisu urađeni u ovom koraku.

---

## 15. Implementacija — Forenzički risk indikatori

**Zahtev:** dodati forenzičke indikatore koji pomažu istražitelju da PROCENI rizik —
**nikad** automatska tvrdnja da je adresa/ugovor malicious. Rezultat: `risk_level`
(LOW/MEDIUM/HIGH) + lista razloga, po grupi (owner, spender, token) — sa primerima
(Unlimited approval, Unknown spender, Approval later used, Large token amount transferred,
Multiple transferFrom operations, Approval remained active for long period, Approval
followed by rapid token draining, Multiple tokens approved to same spender, Multiple
addresses approving same spender — ovo poslednje "ako postoje podaci").

**Obim:** i dalje backend-only, Faza 1 (isti okvir kao §12/§13/§14). Nula izmena
Taint/Graph/Pathfinding/Behavioral/DEX Swap koda — **postojeća dva registra ponovo
korišćena, nijedan nov spisak "malicioznih" adresa nije napravljen** (vidi §15.4).

### 15.1 Novi/izmenjeni fajlovi

| Fajl | Šta je dodato |
|---|---|
| `backend/app/analytics/token_approval_analysis.py` (izmenjen — dodato, ništa obrisano) | 6 novih risk indikatora + `risk_score`/`risk_level` na svakoj grupi (`_build_group_result`); helperi `_risk_level()`, `_is_known_spender()`; 3 nova podesiva praga (`large_amount_threshold`, `multiple_transfer_threshold`, `long_active_period_seconds`) provučena kroz `analyze_token_approvals()`, `build_token_approval_history()` i `correlate_approval_usage()`; 2 nova (read-only) importa — `classify_dex_node`/`_load_known_dex_contracts` iz `dex_swap_analysis.py`, `get_known_entity` iz `address_enrichment.py`. |
| `backend/app/api/routes/cases.py` (izmenjen — dodato) | Sve tri postojeće rute (`token-approval-analysis`, `-history`, `-correlation`) dobijaju tri nova opciona query parametra i prosleđuju ih dalje. |
| `backend/tests/test_token_approval_analysis.py` (izmenjen — dodato) | 13 novih testova (`TestForensicRiskIndicators`) — ukupno sad **57** testova u fajlu. |

### 15.2 Devet indikatora — pet postojećih (§12.4, nepromenjeni) + šest novih

| Kod | Otkad postoji | Šta znači | Težina* |
|---|---|---|---|
| `unlimited_never_used` | §12 | Neograničen allowance, nikad iskorišćen | 2 |
| `unlimited_rapid_drain` | §12 | Neograničen allowance povučen ubrzo posle odobrenja | 3 |
| `revoked_after_use` | §12 | Opozvano tek posle korišćenja (informativno) | 1 |
| `active_used_never_revoked` | §12 | Aktivno i već korišćeno, nikad opozvano | 1 |
| `spender_multi_owner` | §12 | Spender odobren od **više vlasnika** — "Multiple addresses approving same spender" iz zahteva, **računa se samo ako podaci postoje** (potreban `spender_address`/derivacija iz `recipient_address` — kad ga nema, indikator prosto nikad ne upali za tu grupu, ne pretvara se u false positive) | 3 |
| `unknown_spender` | **novo** | Spender NIJE prepoznat ni u jednom postojećem lokalnom registru (§15.4) | 1 |
| `large_amount_transferred` | **novo** | Ukupno povučeno preko dozvole ≥ podesiv prag (`large_amount_threshold`, podrazumevano `1 000 000`) | 2 |
| `multiple_transferfrom_operations` | **novo** | Broj transferFrom transakcija ≥ podesiv prag (`multiple_transfer_threshold`, podrazumevano `3`) | 1 |
| `long_active_period` | **novo** | Bar jedno odobrenje u grupi bilo je na snazi ≥ podesiv prag (`long_active_period_seconds`, podrazumevano 90 dana) — računa se preko **svih** odobrenja u istoriji grupe (§13's `active_duration_seconds`), ne samo trenutno aktivnog | 1 |
| `rapid_first_use` | **novo** | Uopšteni oblik "Approval followed by rapid token draining" — brzo prvo korišćenje **običnog** (ne nužno neograničenog) odobrenja; namerno isključuje slučaj koji već pokriva `unlimited_rapid_drain` (§15.3) da ne bi dva indikatora signalizirala isto | 2 |
| `multiple_tokens_same_spender` | **novo** | Isti (owner, spender) par ima odobrenja za **2+ različita, deklarisana** tokena | 2 |

\* Težina se koristi samo za `risk_score`/`risk_level` (§15.3) — sve težine i pragovi su
otvoreno navedeni brojevi u kodu (`RISK_INDICATOR_WEIGHTS`), ne skriveni "crna kutija"
model.

Svaki indikator (staro i novo) je oblika `{code, label, reasons: [...]}` — `reasons`
UVEK objašnjava TAČNO na osnovu čega je upaljen (isti disciplina kao DEX Swap Analysis
`reasons[]`/`dex_match_basis`).

### 15.3 `risk_score` / `risk_level` — transparentna formula, ne "crna kutija"

```python
risk_score = sum(RISK_INDICATOR_WEIGHTS[indicator.code] for indicator in fired_indicators)

risk_level = 'HIGH'   if risk_score >= 5
           = 'MEDIUM' if risk_score >= 2
           = 'LOW'    inače (0 ili 1)
```

**Namerna odluka:** JEDAN usamljen jak indikator (težina 3, npr. samo `spender_multi_owner`
i ništa drugo) daje `risk_score=3` → **MEDIUM**, ne HIGH. `HIGH` zahteva KONVERGENCIJU —
dva jaka signala zajedno, ili jedan jak plus korišćenje/veličina/trajanje koji ga
potkrepljuju. Ovo direktno sprovodi zahtev "ne tvrdi automatski da je adresa/ugovor
malicious": nijedan pojedinačan heuristički signal sam po sebi ne može dogurati do
najvišeg nivoa.

Testirano: `test_two_strong_indicators_reach_high_risk_level` (spender_multi_owner +
unlimited_rapid_drain + active_used_never_revoked + unknown_spender = score 8 → HIGH),
`test_single_weak_indicator_stays_low_risk` (samo `active_used_never_revoked`, score 1 →
LOW), `test_risk_score_is_sum_of_fired_indicator_weights` (opšta provera formule).

### 15.4 `unknown_spender` / `spender_known` — ponovna upotreba POSTOJEĆIH registara, ne nov spisak

Po zahtevu "ne menjaj postojeće analize" i duhu "koristi postojeću arhitekturu", `spender`
se proverava protiv **dva već postojeća, lokalna, read-only registra** — bez ijedne izmene
njih samih:

1. `app.services.address_enrichment.get_known_entity()` — kurirani spisak poznatih
   exchange/mixer/sankcionisanih adresa (`known_entities.json`), koristi ga već
   Pathfinding.
2. `app.analytics.dex_swap_analysis.classify_dex_node()` (+ njegov
   `known_dex_contracts.json`) — ista funkcija koju DEX Swap Analysis koristi za
   prepoznavanje DEX router/pool adresa, uključujući brand-keyword fallback za demo
   pseudo-adrese.

Spender koji pogodi BILO KOJI od ova dva → `spender_known: true`, indikator
`unknown_spender` se **ne** upaljuje. Bitno: ovo je **namerno slab, informativan**
indikator (težina 1) — u realnim, ručno pripremljenim CSV podacima VEĆINA spender adresa
neće biti u ova dva mala spiska, pa će se `unknown_spender` upaljivati često. `reasons[]`
to eksplicitno kaže ("ne znači da je zloćudan..."), sprečavajući pogrešno čitanje "unknown
= sumnjivo".

**Nijedan nov "spisak poznatih drainer/phishing ugovora" nije napravljen** — tačno kao što
je već najavljeno u §8.4 kao namerno izostavljeno (nema pouzdanog izvora takvih podataka u
projektu danas).

### 15.5 API — tri nova, konzistentna query parametra na sve tri rute

```
GET /cases/{id}/token-approval-analysis
GET /cases/{id}/token-approval-history
GET /cases/{id}/token-approval-correlation
```

svaka sad prima, uz već postojeće `unlimited_threshold`/`rapid_use_seconds`:

| Parametar | Podrazumevano | Opseg |
|---|---|---|
| `large_amount_threshold` | `1 000 000` | `0 – 1e40` |
| `multiple_transfer_threshold` | `3` | `2 – 1000` |
| `long_active_period_seconds` | `7 776 000` (90 dana) | `3600 – 315 360 000` (10 godina) |

Svaka grupa u odgovoru (`groups[]`, na sve tri rute — `-history`/`-correlation` sada i
same vraćaju `groups` radi ovoga) dobija dva nova polja: `risk_score` (int),
`risk_level` (`'LOW'|'MEDIUM'|'HIGH'`), plus `spender_known` (bool). Top-level
`disclaimer` je ažuriran da EKSPLICITNO kaže: *"Svi risk_indicators i izvedeni risk_level
... su OZNAČENE HEURISTIKE za prioritizaciju pregleda - NIKAD tvrdnja da je adresa ili
ugovor zloćudan/kriminalan, i NIKAD dokaz."*

### 15.6 Testirano

**Jedinični testovi** (`TestForensicRiskIndicators`, 13, ukupno **57** u fajlu): nema
indikatora ⇒ LOW/score 0; `unknown_spender` za neprepoznatu adresu; poznata DEX router
adresa (`0x7a25...2488D`, stvarna, iz `known_dex_contracts.json`) NE prijavljuje
`unknown_spender`; `large_amount_transferred`/`multiple_transferfrom_operations` sa
podesivim pragom; `long_active_period` za staro odobrenje (i NE za skoro odobrenje, sa
istim pragom); `rapid_first_use` za običnu (ne neograničenu) dozvolu, i eksplicitna
provera da se `unlimited_rapid_drain` u tom slučaju NE dupira; `multiple_tokens_same_spender`
preko dva deklarisana tokena; `risk_score` = zbir težina; konvergencija više jakih
indikatora ⇒ HIGH; usamljen slab indikator ⇒ LOW; disclaimer eksplicitno kaže
"heuristika"/"nikad" bez tvrdnje o zloupotrebi.

```bash
python -m pytest backend/tests/test_token_approval_analysis.py -v   # 57 passed
python -m pytest backend/ -q                                        # 359 passed (302 + 57), 0 failed
```

**End-to-end kroz pravu rutu** (`TestClient`): dva vlasnika (`0xOwnerA`, `0xOwnerB`)
odobravaju isti, neprepoznat `0xShadySpender` za veliki/neograničen iznos; `0xOwnerA`-ino
odobrenje se brzo povuče. `GET .../token-approval-analysis` vraća obe grupe sa
`risk_level: "HIGH"` (score 11 za OwnerA - konvergencija `unlimited_rapid_drain` +
`active_used_never_revoked` + `spender_multi_owner` + `unknown_spender` +
`large_amount_transferred` + `long_active_period`; score 7 za OwnerB), i disclaimer
sadrži tačan tekst o heuristici.

### 15.7 Ograničenja (dodatna, specifična za risk indikatore)

- **`risk_level` je heuristički prioritizacioni signal, NIJE dokaz kriminalne
  aktivnosti** — eksplicitno ponovljeno ovde po zahtevu, ne samo u kodu/disclaimer-u.
- Težine (`RISK_INDICATOR_WEIGHTS`) i pragovi HIGH/MEDIUM su **inženjerska procena**, ne
  formalno izveden/kalibrisan model (nema označenog dataset-a stvarnih ice-phishing
  slučajeva u projektu da bi se kalibrisalo) — namerno jednostavni i transparentni radi
  objašnjivosti, ne radi statističke tačnosti.
- `large_amount_transferred`/`unlimited_*` pate od istog ograničenja kao §7.4/§12.6: bez
  registra decimala po tokenu, "veliko" je heuristika po SIROVOM broju iz evidencije, ne
  po stvarnoj (USD ili čak token) vrednosti — demonstrirano u §15.6's end-to-end primeru
  (18-decimalni raw iznosi lako prelaze podrazumevani prag).
- `spender_multi_owner`/`multiple_tokens_same_spender` su tačni SAMO u opsegu evidencije
  koja je analizirana (jedan slučaj/jedna kombinacija fajlova) — isti owner/spender par u
  DRUGOM slučaju se ne vidi (vidi IDEJE-NOVE-ANALIZE.md #1, "unakrsna provera adresa kroz
  slučajeve" — nepovezano, van obima).
- `unknown_spender` je slab, čest signal (§15.4) — NE treba ga čitati kao "sumnjivo" samo
  zato što je upaljen; `reasons[]` to eksplicitno kaže.
- Van obima (nepromenjeno): custody/PDF izveštaj/audit log/frontend prikaz risk nivoa nisu
  urađeni u ovom koraku.
