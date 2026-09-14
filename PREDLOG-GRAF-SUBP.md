# Predlog: uvođenje graf-baziranog SUBP-a

Odgovor na mentorovu napomenu ("Razmisli da koristiš neki graf-bazirani SUBP") na predlogu
teme. Cilj ovog dokumenta je da posluži kao osnova za razgovor sa mentorom — **ništa od
ovoga još nije implementirano niti menja postojeći kod.**

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

## Predlog obima (ako se krene u implementaciju)

Ne menjati sve odjednom. Zahvaljujući nedavnom VSA refaktoringu backend-a, svaka analiza je
već izolovan slice (`app/features/case_pathfinding/`, `case_management/`, itd.), pa je
migracija JEDNOG slice-a bez diranja ostalih realno mala i bezbedna promena:

1. **Pilot: `case_pathfinding`.** BFS najkraći put je najprirodniji kandidat — u Cypher-u je
   to `shortestPath()` u jednoj liniji. Graf baza se puni iz iste, već očišćene evidencije
   (`clean_evidence_frames`) kad se dokaz uveze — dokazni CSV i heš ostaju netaknuti, izvor
   istine.
2. Ako pilot pokaže vrednost, sledeći kandidat je `analytics/plugins/wallet_clustering.py`
   (community detection je ugrađen algoritam u većini graf baza).
3. Taint analiza, peel chains, chain hopping ostaju na NetworkX-u dok se ne pokaže da graf
   baza tu stvarno nešto dobija, umesto da se prepravlja "jer možemo".

## Sledeći korak

Ovo je materijal za razgovor sa mentorom — treba odlučiti (a) da li se ovo uopšte traži za
"osnovnu verziju" (mentor je u drugoj napomeni rekao da obim modula dogovarate naknadno),
i (b) koji kandidat iz tabele gore ima smisla za vaš rad. Kad se to razjasni, mogu da
napravim konkretan plan migracije za pilot slice.
