# Predlog: uvođenje graf-baziranog SUBP-a

Odgovor na mentorovu napomenu ("Razmisli da koristiš neki graf-bazirani SUBP") na predlogu
teme. Prvi deo dokumenta (do "Odluka i implementacija") je analiza koja je poslužila kao
osnova za razgovor; drugi deo opisuje šta je posle toga stvarno urađeno.

**Odluka:** Neo4j, kao Opcija A — dodatni, opcioni sloj koji ništa postojeće ne menja niti
od njega zavisi (videti "Odluka i implementacija" na dnu za detalje i uputstvo za ručno
testiranje).

## Trenutno stanje

Aplikacija danas **nema graf bazu podataka**. Persistencija je:

- **JSON fajlovi po entitetu** — `data/cases/<id>/case.json`, `data/investigations/<id>/*.json`
  (bez ORM-a, bez relacione šeme)
- **Sirovi CSV dokazi** — `data/cases/<id>/evidence/*.csv`, hešovani (SHA-256) radi lanca
  dokaza, nikad menjani nakon uvoza

Sam **transakcioni graf se NE čuva nigde** — gradi se **u memoriji**, iznova, pri svakom
zahtevu: [analytics/graph_building.py](master_rad_lusi_blockchain_forensics/backend/app/analytics/graph_building.py) čita CSV(ove) preko Pandas-a i pravi
`networkx.DiGraph`, koji živi samo za trajanje tog HTTP zahteva, a onda se odbacuje. Svaka
analiza (taint, path finding, peel chains, chain hopping, wallet clustering) je čist Python
kod koji hoda po tom NetworkX grafu u memoriji — ukupno oko 1300 linija algoritamskog koda
u `app/analytics/`.

**Obim podataka:** proverio sam realne dokazne fajlove u projektu — najveći imaju ~1000
redova (Etherscan API vraća stranice do te veličine), većina je znatno manja. Ovo je bitno:
na ovom obimu, NetworkX u memoriji je i danas trenutno brz — **performanse NISU razlog** da
se uvede graf baza. Vrednost bi bila drugde (videti ispod).

## Šta bi graf-bazirani SUBP realno doneo

1. **Perzistentan graf, ne rekonstrukcija iz CSV-a pri svakom pozivu.** Danas se isti graf
   gradi iznova za graph-view, taint analizu, pathfinding, dashboard summary... Sa graf bazom,
   gradi se jednom (pri uvozu dokaza) i posle se samo upituje.
2. **Prirodan upitni jezik za tačno ono što ovaj alat radi.** Pathfinding, wallet clustering
   i chain hopping su suštinski grafovski upiti (najkraći put, community detection, dohvat
   suseda) koje danas ručno implementiramo u Python-u; u Cypher-u (Neo4j/Memgraph) ili
   sličnom jeziku, mnogi od njih se svode na par linija ugrađenog algoritma.
3. **Jak argument za poglavlje "Arhitektura"** — pokazuje da je izabrana baza podataka
   svesno usklađena sa prirodom podataka (graf), a ne "sve u JSON jer je bilo najlakše".
4. **Live demo na odbrani** — Cypher upit nad grafom uživo (npr. "nađi sve adrese na
   udaljenosti ≤3 od X koje su označene kao mikser") je efektan deo prezentacije.

## Šta bi to koštalo / rizici

- **Dodatna infrastruktura.** Neo4j/Memgraph/ArangoDB rade kao poseban server (obično u
  Docker-u) — ovo je direktno u sukobu sa ciljem iz uvoda predloga: *"alat koji je dostupan
  svima"*. Neko ko hoće da pokrene aplikaciju sad samo uradi `pip install` + `npm install`;
  sa graf bazom dodaje se i "podigni bazu" korak. (Postoji i ugrađena/embedded opcija bez
  servera — videti Kuzu ispod — koja ovo zaobilazi.)
- **Lanac dokaza se NE sme dovesti u pitanje.** SHA-256 heš i custody log danas su vezani
  za ORIGINALNI CSV fajl, ne za graf. Graf baza bi bila DODATNA, izvedena reprezentacija
  (kao što je NetworkX graf danas) — nikad zamena za dokazni CSV. Ovo mora ostati tako.
- **Nisu sve analize podjednako prirodne za migraciju.** Taint analiza ovde koristi
  prilagođeni "haircut/dilution" procentualni model (koji procenat "prljavog" novca ide na
  svaku granu) — to nije ugrađeni graf algoritam ni u jednoj bazi, i dalje bi se pisao kao
  prilagođena logika (samo bi čitala iz baze umesto iz NetworkX-a). Realna dobit je najveća
  kod pathfinding-a i clustering-a, manja kod taint/peel-chains/chain-hopping.
- **Vreme.** Ovo je nova zavisnost + novi sloj (repository za graf) + testovi za njega.
  Ne mala stvar da se ubaci uz sve ostalo, ali izvodljivo ako se ograniči obim (videti Predlog
  ispod).

## Kandidati

| | Neo4j | Memgraph | Kuzu |
|---|---|---|---|
| Model rada | server (Docker/Desktop) | server (Docker), in-memory | **embedded** (kao SQLite — biblioteka, bez servera) |
| Upitni jezik | Cypher | Cypher (kompatibilan) | Cypher-like |
| Python drajver | zreo, standard u industriji/akademiji | zreo | mlađi, ali jednostavan (`pip install kuzu`) |
| Ugrađeni algoritmi | Graph Data Science biblioteka (PageRank, community detection, shortest path...) | slično | manje razrađeno |
| Uklapanje u "dostupno svima" | slabije — treba Docker | slabije — treba Docker | **odlično** — nula dodatne infrastrukture |
| Prepoznatljivost za komisiju/mentora | najveća (de facto standard za "graph DBMS" u nastavi) | manja | najmanja (nova, malo poznata) |

**Moja preporuka za razgovor sa mentorom:** ako je cilj pre svega da se ispuni akademski
očekivan "koristi graf bazu" i pokaže Cypher upit uživo — **Neo4j Community Edition**
(besplatan, standard, mentor ga verovatno i misli kad kaže "graf-bazirani SUBP"). Ako je
prioritet da alat ostane "pokreni i radi" bez dodatne infrastrukture — **Kuzu**, uz jasno
objašnjenje u radu zašto je embedded baza izabrana svesno (ista logika kao "zašto JSON fajlovi
umesto pune relacione baze" koja već postoji u projektu).

## Odluka i implementacija

Dogovoreno: **Neo4j**, kao **Opcija A** — dodatni, opcioni sloj. Postojeći
`case_pathfinding` (NetworkX/BFS) i svih 7 test datoteka koje su prolazile pre ovoga i
dalje rade **potpuno nepromenjeno**. Umesto migracije postojeće rute, napravljen je nov,
samostalan slice koji pokazuje baš ono što graf baza radi bolje od ručnog BFS-a:
**susedstvo adrese do N koraka** (`(a)-[:TRANSACTED*1..N]-(b)` — jedan Cypher upit, umesto
ograničenog BFS-a koji bi se za to ručno pisao u NetworkX-u).

### Šta je dodato (ništa postojeće nije menjano)

- **`docker-compose.yml`** — nov `neo4j` servis (Neo4j Browser na `:7474`, bolt na `:7687`,
  perzistentan volumen `neo4j_data`). `backend` servis dobija `NEO4J_*` env promenljive i
  `depends_on: neo4j`.
- **`backend/requirements.txt`** — dodat `neo4j` (zvanični Python drajver).
- **[app/shared/graph_db.py](master_rad_lusi_blockchain_forensics/backend/app/shared/graph_db.py)** — konekcija (lazy singleton) + `is_available()` provera.
  Ako Neo4j nije pokrenut, sve što zavisi od njega vraća jasan `503`, ne pada.
- **[app/features/case_graph_search/](master_rad_lusi_blockchain_forensics/backend/app/features/case_graph_search)** — nov slice:
  `GET /cases/{case_id}/graph-search/neighborhood?address=...&max_hops=N`. Pri svakom
  pozivu, graf tog slučaja se (ponovo) upiše u Neo4j iz iste očišćene evidencije koju čita
  i NetworkX (`clean_evidence_frames`) — ništa se ne "duplira trajno", to je samo ogledalo,
  isto kao što je NetworkX graf danas.
- **`tests/test_case_graph_search.py`** — 7 testova, **stvarno pokrenuti protiv žive Neo4j
  instance** (ne mock). Ceo modul se **preskače** (ne pada) ako Neo4j nije dostupan, pa
  glavni test suite ostaje 372/372 zelen bez Docker-a.
- **UI dugme** — "Napredna pretraga grafa" na stranici Slučajevi (`localhost:4200/cases`),
  u zaglavlju Depoa dokaza kad je slučaj izabran. Otvara dijalog (adresa + broj koraka 1-5,
  rezultati grupisani po udaljenosti). Namerno diskretno stilizovano (ljubičasti akcenat,
  ne plavi kao ostatak aplikacije) — signalizira da je ovo dodatna, opciona mogućnost, ne
  osnovna radnja nad slučajem.

### Provera koja je stvarno izvedena (ne samo napisana)

- Neo4j pokrenut preko `docker compose up -d neo4j` i stvarno testiran
- Sa Neo4j-om upaljenim: **379/379** testova prolazi (372 stara + 7 nova)
- Sa Neo4j-om ugašenim: **372 prolazi, 7 se preskače** (ne pada), a HTTP ruta vraća čist
  `503` — ostatak aplikacije potpuno neosetljiv na to da li je Neo4j pokrenut
- Pravi HTTP poziv kroz `TestClient` (upload CSV-a → sinhronizacija u Neo4j → Cypher upit)
  vratio tačan rezultat
- `npm run build` na frontend-u prošao bez grešaka (uklj. Angular-ov strogi
  template type-checking)

## Ručno testiranje — korak po korak

1. **Pokreni sve odjednom:**
   ```
   docker compose up -d --build
   ```
   (`--build` je potreban prvi put posle ove izmene, jer je `backend` slika napravljena
   pre dodavanja `neo4j` paketa u `requirements.txt`. Posle toga dovoljno je samo
   `docker compose up -d`.) Neo4j-u treba ~15-20s da se potpuno podigne; backend radi
   odmah, samo `graph-search` ruta do tada vraća 503.

2. **Uloguj se** na `http://localhost:4200` (`admin` / `admin123`).

3. **Otvori "Slučajevi"** → izaberi **"Demo: Sumnjiva laundering sema (hakovan novčanik)"**
   (već postojeći demo slučaj, `id 46ae7f91db9b`) → u zaglavlju Depoa dokaza klikni
   **"Napredna pretraga grafa"** (ljubičasto dugme).

4. **Unesi adresu** — polje sad nudi i padajuću listu adresa iz evidencije ovog slučaja
   (klikni u polje, pojaviće se predlozi), ili je otkucaj ručno. Izaberi broj koraka
   (1-5), klikni "Pretraži graf". Konkretan primer sa ovim slučajem — videti odeljak
   "Konkretan primer" ispod za tačno šta uneti i šta očekivati. Trebalo bi da se pojavi
   lista povezanih adresa grupisana po udaljenosti, sa brojem indeksiranih
   transakcija.

5. **Da vidiš i samu graf bazu uživo:** otvori `http://localhost:7474` (Neo4j Browser),
   uloguj se (**user:** `neo4j`, **password:** `dev-insecure-password` — iz
   `NEO4J_AUTH` u `docker-compose.yml`, nema override u `.env`), pa pusti:
   ```cypher
   MATCH (a:Address {case_id: '46ae7f91db9b'})-[:TRANSACTED*1..2]-(b)
   RETURN a, b
   ```
   Neo4j Browser ovo iscrtava kao graf — najjači deo za demonstraciju, jer se vidi da su
   podaci stvarno u pravoj graf bazi, ne samo u tabeli.

6. **Provera da ništa nije pokvareno kad Neo4j nije pokrenut:**
   ```
   docker compose stop neo4j
   ```
   Dugme i dalje otvara dijalog, ali pretraga vraća jasnu poruku (503) umesto da nešto
   pukne — a sve ostale stranice (Graf, Taint analiza, Putanje...) rade nepromenjeno.

## Konkretan primer (isti podaci koji su već u aplikaciji)

Slučaj **"Demo: Sumnjiva laundering sema (hakovan novčanik)"** (`46ae7f91db9b`) ima 9
dokaznih fajlova (66 transakcija ukupno, kombinovano) — namerno napravljenu peel-chain
šemu iz hakovanog novčanika. Sledeći rezultati su **stvarno pokrenuti i potvrđeni** protiv
prave rute i prave evidencije ovog slučaja (ne pretpostavljeni):

**Šta uneti:**
- Adresa: `0xVictimWallet` (izabrana iz padajuće liste — pojaviće se čim klikneš u polje)
- Broj koraka: probaj redom **2**, pa **4**, pa **5**

**Stvarno potvrđen rezultat za 2 koraka** (66 indeksiranih transakcija, 7 pronađenih adresa):
```
Korak 1: 0xDrainerContract, 0xDrainerWallet, 0xPeelSeed, 0xSweepContract
Korak 2: 0xMuleWallet1, 0xPeelRelay1, 0xVictimWallet2
```
Već na 1 koraku se vidi da je novac žrtve otišao direktno na "drainer" ugovor/novčanik i
"sweep" ugovor — tipičan obrazac wallet-drainer napada, tačno ono što naziv slučaja
najavljuje.

**Stvarno potvrđen rezultat za 4 koraka** (dodaje se na gornje):
```
Korak 3: 0xBridgeRouterHop, 0xMuleWallet2, 0xPeelRelay2
Korak 4: 0xDeadDropWallet, 0xbad0000000000000000000000000000000000001
```
Ovde priča postaje forenzički zanimljiva: u tačno 4 koraka od žrtve stiže se do
`0xbad000...001` — adrese sa crne liste. Dobar argument za odbranu: *"Jednim upitom nad
graf bazom, umesto ručnog praćenja tabele red-po-red, pokazujem da se novac žrtve za 4
koraka stiže do sankcionisane adrese."*

**Stvarno potvrđen rezultat za 5 koraka** (dodaje se na gornje):
```
Korak 5: 0xInvestorWallet, 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
```

**Ako rezultat ne odgovara ovome:** proveri da li je case zaista `46ae7f91db9b` (vidi ID
ispod naziva slučaja na kartici) — pretraga sama upisuje svež graf pri svakom pozivu, pa
stari podaci u Neo4j-u ne bi trebalo da smetaju.

## Sledeći korak (opciono)

Ako se pilot pokaže korisnim za odbranu, sledeći prirodan kandidat je
`analytics/plugins/wallet_clustering.py` (community detection je ugrađen algoritam u
Neo4j-u) — kao potpuno odvojen dodatak, istim obrascem kao ovaj.
