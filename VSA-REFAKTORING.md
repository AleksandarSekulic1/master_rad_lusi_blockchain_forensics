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

## Status

`pytest tests/` → **372 testa, svi prolaze.** Backend pretražen na "smeće" komentare
(mrtav kod, TODO) — nije nađen nijedan; postojeći komentari su forenzičko/poslovno
obrazloženje i ostaju netaknuti.

## Sledeće na redu

Razbijanje `app/api/routes/cases.py` (1231 linija, meša CRUD/evidence/taint/dex-swap/
token-approval/path-finding/seed-suggestion) na zasebne slice-ove — najveći preostali
zahvat.
