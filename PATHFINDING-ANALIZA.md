# Pathfinding Analiza — prva verzija (BFS)

Dokumentacija novog, samostalnog modula koji odgovara na pitanje **„kojim putem se sredstva
kreću između dve tačno određene adrese"** — namerno odvojeno od Taint analize, koja
odgovara na drugo pitanje: „kako se zaprljana sredstva propagiraju kroz celu mrežu".

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Zašto zasebna analiza](#1-zašto-zasebna-analiza) | razlika u odnosu na Taint analizu |
| [2. Algoritam](#2-algoritam-bfs) | BFS, usmerenost, šta prva verzija namerno nema |
| [3. API](#3-api) | ruta, telo zahteva, oblik odgovora |
| [4. Frontend stranica](#4-frontend-stranica) | šta se prikazuje i kako |
| [5. Testiranje korak po korak](#5-testiranje-korak-po-korak) | UI i automatski testovi |
| [6. Ograničenja prve verzije](#6-ograničenja-prve-verzije) | šta namerno nedostaje, zašto |
| [7. Gde je šta u kodu](#7-gde-je-šta-u-kodu) | putanje |

---

## 1. Zašto zasebna analiza

| | Taint analiza | Pathfinding analiza |
|---|---|---|
| Pitanje | Koliki deo sredstava na ovoj adresi potiče od izvora X? | Kojim redosledom adresa su sredstva stigla od A do B? |
| Ulaz | jedan ili više seed čvorova | tačno dve adrese: From, To |
| Rezultat | procenat zaprljanosti na SVAKOM čvoru grafa | JEDNA konkretna putanja (niz adresa) između dve adrese |
| Model | proporcionalni haircut, hronologija cele evidencije | nezatežena (BFS) najkraća putanja, bez vremenske dimenzije |

Obe rade nad istim grafom slučaja, ali su konceptualno različiti nalazi — spajanje u istu
stranicu bi zamaglilo tu razliku, zato su fizički odvojene (`/taint` i `/pathfinding`),
sa odvojenim backend rutama i bez deljenog stanja.

## 2. Algoritam (BFS)

`backend/app/analytics/path_finding.py` → `bfs_shortest_path(graph, from_address, to_address)`

- **Nezatežena** (unweighted) pretraga u širinu — prva pronađena putanja je i najkraća po
  broju skokova (nema pojma o iznosu transakcije, prvoj verziji to i ne treba).
- **Usmerena** — ide isključivo preko `graph.successors()`, tj. u smeru pošiljalac →
  primalac. Grana `B → A` ne znači da postoji put `A → B`; to je namerno, jer prati stvaran
  smer kretanja sredstava, ne samo povezanost adresa.
- Posebni slučajevi: ista adresa za `from` i `to` → put dužine 0 (nije greška); adresa koja
  uopšte nije u grafu (tipfeler, ili van izabrane evidencije) → `found: false`, bez greške.

## 3. API

```
POST /api/v1/cases/{case_id}/pathfinding?evidence=<opciono>
```

Telo zahteva:
```json
{ "from": "0x...", "to": "0x..." }
```

Odgovor (uvek tačno ova tri polja, ništa više):
```json
{ "found": true, "path": ["0x...", "0x...", "0x..."], "hops": 2 }
```
ili
```json
{ "found": false, "path": [], "hops": 0 }
```

Graf se gradi isto kao za Graf/Taint stranicu (`build_case_graph` nad evidencijom
slučaja — ceo obuhvaćen materijal, ili jedan fajl ako je `evidence` naveден). Svako
pokretanje se beleži u log aktivnosti pod već postojećom akcijom `path_finding`, sad sa
`case_id` (vidljivo na stranici „Log aktivnosti").

## 4. Frontend stranica

Ruta `/pathfinding`, link „Pathfinding" u glavnom meniju.

- Aktivan slučaj + birač evidencije (isti obrazac kao Graf stranica) — graf se učitava
  **automatski**, bez boja po riziku (Pathfinding ne pokreće analitički pipeline, pa nema
  potrebe ni za dijalogom za lanac dokaza — to je rezervisano za Taint analizu i „Analiziraj
  graf").
- Polja **From** / **To** + dugme **FIND PATH**.
- Kad je put pronađen: broj skokova, vertikalna lista adresa (`Address A ↓ Address B ↓ ...`),
  i na grafu se boji **samo** ta putanja (cijan, isti vizuelni jezik kao isticanje putanje u
  Taint analizi), dok se ostatak grafa zatamni.
- Kad put nije pronađen: jasna poruka, graf ostaje u normalnom (nezatamnjenom) prikazu.

## 5. Testiranje korak po korak

### 5.1 Automatski testovi

```bash
python -m pytest backend/tests/test_path_finding_bfs.py -v
```

9 testova: direktan put, put kroz više skokova, biranje kraćeg puta kad postoji i duži,
poštovanje smera grane (BFS ne ide unazad), nepovezane adrese, adresa koja ne postoji u
grafu, ista adresa sa oba kraja, i da sama ruta gradi graf od evidencije i vraća tačan oblik
`{found, path, hops}`.

### 5.2 Ručna provera kroz UI (demo podaci sa poznatim odgovorom)

Isti demo slučaj koji koristi i Taint analiza (vidi `TAINT-ANALIZA.md`, §3.1) — **„Demo:
Sumnjiva laundering sema"**, evidencija `demo_taint_dilution.csv`:

```
sender_address,recipient_address,amount,timestamp
0xThief,0xMixer,1000,2026-03-01T00:00:00Z
0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z
0xMixer,0xExitWallet,750,2026-03-01T00:10:00Z
0xHacker1,0xLaunderingHub,600,2026-04-01T00:00:00Z
0xHacker2,0xLaunderingHub,400,2026-04-01T00:05:00Z
0xLaunderingHub,0xFinalDestination,800,2026-04-01T00:10:00Z
0xExchangeHacker,0xExchangeMule,200,2026-06-01T00:00:00Z
0xExchangeMule,0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be,200,2026-06-01T00:05:00Z
```

**Test A — direktan put kroz jednog posrednika:**

1. **Slučajevi** → izaberi „Demo: Sumnjiva laundering sema (hakovan novcanik)" (postaje aktivan slučaj).
2. **Pathfinding** → u „Prikaz transakcija" izaberi `demo_taint_dilution.csv` (ili ostavi
   „Sve transakcije (kombinovano)").
3. From: `0xThief`, To: `0xExitWallet` → **FIND PATH**.
4. Očekivano: **2 skoka**, putanja `0xThief → 0xMixer → 0xExitWallet`. Na grafu su ta tri
   čvora i dve grane obojeni cijan, ostatak zatamnjen.

**Test B — dve različite adrese vode do istog posrednika:**

5. From: `0xCleanUser`, To: `0xExitWallet` → **FIND PATH**.
6. Očekivano: **2 skoka**, `0xCleanUser → 0xMixer → 0xExitWallet` — potvrđuje da BFS
   pronalazi put nezavisno od toga koji je pošiljalac, sve dok grane stvarno postoje.

**Test C — smer se poštuje (ne postoji obrnut put):**

7. From: `0xExitWallet`, To: `0xThief` → **FIND PATH**.
8. Očekivano: **„Putanja nije pronađena"** — `0xExitWallet` samo prima, nikad ne šalje u
   ovoj evidenciji, pa nema izlazne grane. Ovim se potvrđuje da graf poštuje stvaran smer
   transakcija, ne samo povezanost.

**Test D — potpuno nepovezane adrese:**

9. From: `0xThief`, To: `0xFinalDestination` → **FIND PATH**.
10. Očekivano: **„Putanja nije pronađena"** — `0xThief`-ova grana i
    `0xHacker1/0xHacker2 → 0xLaunderingHub → 0xFinalDestination` grana su dve nezavisne
    šeme u istom fajlu, bez ijedne veze između njih.

**Test E — druga nezavisna šema, dva izvora spajaju se u istog primaoca:**

11. From: `0xHacker1`, To: `0xFinalDestination` → **FIND PATH**.
12. Očekivano: **2 skoka**, `0xHacker1 → 0xLaunderingHub → 0xFinalDestination`.

**Test F — put ka stvarnoj berzanskoj adresi:**

13. From: `0xExchangeHacker`, To: `0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be` → **FIND PATH**.
14. Očekivano: **2 skoka**, preko `0xExchangeMule` — ova adresa je stvarna Binance adresa
    (vidi TAINT-ANALIZA.md §5.10), pa se ovaj put može uporediti sa istim nalazom koji Taint
    analiza daje za taj slučaj.

**Test G — ista adresa sa oba kraja:**

15. From i To: `0xMixer` → **FIND PATH**.
16. Očekivano: **0 skokova**, putanja je samo `["0xMixer"]" — trivijalan slučaj, ne greška.

## 6. Ograničenja prve verzije

Namerno izostavljeno iz ove verzije (videti zahtev — dodaje se tek kad zatreba):

- **Nema prepoznavanja CEX/cash-out tačaka** — Pathfinding ne zna da je neka adresa berza.
- **Nema weighted pathfinding-a** — ne bira „najverovatniji" put po iznosu, samo najkraći
  po broju skokova (za to postoji `find_transaction_paths` sa strategijom `most_likely`,
  već u kodu ali nije povezano sa ovom stranicom — videti §7).
- **Samo jedna putanja** — ne prikazuje alternativne/sve moguće puteve.
- **Bez custody/lanac dokaza gejtinga** — Pathfinding ne pokreće analitički pipeline, pa
  ne postoji zaprljanost/rizik koji bi trebalo potpisivati; ako se to promeni, dijalog za
  lanac dokaza (`CustodyAccessDialogComponent`) je već generički i spreman za ponovnu
  upotrebu.

## 7. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| BFS algoritam | `backend/app/analytics/path_finding.py` (`bfs_shortest_path`) |
| Ruta | `backend/app/api/routes/cases.py` (`run_case_pathfinding`, `CasePathfindingRequest`) |
| Testovi | `backend/tests/test_path_finding_bfs.py` |
| Frontend stranica | `frontend/src/app/features/pathfinding/` |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`findCasePath`) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`CasePathfindingResult`) |

**Ruta:**

| Ruta | Namena |
|---|---|
| `POST /api/v1/cases/{id}/pathfinding` | BFS put između dve adrese unutar evidencije slučaja |

**Napomena:** postoji i stariji, nepovezan `POST /api/v1/graph/path-finding`
(`backend/app/api/routes/graph.py`) koji radi direktno nad `data/raw/*.csv` po imenu
fajla, bez pojma o slučaju — prethodi sistemu slučajeva i nije korišćen od strane
frontenda. Nije menjan niti korišćen za ovaj modul.
