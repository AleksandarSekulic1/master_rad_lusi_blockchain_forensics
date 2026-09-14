# Token Approval / Ice Phishing Analiza

> Ovaj dokument je namerno kratak — samo ono što je potrebno da se razume šta analiza radi,
> kako je urađena i zašto postoji, plus jedan potpun, ručno proverljiv test. Za istorijat
> istraživanja/odluka koje su prethodile implementaciji (§1–§11 stare verzije ovog fajla),
> vidi git istoriju ovog fajla — ovde je zadržan samo trenutni, tačan opis gotovog stanja.

**Sadržaj**

1. [Šta je ova analiza](#1-šta-je-ova-analiza)
2. [Zašto nam je potrebna](#2-zašto-nam-je-potrebna)
3. [Kako je implementirana](#3-kako-je-implementirana)
4. [Ograničenja — šta ova analiza NIJE](#4-ograničenja--šta-ova-analiza-nije)
5. [Predlog adresa za dalju analizu (Taint Analysis)](#5-predlog-adresa-za-dalju-analizu-taint-analysis)
6. [Demo podaci za ručno testiranje](#6-demo-podaci-za-ručno-testiranje)
7. [Ručno testiranje — vodič korak po korak](#7-ručno-testiranje--vodič-korak-po-korak)
8. [Gde je šta u kodu](#8-gde-je-šta-u-kodu)
9. [Automatski testovi](#9-automatski-testovi)

---

## 1. Šta je ova analiza

Za jednu adresu — bilo kao **vlasnik tokena (owner)** koji je nekome dao dozvolu, bilo kao
**odobreni trošilac (spender)** koji je tu dozvolu dobio — analiza pokazuje:

- koje je ERC-20 `approve`/`permit` dozvole (allowance) ta adresa dala ili primila,
- da li je dozvola **neograničena** (unlimited) i na osnovu čega se to tvrdi,
- da li je i kada dozvola **iskorišćena** (`transferFrom`) — koliko puta, koliko je ukupno
  povučeno, i gde su sredstva otišla,
- da li je dozvola kasnije **opozvana** (`approve(0, ...)`),
- i, na osnovu svega gore, jedan **heuristički risk nivo** (LOW/MEDIUM/HIGH) sa listom
  konkretnih, objašnjenih razloga zašto je taj nivo dodeljen.

Ovo je forenzički pandan onome što bi analitičar inače morao ručno da sklapa gledajući
Etherscan-ovu stranicu "Token Approvals" za jednu adresu — samo automatizovano, sa
istorijom (šta se desilo sa SVAKOM dozvolom, ne samo trenutnim stanjem) i sa objašnjenom
procenom rizika.

## 2. Zašto nam je potrebna

**"Ice phishing"** (ili "approval phishing") je danas jedan od najčešćih načina krađe
kripto sredstava: žrtva ne prepiše svoj privatni ključ nikome, nego je namamljena da
potpiše naizgled bezazlenu `approve()`/`permit()` transakciju (npr. na lažnom sajtu koji
liči na pravu dApp aplikaciju) — time daje napadačevom ugovoru dozvolu da **kasnije**, u
bilo kom trenutku, povuče (deo ili sav) njen token preko `transferFrom()`. Sama `approve`
transakcija ne pomera nijedan token, pa žrtva često ni ne primeti da se nešto desilo — sve
dok napadač ne iskoristi dozvolu.

Bez ove analize, taj obrazac je nevidljiv za ostatak aplikacije: Graph/Taint/Pathfinding
prate **stvarno kretanje sredstava**, a `approve`/`permit` transakcija formalno jeste
transakcija (ima pošiljaoca, primaoca, iznos, vreme), ali njeno **značenje** — "ovo je
dozvola, ne transfer, i evo šta se kasnije desilo s njom" — ne postoji nigde drugde u
projektu. Ova analiza postoji da to značenje eksplicitno izvuče i prikaže, sa jasno
obeleženim, objašnjenim heuristikama — nikad kao gola tvrdnja da je neka adresa
zloćudna.

## 3. Kako je implementirana

### 3.1 Backend

- **`backend/app/analytics/token_approval_analysis.py`** — ceo algoritam. Čita
  **already-cleaned evidenciju slučaja** direktno (ne deljeni graf — graf namerno ne čuva
  polja koja ovoj analizi trebaju), izdvaja redove koji imaju kolonu `event_type`
  (`approve` / `permit` / `transferFrom`), grupiše ih po `(owner, spender, token)`, i za
  svaku grupu računa hronologiju (koje odobrenje je aktivno/korišćeno/opozvano u kom
  trenutku) i risk indikatore (§3.3 ispod).
- **Dve GET rute (pasivne, bez lanca dokaza)** i **jedna POST ruta (deliberatna, sa lancem
  dokaza)** u `backend/app/api/routes/cases.py`:
  - `GET /cases/{id}/token-approval-analysis` — sirova ekstrakcija.
  - `GET /cases/{id}/token-approval-correlation` — ekstrakcija + uparivanje sa
    `transferFrom` (isto što frontend stranica zove pri pasivnom prikazu).
  - `POST /cases/{id}/token-approval-analysis/run` — isto što gornja GET korelacija, ali
    uz **lanac dokaza** (§3.4) — ovo zove dugme **"ANALIZIRAJ"** na frontend stranici.
- Nijedna nova kolona nije obavezna za CSV koji projekat već koristi — sve navedeno u §3.3
  je **opciono**: evidencija bez ovih kolona i dalje normalno radi za SVE ostale analize,
  prosto se ne razmatra za Token Approval.

### 3.2 Frontend — stranica `/token-approval`

Jedna adresa + dugme **"ANALIZIRAJ"** (isti obrazac kao Taint/Pathfinding/DEX Swaps):
unos adrese pokreće dijalog **"Razlog pristupa i potpis"** (lanac dokaza), pa tek posle
potvrde prikazuje: sažetak (7 brojčanih kartica), tabelu svih pronađenih odobrenja, i
klik-detalje po odobrenju (ceo životni ciklus, risk indikatori sa razlozima, prečice ka
Grafu/Pathfinding-u/beleškama istražitelja).

### 3.3 Risk indikatori (heuristika, uvek objašnjena)

Svaki nalaz nosi **listu** upaljenih indikatora, svaki sa svojom težinom; zbir težina daje
`risk_score`, a `risk_score` se prevodi u **LOW / MEDIUM (≥2) / HIGH (≥5)**:

| Indikator | Težina | Šta znači |
|---|---|---|
| `unlimited_rapid_drain` | 3 | Neograničena dozvola povučena ubrzo (podrazumevano: unutar 1h) posle odobrenja — klasičan ice-phishing obrazac |
| `spender_multi_owner` | 3 | Isti spender odobren od više različitih vlasnika u istoj evidenciji — strukturni signal (moguć drainer sa više žrtava) |
| `unlimited_never_used` | 2 | Neograničena dozvola, još aktivna, nikad iskorišćena — "uspavan" rizik |
| `rapid_first_use` | 2 | Prvo korišćenje ubrzo posle odobrenja (opšti oblik, bez potvrđenog neograničenog iznosa) |
| `large_amount_transferred` | 2 | Ukupno povučeno dostiže podesiv prag |
| `multiple_tokens_same_spender` | 2 | Isti spender odobren za više različitih tokena — obrazac "potpiši ovde za ceo novčanik" |
| `unknown_spender` | 1 | Spender nije prepoznat ni u jednom lokalnom registru (DEX/exchange) — **ne znači** da je zloćudan |
| `revoked_after_use` | 1 | Opozvano tek posle korišćenja — informativno |
| `active_used_never_revoked` | 1 | Aktivna, već korišćena, nikad opozvana |
| `multiple_transferfrom_operations` | 1 | Više povlačenja nad istom dozvolom |
| `long_active_period` | 1 | Dozvola (bila) aktivna dugo (podrazumevano: 90+ dana) |

Pragovi (`unlimited_threshold`, `rapid_use_seconds`, `large_amount_threshold`,
`multiple_transfer_threshold`, `long_active_period_seconds`) su **podesivi parametri**, ne
fiksni forenzički standard — svaki izveštaj to eksplicitno kaže.

### 3.4 Lanac dokaza, PDF izveštaj, Log aktivnosti

Sve troje su **potpuno ponovna upotreba** postojećih, već izgrađenih mehanizama — ništa
posebno napravljeno samo za ovu analizu:

- **Lanac dokaza**: isti `_record_custody_access()` helper i isti
  `CustodyAccessDialogComponent` koje koriste Taint/Graf/Pathfinding/DEX Swaps (vidi
  `LANAC-DOKAZA.md`).
- **PDF izveštaj**: isti mehanizam kao Taint/Pathfinding/DEX Swap
  (`registerReport()` → kontrolni broj + otisak sadržaja → potpis mišem → pečat "Lusi"),
  izgrađen u `buildTokenApprovalPdf()` sa **istim vizuelnim jezikom** (teget zaglavlje sa
  logom, žuti okvir sa napomenom o heuristici, kartice sažetka, tabele nalaza, sekcija
  procene rizika, potpis + pečat na kraju) kao PDF izveštaji svih ostalih analiza — namerno
  identičan izgled, ne novi stil.
- **Log aktivnosti**: `token_approval_analysis_run` i `report_type: 'token_approval'` su
  registrovani u `activity_report.py` i `activity-log.component.ts` na isti način kao i
  ostale analize.

## 4. Ograničenja — šta ova analiza NIJE

- **Ne dekodira ništa sa lanca.** Projekat danas ne povlači `Approval` evente ni
  `permit()` pozive sa Etherscan-a (samo native ETH transakcije) — sve što ova analiza vidi
  mora već biti u uvezenoj CSV evidenciji, u kolonama opisanim u §3.1.
- **Risk nivo nije dokaz ni optužba.** To je težinski zbir objašnjenih heuristika za
  prioritizaciju pregleda — nikad tvrdnja da je adresa ili ugovor zlonameran.
- **Nema USD vrednosti.** Projekat nema izvor cena tokena, pa se "izloženost" nikad ne
  izražava u dolarima, samo u sirovim jedinicama iznosa.

## 5. Predlog adresa za dalju analizu (Taint Analysis)

Posle uspešne analize, stranica pokazuje panel **"Predlog za dalju analizu"** — ovo **nije
nov, poseban algoritam**, nego samo `risk_score`/`risk_level` iz §3.3, pročitan i rangiran:
jedan red po **spender** adresi koja ima bar jednu grupu ocenjenu MEDIUM ili HIGH (kad se
ista spender adresa pojavi u više grupa, zadržava se ona sa najvišim skorom), sortirano
opadajuće po skoru, sa najkraćim razlogom (labela prvog upaljenog indikatora).

Analitičar čekira jednu, više, ili sve (**"Izaberi sve"**) i klikne **"Pošalji izabrane u
Taint analizu"** — stranica Taint analiza se otvara sa tim adresama već dodatim u listu
seed adresa (identičan mehanizam kao postojeće dugme "Otvori u Pathfinding": jednokratna
predaja preko `AnalysisStateService`, Taint analiza sama odlučuje da li/kako da pokrene
analizu — ova stranica ne pokreće taint analizu umesto nje).

## 6. Demo podaci za ručno testiranje

Demo slučaj **"Demo: Sumnjiva laundering sema (hakovan novcanik)"** (ID `46ae7f91db9b`) do
sada nije imao nijedan red sa `event_type` kolonom — dakle **nijedna** postojeća demo
evidencija nije bila upotrebljiva za ručno testiranje Token Approval analize. Dodat je nov
fajl, isti obrazac kao ostale demo skripte (`backend/scripts/seed_demo_token_approval_evidence.py`):

```bash
python backend/scripts/seed_demo_token_approval_evidence.py
```

Ovo dodaje `demo_token_approval_evidence.csv` (8 redova) u postojeći demo slučaj — četiri
namerno odvojena scenarija, svaki demonstrira tačno jedan (ili dva) risk indikatora, bez
međusobnog preklapanja brojeva:

| # | Owner → Spender (token) | Šta demonstrira |
|---|---|---|
| 1 | `0xVictimWallet` → `0xDrainerContract` (USDC) | Neograničena dozvola (**deklarisano**), povučena za 12 min → `unlimited_rapid_drain` + `active_used_never_revoked` + `unknown_spender` + `spender_multi_owner` → **HIGH** |
| 2 | `0xVictimWallet2` → `0xDrainerContract` (USDC) | Neograničena dozvola (samo po **veličini iznosa**, nikad korišćena) → `unlimited_never_used` + `unknown_spender` + `spender_multi_owner` → **HIGH** |
| 3 | `0xCarefulTrader` → `0xUniswapRouter` (DAI) | Obična dozvola, korišćena pa uredno opozvana → samo `revoked_after_use` → **LOW** (kontrolni primer — nije sve rizično) |
| 4 | `0xVictimWallet` → `0xSweepContract` (USDC **i** DAI) | Isti nepoznati spender odobren za dva tokena, ništa još povučeno → `unknown_spender` + `multiple_tokens_same_spender` → **MEDIUM** (× 2, jedan po tokenu) |

Grupe 1 i 4 dele istog vlasnika (`0xVictimWallet`) namerno — analiza JEDNE adrese već
pokazuje tri odvojena rizična nalaza odjednom (vidi §7).

## 7. Ručno testiranje — vodič korak po korak

Svi brojevi ispod su **stvarno pokrenuti i provereni** (`analyze_token_approvals`/
`correlate_approval_usage` direktno nad seed-ovanom evidencijom), ne pretpostavljeni.

### 7.0 Priprema (jednom)

1. Pokreni backend i frontend (`uvicorn app.main:app` iz `backend/`, `ng serve` iz
   `frontend/`).
2. Iz `backend/` foldera pokreni skriptu iz §6 (ako još nije pokrenuta).
3. Prijavi se u aplikaciju.
4. **Slučajevi** → izaberi **"Demo: Sumnjiva laundering sema (hakovan novcanik)"** kao
   aktivan slučaj.

### 7.1 Korak 1 — Glavni primer: hakovan novčanik (`0xVictimWallet`)

1. Klikni **"Token Approval"** u meniju.
2. "Prikaz transakcija" → izaberi `demo_token_approval_evidence.csv` (radi preglednosti —
   radi i nad "Sve transakcije (kombinovano)", samo sa istim brojevima jer ostala demo
   evidencija nema `event_type` kolonu pa se ignoriše).
3. Adresa: `0xVictimWallet` → klikni **ANALIZIRAJ**.
4. **Očekivano:** otvara se dijalog **"Razlog pristupa i potpis"** (deliberatan pristup
   evidenciji, isto kao Taint/Graf/Pathfinding/DEX Swaps) — nijedan rezultat se ne vidi pre
   potvrde.
5. Upiši bilo šta u "Opis radnje", nacrtaj potpis, čekiraj izjavu, klikni **"Potpiši i
   pokreni analizu"**.
6. **Očekivano posle potvrde** (sažetak od 7 kartica):
   - **Ukupno odobrenja: 3**
   - **Neograničenih: 1** (samo `0xDrainerContract` red — oba `0xSweepContract` reda su
     obična, ne-neograničena)
   - **Aktivnih: 3**, **Opozvanih: 0** (nijedno od ova tri nije opozvano)
   - **Korišćenih: 1**, **Nekorišćenih: 2**
   - **Potencijalno rizičnih: 3** — **svako** pronađeno odobrenje je označeno kao rizično.
7. U tabeli, red za `0xDrainerContract`: Allowance = **NEOGRANIČENO**, Status = **APPROVED
   + USED**, Rizik = **HIGH**. Klikni na red → u detaljima: "Neograničen status" =
   **Deklarisano u evidenciji**, "Broj transferFrom transakcija" = **1**, "Ukupno
   povučeno" = **480,000**, "Odredište(a)" = `0xDrainerWallet` (treća adresa, ne sam
   spender ugovor — tipično za drainer koji prosleđuje sredstva dalje). Risk indikatori
   (4): Neograničen allowance povučen ubrzo posle odobrenja, Aktivna dozvola već
   korišćena nikad opozvana, Spender odobren od više vlasnika, Spender nije prepoznat.
8. Oba reda za `0xSweepContract` (USDC i DAI): Allowance = **10,000** / **8,000** (obični
   brojevi, NE "NEOGRANIČENO"), Status = **APPROVED**, Rizik = **MEDIUM**. Otvori detalje
   bilo kog od njih: "Broj transferFrom transakcija" = **0** — dozvola data, ništa još
   povučeno. Risk indikatori (2): Spender nije prepoznat, Isti spender odobren za više
   tokena.
9. **Zašto baš ovako:** §6 tabela iznad — grupa 1 i grupa 4 dele istog vlasnika, pa
   analiza jedne adrese odmah pokazuje i "aktivnu krađu u toku" (Drainer) i "otvorenu,
   još neiskorišćenu izloženost" (Sweep) u istom nalazu.

### 7.2 Korak 2 — Predlog za dalju analizu → Taint analiza

*Vidi §5 za pozadinu.* Nastavak istog rezultata (korak 7.1 i dalje na ekranu).

1. Ispod sažetka, panel **"Predlog za dalju analizu"** treba da pokazuje tačno **2** reda:
   `0xDrainerContract` (**HIGH**, razlog: "Neograničen allowance povučen ubrzo posle
   odobrenja") i `0xSweepContract` (**MEDIUM**, razlog: "Spender nije prepoznat ni u jednom
   lokalnom registru"), `0xDrainerContract` prvi (viši risk skor).
2. Čekiraj **"Izaberi sve"**. **Očekivano:** oba reda postaju čekirana, dugme ispod postaje
   **"Pošalji izabrane u Taint analizu (2)"**.
3. Klikni to dugme. **Očekivano:** aplikacija prelazi na stranicu **"Taint analiza"**, sa
   `0xDrainerContract` i `0xSweepContract` već dodatim kao seed adrese (vidljivi kao čipovi
   u listi seed-ova) — analiza se **ne pokreće automatski**, samo su adrese predlogom
   spremne; klik na "Pokreni taint analizu" je i dalje na analitičaru.

### 7.3 Korak 3 — Sumnjiv, još neiskorišćen nalog (`0xVictimWallet2`)

1. Vrati se na **"Token Approval"** (izbor evidencije i rezultat se ne pamte između
   poseta stranici — ponovo izaberi `demo_token_approval_evidence.csv`).
2. Adresa: `0xVictimWallet2` → **ANALIZIRAJ** → potpiši dijalog.
3. **Očekivano:** **Ukupno: 1**, **Neograničenih: 1**, **Aktivnih: 1**, **Nekorišćenih: 1**,
   **Potencijalno rizičnih: 1** (**HIGH**).
4. Otvori jedini red: "Neograničen status" = **Heuristika po veličini iznosa (nije
   potvrđeno)** — za razliku od koraka 7.1, ovde nema `is_unlimited` kolone, samo je iznos
   (5 × 10¹⁵) iznad podrazumevanog praga (10¹⁵). "Broj transferFrom transakcija" = **0**.
   Risk indikatori: Neograničen allowance nikad iskorišćen, Spender odobren od više
   vlasnika, Spender nije prepoznat.
5. **Zašto baš ovako:** ovo je namerno **drugi put** kako se "neograničeno" može utvrditi
   (heuristika po veličini, ne eksplicitna deklaracija) — isti krajnji risk nivo (HIGH), sa
   jasno drugačijim, poštenije obeleženim obrazloženjem u detaljima.

### 7.4 Korak 4 — Kontrolni primer: uredno korišćena i opozvana dozvola (`0xCarefulTrader`)

1. Adresa: `0xCarefulTrader` → **ANALIZIRAJ** → potpiši dijalog.
2. **Očekivano:** **Ukupno: 1**, **Neograničenih: 0**, **Aktivnih: 0**, **Opozvanih: 1**,
   **Korišćenih: 1**, **Potencijalno rizičnih: 0** — Status = **APPROVED + USED +
   REVOKED**, Rizik = **LOW**.
3. Otvori red: risk indikatori = samo **1** ("Opozvano tek posle korišćenja") —
   informativno, ne otvoren rizik, jer je dozvola ipak na kraju opozvana.
4. **Zašto baš ovako:** namerni kontrolni primer — potvrđuje da analiza NE prijavljuje
   rizik tamo gde ga stvarno nema, ne samo da ume da ga pronađe kad postoji.

### 7.5 Korak 5 — Traženje SPENDER adrese (ne samo vlasnika)

1. Adresa: `0xDrainerContract` (sam ugovor, ne vlasnik) → **ANALIZIRAJ** → potpiši dijalog.
2. **Očekivano: Ukupno: 2** — oba odobrenja koja je ovaj ugovor dobio, od **oba** vlasnika
   (`0xVictimWallet` i `0xVictimWallet2`), oba **HIGH**.
3. **Zašto baš ovako:** ovo je tačno svrha "adresa = vlasnik ILI odobreni trošilac" iz §1 —
   traženje same sumnjive spender adrese odmah pokazuje SVE njene žrtve u ovoj evidenciji,
   ne samo jednu.

### 7.6 Korak 6 — PDF izveštaj

1. Sa rezultatom za `0xVictimWallet` na ekranu (ponovi korak 7.1 ako je potrebno), klikni
   **"Izvezi PDF izveštaj"** → dijalog **"Potpis analitičara"** → izaberi jezik izveštaja →
   nacrtaj potpis → čekiraj izjavu → **"Potpiši i izvezi PDF"**.
2. **Očekivano:** preuzima se potpisan PDF, isti vizuelni format kao izveštaji Taint/
   Pathfinding/DEX Swap analize (§3.4) — teget zaglavlje sa Lusi logom, žuti okvir sa
   napomenom o heuristici, kartice sažetka (7 brojeva), tabela nalaza, tabela
   korišćenja/opoziva, istorija odobrenja, sekcija procene rizika sa punim obrazloženjima,
   povezana aktivnost, i na kraju potpis + pečat "Lusi" + kontrolni broj.
3. Kontrolni broj se može kasnije proveriti na stranici **"Provera izveštaja"** (isti
   `/reports/verify` mehanizam kao svi ostali izveštaji).

## 8. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| Algoritam | `backend/app/analytics/token_approval_analysis.py` |
| Rute | `backend/app/api/routes/cases.py` (`get_case_token_approval_analysis`, `get_case_token_approval_correlation`, `run_case_token_approval_analysis`) |
| Backend testovi | `backend/tests/test_token_approval_analysis.py` |
| Demo podaci | `backend/scripts/seed_demo_token_approval_evidence.py` |
| Frontend stranica | `frontend/src/app/features/token-approval/` |
| Predaja adresa u Taint analizu | `AnalysisStateService.setPendingTaintSeeds()`/`consumePendingTaintSeeds()`, pokupljeno u `taint-analysis.component.ts`'s `ngOnInit` |
| API pozivi | `frontend/src/app/core/services/api.service.ts` |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`TokenApprovalCorrelationEntry`, `TokenApprovalGroup`, `TokenApprovalCorrelationResult`, ...) |

## 9. Automatski testovi

```bash
python -m pytest backend/tests/test_token_approval_analysis.py -q
```

57 testova — klasifikacija event tipova, uparivanje approval↔transferFrom, revocation
detekcija, obe osnove za "neograničeno" (deklarisano i po veličini), svaki risk indikator
pojedinačno, nedostatak opcionih kolona (i dalje radi, samo bez tih nalaza), nepostojeća
adresa → 404, `data_completeness` tačan.
