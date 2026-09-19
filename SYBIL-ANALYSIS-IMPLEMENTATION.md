# Sybil & Bot Network Analysis — Implementation Record

> Ovaj dokument je namerno drugačiji od **`14. SYBIL-ANALIZA.md`**: onaj fajl objašnjava
> **ZAŠTO** i **KAKO RADI** heuristika (grupisanje, risk skor, false-positive mere,
> ograničenja). Ovaj fajl je **implementacioni popis** — šta je tačno izgrađeno, u kom
> fajlu se nalazi, i kako je povezano sa ostatkom aplikacije (Log aktivnosti, Chain of
> Evidence, sistem za PDF izveštaje). Kad god je nešto objašnjeno detaljnije na drugom
> mestu, ovaj dokument na to upućuje umesto da duplira tekst.

**Sadržaj**

1. [Pregled — šta postoji](#1-pregled--šta-postoji)
2. [Backend — algoritam i API](#2-backend--algoritam-i-api)
3. [Integracija sa Log/Audit stranicom](#3-integracija-sa-logaudit-stranicom)
4. [Integracija sa Chain of Evidence (lanac dokaza)](#4-integracija-sa-chain-of-evidence-lanac-dokaza)
5. [Sistem za generisanje izveštaja (PDF)](#5-sistem-za-generisanje-izveštaja-pdf)
6. [Frontend stranica i Forenzički pregled](#6-frontend-stranica-i-forenzički-pregled)
7. [Testovi](#7-testovi)
8. [Kompletna mapa fajlova](#8-kompletna-mapa-fajlova)

---

## 1. Pregled — šta postoji

Sybil & Bot Network Analysis je zasebna analiza (`/sybil-analysis`) koja detektuje grupe
RAZLIČITIH adresa sa sinhronizovanim ponašanjem (isti smart contract i/ili funkcija, kratak
vremenski period) i grupiše ih u potencijalne Sybil klastere sa confidence/risk skorom.
Implementacija ide kroz **četiri faze**, svaka dodata u posebnom krugu rada i svaka
potpuno integrisana sa postojećom infrastrukturom aplikacije (ne paralelan sistem):

| Faza | Šta je dodato | Gde je detaljno objašnjeno |
|---|---|---|
| 1. Detekcija | Algoritam, GET/POST rute, frontend stranica sa listom klastera | `14. SYBIL-ANALIZA.md` §1–§8 |
| 2. Forenzički pregled | Automatski unakrsni poziv Graph/Taint/Pathfinding/DEX Swap po klasteru | `14. SYBIL-ANALIZA.md` §10.1 |
| 3. Chain of Evidence | Strukturiran `sybil_evidence` dokaz po transakciji, prikaz u „Lanac dokaza", automatski forenzički zaključak | `14. SYBIL-ANALIZA.md` §9.1/§9.2, ovaj dokument §4 |
| 4. Log/Audit + PDF izveštaj | FAILED logovanje grešaka, `report_type: 'sybil'`, potpisan PDF izveštaj | ovaj dokument §3/§5 |

Sve četiri faze koriste **postojeće, deljene mehanizme** aplikacije
(`write_audit_log`, `record_custody_access`, `POST /reports/register`,
`CustodyAccessDialogComponent`, `SignaturePadComponent`) — nijedan paralelan
log/izveštaj/dijalog sistem nije napravljen.

## 2. Backend — algoritam i API

| Šta | Fajl |
|---|---|
| Algoritam (`detect_sybil_clusters`, grupisanje, risk skor) | `backend/app/analytics/sybil_analysis.py` |
| Model zahteva (`SybilAnalysisRunRequest`) | `backend/app/features/case_sybil_analysis/models.py` |
| Rute: `GET /cases/{id}/sybil-analysis` (pasivna), `POST /cases/{id}/sybil-analysis/run` (deliberatna) | `backend/app/features/case_sybil_analysis/router.py` |
| Registracija rute u API router-u | `backend/app/api/router.py` |
| Demo podaci | `backend/scripts/seed_demo_sybil_evidence.py` |

Detaljno objašnjenje heuristike, parametara (`time_window_seconds`, `min_addresses`),
sastavnih delova risk skora i mera protiv lažnih pozitiva: **`14. SYBIL-ANALIZA.md`
§2–§5**.

## 3. Integracija sa Log/Audit stranicom

Svako pokretanje analize (`POST .../sybil-analysis/run`) piše u **isti** `audit_log.jsonl`
koji koriste sve ostale analize (`write_audit_log`, akcija `sybil_analysis_run`) — nema
posebnog log fajla.

### 3.1 Šta se loguje

| Polje | Kad | Primer |
|---|---|---|
| `status` | uvek | `'SUCCESS'` ili `'FAILED'` |
| `address`, `contract` | uvek | filter korišćen u tom pokretanju (ili `null`) |
| `evidence_scope` | uvek | naziv fajla ili `'combined'` |
| `time_window_seconds`, `min_addresses` | uvek | parametri tog pokretanja |
| `error` | samo kad `status='FAILED'` | tekst greške (npr. „Adresa nije pronađena u evidenciji: ...") |
| `total_clusters`, `addresses_flagged`, `highest_risk_score` | samo kad `status='SUCCESS'` | rezultati tog pokretanja |
| `custody_recorded`, `custody_transaction_rows`, `custody_evidence_files`, `sybil_findings_recorded` | samo kad `status='SUCCESS'` | da li/koliko je upisano u lanac dokaza |

**Greške se sada loguju** (prethodno se pri 404 — nepostojeća adresa/kontrakt — ništa nije
upisivalo): `run_case_sybil_analysis` hvata `ValueError` iz `detect_sybil_clusters`, piše
`status='FAILED'` sa porukom greške PRE nego što baci `HTTPException`, isti obrazac koji
`case_token_approval_analysis` već koristi za svoje FAILED slučajeve.

**Fajl:** `backend/app/features/case_sybil_analysis/router.py` (`run_case_sybil_analysis`,
oba `try/except ValueError` bloka — jedan za normalan tok, jedan iz kojeg se piše FAILED
pre bacanja 404).

### 3.2 Prikaz na stranici „Log aktivnosti" i u izveštaju aktivnosti

| Šta | Fajl |
|---|---|
| Naziv akcije, boja/ikonica (👥), redosled u legendi | `backend/app/exports/activity_report.py` (`ACTION_LABELS`, `ACTION_HUE_ORDER`) i `frontend/.../activity-log.component.ts` (`ACTION_PRESENTATION`, `ACTION_HUE_ORDER`) |
| Jednorediчni rezime (uključujući FAILED slučaj) | `activity_report.py`'s `summarize_details()` (`action == 'sybil_analysis_run'`) i `activity-log.component.ts`'s `summary()` (`case 'sybil_analysis_run'`) — oba mesta prikazuju identičan sadržaj, jedno za PDF/CSV izveštaj aktivnosti, drugo za ekran |
| Naziv/rezime potpisanog PDF izveštaja (`report_signed` akcija, vidi §5) | isti fajlovi, `_REPORT_TYPE_LABELS['sybil']` / `REPORT_TYPE_LABELS.sybil` + `report_type === 'sybil'` grana u `_report_signed_summary()`/`summary()` |

Rezultat: „Log aktivnosti" stranica i njen PDF/CSV izvoz prikazuju Sybil analizu potpuno
ravnopravno sa Taint/Graph/Pathfinding/DEX Swap/Token Approval — isti izgled, ista logika,
nijedna posebna grana koda za "još jednu analizu".

## 4. Integracija sa Chain of Evidence (lanac dokaza)

Kad `POST .../sybil-analysis/run` dobije `custody` (analitičar potpiše razlog pristupa),
**svaka transakcija koja je upala u OZNAČEN klaster** dobija strukturiran `sybil_evidence`
dokaz na svom custody zapisu — isti mehanizam koji `case_token_approval_analysis` već
koristi za svoje nalaze, **ne** paralelan sistem.

| Šta | Fajl |
|---|---|
| `combine_frames_with_evidence_tag` (tagovanje reda izvorom), `sybil_custody_enrichment` (gradi `tx_id -> sybil_evidence` mapu) | `backend/app/features/case_sybil_analysis/service.py` |
| Prepoznavanje `sybil_evidence` u `custody_chain_for_transaction`/`list_case_transactions` | `backend/app/evidence/custody_log.py` |
| Prikaz u UI: bedž **👥 SYBIL** u listi transakcija, poseban panel (Blockchain činjenice / Heuristički zaključci) pri otvaranju transakcije | `frontend/.../custody-log/custody-log.component.html` (`.sybil-evidence-panel`), `.scss`, `custody-log.models.ts` |
| Tipovi | `frontend/.../core/models/shared.models.ts` (`SybilCustodyEvidence`) |

Svaki `sybil_evidence` dokaz je eksplicitno podeljen na **tačno dve** grupe (po zahtevu):

- **`blockchain_facts`** — `sender_address`, `contract_address`, `amount`, `timestamp`,
  `transaction_hash`, `block_number` (`null` ako evidencija ne deklariše), `function_name`
  — direktno iz reda evidencije, bez tumačenja.
- **`heuristic_conclusions`** — `cluster_id`, `address_count`, `activity_count`,
  `window_start/end/duration_seconds`, `identical_amount_ratio`, `repeated_address_count`,
  `risk_score`, `risk_level`, `reasons[]` — sve što je heuristika ZAKLJUČILA o klasteru,
  nikad predstavljeno kao činjenica.

Pun field-by-field opis: **`14. SYBIL-ANALIZA.md` §9.1**.

### 4.1 Automatski forenzički zaključak

Na dnu stranice `/sybil-analysis`, posle liste klastera, prikazuje se kratak automatski
sastavljen zaključak (`forensicConclusion` getter) — sastavljen **isključivo** od
brojeva/polja iz `result`: broj klastera, broj označenih adresa, najizraženiji klaster
(kontrakt/funkcija/adrese/aktivnosti/risk skor), broj kritičnih/visokorizičnih klastera, i
(kad je custody upisan) broj transakcija zabeleženih u lancu dokaza, sa linkom ka „Lanac
dokaza". Nijedna rečenica ne izlazi izvan onoga što `result` stvarno sadrži.

**Fajl:** `frontend/src/app/features/sybil-analysis/sybil-analysis.component.ts`
(`forensicConclusion` getter), prikazano u `.component.html` (`.sb-conclusion` sekcija).

## 5. Sistem za generisanje izveštaja (PDF)

Dugme **„Izvezi PDF izveštaj"** koristi **isti** potpisan-izveštaj mehanizam kao DEX
Swap/Taint/Pathfinding/Token Approval: `POST /api/v1/reports/register` registruje
kontrolni broj PRE nego što je PDF izgrađen (da bi mogao da se odštampa unutar samog
dokumenta), analitičar nacrta potpis kao izjavu, a PDF se sastavlja na klijentu
(`jspdf`/`jspdf-autotable`). Nijedna nova backend ruta nije dodata — `RegisterReportRequest
.report_type` je već slobodan string (`'sybil'` prosleđen bez ijedne izmene backend-a).

### 5.1 Šta izveštaj sadrži (tačno po zahtevu)

| Sekcija | Sadržaj |
|---|---|
| Zaglavlje | Case ID, ko je izvezao, evidencija, adresa/kontrakt filter, parametri (vremenski prozor, min. adresa), vreme generisanja, disclaimer |
| Rezime analize | kartice: broj klastera, broj označenih adresa, najviši risk score, broj nalaza u lancu dokaza |
| **Sybil klaster** (po klasteru) | kontrakt/funkcija, badge risk score/nivo (obojen), broj adresa/aktivnosti/trajanje prozora |
| **Ključni dokazi** | tabela transakcija tog klastera — pošiljalac, iznos, vreme, tx hash, funkcija (blockchain činjenice, jasno naslovljeno odvojeno od heurističkog dela) |
| Heuristički zaključak klastera | lista razloga (`reasons[]`) koji objašnjavaju risk skor tog klastera |
| **Forenzički pregled (Graph/Taint/Pathfinding/DEX)** | za svaki klaster: ako je dugme „Forenzički pregled" pokrenuto pre izvoza, prikazuju se stvarni nalazi (risk score, wallet-clustering podudaranje, taint %, direktna putanja, DEX swap-ovi); ako nije pokrenuto, izveštaj to iskreno kaže umesto da izmisli prazne redove |
| **Chain of Evidence** | potvrda koliko je transakcija upisano u lanac dokaza (`custody_findings_recorded`), ili napomena da prikaz nije upisan (izvezeno bez prethodnog potpisanog pokretanja) |
| **Završni zaključak** | isti tekst kao `forensicConclusion` na ekranu (§4.1) |
| Metodologija i ograničenja | sažeta verzija `14. SYBIL-ANALIZA.md` §2–§5/§8 |
| Potpis i overa | nacrtan potpis, pečat „LUSI", kontrolni broj, otisak sadržaja (SHA-256) |

### 5.2 Otisak sadržaja

`reportContentPayload()` heshuje: case ID, evidenciju, adresa/kontrakt filter, parametre, i
za svaki klaster — `cluster_id`, kontrakt, funkciju, sortiranu listu adresa, brojeve,
sortirane transakcije (samo `sender_address`/`amount`/`timestamp`/`tx_hash`, bez opisnog
teksta), i (kad je učitan) sažete brojeve iz Forenzičkog pregleda. Namerno **ne** uključuje
`reasons[]` — heš prati brojeve i adrese koje bi neko mogao osporiti, ne prozu (ista
konvencija kao DEX Swap izveštaj).

| Šta | Fajl |
|---|---|
| Ceo PDF izveštaj (`buildSybilPdf`, `reportContentPayload`, `confirmSignatureAndExport`) | `frontend/src/app/features/sybil-analysis/sybil-analysis.component.ts` |
| Dugme + dijalog potpisa (HTML) | `frontend/.../sybil-analysis.component.html` (`.signature-overlay`) |
| Stilovi dijaloga (identična kopija DEX Swap-ovog) | `frontend/.../sybil-analysis.component.scss` |
| `report_type: 'sybil'` labela/rezime u Log aktivnosti i na stranici za proveru izveštaja | `backend/app/exports/activity_report.py` (`_REPORT_TYPE_LABELS`, `_report_signed_summary`), `frontend/.../activity-log.component.ts` (`REPORT_TYPE_LABELS`, `summary()`), `frontend/.../report-verification.component.ts` (`summaryLabels`) |

## 6. Frontend stranica i Forenzički pregled

Ruta `/sybil-analysis`, link „Sybil & Bot mreže" u glavnom meniju. Puni opis kontrola
(adresa/kontrakt filter, vremenski prozor, min. broj adresa, dijalog razloga pristupa) i
Forenzičkog pregleda (automatski poziv Graph/Taint/Pathfinding/DEX Swap po klasteru, bez
custody-ja, opt-in dugme po klasteru): **`14. SYBIL-ANALIZA.md` §10/§10.1**.

## 7. Testovi

| Fajl | Pokriva |
|---|---|
| `backend/tests/test_sybil_analysis.py` | Algoritam: grupisanje, prag adresa, vremenski lanac, funkcija, risk skor, filtriranje, klampovanje parametara, disclaimer (18 testova) |
| `backend/tests/test_sybil_analysis_route.py` | GET/POST rute kroz pravi HTTP poziv: pasivan pregled bez loga, deliberatan pristup sa/bez custody-ja, **FAILED logovanje za nepostojeću adresu i za nepostojeći kontrakt** (novo, §3.1), `highest_risk_score`/`status` polja (8 testova) |
| `backend/tests/test_sybil_custody.py` | `combine_frames_with_evidence_tag`, `sybil_custody_enrichment` (podela na tačno 2 grupe, tx_id uparivanje, transakcije van klastera se preskaču), `record_custody_access` sa `sybil_evidence`, `custody_chain_for_transaction`, `list_case_transactions` (10 testova) |

Ukupno **36 automatskih backend testova** posvećenih isključivo Sybil & Bot Network
Analysis-u (deo od ukupno 415 u `backend/tests/`, svi zeleni).

Pokretanje:
```bash
cd backend
python -m pytest tests/test_sybil_analysis.py tests/test_sybil_analysis_route.py tests/test_sybil_custody.py -v
```

PDF izveštaj i Forenzički pregled su frontend-only funkcionalnosti (jspdf na klijentu,
`forkJoin` unakrsnih poziva) — provereno stvarnim `ng build` (bez grešaka) i ručnim
pregledom generisanog PDF-a, isti nivo provere kao ostali PDF izveštaji u ovoj aplikaciji
(nema headless-browser test paketa za frontend u ovom projektu).

## 8. Kompletna mapa fajlova

**Backend**

```
backend/app/analytics/sybil_analysis.py                     algoritam
backend/app/features/case_sybil_analysis/models.py          model zahteva
backend/app/features/case_sybil_analysis/router.py          GET/POST rute, FAILED logovanje
backend/app/features/case_sybil_analysis/service.py         chain-of-evidence enrichment
backend/app/evidence/custody_log.py                         prepoznavanje sybil_evidence (prošireno)
backend/app/exports/activity_report.py                      Log aktivnosti labele/rezime/report_type (prošireno)
backend/app/api/router.py                                   registracija rute (prošireno)
backend/scripts/seed_demo_sybil_evidence.py                 demo podaci
```

**Backend testovi**

```
backend/tests/test_sybil_analysis.py
backend/tests/test_sybil_analysis_route.py
backend/tests/test_sybil_custody.py
```

**Frontend**

```
frontend/src/app/features/sybil-analysis/sybil-analysis.component.ts     stranica, forenzički pregled, PDF izveštaj, zaključak
frontend/src/app/features/sybil-analysis/sybil-analysis.component.html
frontend/src/app/features/sybil-analysis/sybil-analysis.component.scss
frontend/src/app/features/sybil-analysis/sybil-analysis.api.ts
frontend/src/app/core/models/shared.models.ts                            SybilCluster, SybilAnalysisResult, SybilCustodyEvidence, ...
frontend/src/app/features/custody-log/custody-log.models.ts              sybil_evidence / has_sybil_evidence (prošireno)
frontend/src/app/features/custody-log/custody-log.component.html         panel sa nalazom (prošireno)
frontend/src/app/features/custody-log/custody-log.component.scss         (prošireno)
frontend/src/app/features/activity-log/activity-log.component.ts         labela/ikonica/rezime, report_type (prošireno)
frontend/src/app/features/report-verification/report-verification.component.ts  summaryLabels (prošireno)
frontend/src/app/app.routes.ts                                           ruta /sybil-analysis (prošireno)
frontend/src/app/app.component.ts                                        stavka menija (prošireno)
```

**Dokumentacija**

```
14. SYBIL-ANALIZA.md                    heuristika, API, ograničenja, frontend, forenzički pregled
SYBIL-ANALYSIS-IMPLEMENTATION.md        ovaj dokument — implementacioni popis
```
