# Case Management / Istražiteljski sloj — dokumentacija implementacije

Status: **Završeno (koraci 1–12).** Svih pet kategorija Case Management-a (slučaj/istraga,
beleške na adresama, beleške na transakcijama, zakačeni čvorovi, istražiteljske veze) je
implementirano, trajno se čuva, vezano je za slučaj, integrisano u Graf UI, testirano po
celoj čeklisti i pregledano. Postojeće analize (Graf / Taint / Pathfinding / Behavioral /
DEX) su **nepromenjene** i potvrđeno rade.

Napomena: engleska verzija ove dokumentacije (running log po koracima) ostaje u git
istoriji (commit `c391bd2`). Ovaj dokument je prevod + konsolidovana referenca, sa
dodatom sekcijom **[§13 „Kako se testira"](#13-kako-se-testira)** (automatizovano +
ručno, sa konkretnim primerom).

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [0. Cilj](#0-cilj) | pet zahteva i pravilo odvajanja |
| [1. Kontekst projekta](#1-kontekst-projekta-kako-je-bilo-pre) | kako je aplikacija izgledala pre ovog sloja |
| [2. Konceptualna arhitektura](#2-konceptualna-arhitektura) | dijagram: činjenice → analize → istražiteljski sloj |
| [3. Backend fajlovi](#3-backend-fajlovi) | šta je dodato/izmenjeno na backendu |
| [4. Frontend fajlovi](#4-frontend-fajlovi) | šta je dodato/izmenjeno na frontendu |
| [5. Modeli podataka](#5-modeli-podataka-na-disku) | oblik zapisa na disku i veze između njih |
| [6. API endpoint-i](#6-api-endpoint-i) | sve rute istražiteljskog sloja |
| [7. Integracija sa grafom](#7-integracija-sa-grafom) | šta se vidi na stranici „Graf" |
| [8. Beleške — implementacija](#8-beleške--implementacija) | |
| [9. Zakačeni čvorovi — implementacija](#9-zakačeni-čvorovi--implementacija) | |
| [10. Istražiteljske veze — implementacija](#10-istražiteljske-veze--implementacija) | |
| [11. Odvajanje činjenica od zaključaka](#11-odvajanje-činjenica-od-zaključaka) | kako je odvajanje sprovedeno |
| [12. Baza / relacije](#12-baza--relacije) | (nema baze — flat JSON) |
| [13. KAKO SE TESTIRA](#13-kako-se-testira) | **automatizovano + ručno + konkretan primer** |
| [14. Važne projektne odluke](#14-važne-projektne-odluke) | |
| [15. Poznata ograničenja](#15-poznata-ograničenja) | |
| [16. Namerno NIJE urađeno](#16-namerno-nije-urađeno-buduća-poboljšanja) | buduća poboljšanja |
| [17. Istorija implementacije (koraci 1–12)](#17-istorija-implementacije-koraci-112) | kratak rezime svakog koraka |

---

## 0. Cilj

Dodati „istražiteljski sloj" iznad postojećih forenzičkih alata, koji istražitelju
omogućava da:

1. dodaje beleške na adrese / čvorove grafa,
2. dodaje beleške na transakcije / grane grafa,
3. zakači (fiksira) važne čvorove na grafu,
4. ručno poveže dve adrese na osnovu **vanlančanih (off-chain)** dokaza,
5. drži ove istražiteljske zaključke **jasno odvojene** od on-chain činjenica.

Tvrdi zahtev koji prožima svih pet tačaka je **odvajanje**: ništa što istražitelj tvrdi ne
sme biti pomešano sa podacima izvedenim iz uvezenih blockchain dokaza, niti se sme s njima
zameniti.

---

## 1. Kontekst projekta (kako je bilo pre)

Ključne činjenice o postojećoj aplikaciji, koje su odredile dizajn ovog sloja:

- **Nema baze podataka.** Sve je flat JSON/JSONL na disku:
  `data/cases/<case_id>/case.json` + `data/cases/index.json` za evidencijske slučajeve,
  `data/report_registry.json`, `data/users.json`, `logs/audit_log.jsonl` (append-only log
  aktivnosti), `logs/custody_log.jsonl` / `custody_evidence_log.jsonl` (lanac dokaza).
  Helperi `_read_json` / `_write_json` su mali i ponavljaju se po modulu
  (`app/services/case_management.py`).
- **Dva pojma „slučaja".** Postojeći **evidencijski `Case`** (`data/cases/`) drži uvezene
  on-chain podatke (CSV/on-chain izvoz) i njihov lanac dokaza. Novi **`InvestigationCase`
  („istraga")** (`data/investigations/`) drži istražiteljsku interpretaciju. **Nisu
  povezani** u modelu podataka.
- **Graf se gradi dinamički, nikad se ne čuva.** `build_case_graph(...)`
  (`app/analytics/case_graph.py`) na **svaki** zahtev očisti CSV → spoji u jedan
  DataFrame → `build_transaction_graph(df)` → `networkx.DiGraph`. ID čvora je **sam string
  adrese, doslovno** (case-sensitive, bez normalizacije). Grana je jedna po paru
  `(pošiljalac, primalac)`, sa listom pojedinačnih transakcija.
- **Identitet transakcije** (`app/evidence/tx_identity.py::transaction_id`): heš
  transakcije ako postoji, inače
  `"row-" + sha256(pošiljalac|primalac|iznos|vreme|naziv_dokaza)[:16]`. Isti identitet
  koristi lanac dokaza. Beleške na transakcijama koriste ovaj isti `tx_id` — nije uveden
  nikakav nov mehanizam.
- **Layout grafa (frontend):** cytoscape + `cytoscape-fcose`. Svako renderovanje poziva
  `cytoscape({ layout: { name: 'fcose', randomize: true, ... }})` — **nema preset
  layout-a, nema sačuvanih koordinata, nema `node.lock()`, nema čuvanja pozicija**. Layout
  je drugačiji na svako učitavanje. Ovo je bio glavni izazov za zahtev #3 (zakačivanje).
- **Overlay mehanizam već postoji** — DEX-swap overlay u
  `graph-visualization.component.ts` (`renderSwapOverlay` / `buildSwapEdgeElements` /
  `toggleDexSwapOverlay` + `edge.swap-edge` stilovi) dodaje/uklanja isprekidane grane
  **bez ponovnog pokretanja layout-a**. Iskorišćen kao šablon za istražiteljske veze.
- **Auth:** JWT bearer token (localStorage), `get_current_user` → `{ id, username, role }`,
  role `admin` i `analyst`. Većina ruta traži samo prijavljenog korisnika.
- **Konvencija:** stranice analize „izgledaju konzistentno, ostaju nezavisne" — svaka
  gradi svoj cytoscape, nema deljene graf-komponente.

---

## 2. Konceptualna arhitektura

```
                              BLOCKCHAIN ČINJENICE
             (evidencijski Case: data/cases/<case_id>/ + data/raw/*.csv)
                                     │
   ┌─────────────────────────────────┼─────────────────────────────────┐
   │        automatski izvedeno, na zahtev, nikad se ne čuva:          │
   │   Graf analiza · Taint analiza · Pathfinding · Behavioral        │
   │   analiza · DEX Swap analiza   (app/analytics/*, NEPROMENJENO)   │
   └─────────────────────────────────┼─────────────────────────────────┘
                                     │   samo ulaz (stringovi adresa,
                                     │   tx id-jevi, pozicije čvorova)
                                     ▼
                              ISTRAŽITELJSKI SLOJ
              (Istraga / „slučaj": data/investigations/<id>/)
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
           Beleške              Zakačeni čvorovi        Istražiteljske veze
       notes.json            pinned_nodes.json          links.json
   (cilj = adresa ILI tx)   (po adresi, x/y)      (2 adrese, neusmerena,
                                                   razlog/dokaz/pouzdanost)
```

- **Analitički sloj je čist, jednosmeran ulaz.** Nijedan modul istražiteljskog sloja ne
  uvozi ništa iz `app/analytics`; ništa u `app/analytics` ne zna da istražiteljski sloj
  postoji. Istražiteljski podaci se nikad ne ubacuju u `build_transaction_graph`, u
  node-link JSON, ni u izvoze slučaja.
- **Skladište: flat JSON, bez baze** — jedan direktorijum po istrazi, u stilu postojećeg
  `case_management.py`. Brisanje istrage briše ceo direktorijum (kaskadno na sve
  potkolekcije).
- **Frontend: nema nove rute.** Ceo sloj se koristi sa stranice **„Graf"** — preko
  `<select>`-a za istragu, bloka akcija u panelu detalja čvora, modala, overlay-a i panela
  „Pregled slučaja".

---

## 3. Backend fajlovi

*(novi paket `app/investigations/`, osim gde je naznačeno. `app/services/case_management.py`
je stariji, nepovezani servis evidencijskog slučaja i NIJE deo ovog sloja.)*

| Fajl | Uloga |
|---|---|
| `app/investigations/__init__.py` | oznaka paketa |
| `app/investigations/repository.py` | koren skladišta — putanje (`investigation_dir`), deljeni JSON I/O (`read_json` / `write_json` / `read_collection` / `write_collection`), CRUD indeksa i zapisa istrage, `delete_record` (rmtree) |
| `app/investigations/models.py` | `InvestigationCase`, `InvestigationCaseCreate`, `InvestigationCaseUpdate` |
| `app/investigations/service.py` | orkestracija CRUD-a istrage; `InvestigationCaseNotFoundError` |
| `app/investigations/notes_models.py` | `InvestigatorNote`, `…Create`, `…Update`; `NoteTargetType`; ograničenja dužine |
| `app/investigations/notes_repository.py` | load/save `notes.json` (tanak omotač oko `repository.read_collection`) |
| `app/investigations/notes_service.py` | CRUD beleški, `list_notes(address? / tx_id? / target_type?)`; `InvestigatorNoteNotFoundError` |
| `app/investigations/links_models.py` | `InvestigatorLink`, `…Create`, `…Update`; `LinkConfidence`; ograničenja; `involves(address)` |
| `app/investigations/links_repository.py` | load/save `links.json` (tanak omotač) |
| `app/investigations/links_service.py` | CRUD veza, `list_links(address?)` (neusmereno); `InvestigatorLinkNotFoundError` |
| `app/investigations/pins_models.py` | `PinNodeRequest`, `PinnedNode` |
| `app/investigations/pins_repository.py` | load/save `pinned_nodes.json` (tanak omotač) |
| `app/investigations/pins_service.py` | `list_pins`, `set_pin` (upsert po adresi), `clear_pin`; `PinnedNodeNotFoundError` |
| `app/api/routes/investigations.py` | `/investigations` CRUD |
| `app/api/routes/investigation_notes.py` | `/investigations/{id}/notes` |
| `app/api/routes/investigation_links.py` | `/investigations/{id}/links` (+ `disclaimer` u listi) |
| `app/api/routes/investigation_pins.py` | `/investigations/{id}/pins` |
| `app/api/router.py` | *(izmenjeno)* registruje četiri routera pod deljenom `authenticated` zavisnošću |
| `app/paths.py` | *(izmenjeno)* `INVESTIGATIONS_DIR = DATA_DIR / 'investigations'` |

**Backend testovi:** `test_investigation_management.py` (11), `test_investigator_notes.py`
(25), `test_investigator_links.py` (20), `test_investigator_pins.py` (11),
`test_case_management_persistence.py` (7), `test_case_management_full_pass.py` (19). Svi
izoluju skladište preko `monkeypatch.setattr(repository, '_root', …)`.

---

## 4. Frontend fajlovi

| Fajl | Uloga |
|---|---|
| `models/blockchain-forensics.models.ts` | *(izmenjeno)* + `Investigation`, `InvestigatorLink(+Confidence, +ListResponse)`, `InvestigatorNote(+TargetType, +ListResponse)`, `PinnedNode(+ListResponse)` |
| `core/services/api.service.ts` | *(izmenjeno)* + `listInvestigations`; beleške `get/add/update/delete`; veze `get/add/delete`; zakačivanje `get/pin/unpin` |
| `features/graph-visualization/…component.ts` | *(izmenjeno)* `<select>` za istragu + `localStorage` restore; overlay veza (build/render/toggle/select/remove); stanje zakačenih čvorova učitano iz API-ja i sinhronizovano; povezivanje modala + broj beleški; podaci za „Pregled slučaja" + handleri „na graf / otkači / detalji" |
| `features/graph-visualization/…component.html` | *(izmenjeno)* red sa `<select>`-om za istragu; toggle za overlay veza; panel detalja veze (`<aside>`); razdvojen blok istražiteljskih akcija u panelu čvora; montiranje panela „Pregled slučaja"; dva reda legende |
| `features/graph-visualization/…component.scss` | *(izmenjeno)* narandžasti akcenat istražiteljskog sloja, panel veze + „amber" badge pouzdanosti, `.danger-ghost`, akcioni „chip"-ovi, markeri legende |
| `features/investigator-node-dialog/` *(novo, .ts/.html/.scss)* | `InvestigatorNodeDialogComponent` — modal: tab „Beleške" (lista + dodaj + izmeni + obriši) i tab „Nova veza", otvara se iz panela čvora |
| `features/case-overview-panel/` *(novo, .ts/.html/.scss)* | `CaseOverviewPanelComponent` — kompaktan pregled slučaja: naziv/opis + brojevi beleški / zakačenih adresa / veza, svaki proširiv |
| `scripts/pin-node-behavior-check.mjs` *(novo)* | headless cytoscape+fcose provera: zakačen čvor ostaje fiksiran kroz ponovni layout, otkačivanje ga oslobađa (12 provera) |
| `scripts/investigator-link-overlay-check.mjs` *(novo)* | headless provera: grana veze je vizuelno različita i odvojena; stvarne transakcione grane netaknute pri toggle-u (12 provera) |

*Nema promena u `angular.json`/rutama; ne postoji Karma runner, otud `node` smoke skripte.*

---

## 5. Modeli podataka (na disku)

Nema relacione baze — ovo su JSON oblici na disku. Svaki podređeni zapis nosi
`investigation_id` **i** živi unutar `data/investigations/<investigation_id>/`.

**`InvestigationCase`** — `investigation.json` (+ sažetak u `index.json`)
```
id: str(12-hex)  ·  name: str(1..200)  ·  description: str(0..5000)|null
created_at: iso  ·  updated_at: iso        (updated_at raste pri izmeni; created_at nikad)
```

**`InvestigatorNote`** — `notes.json → { "notes": [ … ] }`
```
id: str(12-hex)  ·  investigation_id: str  ·  target_type: "address" | "transaction"
address: str|null   (postavljen ⟺ target_type=="address")
tx_id:   str|null   (postavljen ⟺ target_type=="transaction"; isti tx id kao lanac dokaza)
text: str(1..10000)  ·  author: str  ·  created_at: iso  ·  updated_at: iso
```

**`PinnedNode`** — `pinned_nodes.json → { "pinned_nodes": [ … ] }`
```
investigation_id: str  ·  address: str(1..256, doslovno)   ← ključ (upsert)
x: float|null  ·  y: float|null        (cytoscape model-koordinate zabeležene pri zakačivanju)
pinned_by: str  ·  pinned_at: iso  ·  updated_at: iso
```

**`InvestigatorLink`** — `links.json → { "links": [ … ] }`
```
id: str(12-hex)  ·  investigation_id: str
source_address: str(1..256)  ·  target_address: str(1..256)   (redosled nije bitan)
directed: bool = false        (uvek false — neusmerena istražiteljska asocijacija)
reason:   str(1..5000)        (slobodan tekst — „zašto")
evidence: str(1..2000)        (slobodan tekst — vanlančana referenca)
confidence: "Low" | "Medium" | "High"
author: str  ·  created_at: iso  ·  updated_at: iso
```

**Relacije:** `InvestigationCase (1) ──< Beleška (0..N)`, `──< PinnedNode (0..N, jedinstven
po adresi)`, `──< InvestigatorLink (0..N)`. Nema strane ključa ka evidencijskom `Case`-u.
Nema unakrsnih referenci između beleški/zakačenih/veza. Kaskadno brisanje = uklanjanje
direktorijuma.

---

## 6. API endpoint-i

Sve pod `/api/v1`, sve zahteva bearer token (montirano sa deljenom `get_current_user`
zavisnošću — bilo koji aktivan, neblokiran korisnik, admin ili analyst).

| Metoda i putanja | Telo / query | Uspeh | Greške |
|---|---|---|---|
| `GET  /investigations` | — | `200 {investigations:[…]}` | `401` |
| `POST /investigations` | `{name, description?}` | `200 InvestigationCase` | `401`, `422` |
| `GET  /investigations/{id}` | — | `200 InvestigationCase` | `401`, `404` |
| `PATCH /investigations/{id}` | `{name?, description?}` | `200 InvestigationCase` | `401`, `404`, `422` |
| `DELETE /investigations/{id}` | — | `204` | `401`, `404` |
| `GET  /investigations/{id}/notes` | `?address=` / `?tx_id=` / `?target_type=` (≤1) | `200 {investigation_id,address,tx_id,target_type,notes}` | `401`, `404`, `400` (>1 filter / loš `target_type`) |
| `POST /investigations/{id}/notes` | `{address? XOR tx_id?, text}` | `200 InvestigatorNote` | `401`, `404`, `422` (0/2 cilja, prazan/predugačak tekst) |
| `GET  /investigations/{id}/notes/{note_id}` | — | `200 InvestigatorNote` | `401`, `404` |
| `PATCH /investigations/{id}/notes/{note_id}` | `{text}` | `200 InvestigatorNote` | `401`, `404`, `422` |
| `DELETE /investigations/{id}/notes/{note_id}` | — | `204` | `401`, `404` |
| `GET  /investigations/{id}/pins` | — | `200 {investigation_id,pins:[…]}` | `401`, `404` |
| `PUT  /investigations/{id}/pins` | `{address, x?, y?}` (**upsert**) | `200 PinnedNode` | `401`, `404`, `422` |
| `DELETE /investigations/{id}/pins` | `?address=` | `204` | `401`, `404` (nije zakačeno) |
| `GET  /investigations/{id}/links` | `?address=` (bilo koji kraj) | `200 {investigation_id,address,disclaimer,links:[…]}` | `401`, `404` |
| `POST /investigations/{id}/links` | `{source_address,target_address,reason,evidence,confidence}` | `200 InvestigatorLink` | `401`, `404`, `422` (iste adrese, prazno/nedostaje polje, loš confidence) |
| `GET  /investigations/{id}/links/{link_id}` | — | `200 InvestigatorLink` | `401`, `404` |
| `PATCH /investigations/{id}/links/{link_id}` | `{reason?, evidence?, confidence?}` | `200 InvestigatorLink` | `401`, `404`, `422` |
| `DELETE /investigations/{id}/links/{link_id}` | — | `204` | `401`, `404` |

**Konzistentnost:** beleške i veze imaju identičan CRUD oblik (`GET` lista / `POST`
kreiranje / `GET`·`PATCH`·`DELETE` po id-ju). Zakačeni čvorovi namerno odstupaju — čvor
nema id, ili je zakačen ili nije — zato `PUT` (upsert po adresi) + `DELETE ?address=`.
Svaki upis dodaje red u `logs/audit_log.jsonl` (`investigation_case_*`, `investigator_note_*`,
`investigator_pin_*`, `investigator_link_*`), sa `user` i `details.investigation_id`. Svako
„nije pronađeno" je tipizovana `FileNotFoundError` podklasa mapirana na HTTP `404`;
greške validacije su Pydantic `422`; greške u kombinaciji filtera su eksplicitni `400`.

---

## 7. Integracija sa grafom

Sloj je dodat na `features/graph-visualization` bez nove rute i bez diranja graditelja
grafa, postojećih grana ili drugih analiza:

| Element | Gde | Šta ponovo koristi |
|---|---|---|
| **`<select>` za istragu** („Istražiteljski sloj") | red ispod izbora evidencije | markup/stil `<select>`-a za evidenciju; narandžasti levi akcenat da se čita kao „istražiteljski sloj" |
| **Overlay istražiteljskih veza** | isprekidana **narandžasta**, bez strelica, natpis `◆ INVESTIGATOR LINK · <conf>`, pouzdanost menja providnost/debljinu | mehanizam DEX-swap overlay-a (`cy.remove`/`cy.add`, bez re-layout-a); nov `edge.investigator-link*` stil — pravila `edge` / `edge.swap-*` / `edge.bridge-edge` su netaknuta |
| **Panel detalja veze** | najspoljašnja grana lanca `veza → swap → čvor → prazno`: izvor/cilj/razlog/dokaz/pouzdanost/kreirano/autor + disclaimer + „Ukloni vezu" | layout `.node-inspector` |
| **Zakačeni čvorovi** | zlatna dvostruka ivica + zlatni „glow"; fcose `fixedNodeConstraint` + `node.lock()` pri re-layout-u | fcose layout koji već postoji — nema zasebnog sistema za pozicioniranje; učitano iz / sinhronizovano sa API-jem |
| **Blok akcija u panelu čvora** | razdvojen blok iznad on-chain `<dl>`, kicker „Istražiteljski sloj — nije blockchain podatak": `[📌 Zakači/Otkači] [📝 Dodaj belešku] [🔗 Istražiteljska veza]` + „N beleški · Prikaži beleške" | postojeći panel; modal za detalje |
| **Modal za čvor** | modal, tab beleške + tab nova veza | overlay šablon iz custody dijaloga |
| **Panel „Pregled slučaja"** | kompaktan: naziv/opis + `Beleške: N` / `Zakačene adrese: N` / `Istražiteljske veze: N`, svaki proširiv sa skokom na graf / panel veze | podaci već učitani na stranici |
| **Legenda** | dva reda — zakačen čvor, istražiteljska veza („nije blockchain transakcija") | postojeća legenda |

`applyVisibilityFilters()` tretira `investigator-link` grane kao overlay (uvek vidljive,
van vremenske trake), isto kao `swap-edge`. Izabrana istraga se pamti u `localStorage` i
ponovo se bira posle osvežavanja stranice, pa se njene beleške/zakačeni čvorovi/veze
odmah vrate.

---

## 8. Beleške — implementacija

- **Cilj:** tačno jedno od **adrese** (`target_type:"address"`) ili **transakcije**
  (`target_type:"transaction"`, ključ = `tx_id` iz lanca dokaza,
  `app/evidence/tx_identity.py` — nema nove sheme id-ja). Eksplicitno polje `target_type`
  drži dve vrste razdvojenim čak i u sirovom JSON-u, a filtriranje je po njemu, pa se ista
  niska korišćena kao adresa i kao tx id nikad ne pomešaju.
- **Identifikatori se čuvaju doslovno** (trimuje se razmak, veličina slova se ne dira) —
  id čvora u grafu i tx id lanca dokaza porede se case-sensitive drugde, pa se beleška
  poklapa sa ciljem samo ako sačuva isti zapis.
- **Menja se samo `text`.** `id` / cilj / `author` / `created_at` su nepromenljivi —
  prevezivanje beleške bi falsifikovalo poreklo; brisanje + ponovno kreiranje.
- **Validacija:** `text` 1..10 000, trimovan, ne-prazan (prazno / nedostaje / razmak →
  `422`, i pri kreiranju i pri izmeni). Frontend textarea ima `maxlength=10000`.
- **Frontend:** u panelu samo BROJ (nikad tekst); puna lista + dodaj/izmeni/obriši u tabu
  „Beleške" u modalu. `GET …/notes` bez filtera (sve beleške, obe vrste) puni brojač u
  „Pregledu slučaja".

---

## 9. Zakačeni čvorovi — implementacija

- **Mehanizam:** ponovo se koristi postojeći **cytoscape-fcose** layout — pozicija
  zakačenog čvora ide u fcose-ov `fixedNodeConstraint` pri svakom re-layout-u, a čvor se
  `node.lock()`-uje. Nije napravljen zaseban sistem za pozicioniranje.
- **Trajno čuvanje (korak 10):** svaki zakačen čvor je `PinnedNode` u `pinned_nodes.json`,
  ključ = **adresa** (čvor je zakačen ili nije — `PUT` je upsert; ponovno zakačivanje
  menja `x`/`y`/`updated_at`, čuva `pinned_at`). Preživljava osvežavanje / restart
  backenda.
- **Opseg:** zakačivanje pripada istrazi — dugme „Zakači" je onemogućeno dok se istraga ne
  izabere. Prebacivanje istrage briše vizuelno zakačivanje i učitava zakačene čvorove nove
  istrage.
- **Frontend stanje:** `Map<address,{x,y}>` render keš, popunjen iz `GET …/pins` pri
  izboru istrage i ponovo primenjen pri svakom renderu; `togglePinSelectedNode` /
  `unpinNodeById` takođe rade `PUT` / `DELETE` na serveru.
- **Provereno:** `pin-node-behavior-check.mjs` — zakačen čvor ostaje na svojim
  koordinatama (< 1e-6 px) kroz jedan i više uzastopnih re-layout-a dok se ostatak
  raspoređuje oko njega; otkačivanje ga oslobađa. Ponašanje ne-zakačenih čvorova je
  identično kada ništa nije zakačeno.

---

## 10. Istražiteljske veze — implementacija

- **Šta je to:** ručno zabeležena **pretpostavljena veza** (suspected relation) između
  dve adrese na osnovu **vanlančanih** dokaza — „istražiteljska asocijacija". Sprovedeno
  kao *nije činjenica*: nazivi entiteta/rute/audit-a, `disclaimer` na svakoj listi, **nema
  `relationship_type` enum-a** (nema vrednosti `same_owner` / `same_person` / `proven` —
  tvrdnja ide u slobodan tekst `reason`/`evidence`), a `confidence` je ograničen na
  Low/Medium/High.
- **Neusmerena:** model istrage nema potrošača smera, pa je `directed` uvek `false`;
  redosled `source_address`/`target_address` nije bitan; pretraga po adresi poklapa
  **bilo koji** kraj.
- **Menja se:** `reason` / `evidence` / `confidence`. Dve adrese su nepromenljive.
- **Validacija:** svih pet polja pri kreiranju je obavezno i ne-prazno; `source == target`
  (posle trima) → `422`; `confidence` van tri vrednosti → `422`; `reason` 1..5000,
  `evidence` 1..2000. Duplikat veza između istog para je **dozvoljen** (dva nezavisna
  vanlančana dokaza).
- **Graf:** iscrtano kao vizuelno različit overlay (vidi §7), nikad kao transakciona
  grana; klik za detalje; uklonjivo iz panela. Kreira se iz taba „Nova veza" u modalu.

---

## 11. Odvajanje činjenica od zaključaka

Odvajanje je sprovedeno **strukturno**, ne samo tekstom:

| Nivo | Kako |
|---|---|
| **Skladište** | zaseban `data/investigations/` direktorijum; nikad se ne piše u `data/cases/` ni u graf |
| **Kod** | nijedan `app/investigations/*` modul ne uvozi `app/analytics`; ništa u analitici ne zna za istražiteljski sloj |
| **API** | zasebni endpoint-i; lista veza uvek nosi `disclaimer`; audit akcije `investigator_*` |
| **Model** | `directed:false` na vezi; `target_type` na belešci; nema `relationship_type` sa „nabijenim" vrednostima; `confidence` samo Low/Medium/High |
| **Graf** | veza = isprekidana **narandžasta**, bez strelice, natpis `◆ INVESTIGATOR LINK` (transakcija = puna plava sa strelicom; swap = ljubičasta sa strelicom) |
| **Panel čvora** | blok istražiteljskih akcija je odvojen kicker-om „Istražiteljski sloj — nije blockchain podatak" i donjom linijom od on-chain `<dl>`-a ispod |
| **Legenda** | red „Istražiteljska veza (off-chain) — … nije blockchain transakcija" |
| **Pregled slučaja** | naslov „ISTRAŽITELJSKI PREGLED SLUČAJA" + fus-nota „Samo pregled zapažanja i zaključaka istražitelja — analize su na stranicama Graf / Taint / …" |

---

## 12. Baza / relacije

Nema baze. Skladište je flat JSON, jedan direktorijum po istrazi:

```
data/investigations/
├── index.json                                  { "investigations": [ {id,name,description,created_at,updated_at}, … ] }
└── <investigation_id>/                          ← „slučaj" (istraga)
    ├── investigation.json                       zapis InvestigationCase
    ├── notes.json      { "notes":        [ … ] }   beleške na adresama + na transakcijama (razlikuje target_type)
    ├── pinned_nodes.json { "pinned_nodes": [ … ] }  zakačeni čvorovi (korak 10)
    └── links.json      { "links":        [ … ] }   istražiteljske veze
```

| # | Kategorija | Entitet | Fajl | Ključ u fajlu | Veza sa slučajem | Kaskada pri brisanju slučaja |
|---|---|---|---|---|---|---|
| 1 | **Slučaj / istraga** | `InvestigationCase` | `investigation.json` + `index.json` | `id` | *jeste* slučaj | — |
| 2 | **Beleška na adresi** | `InvestigatorNote` (`target_type:"address"`) | `<id>/notes.json` | `id` (12-hex); cilj = `address` | polje `investigation_id` + direktorijum | dir se briše |
| 3 | **Beleška na transakciji** | `InvestigatorNote` (`target_type:"transaction"`) | `<id>/notes.json` | `id`; cilj = `tx_id` (lanac dokaza) | polje `investigation_id` + direktorijum | dir se briše |
| 4 | **Zakačen čvor** | `PinnedNode` | `<id>/pinned_nodes.json` | `address` (upsert) | polje `investigation_id` + direktorijum | dir se briše |
| 5 | **Istražiteljska veza** | `InvestigatorLink` | `<id>/links.json` | `id`; krajevi = `source_address`/`target_address` | polje `investigation_id` + direktorijum | dir se briše |

**Izolacija je strukturna**, ne samo na nivou koda: stavke istrage A su fizički pod
`data/investigations/A/`, a stavke B pod `data/investigations/B/`; svaki poziv servisa je
u opsegu `investigation_id`, i svaki endpoint za listu čita samo taj jedan direktorijum.
Podaci iz A ne mogu da se pojave pod B.

---

## 13. KAKO SE TESTIRA

Tri sloja provere: **automatizovani backend testovi**, **automatizovane frontend smoke
skripte**, i **ručni test-prolaz kroz aplikaciju** (sa konkretnim reproduktivnim
primerom). Ovo je „real primer testiranja" za kasniju referencu.

Pretpostavke o okruženju u ovom repozitorijumu:
- Python venv je na `../.venv` (relativno na koren projekta `master_rad_lusi_blockchain_forensics/`).
- Node/npm su instalirani, `frontend/node_modules` postoji.
- Admin nalog: **admin / admin123**.

---

### 13.1 Automatizovani backend testovi (pytest)

Iz korena projekta (`master_rad_lusi_blockchain_forensics/`):

```bash
# ceo paket (uključuje i sve testove analitike - Taint/Pathfinding/Behavioral/DEX)
../.venv/Scripts/python -m pytest backend/tests -q
# očekivano: 279 passed

# samo istražiteljski sloj:
../.venv/Scripts/python -m pytest backend/tests/test_investigation_management.py \
                                  backend/tests/test_investigator_notes.py \
                                  backend/tests/test_investigator_links.py \
                                  backend/tests/test_investigator_pins.py \
                                  backend/tests/test_case_management_persistence.py \
                                  backend/tests/test_case_management_full_pass.py -v
```

Šta koji fajl pokriva:

| Fajl | Broj | Pokriva |
|---|---|---|
| `test_investigation_management.py` | 11 | kreiranje slučaja (jedinstven id, jednaki pečati) / čitanje / redosled liste / izmena (pomera samo `updated_at`) / brisanje + kaskada / odbijanje praznog naziva |
| `test_investigator_notes.py` | 25 | CRUD beleški na čvoru i na transakciji, „tačno jedan cilj", doslovno poklapanje id-ja, filter po address/tx_id/target_type, redosled od najnovije, odbijanje praznog, opseg po istrazi, kaskada |
| `test_investigator_links.py` | 20 | CRUD veza, neusmereno preuzimanje (oba kraja), odbijanje istih adresa, enum confidence, odbijanje praznog, prazan PATCH = no-op, opseg po istrazi, kaskada, čuvanje odvojeno od beleški/grafa |
| `test_investigator_pins.py` | 11 | zakačivanje čuva adresu/poziciju/autora/pečate, **upsert nije duplikat**, lista, otkačivanje (+ nepoznato → 404), opseg po istrazi, kaskada |
| `test_case_management_persistence.py` | 7 | ceo tok → **nova `TestClient(app)` instanca = „reload"** → svih 5 tu sa id-jevima i koordinatama; svaka stavka nosi `investigation_id`; fajlovi pod dir slučaja; izolacija A/B; brisanje A ⇒ 404 svuda + dir nestao, B netaknut; **piše samo u `data/investigations/` + audit log** |
| `test_case_management_full_pass.py` | 19 | čeklista koraka 11 (SLUČAJ / BELEŠKE / ZAKAČIVANJE / VEZE / PERSISTENCE / IZOLACIJA) + **granice veoma dugog unosa** + **polje nedostaje vs prazno** + **sve vrednosti confidence** + postojeće analitičke rute i dalje registrovane i vraćaju `404` (ne `500`) za nepostojeći slučaj |

---

### 13.2 Automatizovane frontend provere

Iz `frontend/`:

```bash
# 1) build - proverava i kompilaciju i tipove u šablonima
npm run build
# očekivano: "Application bundle generation complete.", 0 errors, 0 warnings

# 2) headless cytoscape+fcose provera zakačivanja (nema ng test runnera u projektu)
node scripts/pin-node-behavior-check.mjs
# očekivano: "ALL 12 CHECKS PASSED"

# 3) headless provera overlay-a istražiteljskih veza
node scripts/investigator-link-overlay-check.mjs
# očekivano: "ALL 12 CHECKS PASSED"
```

`pin-node-behavior-check.mjs` verno oponaša `renderGraph()`: zakačen čvor mora ostati na
istoj poziciji kroz ponovni layout, a ne-zakačeni se i dalje raspoređuju.
`investigator-link-overlay-check.mjs` proverava da je grana veze isprekidana / narandžasta
/ bez strelice, da nosi ceo objekat veze, i da **nijedna transakciona grana nije dodata,
uklonjena ni izmenjena** pri uključivanju/isključivanju overlay-a.

---

### 13.3 Ručni test-prolaz kroz aplikaciju

**Pokretanje:**

```bash
# Terminal 1 - backend  (iz master_rad_lusi_blockchain_forensics/backend)
../../.venv/Scripts/python -m uvicorn app.main:app --reload      # -> http://localhost:8000

# Terminal 2 - frontend  (iz master_rad_lusi_blockchain_forensics/frontend)
npm start                                                        # -> http://localhost:4200
```

(ili `docker compose up` iz korena repozitorijuma)

Otvori `http://localhost:4200`, prijavi se kao **admin / admin123**.

> **Napomena:** trenutno NE postoji ekran za pravljenje istrage. Istragu pravi API poziv
> (vidi korak 1 dole ili §13.4). Sve ostalo je u pretraživaču.

| Korak | Radnja | Očekivano |
|---|---|---|
| 1 | **Napravi istragu** (API): `POST /api/v1/investigations {"name":"Predmet 2026-001","description":"Test"}` (vidi §13.4 za ceo `curl`). Zapiši `id` iz odgovora. | `200`, vraća `{id, name, description, created_at, updated_at}` sa `created_at == updated_at` |
| 2 | U pretraživaču: **Slučajevi → izaberi neki evidencijski slučaj** koji ima učitane dokaze; otvori **Graf**. | graf se iscrta (čvorovi + grane) |
| 3 | U redu **„Istražiteljski sloj — istraga"** izaberi istragu iz koraka 1. | pojavi se panel **„Istražiteljski pregled slučaja"** sa nazivom/opisom i `Beleške: 0 · Zakačene adrese: 0 · Istražiteljske veze: 0`; dugmad „Zakači/Dodaj belešku/Istražiteljska veza" u panelu čvora više nisu onemogućena |
| 4 | Klikni na **čvor** u grafu → u panelu desno klikni **📝 Dodaj belešku** → u modalu (tab „Beleške") upiši tekst → **Dodaj belešku**. | beleška se pojavi u listi; broj u „Pregledu slučaja" postane `Beleške: 1`; u panelu čvora se pojavi „1 beleška · Prikaži beleške" |
| 5 | U modalu klikni **Izmeni** na toj belešci → promeni tekst → **Sačuvaj**. | tekst se ažurira; ispod stoji „· izmenjeno" |
| 6 | **Osveži stranicu (F5)**, ponovo izaberi isti evidencijski slučaj i otvori Graf. | istraga je **već izabrana** (pamti se u `localStorage`); klik na isti čvor i dalje pokazuje „1 beleška" |
| 7 | Klikni **Obriši** na belešci u modalu → potvrdi. | broj se vrati na `Beleške: 0`; red „N beleški" nestane |
| 8 | Klikni čvor → **🔗 Istražiteljska veza** → u modalu (tab „Nova veza") upiši **ciljnu adresu**, **razlog**, **dokaz**, izaberi **pouzdanost** → **Kreiraj vezu**. | modal se zatvori; na grafu se pojavi **isprekidana narandžasta linija bez strelice** `◆ INVESTIGATOR LINK · <pouzdanost>`; „Pregled slučaja" pokaže `Istražiteljske veze: 1` |
| 9 | Klikni na tu **narandžastu liniju**. | otvori se panel „Istražiteljska veza — nije blockchain činjenica" sa izvor/cilj/razlog/dokaz/pouzdanost/kreirano/autor + disclaimer + dugme **Ukloni vezu** |
| 10 | Klikni **Zakači** u panelu nekog čvora → povuci taj čvor mišem malo → klikni **Prikaži ceo graf** pa opet učitaj graf (npr. promeni „Prikaz transakcija" pa vrati). | zakačen čvor **ostaje na istoj poziciji** dok se ostatak grafa preraspoređuje; ima **zlatnu dvostruku ivicu**; dugme u panelu piše „📌 Otkači" |
| 11 | Zakači još 2–3 čvora. Otvori **„Pregled slučaja" → „Zakačene adrese: N"**. | lista svih zakačenih adresa; „na graf →" bira čvor, „otkači" ga oslobađa |
| 12 | **Osveži stranicu (F5)**, vrati se na Graf sa istom istragom. | svi zakačeni čvorovi su **i dalje zakačeni**, na istim pozicijama (učitani iz `pinned_nodes.json`) |
| 13 | Napravi **drugu istragu** (API), izaberi je u `<select>`-u. | „Pregled slučaja" pokaže `0 · 0 · 0`; nijedna beleška/veza/zakačen čvor iz prve istrage se NE vidi |
| 14 | Vrati se na **prvu istragu** u `<select>`-u. | njeni podaci se ponovo pojave |
| 15 | Otvori redom **Taint analiza**, **Pathfinding**, **Behavioral**, **DEX Swaps** za isti evidencijski slučaj. | sve rade kao i pre — istražiteljski sloj ih ni na koji način ne dira |

---

### 13.4 Konkretan reproduktivan primer (HTTP / curl)

Ceo tok „napravi slučaj → beleška na čvoru → beleška na transakciji → zakači čvor →
napravi vezu → ‚restartuj' → proveri → izolacija", isključivo kroz API. (Radi u Git Bash /
PowerShell 7 / bilo gde ima `curl`.)

```bash
BASE=http://localhost:8000/api/v1

# --- 0. login ---
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

# --- 1. napravi slučaj (istragu) ---
IID=$(curl -s -X POST $BASE/investigations -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"Predmet 2026-001","description":"Sumnjiva laundering sema"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "istraga: $IID"

# --- 2. učitaj slučaj ---
curl -s $BASE/investigations/$IID -H "$AUTH"

# --- 3. izmeni slučaj ---
curl -s -X PATCH $BASE/investigations/$IID -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"description":"Dopunjen opis"}'

# --- 4. beleška na ČVORU (adresi) ---
NID=$(curl -s -X POST $BASE/investigations/$IID/notes -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"address":"0xABC","text":"Sumnja se da je ovo cold wallet. Ceka se dodatna provera."}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# izmeni je
curl -s -X PATCH $BASE/investigations/$IID/notes/$NID -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"text":"Potvrdjeno: cold wallet."}'

# --- 5. beleška na TRANSAKCIJI ---
curl -s -X POST $BASE/investigations/$IID/notes -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"tx_id":"0x123abc","text":"Transfer izgleda kao pocetna faza pranja."}'

# --- 6. zakači čvor (sa pozicijom) ---
curl -s -X PUT $BASE/investigations/$IID/pins -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"address":"0xABC","x":123.5,"y":-77.25}'

# --- 7. istražiteljska veza ---
curl -s -X POST $BASE/investigations/$IID/links -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"source_address":"0xABC","target_address":"0xDEF","reason":"IP adresa iz logova servera povezuje obe adrese.","evidence":"Server log #42","confidence":"High"}'

# --- 8. "RESTART": ovde stvarno ugasi i ponovo pokreni uvicorn, pa proveri ---
curl -s $BASE/investigations/$IID/notes  -H "$AUTH"   # -> 2 beleške (address + transaction)
curl -s $BASE/investigations/$IID/pins   -H "$AUTH"   # -> 1 zakačen, x=123.5, y=-77.25
curl -s $BASE/investigations/$IID/links  -H "$AUTH"   # -> 1 veza, directed:false, + "disclaimer"

# --- 9. neusmereno preuzimanje veze (poklapa se sa OBA kraja) ---
curl -s "$BASE/investigations/$IID/links?address=0xABC" -H "$AUTH"   # -> vraća vezu
curl -s "$BASE/investigations/$IID/links?address=0xDEF" -H "$AUTH"   # -> vraća ISTU vezu

# --- 10. validacija (sve treba da vrati 422) ---
curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/investigations/$IID/notes -H "$AUTH" -H 'Content-Type: application/json' -d '{"address":"0xA","text":"   "}'                       # prazan tekst
curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/investigations/$IID/links -H "$AUTH" -H 'Content-Type: application/json' -d '{"source_address":"0xA","target_address":"0xA","reason":"r","evidence":"e","confidence":"Low"}'   # iste adrese
curl -s -o /dev/null -w "%{http_code}\n" -X POST $BASE/investigations/$IID/links -H "$AUTH" -H 'Content-Type: application/json' -d '{"source_address":"0xA","target_address":"0xB","reason":"r","evidence":"e","confidence":"Proven"}'  # los confidence

# --- 11. IZOLACIJA: druga istraga ne vidi ništa od prve ---
IID2=$(curl -s -X POST $BASE/investigations -H "$AUTH" -H 'Content-Type: application/json' -d '{"name":"Predmet B"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s $BASE/investigations/$IID2/notes -H "$AUTH"   # -> {"notes": []}
curl -s $BASE/investigations/$IID2/pins  -H "$AUTH"   # -> {"pins": []}
curl -s $BASE/investigations/$IID2/links -H "$AUTH"   # -> {"links": []}

# --- 12. brisanje slučaja briše sve njegovo (kaskada) ---
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE $BASE/investigations/$IID -H "$AUTH"   # -> 204
curl -s -o /dev/null -w "%{http_code}\n" $BASE/investigations/$IID/notes -H "$AUTH"       # -> 404
```

Posle koraka 8, direktno na disku (bez aplikacije) mora da postoji:

```
data/investigations/<IID>/investigation.json
data/investigations/<IID>/notes.json          -> { "notes":        [ 2 stavke ] }
data/investigations/<IID>/pinned_nodes.json   -> { "pinned_nodes": [ 1 stavka  ] }
data/investigations/<IID>/links.json          -> { "links":        [ 1 stavka  ] }
```

Automatizovana verzija istog toka je `backend/tests/test_case_management_persistence.py`
i `test_case_management_full_pass.py` — „restart" tamo je nova `TestClient(app)` instanca
nad istim fajlovima.

---

### 13.5 Provera da postojeće analize i dalje rade

Automatski: `../.venv/Scripts/python -m pytest backend/tests -q` pokreće i
`test_taint_analysis.py`, `test_path_finding_bfs.py`, `test_behavioral_analysis.py`,
`test_dex_swap_analysis.py`, `test_peel_chains.py`, `test_seed_suggestion.py` — svi zeleni
(**279 passed** ukupno).

Ručno (na zasejanom slučaju sa dokazima): otvori redom **Graf**, **Taint analiza**,
**Pathfinding**, **Behavioral**, **DEX Swaps** — sve vraća rezultate kao i pre. Poslednji
end-to-end smoke sa 5-redova CSV-a: Graf `200` (5 čvorova / 5 grana), Taint `200` (ceo
pipeline od 7 plugina), Pathfinding `200` (put nađen, 3 skoka), Behavioral `200` (3 tx,
najaktivniji sat), DEX Swap `200` (1 događaj).

---

## 14. Važne projektne odluke

1. **Istražiteljski sloj je zaseban entitet i zaseban direktorijum, ne polja na
   evidencijskom `Case`-u.** Drži zaključke strukturno odvojene od on-chain činjenica i
   njihovog lanca dokaza.
2. **Referenciraj blockchain elemente neprozirnim stringom, nikad graf-objektom.** Graf se
   ponovo gradi pri svakom zahtevu i nema trajnu tabelu čvorova/grana; adrese i `tx_id`
   lanca dokaza su jedini stabilni identifikatori i važe bez obzira na trenutni filter
   evidencije.
3. **Koristi postojeće mehanizme, ne pravi paralelne.** Zakačivanje koristi fcose
   `fixedNodeConstraint`; overlay veza koristi put dodavanja/uklanjanja DEX-swap overlay-a;
   modal koristi overlay šablon custody dijaloga; skladište koristi flat-JSON stil
   `case_management`-a.
4. **„Nije činjenica" je sprovedeno strukturno** (vidi §11), ne samo tekstom.
5. **Čuvaj trajno samo ono što mora da preživi osvežavanje.** Sam graf se ne čuva (po
   dizajnu); beleške/veze/zakačeni čvorovi se čuvaju jer im je cela vrednost u
   kontinuitetu. Pozicije zakačenih čvorova se čuvaju da bi osvežavanje vratilo tačan
   raspored.
6. **Zakačeni čvorovi su ključ = adresa (upsert), beleške/veze = generisan id.** Čvor je
   zakačen ili nije — id bi bio ceremonija; beleška/veza je zaseban zapis koji može biti
   dupliran i treba mu sopstvena ručka.
7. **Stranica „Graf" hostuje ceo sloj** (za sad nema zasebne rute) — ona je već
   istražiteljev radni prostor (graf, adrese, izabran čvor).
8. **Refaktori u finalnom pregledu (bez promene ponašanja):**
   (a) 4× dupliran `_read_json`/`_write_json` i `load_/save_` boilerplate po kolekciji
   sažeti u `repository.read_json/write_json/read_collection/write_collection` — tri
   podređena repozitorijuma su sad ~10-linijski omotači;
   (b) istražiteljske kontrole u panelu čvora obavijene u jedan razdvojen `.investigator-block`
   sa kicker-om „Istražiteljski sloj — nije blockchain podatak" i donjom linijom, da
   granica ka on-chain `<dl>`-u bude eksplicitna.
   *(Nije sažeto: 1-linijski `utc_now_iso()` po modelskom modulu — projekat namerno bira
   dupliranje malih helpera umesto deljenog utils modula.)*

---

## 15. Poznata ograničenja

- **Nema zasebne Case Management stranice.** Istrage se u UI-ju mogu samo *listati /
  birati*; **pravljenje / preimenovanje / zatvaranje** zahteva direktan API poziv
  (endpoint-i postoje). Ceo sloj je dostupan samo sa `/graf`.
- **Gustina UI-ja na stranici „Graf".** Ona sad nosi `<select>` za istragu, toggle
  overlay-a, panel „Pregled slučaja", blok akcija u panelu čvora, dva modala i dva reda
  legende, povrh sopstvenih kontrola. Svaki dodatak je kompaktan, ali je stranica gusta —
  zasebna ruta bi je rasteretila.
- **Pamćenje izbora je po pretraživaču.** Aktivna istraga se pamti u `localStorage`;
  aktivan *evidencijski slučaj* se ne pamti (postojeće ponašanje aplikacije), pa se posle
  osvežavanja evidencijski slučaj mora ponovo izabrati da bi se graf iscrtao.
- **Nema autorizacije van „prijavljen".** Bilo koji prijavljen korisnik može da izmeni ili
  obriše bilo koju istražiteljsku belešku / zakačivanje / vezu, bez obzira na autora.
  `author` / `pinned_by` se beleže i svaka promena je u audit logu, ali nema provere
  „autor ili admin".
- **Konkurentnost.** Flat-file JSON bez zaključavanja — dva istovremena upisa u isti
  `notes.json` mogli bi da izgube jedan (isti rizik kao postojeći `case.json`). Append-only
  je razmatran i odbačen jer beleškama/zakačivanjima treba izmena/brisanje na licu mesta.
- **Beleška na agregiranoj grani.** Postoje samo beleške na *nivou transakcije* (ključ
  `tx_id`), u skladu sa granularnošću lanca dokaza; nema cilja za celu agregiranu granu
  `pošiljalac→primalac`.
- **Veza/beleška/zakačen čvor može referencirati adresu koja nije u trenutnom prikazu
  grafa** — čuva se i lista, ali je „na graf →" onemogućeno i veza/zakačivanje se ne
  iscrtava dok ta adresa nije u prikazu.
- **`x`/`y` na zakačivanju su cytoscape model-koordinate za jedan pokretanje layout-a.**
  Verno vraćaju zakačivanje pri osvežavanju, ali nemaju značenje van grafa ove aplikacije.

---

## 16. Namerno NIJE urađeno (buduća poboljšanja)

- **Zasebna Case Management ruta** (`/case-management`): pun životni ciklus istrage, sve
  beleške/zakačivanja/veze po istrazi na jednom mestu, van stranice „Graf".
- **Ograničenje „autor ili admin"** pri izmeni / brisanju tuđe stavke.
- **Povezivanje `InvestigationCase`-a sa evidencijskim `Case`-om** (opcioni strani ključ),
  da bi stranica „Graf" automatski izabrala istragu za otvoren evidencijski slučaj.
- **Istražiteljski zaključci u izvezenom izveštaju slučaja** — zasebna, jasno naslovljena
  sekcija „Istražiteljski zaključci (nisu blockchain činjenice)" u PDF/CSV izvozu, i
  `edge_kind="manual_offchain"` označavanje veza u GraphML/GEXF izvozu.
- **Usmerena varijanta istražiteljske veze** (`directed:true`) — polje postoji i uvek je
  `false`; usmerena „A kontroliše B" asocijacija bi se mogla dodati bez migracije.
- **Rečnik `relationship_type` za veze** (npr. `same_entity` / `associated` /
  `off_chain_payment`) — namerno izostavljen da sloj nikad ne isporuči „nabijene" oznake;
  tvrdnja ostaje u slobodnom tekstu.
- **Beleške / zakačivanja / veze na stranicama Taint, Pathfinding, Behavioral i DEX** —
  overlay + panel za sad žive samo na glavnoj stranici „Graf" (u skladu sa konvencijom
  „izgledaj konzistentno, ostani nezavisan" između stranica analize).
- **Stanje izbora deljeno između uređaja**, bogatije formatiranje beleški, prilozi uz
  beleške, grupne operacije, feed aktivnosti po istrazi — ništa od toga nije potrebno za
  obim rada.

---

## 17. Istorija implementacije (koraci 1–12)

Kratak rezime; puni engleski log po koracima je u git istoriji (commit `c391bd2` i
raniji).

| Korak | Šta je urađeno |
|---|---|
| **1** | Backend model **kontejnera istrage** (`InvestigationCase`): id / naziv / opis / `created_at` / `updated_at`; `models.py` + `repository.py` + `service.py` + rute `/investigations` + testovi. Skladište `data/investigations/<id>/`. |
| **2** | *(preskočeno u numeraciji)* |
| **3** | **Beleške na adresama/čvorovima**: `InvestigatorNote` (id / investigation_id / address / text / author / vremena), CRUD API, filter `?address=`, validacija (ne-prazno, ≤10 000). |
| **4** | **Beleške na transakcijama/granama**: dodati `target_type` (`address`/`transaction`) i `tx_id` (isti identitet kao lanac dokaza); „tačno jedan cilj"; filter `?tx_id=` / `?target_type=`. Bez nove sheme id-ja. |
| **5** | **„Zakači čvor" (frontend)**: fcose `fixedNodeConstraint` + `node.lock()` — bez zasebnog sistema pozicioniranja; akcija u panelu detalja; zlatna dvostruka ivica; headless smoke skripta. (Tada još bez trajnog čuvanja.) |
| **6** | **Istražiteljske veze (backend)**: `InvestigatorLink` (2 adrese, `directed:false`, `reason` / `evidence` / `confidence` Low/Medium/High); bez `relationship_type` enum-a; `disclaimer` na listi; neusmereno; duplikati dozvoljeni; odbijanje istih adresa. |
| **7** | **Veze na Graf UI**: isprekidan **narandžast** overlay bez strelica, natpis `◆ INVESTIGATOR LINK`, klik za detalje, „Ukloni vezu". Ponovo koristi DEX-swap overlay mehanizam; stvarne grane netaknute. Dodat `<select>` za istragu. |
| **8** | **Akcije u panelu detalja čvora**: `[Pin] [Dodaj belešku] [Istražiteljska veza]` + „N beleški · Prikaži beleške"; detalji u **modalu** (`InvestigatorNodeDialogComponent`) — prva frontend implementacija beleški (lista + dodaj + izmeni + obriši) + kreiranje veze sa čvora. |
| **9** | **Panel „Pregled slučaja"** (`CaseOverviewPanelComponent`): naziv/opis + `Beleške: N` / `Zakačene adrese: N` / `Istražiteljske veze: N`, svaki proširiv. Bez grafikona/analitike. |
| **10** | **Trajno čuvanje zakačenih čvorova**: `pins_models/repository/service` + rute `/pins` (`GET` / `PUT` upsert / `DELETE ?address=`). Frontend sada učitava/sinhronizuje zakačivanje sa API-jem; zakačivanje zahteva izabranu istragu; izabrana istraga se pamti u `localStorage`. Verifikovana finalna persistencija svih 5 kategorija. |
| **11** | **Fokusirani test-prolaz** kroz celu čeklistu (`test_case_management_full_pass.py`, 19 testova) + provera da Graf/Taint/Pathfinding/Behavioral/DEX rade. Jedina ispravka: `maxlength` na poljima modala. |
| **12** | **Finalni pregled**: dva mala refaktora bez promene ponašanja (deljeni JSON helperi u `repository.py`; razdvojen `.investigator-block` sa kicker-om) + ova konsolidovana referenca. |

---

*Kraj dokumenta. Backend: 279 testova prolazi. Frontend: `ng build` čist, obe headless
provere 12/12. Postojeće analize nepromenjene i potvrđeno rade.*
