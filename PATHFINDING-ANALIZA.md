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
| [5. Path Analysis panel](#5-path-analysis-panel-drugi-korak) | forenzički detalji pronađenog puta, opciona taint provera |
| [6. Odredište: Nearest known CEX](#6-odredište-nearest-known-cex-treći-korak) | biranje cilja umesto ručnog unosa, samo iz postojećih podataka |
| [7. Testiranje korak po korak](#7-testiranje-korak-po-korak) | UI i automatski testovi |
| [8. Ograničenja prve verzije](#8-ograničenja-prve-verzije) | šta namerno nedostaje, zašto |
| [9. Gde je šta u kodu](#9-gde-je-šta-u-kodu) | putanje |

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

Telo zahteva (`destination_mode` je opciono, podrazumevano `"specific_address"` — vidi §6
za `"nearest_cex"`):
```json
{ "from": "0x...", "to": "0x..." }
```

Odgovor za `destination_mode: "specific_address"` (uvek tačno ova tri polja, ništa više —
nepromenjeno od prve verzije):
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
`case_id` i `destination_mode` (vidljivo na stranici „Log aktivnosti").

## 4. Frontend stranica

Ruta `/pathfinding`, link „Pathfinding" u glavnom meniju.

- Aktivan slučaj + birač evidencije (isti obrazac kao Graf stranica) — graf se učitava
  **automatski**, bez boja po riziku (samo pronalaženje puta ne pokreće analitički pipeline,
  pa tu nema potrebe za dijalogom za lanac dokaza; opciona taint provera ispod — §5 — ga
  ipak koristi, jer ona TO jeste pokretanje analize).
- Polje **From** + padajući meni **Destination** (`Specific address` / `Nearest known CEX` /
  `Cash-out point` — poslednje onemogućeno, vidi §6) + polje **To** (samo kad je izabrano
  „Specific address") + dugme **FIND PATH**.
- Kad je put pronađen: (za „Nearest known CEX") koja je adresa pronađena i njena oznaka,
  vertikalna lista adresa (`Address A ↓ Address B ↓ ...`), panel **„Path Analysis"** (§5), i
  na grafu se boji **samo** ta putanja (cijan, isti vizuelni jezik kao isticanje putanje u
  Taint analizi), dok se ostatak grafa zatamni.
- Kad put nije pronađen: jasna poruka (za „Nearest known CEX" razlikuje „nema CEX uopšte" od
  „CEX postoji, nije dostiživ" — §6.3), graf ostaje u normalnom (nezatamnjenom) prikazu.

## 5. Path Analysis panel (drugi korak)

Kad je put pronađen, panel **„Path Analysis"** izvlači forenzičke podatke IZ TE putanje —
bez novog algoritma za pronalaženje puta, bez ponovnog implementiranja Taint analize.

### 5.1 Podaci po skoku — iz grafa koji je stranica već učitala

Graf koji se iscrtava na platnu (`this.graph`, iz `GET /cases/{id}/graph`) već nosi, na
svakoj grani, kompletnu listu pojedinačnih transakcija (`link.transactions[]`: `amount`,
`timestamp`, `metadata` = tx heš). Za svaki skok `(path[i], path[i+1])` frontend samo
pronađe odgovarajuću granu i pročita je — **nema dodatnog backend poziva**.

Jedna grana ume da agregira više transakcija između iste dve adrese. Prikazuje se
**hronološki najranija** kao predstavnik skoka (jedna linija po skoku, kao u mokapu), a
ako ih ima više, ispod stoji napomena „+ N dodatnih transakcija na ovoj vezi" — ništa se ne
sakriva ćutke.

Iz ovih po-skok podataka se izvode: **Initial amount** / **Final amount** (iznos prve i
poslednje odabrane transakcije) i **Duration** (razlika vremena poslednje i prve).

**Poznato ograničenje:** BFS je čisto strukturni (§2) — moguće je da put topološki postoji
a hronološki NIJE realan tok istog novca (npr. transakcija na skoku 2 desila se pre
transakcije na skoku 1). Prva verzija to ne proverava; „Duration" opisuje odabrane
transakcije, ne garantovano jedan kontinuirani tok.

### 5.2 Taint provera puta — postojeći plugin, nov okidač

Dugme **„Pokreni taint analizu za ovu putanju"** poziva **isti** `POST
/cases/{id}/analytics/run` koji već koriste Taint analiza i Graf, sa jednom razlikom: seed
je automatski **prva adresa na putu** (`path[0]`). Pošto seed adresa po definiciji modela
uvek dobija 100%, to je tačno „Initial taint: 100%" iz mokapa — a „Final taint" je koliko
je od TE konkretne, seed-ovane vrednosti stiglo do poslednje adrese, baš kroz ovaj put.
„Taint dilution" = Initial − Final.

Pošto je ovo namerno pokretanje analize (isto kao „Pokreni taint analizu"/„Analiziraj
graf"), prolazi kroz **isti dijalog za lanac dokaza** (`CustodyAccessDialogComponent`) —
razlog pristupa + potpis, i upisuje se u `custody_log`/`custody_evidence_log` i u
„Log aktivnosti" isto kao svako drugo pokretanje.

**Napomena:** `TaintAnalysisPlugin` podrazumevano DODATNO seeduje i svaku adresu sa crne
liste (postojeće ponašanje, ne nešto uvedeno ovde) — ako neka adresa na putu slučajno
pripada crnoj listi, „Final taint" može uključivati i njen doprinos, ne samo `path[0]`-ov.

**Zašto rezultat ne dolazi iz Taint Analysis stranice:** taj rezultat živi samo lokalno na
`/taint` stranici (ne piše se u deljeno stanje), pa Pathfinding ne može da ga „vidi" a da ga
ne pokrene iznova — što ovo dugme i radi, eksplicitno, na zahtev korisnika.

## 6. Odredište: Nearest known CEX (treći korak)

Umesto da korisnik uvek ručno unese `To` adresu, polje **Destination** sad nudi izbor:

- **Specific address** (podrazumevano) — ponaša se identično kao pre, ručni unos `To`.
- **Nearest known CEX** — odredište se **automatski** određuje: najbliža (po broju
  skokova) adresa u grafu koja je poznata berza.
- **Cash-out point** — prikazano u padajućem meniju, ali onemogućeno („uskoro"); backend
  eksplicitno odbija ovu vrednost (400) ako bi ipak stigla, umesto da je tiho prihvati i ne
  uradi ništa korisno.

### 6.1 Otkuda se zna da je adresa CEX — bez pogađanja

Jedini izvor istine je **postojeći, lokalni registar poznatih entiteta**
(`backend/app/services/known_entities.json`, 362 unosa: 264 berze, 26 miksera, 72
sankcionisane adrese — isti fajl koji već koristi „Ko je zapravo iza ove adrese" panel u
Taint analizi, §5.10 u TAINT-ANALIZA.md). Funkcija `get_known_entity(address)` je čist
lokalni `dict` lookup, bez mrežnog poziva — adresa je CEX **isključivo** ako taj registar
kaže `category == "exchange"`. Nikakvo pogađanje po imenu/izgledu adrese (za razliku od
starog, neiskorišćenog `graph.py`-evog `enrich_node_metadata`, koji radi baš to i koji ova
funkcija namerno ne koristi).

Skup kandidata se pravi tako što se **svaki čvor trenutno učitanog grafa** (samo adrese
koje se stvarno pojavljuju u ovoj evidenciji, ne ceo registar) proveri protiv registra —
ako ih ima nula, to se i kaže, ne pretpostavlja se ništa.

### 6.2 „Najbliži" — kriterijum i determinizam

`find_path_to_nearest_of(graph, from_address, candidate_addresses)` — ista usmerena BFS
logika kao osnovna pretraga (§2), samo umesto jednog fiksnog cilja ide nivo-po-nivo dok ne
naiđe na **bilo koju** adresu iz skupa kandidata. Zato što BFS obilazi čvorove u
neopadajućem redosledu udaljenosti, **prvi pronađeni kandidat je uvek i najbliži** — nema
posebnog poređenja udaljenosti.

Kad je više kandidata na **istom, najmanjem** broju skokova, bira se **alfabetski manja**
adresa — determinističko pravilo, da rezultat ne zavisi tiho od redosleda grana u grafu.

### 6.3 Odgovor i UI

Kad je `destination_mode: "nearest_cex"`, odgovor dobija dva dodatna polja (osnovni oblik
`{found, path, hops}` ostaje nepromenjen za `specific_address`):

```json
{
  "found": true,
  "path": ["0x...", "0x...", "0x..."],
  "hops": 2,
  "destination_address": "0x...",
  "destination_label": "Binance"
}
```

Kad ne postoji rezultat, poruka razlikuje dva različita razloga (`message` polje):
„Nijedna poznata CEX adresa nije prisutna u ovoj evidenciji" (registar nema poklapanja ni
sa jednim čvorom grafa) naspram „...nije dostupna (nema puta)..." (CEX adresa postoji u
evidenciji, ali nije dostiživa iz polazne adrese) — čitalac ne treba da nagađa koje od to
dvoje se desilo.

Na UI-u, pronađeno odredište se ispisuje iznad liste adresa (`0x... [Binance]`), a ostatak
(lista, Path Analysis panel, isticanje na grafu) je **potpuno isti kod** kao za
`specific_address` — `result.path`/`result.hops` se ne razlikuju po poreklu.

## 7. Testiranje korak po korak

### 7.1 Automatski testovi

```bash
python -m pytest backend/tests/test_path_finding_bfs.py -v
```

21 test: 7 za osnovni BFS (nepromenjeno), 6 za `find_path_to_nearest_of` (bira bližeg
kandidata, deterministički raspetljava izjednačenje, polazna adresa je i sama kandidat,
nema kandidata, kandidat postoji ali nije dostiživ, polazna adresa ne postoji), i 8 na nivou
rute (uključujući da `specific_address` odgovor ostaje `{found, path, hops}` bez izmene, da
`to` nedostaje → 400, da `cash_out_point` → 400, i da se CEX kategorija nikad ne meša sa
mikserom/sankcionisanom adresom).

### 7.2 Ručna provera kroz UI (demo podaci sa poznatim odgovorom)

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
5. U panelu „Path Analysis": **Initial amount 1000**, **Final amount 750**, i lista
   transakcija ispod pokazuje `0xThief → 0xMixer` (iznos 1000, 01.03.2026.) i
   `0xMixer → 0xExitWallet` (iznos 750) — potvrđuje da se podaci po skoku ispravno čitaju
   iz već učitanog grafa (uporedi sa demo CSV-om iznad).
6. Klikni **„Pokreni taint analizu za ovu putanju"** → potpiši se u dijalogu → potvrdi.
   Očekivano: **Initial taint 100%** (jer je `0xThief` upravo seed), **Final taint 66.67%**
   (isti broj koji TAINT-ANALIZA.md §3.1 Test A navodi za `0xMixer`/`0xExitWallet` sa
   seed-om `0xThief`), **Taint dilution 33.33%**.

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

**Test H — Nearest known CEX (verifikovano direktno protiv pravog registra):**

17. From: `0xExchangeHacker`, Destination: **„Nearest known CEX"** → **FIND PATH** (polje
    „To" nestaje/nije potrebno u ovom režimu).
18. Očekivano: **2 skoka**, `0xExchangeHacker → 0xExchangeMule → 0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be`,
    sa natpisom „Pronađeno odredište: ... [Binance]" iznad liste — u ovom demo slučaju je
    to **jedina** adresa koju lokalni registar prepoznaje kao berzu.
19. From: `0xThief`, Destination: **„Nearest known CEX"** → **FIND PATH**.
20. Očekivano: **„Nijedna poznata CEX adresa nije dostupna (nema puta) od izabrane polazne
    adrese..."** — ta ista Binance adresa POSTOJI u evidenciji, ali `0xThief`-ova šema
    (`0xThief → 0xMixer → 0xExitWallet`) nikad se ne spaja sa njom; poruka mora reći baš
    „nije dostupna", ne „nije prisutna".

## 8. Ograničenja prve verzije

Namerno izostavljeno iz ove verzije (videti zahtev — dodaje se tek kad zatreba):

- **Nema prepoznavanja cash-out tačaka** (u smislu „poslednja adresa pre nego što sredstva
  napuste praćenu mrežu na bilo koji način") — implementiran je samo užи, pouzdaniji slučaj:
  poznata CEX adresa iz lokalnog registra (§6). Cash-out point ostaje prikazan ali
  onemogućen u UI-u dok se ne definiše na čemu bi se pouzdano zasnivao.
- **Nema weighted pathfinding-a** — ne bira „najverovatniji" put po iznosu, samo najkraći
  po broju skokova (za to postoji `find_transaction_paths` sa strategijom `most_likely`,
  već u kodu ali nije povezano sa ovom stranicom — videti §9).
- **Samo jedna putanja** — ne prikazuje alternativne/sve moguće puteve.
- **Bez hronološke provere puta** — BFS ne proverava da li skokovi u putu stvarno mogu da
  predstavljaju JEDAN kontinuirani tok istog novca kroz vreme (videti §5.1).
- **Taint provera je opciona i posebno pokretanje** — sâmo pronalaženje puta ostaje bez
  custody gejtinga (nije analiza, samo pretraga strukture); dugme „Pokreni taint analizu za
  ovu putanju" JESTE gejtovano (§5.2), isto kao svako drugo pokretanje analize u aplikaciji.

## 9. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| BFS ka tačno određenoj adresi | `backend/app/analytics/path_finding.py` (`bfs_shortest_path`) |
| BFS ka najbližoj adresi iz skupa kandidata | `backend/app/analytics/path_finding.py` (`find_path_to_nearest_of`) |
| Lokalni registar poznatih entiteta (CEX/mikser/sankcionisano) | `backend/app/services/address_enrichment.py` (`get_known_entity`), podaci u `known_entities.json` |
| Ruta | `backend/app/api/routes/cases.py` (`run_case_pathfinding`, `CasePathfindingRequest`) |
| Testovi | `backend/tests/test_path_finding_bfs.py` |
| Frontend stranica | `frontend/src/app/features/pathfinding/` |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`findCasePath`) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`CasePathfindingResult`, `PathfindingDestinationMode`) |

**Ruta:**

| Ruta | Namena |
|---|---|
| `POST /api/v1/cases/{id}/pathfinding` | BFS put između dve adrese unutar evidencije slučaja |

**Napomena:** postoji i stariji, nepovezan `POST /api/v1/graph/path-finding`
(`backend/app/api/routes/graph.py`) koji radi direktno nad `data/raw/*.csv` po imenu
fajla, bez pojma o slučaju — prethodi sistemu slučajeva i nije korišćen od strane
frontenda. Nije menjan niti korišćen za ovaj modul.
