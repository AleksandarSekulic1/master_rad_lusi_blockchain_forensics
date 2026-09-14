# Prelazak backend-a na Vertical Slice arhitekturu

Ovaj dokument prati postepeni refaktoring `backend/` dela projekta iz slojevite
(layered) arhitekture ka **Vertical Slice Architecture (VSA)** — organizaciji koda po
*funkcionalnosti* (feature/slice), a ne po tehničkom sloju (routes/services/models).

## Vodeći principi

1. **Ništa od forenzičke dokumentacije se ne dira.** Docstring-ovi koji objašnjavaju
   *zašto* je nešto urađeno na određeni način (custody lanac, case-sensitivity adresa,
   pravna ograničenja tumačenja rezultata — npr. [timezone_heuristics.py](master_rad_lusi_blockchain_forensics/backend/app/analytics/timezone_heuristics.py))
   ostaju netaknuti. To nije "šum" — to je dokumentacija koja sledećem developeru
   sprečava da nenamerno naruši forenzičku ispravnost alata.
2. **Nove funkcionalnosti = novi, samostalni moduli.** Cilj VSA-a ovde je da neko ko
   kasnije radi upgrade aplikacije (novi tip analize, nova vrsta izveštaja...) može da
   doda **jedan nov folder/slice** a da ne dira postojeći kod — umesto da mora da menja
   fajlove razbacane po 4-5 slojevitih foldera.
3. **Mala, proverena koraka.** Svaki slice se izdvaja pojedinačno, uz pokretanje pune
   test suite (`pytest`) posle svake izmene, pre prelaska na sledeći.
4. **Postojeća, radna infrastruktura se poštuje.** Npr. stranica "Testovi" u frontend-u
   čita test fajlove sa `backend/tests/test_*.py` **ne-rekurzivno**
   ([test_suite_runner.py:90](master_rad_lusi_blockchain_forensics/backend/app/services/test_suite_runner.py#L90)) — zato test fajlovi ostaju
   fizički u `backend/tests/` (flat), čak i kad je kod feature-a organizovan po slice-u
   u `app/features/<naziv>/`.

## Šta je urađeno do sada

### 1. Analiza (bez izmena koda)
Pregledan je ceo `backend/app` (analytics, api/routes, evidence, exports,
investigations, services — ukupno ~11.000 linija) i utvrđeno:
- Persistencija je JSON-fajlovi po entitetu (nema ORM-a/deljene relacione šeme), što
  vertikalno sečenje čini prirodnim.
- [app/investigations/](master_rad_lusi_blockchain_forensics/backend/app/investigations) je već delimično organizovan kao VSA (svaki od
  notes/links/pins ima svoj `*_models.py` + `*_repository.py` + `*_service.py`).
- [app/api/routes/cases.py](master_rad_lusi_blockchain_forensics/backend/app/api/routes/cases.py) (1231 linija) je najveći problem: jedan router fajl
  koji pokriva 8-9 različitih use-case-ova (case CRUD, upload, taint analysis, dex-swap,
  token-approval, path-finding, seed-suggestion...).
- `app/analytics/plugins/` (blacklist_check, chain_hopping, taint_analysis,
  wallet_clustering, anomaly_detection...) je već slice-orijentisan kroz
  plugin-manager/pipeline — ne treba ga menjati.

Pretražen je i ceo backend na "smeće" komentare (mrtav kod, TODO, komentari koji samo
ponavljaju kod) — **nije pronađen nijedan**. Postojeći komentari su svi suštinski
(objašnjavaju forenzičku ili poslovnu logiku) i ostaju.

### 2. Uklonjena duplirana funkcija `utc_now_iso()`
Funkcija je bila **identično kopirana** (isti kod, isti docstring) u 4 fajla:
[app/investigations/models.py](master_rad_lusi_blockchain_forensics/backend/app/investigations/models.py), [notes_models.py](master_rad_lusi_blockchain_forensics/backend/app/investigations/notes_models.py),
[links_models.py](master_rad_lusi_blockchain_forensics/backend/app/investigations/links_models.py) i (bivši) `pins_models.py`.

- **Novo:** [app/shared/time_utils.py](master_rad_lusi_blockchain_forensics/backend/app/shared/time_utils.py) — jedina definicija `utc_now_iso()`,
  zajednička za sve slice-ove (`app/shared/` je "shared kernel", ne feature).
- **Izmenjeno:** sva 4 fajla sada uvoze `utc_now_iso` iz `app.shared.time_utils` umesto
  da je redefinišu. Postojeći uvozi tipa `from app.investigations.models import
  utc_now_iso` i dalje rade nepromenjeno (re-export), tako da ništa drugo u kodu nije
  moralo da se dira.

### 3. Prvi izdvojeni slice: `investigation_pins`
Uzet je najmanji, najsamostalniji kandidat kao dokaz koncepta pre nego što se ide na
veće (kao `cases.py`).

**Novo — `app/features/investigation_pins/`** (kompletna funkcionalnost "zakačenih
čvorova" na jednom mestu):
- [models.py](master_rad_lusi_blockchain_forensics/backend/app/features/investigation_pins/models.py) — `PinNodeRequest`, `PinnedNode` (Pydantic modeli)
- [repository.py](master_rad_lusi_blockchain_forensics/backend/app/features/investigation_pins/repository.py) — čitanje/pisanje `pinned_nodes.json`
- [service.py](master_rad_lusi_blockchain_forensics/backend/app/features/investigation_pins/service.py) — poslovna logika (upsert po adresi, validacija roditeljske istrage)
- [router.py](master_rad_lusi_blockchain_forensics/backend/app/features/investigation_pins/router.py) — REST endpoint-i (`GET`/`PUT`/`DELETE
  /investigations/{id}/pins`)

**Obrisano** (sadržaj prebačen u gornje fajlove, ništa izgubljeno):
- `app/investigations/pins_models.py`
- `app/investigations/pins_repository.py`
- `app/investigations/pins_service.py`
- `app/api/routes/investigation_pins.py`

**Izmenjeno:**
- [app/api/router.py](master_rad_lusi_blockchain_forensics/backend/app/api/router.py) — uvoz router-a sada pokazuje na
  `app.features.investigation_pins.router` umesto na `app.api.routes.investigation_pins`.

**Test:** [tests/test_investigation_pins.py](master_rad_lusi_blockchain_forensics/backend/tests/test_investigation_pins.py) — sadržaj bivšeg
`tests/test_investigator_pins.py`, samo sa ažuriranim uvozima; ostaje fizički u flat
`tests/` folderu (razlog: pravilo #4 gore).

**Šta ostaje deljeno (namerno, van slice-a):** `app/investigations/repository.py`,
`models.py` i `service.py` (osnovni `InvestigationCase` entitet + generički JSON
collection I/O) — koristi ih i `pins`, i `notes`, i `links`, pa pripadaju zajedničkom
"investigations" kontekstu, ne jednom slice-u.

### 4. Verifikacija
- `pytest tests/` — **372 testa, svi prolaze** (nepromenjeno u odnosu na pre refaktora).
- Provereno da FastAPI aplikacija i dalje ispravno gradi rutu
  `/api/v1/investigations/{investigation_id}/pins` (preko OpenAPI šeme).

## Plan za nastavak (sledeći koraci, jedan po jedan)

1. `investigation_notes` i `investigation_links` — isti obrazac kao `pins` (najmanji rizik).
2. Razbijanje **`cases.py`** (najveći dobitak, najveći rizik) na zasebne slice-ove, npr.:
   `case_management` (CRUD + evidence), `case_taint_analysis`, `case_dex_swap_analysis`,
   `case_token_approval`, `case_path_finding`, `case_seed_suggestion`.
3. Preostali route-ovi (`custody`, `exports`, `graph`, `onchain`, `addresses`, `users`,
   `auth`, `activity_log`) — svaki već ima jasnu 1:1 granicu router↔servis, samo fizičko
   premeštanje u `app/features/<naziv>/`.
4. `app/evidence/` (audit/custody log, hashing) ostaje **shared kernel** — koriste ga
   skoro svi slice-ovi podjednako, ne sme se duplirati po feature-ima.
