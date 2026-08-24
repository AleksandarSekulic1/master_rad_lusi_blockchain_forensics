# Behavioral Analysis (Time-of-Day) — prva verzija

Dokumentacija novog, samostalnog modula koji odgovara na pitanje **„kada je ova adresa
aktivna — po satu i danu u nedelji"** — namerno odvojeno od Graph/Taint/Pathfinding
stranica, koje odgovaraju na sasvim druga pitanja (struktura mreže, poreklo zaprljanosti,
konkretan put sredstava). Ovde se ne prati novac, nego **vremenski obrazac** ponašanja
jedne adrese.

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Zašto zasebna analiza](#1-zašto-zasebna-analiza) | razlika u odnosu na Graph/Taint/Pathfinding |
| [2. Metod](#2-metod) | UTC bucketing, prikupljanje transakcija adrese, statistike |
| [3. API](#3-api) | ruta, parametri, oblik odgovora |
| [4. Frontend stranica](#4-frontend-stranica) | šta se prikazuje i kako |
| [5. Heatmap vizuelizacija](#5-heatmap-vizuelizacija) | boje, tabela-dvojnik, pristupačnost |
| [6. Testiranje korak po korak](#6-testiranje-korak-po-korak) | automatski i ručni testovi, sa tačnim brojevima |
| [7. Ograničenja prve verzije](#7-ograničenja-prve-verzije) | šta namerno nedostaje, zašto |
| [8. Gde je šta u kodu](#8-gde-je-šta-u-kodu) | putanje |

---

## 1. Zašto zasebna analiza

| | Graph | Taint | Pathfinding | Behavioral Analysis |
|---|---|---|---|---|
| Pitanje | Kako izgleda cela mreža transakcija? | Koliki deo sredstava potiče od izvora X? | Kojim redosledom su sredstva stigla od A do B? | Kada je ova adresa aktivna — po satu/danu? |
| Ulaz | evidencija slučaja (opciono, jedan fajl) | jedan ili više seed čvorova | tačno dve adrese: From, To | tačno jedna adresa |
| Rezultat | graf čvorova/grana | procenat zaprljanosti na svakom čvoru | jedna konkretna putanja | raspodela transakcija po UTC satu i danu u nedelji |
| Vremenska dimenzija | `first_seen`/`last_seen` po grani, uzgredno | hronologija cele evidencije (haircut model) | nema (BFS je čisto strukturni) | **jedina svrha stranice** |

Sve četiri rade nad istim grafom slučaja (`build_case_graph`), ali su konceptualno
različiti nalazi — zato je ovo fizička, potpuno odvojena stranica (`/behavioral`), sa
odvojenom backend rutom i bez deljenog stanja sa ostale tri.

## 2. Metod

`backend/app/analytics/behavioral_analysis.py` → `analyze_time_of_day(graph, address)`

### 2.1 Prikupljanje transakcija jedne adrese

Graf slučaja (isti koji koriste Graph/Taint/Pathfinding) se ne gradi ponovo — funkcija
samo čita njegove postojeće grane. Za svaku granu `(source, target)` gde je `address`
pošiljalac ILI primalac, uzima se cela njena lista `transactions[]` (svaka grana može
agregirati više pojedinačnih transakcija između istog para adresa).

**Self-transfer** (adresa šalje samoj sebi) se broji **tačno jednom**, ne dvaput — pošto je
to jedna grana u usmerenom grafu (`graph.edges()` je iterisan jednom po paru, ne odvojeno
kroz `in_edges`/`out_edges`), a ne dva odvojena „događaja".

### 2.2 UTC bucketing — bez pretpostavki o vremenskoj zoni

Svaki timestamp se parsira preko `pd.to_datetime(value, utc=True)` (ista funkcija koju već
koristi `anomaly_detection.py`) — rezultat je **uvek** UTC, bez obzira kog je offseta bio
originalni zapis (`+02:00`, `-05:00`, ...). Sat (`0–23`) i dan u nedelji se čitaju direktno
iz te UTC vrednosti.

**Namerno NEMA zaključivanja vremenske zone ili kontinenta** iz obrasca aktivnosti — to je
zaseban, kasniji korak (ako se ikad doda), ne deo ove prve verzije.

### 2.3 Raspodele — uvek popunjene, nikad delimične

- `hourly_distribution` — svih **24** sata (`"00".."23"`), i onih sa 0 transakcija.
- `day_of_week_distribution` — svih **7** dana (`Monday..Sunday`, taj redosled), i onih sa
  0 transakcija.
- `hour_by_day_distribution` — ista 7×24 mreža, ugnježdena po danu pa po satu — tačno ono
  što heatmap iscrtava, bez ijedne dodatne transformacije na frontend-u.

Zero-fill nije kozmetički detalj: bez njega bi npr. „nema transakcija u 14h" i „14h uopšte
ne postoji u odgovoru" izgledali isto na frontend-u, a to su dve različite tvrdnje.

### 2.4 Statistike

| Polje | Definicija |
|---|---|
| `most_active_hour` / `most_active_hour_count` | sat (agregirano preko **svih** dana) sa najviše transakcija |
| `most_active_day` / `most_active_day_count` | dan u nedelji (agregirano preko **svih** sati) sa najviše transakcija |
| `peak_period` | **jedna konkretna** (dan, sat) ćelija sa najviše transakcija — uža, specifičnija tvrdnja od prethodne dve (vidi §6.2 primer gde se to jasno razlikuje) |
| `total_analyzed_transactions` | isto što i `total_transactions` na vrhu odgovora |

**Remi se rešava deterministički** — bira se raniji ključ po fiksnom redosledu (sat `00`
pre `23`, dan `Monday` pre `Sunday`), nikad zavisno od redosleda grana u grafu (isti
princip kao `path_finding.find_path_to_nearest_of`, vidi PATHFINDING-ANALIZA.md §6.2).

### 2.5 Adresa mora tačno da postoji u grafu

Isti princip kao `path_finding.find_transaction_paths` — adresa se traži **tačnim**
poklapanjem sa ID-jem čvora u grafu (case-sensitive), ne normalizovano na malа slova.
Nepostojeća adresa → `ValueError` → `404`, ne tih prazan rezultat.

## 3. API

```
GET /api/v1/cases/{case_id}/behavioral-analysis?address=0x...&evidence=<opciono>
```

Read-only ruta — isti tretman kao `GET /cases/{id}/graph` i `GET
/cases/{id}/seed-suggestions`: nema dijaloga za lanac dokaza i ne piše u `custody_log`, jer
samo ponovo čita već izgrađen graf slučaja, ne pokreće nikakvu novu analitičku obradu nad
evidencijom.

Odgovor:
```json
{
  "case_id": "46ae7f91db9b",
  "evidence": null,
  "address": "0xNightOwlWallet",
  "total_transactions": 10,
  "hourly_distribution": { "00": 0, "01": 0, "02": 3, "03": 6, "04": 1, "05": 0, "...": 0 },
  "day_of_week_distribution": { "Monday": 2, "Tuesday": 1, "Wednesday": 4, "Thursday": 1, "Friday": 1, "Saturday": 1, "Sunday": 0 },
  "hour_by_day_distribution": {
    "Monday": { "00": 0, "...": 0, "02": 1, "03": 1, "...": 0 },
    "Wednesday": { "02": 1, "03": 2, "04": 1, "...": 0 },
    "...": { "...": 0 }
  },
  "stats": {
    "most_active_hour": "03",
    "most_active_hour_count": 6,
    "most_active_day": "Wednesday",
    "most_active_day_count": 4,
    "peak_period": { "day": "Wednesday", "hour": "03", "count": 2, "label": "Wednesday 03:00 UTC" },
    "total_analyzed_transactions": 10
  },
  "generated_at": "2026-08-24T00:03:55+00:00"
}
```
(skraćeno radi čitljivosti — `hourly_distribution` uvek ima svih 24 ključa, `hour_by_day_distribution` svih 7×24)

`evidence` je opciono (isti obrazac kao Graph/Taint/Pathfinding): izostavljeno ili prazno =
sva evidencija slučaja kombinovana; `stored_name` konkretnog fajla = samo taj fajl.
Nepostojeća adresa u obuhvaćenoj evidenciji → `404`.

## 4. Frontend stranica

Ruta `/behavioral`, link **„Behavioral"** u glavnom meniju.

- Aktivan slučaj + birač evidencije (isti obrazac kao Graph/Taint/Pathfinding) — bez
  automatskog učitavanja grafa na platnu, jer ova stranica ne crta mrežu (nema
  cytoscape-a).
- Polje **Address** + dugme **ANALYZE** — jedina dva unosa na stranici.
- Nakon analize: **Activity Heatmap** (§5) kao glavni sadržaj, i uz nju samo **četiri**
  statistike (Most active hour, Most active day, Peak activity, Active period) — namerno
  bez dodatnih kartica/filtera, po zahtevu da stranica ostane jednostavna.
- `Active period` **nije backend polje** — računa se na frontend-u kao raspon od
  najranijeg do najkasnijeg UTC sata sa bilo kakvom aktivnošću (agregirano preko svih
  dana), iz `hourly_distribution` koji stranica već ima učitan. Vidi §7 za posledicu ove
  definicije (jedna izdvojena transakcija ume znatno da proširi prikazani raspon).

## 5. Heatmap vizuelizacija

- **Jedna nijansa boje** (ista cyan porodica kao postojeći `#7dd3fc` akcent u aplikaciji),
  tamno→svetlo za malo→mnogo transakcija u toj (dan, sat) ćeliji — sekvencijalno
  kodiranje magnitude, ne kategorijalno.
- Ćelija sa **0** transakcija je namerno vizuelno drugačija (ravna, van skale boje) od
  „najmanje aktivne, ali ipak aktivne" ćelije — ista razlika kao u GitHub-ovom
  contribution grafu, da „nema aktivnosti" nikad ne izgleda kao „najbleđi nivo aktivnosti".
- Svaka ćelija je fokusabilno dugme (tastatura + miš), sa `title`/`aria-label` koji čita
  tačan dan, sat i broj transakcija.
- **„Prikaži kao tabelu"** (skriveno po default-u) ispod heatmap-e — ista 7×24 mreža kao
  obična HTML tabela sa brojevima, pristupačan dvojnik za heatmap koji enkodira vrednost
  isključivo bojom.

## 6. Testiranje korak po korak

### 6.1 Automatski testovi

```bash
python -m pytest backend/tests/test_behavioral_analysis.py -v
```

11 testova: brojanje transakcija gde je adresa pošiljalac/primalac, self-transfer brojan
tačno jednom, zero-fill svih 24 sata i svih 7 dana, tačno postavljanje u (dan, sat) ćeliju,
UTC konverzija iz ne-UTC ulaznog timestamp-a (`-05:00` → tačan UTC sat i dan), najaktivniji
sat/dan sa brojem, `peak_period` kao konkretna (dan, sat) ćelija (različita od
najaktivnijeg sata samog za sebe), deterministički tie-break, `404` za adresu koja ne
postoji u grafu, i da se adresa traži tačnim poklapanjem (case-sensitive).

### 6.2 Ručna provera kroz UI (demo podaci sa unapred poznatim odgovorom)

Isti slučaj koji koriste i Taint i Pathfinding dokumentacija — **„Demo: Sumnjiva
laundering sema (hakovan novcanik)"** — sad ima i dva dokaza napravljena baš za ovaj modul
(`backend/scripts/seed_demo_behavioral_evidence.py`), oba za adresu `0xNightOwlWallet`:

**`demo_behavioral_analysis.csv`** (10 redova) — uzak „noćni" obrazac, sve unutar
02:00–04:00 UTC, najgušće sredom u 03h:
```
sender_address,recipient_address,amount,timestamp
0xNightOwlWallet,0xExchangeCounterparty,50,2026-08-24T02:15:00Z
0xPeerWalletA,0xNightOwlWallet,30,2026-08-24T03:05:00Z
0xNightOwlWallet,0xExchangeCounterparty,45,2026-08-25T03:10:00Z
0xNightOwlWallet,0xPeerWalletB,20,2026-08-26T02:40:00Z
0xNightOwlWallet,0xExchangeCounterparty,60,2026-08-26T03:00:00Z
0xPeerWalletA,0xNightOwlWallet,25,2026-08-26T03:20:00Z
0xNightOwlWallet,0xPeerWalletB,15,2026-08-26T04:00:00Z
0xNightOwlWallet,0xExchangeCounterparty,55,2026-08-27T03:30:00Z
0xPeerWalletA,0xNightOwlWallet,35,2026-08-28T02:50:00Z
0xNightOwlWallet,0xExchangeCounterparty,40,2026-08-29T03:15:00Z
```
(2026-08-24 je ponedeljak, 08-26 sreda, 08-29 subota)

**`demo_behavioral_analysis_outlier.csv`** (1 red) — jedna izdvojena dnevna transakcija,
za §6.2 Test B:
```
sender_address,recipient_address,amount,timestamp
0xNightOwlWallet,0xExchangeCounterparty,10,2026-08-25T14:00:00Z
```

Svaki broj ispod je stvarno izračunat pokretanjem `analyze_time_of_day()` nad ovim tačnim
sadržajem (ne izračunat ručno), uključujući i kroz stvarno seed-ovanu evidenciju slučaja.

**Test A — samo osnovni obrazac (jedan fajl):**

1. **Slučajevi** → izaberi „Demo: Sumnjiva laundering sema (hakovan novcanik)".
2. **Behavioral** → u „Prikaz transakcija" izaberi `demo_behavioral_analysis.csv` (NE
   „Sve transakcije (kombinovano)").
3. Address: `0xNightOwlWallet` → **ANALYZE**.
4. Očekivano:
   - **Most active hour: 03:00 UTC**, **Peak activity: 6 transactions**
   - **Most active day: Wednesday**
   - **Active period: 02:00–04:00 UTC** — uzan, jer je SVA aktivnost u ovom fajlu unutar
     02–04h.
   - Na heatmap-i: samo tri kolone (02, 03, 04) imaju boju, sve ostale su prazne; najsvetlija
     ćelija je Wed/03 (2 transakcije — to je `peak_period`, konkretna ćelija, ne isto što i
     „Most active hour" koji sabira 03h **preko svih dana** = 6).
   - „Prikaži kao tabelu": red Wed, kolona 03 = **2**; ukupan zbir svih ćelija = **10**.

**Test B — sa dodatim izdvojenim danom (kombinovana evidencija):**

5. „Prikaz transakcija" → **„Sve transakcije (kombinovano)"** (uključuje i
   `demo_behavioral_analysis_outlier.csv`, i sva ostala evidencija ovog slučaja — nijedna
   od nje ne pominje `0xNightOwlWallet`, pa ne utiče na rezultat).
6. Address: `0xNightOwlWallet` → **ANALYZE**.
7. Očekivano:
   - **Most active hour**, **Peak activity** i **Most active day** ostaju **nepromenjeni**
     (03:00 UTC / 6 transactions / Wednesday) — jedna dodatna transakcija u 14h ne dostiže
     najgušće sate.
   - **Active period se širi na 02:00–14:00 UTC** — jedna jedina transakcija van osnovnog
     obrasca razvlači prikazani raspon skoro preko cele polovine dana. Ovo je namerna
     demonstracija ograničenja iz §7: „Active period" je **omotač** (najraniji→najkasniji
     aktivan sat), ne procena „tipičnog prozora".
   - Heatmap dobija još jednu, usamljenu ćeliju u koloni 14 — u redu **Tuesday**, jer je
     2026-08-25 utorak (dan te dodate transakcije).
   - Ukupan zbir u „Prikaži kao tabelu": **11**.

## 7. Ograničenja prve verzije

Namerno izostavljeno iz ove verzije (videti zahtev — dodaje se tek kad zatreba):

- **Samo UTC** — nema zaključivanja vremenske zone ili kontinenta iz obrasca aktivnosti
  (§2.2). Analitičar koji radi u drugoj zoni mora sam da preračuna „lokalno" vreme.
- **„Active period" je omotač, ne procena tipičnog prozora** — najraniji→najkasniji UTC sat
  sa BILO KAKVOM aktivnošću (agregirano preko svih dana), pa ga jedna usamljena
  transakcija van glavnog obrasca može znatno raširiti — demonstrirano konkretno u §6.2
  Test B (uzan 02:00–04:00 postaje 02:00–14:00 zbog JEDNE dodatne transakcije). Nije
  klasterovanje niti "gustina po prozoru" — to bi bila druga, složenija metrika.
  Trenutno je jedina agregacija po satu-dana, ne i „najgušći N-časovni prozor".
- **Bez unakrsnog poređenja sa ostalim analitičkim modulima** — ne kombinuje se
  automatski sa Taint/anomaly_detection nalazima (npr. „da li je ovaj obrazac aktivnosti
  neuobičajen za adrese slične ove"). Heatmap pokazuje sirovu vremensku raspodelu, ništa
  se ne ocenjuje kao „sumnjivo" na osnovu nje.
- **Adresa se traži tačnim poklapanjem** (case-sensitive), isto kao Pathfinding — ne
  normalizuje se na mala slova (§2.5).
- **Nema PDF izveštaja** (za razliku od Taint/Pathfinding) — prva verzija je samo prikaz na
  ekranu; izveštaj sa potpisom/pečatom/kontrolnim brojem može se dodati kasnije po istom
  obrascu (`report_registry.py` je već generički, vidi PATHFINDING-ANALIZA.md §8.2).
- **Bez dijaloga za lanac dokaza** — read-only pregled već izgrađenog grafa (§3), ne
  pokreće se nova obrada nad evidencijom, pa se ne beleži u `custody_log` (za razliku od
  „Pokreni taint analizu"/„FIND PATH").

## 8. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| Algoritam (bucketing, statistike) | `backend/app/analytics/behavioral_analysis.py` (`analyze_time_of_day`) |
| Ruta | `backend/app/api/routes/cases.py` (`get_case_behavioral_analysis`) |
| Testovi | `backend/tests/test_behavioral_analysis.py` |
| Demo podaci (§6.2) | `backend/scripts/seed_demo_behavioral_evidence.py` |
| Frontend stranica | `frontend/src/app/features/behavioral-analysis/` |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`getBehavioralAnalysis`) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`BehavioralAnalysisResult`, `BehavioralAnalysisStats`, `BehavioralAnalysisPeakPeriod`) |

**Ruta:**

| Ruta | Namena |
|---|---|
| `GET /api/v1/cases/{id}/behavioral-analysis` | UTC vremenski obrazac (sat × dan u nedelji) jedne adrese unutar evidencije slučaja |
