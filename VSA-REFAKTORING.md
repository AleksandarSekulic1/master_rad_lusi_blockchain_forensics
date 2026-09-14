# Prelazak backend-a na Vertical Slice arhitekturu

Prati koji fajl je nastao/izmenjen/obrisan pri prebacivanju `backend/` dela projekta sa
slojevite (layered) na Vertical Slice arhitekturu (organizacija po funkcionalnosti, ne po
tehničkom sloju). Test fajlovi ostaju flat u `backend/tests/` (to čita stranica "Testovi"
u frontend-u, ne-rekurzivno) i dalje se prati puna `pytest` suita posle svake izmene.

## app/shared/time_utils.py — novo

Jedina definicija `utc_now_iso()`. Ranije je identičan kod bio kopiran u 4 fajla
(`investigations/models.py`, `notes_models.py`, `links_models.py`, `pins_models.py`).

**Izmenjeno** (uvoze `utc_now_iso` odavde umesto da je redefinišu, bez uticaja na
postojeće pozivaoce jer je re-export ostao):
- `app/investigations/models.py`

## app/features/investigation_pins/ — novo (slice)

Kompletna funkcionalnost "zakačenih čvorova" (`GET`/`PUT`/`DELETE
/investigations/{id}/pins`): `models.py`, `repository.py`, `service.py`, `router.py`.

**Obrisano:** `app/investigations/pins_models.py`, `pins_repository.py`,
`pins_service.py`, `app/api/routes/investigation_pins.py`.

## app/features/investigation_notes/ — novo (slice)

Kompletna funkcionalnost istražiteljskih beleški (`/investigations/{id}/notes`, beleška
vezana za adresu ili transakciju): `models.py`, `repository.py`, `service.py`,
`router.py`.

**Obrisano:** `app/investigations/notes_models.py`, `notes_repository.py`,
`notes_service.py`, `app/api/routes/investigation_notes.py`.

## app/features/investigation_links/ — novo (slice)

Kompletna funkcionalnost istražiteljskih veza (`/investigations/{id}/links`, pretpostavljena
veza između dve adrese): `models.py`, `repository.py`, `service.py`, `router.py`.

**Obrisano:** `app/investigations/links_models.py`, `links_repository.py`,
`links_service.py`, `app/api/routes/investigation_links.py`.

## app/investigations/ — ostaje (shared kernel za investigator sloj)

`models.py` (entitet `InvestigationCase`), `repository.py` (generički JSON collection
I/O), `service.py` (`get_investigation`, CRUD nad `InvestigationCase`) — koriste ih sva
tri gornja slice-a podjednako, pa ostaju zajednički, van pojedinačnih slice-ova.

## app/api/router.py — izmenjeno

Uvozi za pins/notes/links router sada pokazuju na `app.features.<naziv>.router` umesto
na `app.api.routes.<naziv>`.

## tests/ — izmenjeno (samo uvozi, sadržaj testova nepromenjen)

`test_investigation_pins.py` (preimenovan iz `test_investigator_pins.py`),
`test_investigator_notes.py`, `test_investigator_links.py`,
`test_case_management_full_pass.py` — uvoze modele/servise sa novih putanja.

## app/shared/case_access.py, app/shared/custody_recording.py — novo

Zajednička infrastruktura za sve "case"-slice-ove (nastalo razbijanjem `cases.py`, vidi
ispod): `case_access.py` (`get_case_or_404`, `get_case_evidence_paths_or_404`,
`filter_evidence_paths`) i `custody_recording.py` (`TransactionCustodyEntry`,
`record_custody_access`) — svaka analiza koja se tretira kao namerni pristup dokazu
(taint/pathfinding/behavioral/dex-swap/token-approval) upisuje kroz ovo isto mesto.

## app/api/routes/cases.py (1231 linija) — obrisano, razbijeno na 8 slice-ova

Router je mešao CRUD nad slučajem sa 7 različitih analiza. Svaki use-case sada ima svoj
folder u `app/features/`:

- **case_management/** — CRUD slučaja, evidence lista/brisanje, status (`models.py`,
  `router.py`)
- **case_graph/** — pregled transakcionog grafa + CSV izvoz (`router.py`)
- **case_behavioral_analysis/** — analiza doba dana (GET + POST run) (`models.py`,
  `router.py`)
- **case_dex_swap_analysis/** — detekcija DEX swap-ova (GET + POST run) (`models.py`,
  `router.py`)
- **case_token_approval_analysis/** — token approval analiza/istorija/korelacija + POST
  run, uključujući pomoćne funkcije za upis u lanac dokaza (`models.py`, `service.py`,
  `router.py`)
- **case_seed_suggestion/** — predlozi seed adresa za taint analizu (`router.py`)
- **case_analytics_run/** — pokretanje plugin pipeline-a (taint/chain-hopping/peel-chains/
  wallet-clustering) (`models.py`, `router.py`)
- **case_pathfinding/** — BFS pretraga puta, uklj. "nearest CEX" (`models.py`,
  `router.py`)

## app/api/router.py — izmenjeno

Jedan uvoz `cases_router` zamenjen sa 8 uvoza (po jedan iz svakog gornjeg slice-a), svi i
dalje mount-ovani pod `/cases` sa istim `authenticated` gate-om.

## tests/ — izmenjeno (samo uvozi/monkeypatch mete, sadržaj testova nepromenjen)

`test_custody_log.py`, `test_token_approval_custody.py` — uvoze
`TransactionCustodyEntry`/`record_custody_access`/`combine_frames_with_evidence_tag`/
`token_approval_custody_enrichment` sa novih putanja. `test_path_finding_bfs.py` — monkeypatch
mete `get_case`/`get_case_evidence_paths` prebačene sa starog `cases` modula na
`app.shared.case_access`.

## Status

`pytest tests/` → **372 testa, svi prolaze.** Svih 25 `/cases/...` putanja iz starog
router-a i dalje postoji (provereno preko OpenAPI šeme). Nijedan drugi modul u kodu više ne
referencira obrisani `app.api.routes.cases`.

## app/api/routes/ — obrisano (poslednjih 13 route-ova preseljeno)

Svaki od preostalih route fajlova je već bio samostalna, 1:1 router↔servis celina (za
razliku od `cases.py`), pa je premeštanje bilo čisto fizičko — bez cepanja, bez menjanja
logike. Svaki dobija sopstveni folder u `app/features/` (samo `router.py`, pošto su im
Pydantic request modeli mali i već kolocirani uz rutu koja ih koristi):

`auth/`, `addresses/`, `users/`, `investigation_management/` (CRUD nad
`InvestigationCase` kontejnerom — nazvan drugačije od `investigation_notes/links/pins`
da se ne meša sa opštim `app/investigations/` paketom), `graph/` (pregled grafa direktno
nad raw CSV-om iz `data/raw/`, odvojeno od `case_graph` koji radi nad dokazima
slučaja), `onchain/`, `upload/`, `custody/`, `exports/`, `reports/`, `analytics/` (raw
CSV plugin run, odvojeno od `case_analytics_run`), `activity_log/`, `test_suite/`.

Folder `app/api/routes/` je posle ovoga bio prazan i obrisan je u celosti.

**Izmenjeno:** `app/api/router.py` (svi uvozi), `app/analytics/ingestion.py` (komentar),
`tests/test_activity_report.py`, `tests/test_custody_export_audit.py`,
`tests/test_token_approval_run_route.py` (putanje uvoza/monkeypatch meta, sadržaj testova
nepromenjen).

**Ostaje van `app/features/` (namerno, deljeno):** `app/services/*.py` i
`app/analytics/*.py` — servisni/algoritamski moduli koje koristi više od jednog slice-a
(npr. `report_registry.py` koriste i `reports` i `activity_log`; `address_enrichment.py`
koriste i `addresses` i `case_pathfinding`), pa ostaju zajednička infrastruktura umesto da
se dupliraju.

## Status

`pytest tests/` → **372 testa, svi prolaze.** OpenAPI šema pokazuje svih 64 ruta iz
originalne aplikacije (0 izgubljenih/duplih), `app.api.routes` više nigde nije
referenciran u kodu.

## Rezultat

Ceo backend je sada organizovan po funkcionalnosti (`app/features/<naziv>/`, ~24 slice-a),
umesto po tehničkom sloju. `app/api/routes/` više ne postoji; `app/shared/` je
kernel-infrastruktura (vreme, pristup slučaju, upis u lanac dokaza); `app/services/` i
`app/analytics/` su namerno ostali zajednički moduli koje slice-ovi pozivaju, ne
duplira ih se.
