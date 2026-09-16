# Status u odnosu na "Predlog teme za master rad"

Prolazi kroz **svaku** stavku iz `Predlog teme za master rad (3).pdf`, redom kako je
napisana u predlogu, i kaže: da li je urađeno, gde u kodu, i (ako je već dokumentovano) u
kom MD fajlu — da se ne duplira opis, samo uputi na pravi fajl.

## Način funkcionisanja i korisnički interfejs (3 faze digitalne forenzike)

| Stavka iz predloga | Status | Dokumentovano u |
|---|---|---|
| GUI kroz dugmiće/vizuelne menije (po ugledu na Autopsy) | ✅ Angular frontend, stranica po stranica | — (stilsko poređenje sa Autopsy-jem je subjektivno, ne postoji formalna provera) |
| Prikupljanje i čuvanje dokaza (učitavanje + heš + lanac dokaza) | ✅ | `BLOCKCHAIN-UVOZ.md`, `LANAC-DOKAZA.md` |
| Pregledanje i analiza dokaza | ✅ (11 analiza — vidi tabelu ispod) | `TAINT-ANALIZA.md`, `PATHFINDING-ANALIZA.md`, `BEHAVIORAL-ANALIZA.md`, `DEX-SWAP-ANALIZA.md`, `TOKEN-APPROVAL-IMPLEMENTATION.md`, `GRAPH.md` |
| Prezentacija dokaza (izveštaji) | ✅ PDF/CSV izveštaj slučaja | `TAINT-ANALIZA.md` odeljak 6 (PDF), `CASE-MANAGEMENT-IMPLEMENTATION.md` |

## Tehnička realizacija projekta

| Stavka iz predloga | Status | Dokumentovano u |
|---|---|---|
| Modularna arhitektura, izdvojene funkcionalnosti u servise, frontend/backend potpuno odvojeni | ✅ Vertical Slice Architecture — `backend/app/features/` (26 slice-ova), Angular frontend preko REST-a | `VSA-REFAKTORING.md` |
| Plaginovi — nezavisno procesiranje različitih koncepata plaćanja | ✅ `backend/app/analytics/plugins/` (7 plugin-a, `manager.py` pipeline) | `GRAPH.md` |
| **UTXO model (Bitcoin i slične mreže)** | ✅ normalizacija pri uvozu u isti CSV oblik, bez izmene downstream-a | `BITCOIN-UTXO-PLAN.md` (plan/arhitektonska odluka), `BITCOIN-UVOZ.md` (uputstvo + rezultati testiranja) |
| **Account-based model (Ethereum, pametni ugovori, ERC-20)** | ✅ izvorna implementacija | `BLOCKCHAIN-UVOZ.md` |
| Testiranje/usavršavanje inicijalno na EVM-kompatibilnim mrežama | ✅ | `BLOCKCHAIN-UVOZ.md`, `ETHEREUM-DEMO-TESTIRANJE.md` |

## Predložene tehnologije

| Tehnologija | Status | Gde |
|---|---|---|
| Angular + TypeScript (frontend) | ✅ | `frontend/` |
| Python (backend) | ✅ | `backend/` |
| REST API preko FastAPI | ✅ | `backend/app/api/router.py` + 26 feature router-a |
| Pandas (obrada CSV-a) | ✅ | `app/analytics/ingestion.py` i svi ingestion moduli |
| NetworkX (rekonstrukcija grafa) | ✅ | `app/analytics/graph_building.py` |
| GitHub, otvoren kod | ✅ | repo `AleksandarSekulic1/master_rad_lusi_blockchain_forensics`, redovni commit-ovi |

## Pregled planiranih funkcionalnosti

| Funkcionalnost iz predloga | Status | Dokumentovano u |
|---|---|---|
| Bezbedno učitavanje podataka (CSV import) | ✅ | `BLOCKCHAIN-UVOZ.md` |
| Lanac dokaza — hešovanje (SHA-256) | ✅ | `LANAC-DOKAZA.md` |
| Revizorski trag (Audit log) | ✅ append-only, reproduktivno | `LANAC-DOKAZA.md` |
| Dashboard + Search Engine (adresa → transakcije, rizik, klaster) | ✅ | — (opisano usput u više fajlova, nema posebnog) |
| Filtriranje + vremenska korelacija (Time Slider) | ✅ na stranici Graf (play/pause, pozicija transakcije, kumulativni total) | `TAINT-ANALIZA.md` odeljak 5.1 |
| Path Finding (najkraća/najverovatnija putanja) | ✅ | `PATHFINDING-ANALIZA.md` |
| **Blacklist provera** (OFAC, Chainabuse) | ✅ + **stvarne** (ne samo simulirane) sankcionisane adrese dodate | `GRAPH.md`, `BITCOIN-UVOZ.md`, `ETHEREUM-DEMO-TESTIRANJE.md` odeljak 8 |
| **Risk Scoring** | ✅ | `GRAPH.md` |
| **Wallet Clustering** | ✅ | `GRAPH.md` (+ `TAINT-ANALIZA.md` odeljak 9 — zašto se ne koristi u taint obračunu) |
| **Anomaly Detection** (Isolation Forest) | ✅ | `GRAPH.md` |
| **Peel Chains** | ✅ | `GRAPH.md` |
| **Chain Hopping** | ✅ | `GRAPH.md` |
| Case Management + Depo dokaza | ✅ | `CASE-MANAGEMENT-IMPLEMENTATION.md` |
| Izvoz izveštaja (PDF/CSV) | ✅ | `TAINT-ANALIZA.md` odeljak 6 |
| Izvoz grafa (PNG, SVG, GraphML, GEXF) | ✅ sva 4 formata potvrđena | — (frontend: cytoscape PNG/SVG; backend: `app/features/exports/`) |

## Pravci daljeg razvoja (eksplicitno van obima predloga)

| Stavka | Status |
|---|---|
| Duboka analiza pametnih ugovora / Zero-Knowledge Proofs | ❌ namerno neurađeno — predlog ih sam stavlja u "buduća istraživanja" |
| Steganografija (ekstrakcija/dešifrovanje malicioznog sadržaja u Input Data) | ❌ namerno neurađeno — isto, van obima |

Ove dve stavke **ne predstavljaju nedostatak** — predlog ih eksplicitno izuzima iz trenutnog
rada.

## Metodologija i način testiranja (odeljak "Praktično dokazivanje")

Predlog traži testiranje na **stvarnim, istorijskim podacima o poznatom incidentu**
(hakovana menjačnica ili organizovana prevara), da se dokaže da alat uspešno obradi
transakcije, nacrta mrežu i izoluje tačne adrese na kojima je novac sakriven.

| Lanac | Incident | Status | Dokumentovano u |
|---|---|---|---|
| Bitcoin | **Garantex Europe OÜ** — OFAC sankcionisana menjačnica (2022-04-05), korišćena za pranje novca | ✅ prava adresa, uživo povučena istorija (4 tx), blacklist pogodak potvrđen | `BITCOIN-UVOZ.md` |
| Ethereum | **Ronin Bridge hak** (23.03.2022, ~625 miliona $, pripisano Lazarus Group-i), OFAC sankcionisano 14.04.2022 | ✅ prava adresa, uživo povučena istorija (430 tx), blacklist pogodak + risk score 100 potvrđeni | `ETHEREUM-DEMO-TESTIRANJE.md` odeljak 8 |

## Zaključak

Sve stavke iz predloga teme (osim dve eksplicitno najavljene kao buduće istraživanje) su
implementirane i **stvarno provereno testirane** — uključujući centralni zahtev metodologije
(testiranje na pravim, istorijskim podacima dva različita, dobro dokumentovana incidenta,
po jedan za svaki podržani model plaćanja).
