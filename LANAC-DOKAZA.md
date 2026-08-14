# Lanac dokaza — Obrazac evidencije rukovanja dokaznim materijalom

Dokumentacija funkcije koja primenjuje standardni obrazac evidencije rukovanja dokaznim
materijalom (Идентификатор предмета / доказног материјала / произвођач / модел / серијски
број, pa hronološka tabela Бр./Датум/Име и презиме/Опис радње/Потпис) na blockchain
evidenciju — na **dva nivoa istovremeno**, umesto na fizički dokaz.

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Zašto dva nivoa](#1-zašto-dva-nivoa) | forenzički smisao obe granulacije |
| [2. Kad se pravi novi red](#2-kad-se-pravi-novi-red) | okidači: Taint analiza i Graf |
| [3. Identitet transakcije/fajla](#3-identitet-transakcijefajla) | kako red ostaje "isti" kroz vreme |
| [4. Podrazumevane vrednosti polja](#4-podrazumevane-vrednosti-polja) | N/A, predlozi iz baze |
| [5. Tok kroz aplikaciju](#5-tok-kroz-aplikaciju) | dijalog pre pokretanja analize, na obe stranice |
| [6. Pregled i PDF izvoz](#6-pregled-i-pdf-izvoz) | stranica "Lanac dokaza", dva taba |
| [7. Testiranje](#7-testiranje) | koraci provere |
| [8. Gde je šta u kodu](#8-gde-je-šta-u-kodu) | putanje |

---

## 1. Zašto dva nivoa

Postojeći log aktivnosti (`audit_log.jsonl`) beleži *da* je analitičar pokrenuo analizu nad
slučajem. On ne odgovara na pitanje koje traži sudski veštak ili tužilaštvo za **konkretan
dokaz**: ko je, kada, i **zašto** pristupio baš njemu, sa potpisom koji to potvrđuje — u
obliku istog obrasca kakav se koristi za fizičke dokaze (vidi primer skeniranog obrasca
priložen uz rad, sa poljima Идентификатор предмета/доказног материјала/произвођач/модел/
серијски број).

Postavlja se pitanje: šta je ovde tačno "dokazni materijal" — pojedinačna transakcija, ili
ceo uvezeni fajl (CSV/on-chain izvoz)? Odgovor je: **oboje, jer odgovaraju na različita
pitanja**, pa se vode paralelno:

| Nivo | Odgovara na pitanje | Analogija sa fizičkim dokazom |
|---|---|---|
| **Po transakciji** | Da li je BAŠ OVA transakcija ikad pogledana, kada i zašto? | jedan fajl na disku |
| **Po dokaznom fajlu** | Da li je OVAJ dokazni fajl (izvor) ikad pristupljen, kada i zašto? | sam hard disk (BG-HDD-01 sa referentne slike) |

Fajl-nivo je forenzički bliži originalnom obrascu (jedan predmet = jedan disk, ne stotine
pojedinačnih fajlova na njemu) i praktičniji na velikoj evidenciji (jedan red po pristupu,
ne stotine odjednom). Transakcijski nivo je precizniji kad je potrebno dokazati pristup
baš određenom transferu. Aplikacija zato vodi **oba lanca istovremeno** — jedno potpisivanje
upisuje u oba odjednom (vidi §2).

## 2. Kad se pravi novi red

Okidač je **svako deliberatno pokretanje analize**, na bilo kojoj od dve stranice koje to
rade:

| Stranica | Dugme | Šta se dešava |
|---|---|---|
| **Taint analiza** | „Pokreni taint analizu" | traži izvore (seed), boji graf po zaprljanosti |
| **Graf** | „Analiziraj graf" | boji graf po riziku/crnoj listi, bez izbora izvora |

Oba dugmeta otvaraju **isti dijalog** (`CustodyAccessDialogComponent`) tražeći razlog
pristupa, ime i prezime i potpis, i oba, na potvrdu, pozivaju isti backend endpoint
(`POST /cases/{id}/analytics/run`). Taj endpoint, kad dobije `custody` objekat, upisuje:

- **jedan red po transakciji** u opsegu (`custody_log.jsonl`) — potencijalno stotine odjednom
- **jedan red po dokaznom fajlu** u opsegu (`custody_evidence_log.jsonl`) — obično 1-5

...oba dela istog pokretanja, sa istim vremenom/imenom/razlogom/potpisom, jer su stvarno
pristupljeni istim činom.

**Bitno razgraničenje:** `POST /cases/{id}/analytics/run` se poziva i **pasivno** (Kontrolna
tabla, kao i sam Graf pri prvom učitavanju slučaja — vidi niže) radi prikaza grafa bez boja.
Kad `custody` nije poslat u telu zahteva, ništa se ne upisuje ni u jedan lanac — samo
namerni klik na jedno od dva dugmeta iznad predstavlja pristup u smislu ovog obrasca.

**Graf stranica konkretno:** sirovi graf (bez boja rizika) učitava se **automatski** čim se
izabere slučaj/evidencija — to je samo pregled podataka, ne analiza. Bojenje po
riziku/crnoj listi (što JESTE analitika — pokreće se `run_plugin_pipeline`) zahteva klik na
„Analiziraj graf", isti gated tok kao na Taint analizi.

## 3. Identitet transakcije/fajla

**Dokazni fajl** ima prirodan i stabilan identifikator: `stored_name` iz `case.evidence[]`
(dodeljen pri uvozu, jedinstven unutar slučaja).

**Transakcija** mora izvesti stabilan identifikator da bi „red 2" i „red 3" opisivali istu
transakciju kroz više pokretanja:

- Ako evidencija ima heš transakcije (kolona `tx_hash`/`hash`, normalizovana u `metadata`),
  **taj heš je identifikator**.
- Ako ga nema (demo CSV, ručno uneta evidencija), identifikator se izvodi:
  `sha256(pošiljalac|primalac|iznos|vreme|naziv_dokaznog_fajla)`, skraćeno na 16 znakova
  sa prefiksom `row-`.

Implementacija: `backend/app/evidence/tx_identity.py`.

## 4. Podrazumevane vrednosti polja

Identična pravila za oba nivoa (isti dijalog, ista polja):

| Polje | Podrazumevano | Napomena |
|---|---|---|
| Идентификатор предмета | naziv slučaja | editabilno, predlozi iz ranijih unosa za isti slučaj |
| Идентификатор доказног материјала | naziv dokaznog fajla (ili "sva evidencija (kombinovano)") | editabilno, predlozi |
| Произвођач / Модел / Серијски број | `N/A` | digitalni dokaz nema fizički uređaj — polja ostaju jer CSV može poticati sa zaplenjenog uređaja |
| Име и презиме | prijavljeno korisničko ime (samo kao početna vrednost) | **uvek ručno**, bez predloga |
| Опис радње | prazno | **uvek ručno** — razlog/cilj pristupa |
| Потпис | prazno platno | isti mehanizam kao potpis pri izvozu taint izveštaja, deljen kroz `SignaturePadComponent` |
| Бр. | — | računa se pri čitanju kao redni broj unutar hronologije |
| Датум | vreme pokretanja analize (UTC, na serveru) | **ne** vreme same transakcije na lancu |

## 5. Tok kroz aplikaciju

1. Analitičar klikne **„Pokreni taint analizu"** (Taint analiza) ili **„Analiziraj graf"**
   (Graf).
2. Otvara se dijalog **„Razlog pristupa i potpis"** (`features/custody-access-dialog`,
   zajednički za obe stranice) — polja iz tabele iznad, plus potpis mišem i obavezan
   checkbox izjave.
3. Na potvrdu se šalje `POST /cases/{id}/analytics/run` sa `custody` objektom.
4. Backend prolazi kroz **svaki red** evidencije u opsegu i piše: po jedan zapis u
   `custody_log.jsonl` za svaku transakciju, i po jedan zapis u `custody_evidence_log.jsonl`
   za svaki dokazni fajl u opsegu — svi zapisi iz jednog pokretanja dele isti `run_id`.
5. Ako pokretanje ne uspe, dijalog **ostaje otvoren** sa porukom o grešci — ništa uneto se
   ne gubi.

## 6. Pregled i PDF izvoz

Stranica **„Lanac dokaza"** (`/lanac-dokaza`) — dostupna **svim prijavljenim korisnicima**,
ne samo administratoru. Ima **dva taba**:

- **„Po transakciji"** — spisak transakcija sa bar jednim pristupom, klik otvara pun obrazac
  (Бр./Датум/Име и презиме/Опис радње/Потпис, potpis kao minijatura).
- **„Po dokaznom fajlu"** — spisak dokaznih fajlova sa bar jednim pristupom (broj
  transakcija, valuta, SHA-256), klik otvara isti oblik obrasca za ceo fajl.

Oba taba imaju **„Izvezi PDF"** (server-side, fpdf2, ćirilični naslov/nazivi polja kao na
referentnom obrascu — `custody_report.py` za transakcije, `custody_evidence_report.py` za
fajlove, obe dele nisko-nivoske helpere iz `custody_pdf_common.py`).

Iz Taint analize, panel „Detalji transakcije" ima link **„Lanac dokaza za ovu transakciju
→"** (samo kad evidencija ima heš — bez njega se klijent ne može pouzdano poklopiti sa
izvedenim identifikatorom).

**Sam izvoz PDF-a se beleži** u opšti log aktivnosti (akcija `custody_pdf_exported`,
uključena i u izveštaj aktivnosti) — inače bi taj log pokazivao svako pokretanje analize
koje je dotaklo neki dokaz, ali ništa o tome ko je kasnije odštampao/izvezao SAM zapis tih
pristupa. Svaki red u samim log fajlovima (`custody_log.jsonl` / `custody_evidence_log.jsonl`)
je dodatno označen poljem `"scope": "transaction"` / `"scope": "evidence_file"`, pa je
granularnost čitljiva direktno iz fajla, bez potrebe da se pogodi iz konteksta.

## 7. Testiranje

**Iz terminala:**

```bash
python -m pytest backend/tests/test_custody_log.py backend/tests/test_custody_evidence_log.py -v
```

`test_custody_log.py` (13 testova) pokriva transakcijski nivo i proveru da JEDNO
pokretanje upiše i evidencijski nivo (`test_also_writes_one_evidence_level_row_per_file`).
`test_custody_evidence_log.py` (7 testova) pokriva fajl-nivo samostalno: hronologija i
brojanje redova, odvojeni lanci za različite fajlove, PDF izvoz.

**Ručna provera:**

1. Otvori slučaj → Taint analiza → „Pokreni taint analizu" → dijalog, popuni i potpiši →
   analiza se pokreće.
2. Otvori Graf za isti slučaj → graf se učitava **odmah, bez boja** (siva/plava, ne po
   riziku) → klikni „Analiziraj graf" → isti dijalog → potvrdi → graf se oboji po riziku.
3. Idi na „Lanac dokaza" → tab „Po transakciji": neka od upravo obrađenih transakcija ima
   nov red. Tab „Po dokaznom fajlu": fajl ima **dva** reda (jedan od Taint analize, jedan
   od Grafa) — potvrđuje da oba dugmeta pišu u isti fajl-lanac.
4. „Izvezi PDF" na oba taba → uporedi zaglavlje/tabelu sa referentnim obrascem.
5. Prijavi se kao ne-admin korisnik → stranica „Lanac dokaza" i dalje dostupna (za razliku
   od „Testovi").

## 8. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| Identitet transakcije | `backend/app/evidence/tx_identity.py` |
| Lanac dokaza po transakciji | `backend/app/evidence/custody_log.py` |
| Lanac dokaza po dokaznom fajlu | `backend/app/evidence/custody_evidence_log.py` |
| PDF — deljeni helperi | `backend/app/exports/custody_pdf_common.py` (+ `pdf_fonts.py`) |
| PDF — po transakciji / po fajlu | `backend/app/exports/custody_report.py` / `custody_evidence_report.py` |
| API rute (oba nivoa) | `backend/app/api/routes/custody.py` |
| Upis pri pokretanju analize (oba nivoa odjednom) | `backend/app/api/routes/cases.py` (`_record_custody_access`) |
| Testovi | `backend/tests/test_custody_log.py`, `test_custody_evidence_log.py` |
| Dijalog pre pokretanja analize (deljen) | `frontend/src/app/features/custody-access-dialog/` |
| Deljeni potpis (canvas) | `frontend/src/app/core/components/signature-pad/` |
| Stranica „Lanac dokaza" (dva taba) | `frontend/src/app/features/custody-log/` |
| Poziv sa Taint analize | `taint-analysis.component.ts` (`openCustodyDialog`, `confirmCustodyAndRunAnalysis`) |
| Poziv sa Grafa | `graph-visualization.component.ts` (`openCustodyDialog`, `confirmCustodyAndAnalyze`) |

**Rute:**

| Ruta | Namena |
|---|---|
| `POST /api/v1/cases/{id}/analytics/run` | pokretanje analize; `custody` opciono (vidi §2) |
| `GET /api/v1/cases/{id}/custody/suggestions` | predlozi za autocomplete polja (zajedničko) |
| `GET /api/v1/cases/{id}/custody/transactions` | spisak transakcija sa lancem dokaza |
| `GET /api/v1/cases/{id}/custody/transactions/{tx_id}` | pun obrazac za jednu transakciju |
| `GET /api/v1/cases/{id}/custody/transactions/{tx_id}/export.pdf` | PDF izvoz (transakcija) |
| `GET /api/v1/cases/{id}/custody/evidence` | spisak dokaznih fajlova sa lancem dokaza |
| `GET /api/v1/cases/{id}/custody/evidence/{stored_name}` | pun obrazac za jedan dokazni fajl |
| `GET /api/v1/cases/{id}/custody/evidence/{stored_name}/export.pdf` | PDF izvoz (dokazni fajl) |
