# Ideje za nove analize (backlog)

Tri predloga za sledeću analitičku funkciju u aplikaciji, poređana po odnosu uloženog
truda i vrednosti za istražitelja. Nijedna još nije implementirana — ovo je beleška za
kasnije, ne dokumentacija gotove funkcije (uporedi sa TAINT-ANALIZA.md, DEX-SWAP-ANALIZA.md
itd., koji opisuju već implementirane analize).

---

## 1. Unakrsna provera adresa kroz slučajeve (preporučeno — najmanje posla)

**Problem koji rešava:** Analitičar otvori adresu u jednom slučaju i nema način da zna da
li se ta ista adresa već pojavila u nekom DRUGOM slučaju. U praksi se iste "mule" adrese,
menjačnice i infrastruktura često recikliraju kroz više odvojenih istraga — to je upravo
signal koji vezuje slučajeve jedan za drugi.

**Zašto je malo posla:** Nema novog analitičkog algoritma — samo pretraga postojećih
`data/cases/<id>/` fajlova (evidencija transakcija) za datu adresu, preko svih slučajeva
osim trenutnog. Slično kao `case_management.list_cases(search=...)`, samo što se pretražuje
sadržaj evidencije umesto naziva slučaja.

**Skica implementacije:**
- Backend: nova funkcija, npr. `find_address_across_cases(address, exclude_case_id)` u
  `app/services/case_management.py` (ili novi modul) — prođe kroz sve slučajeve, učita
  evidenciju (ili već izgrađen graf ako je keširan), i vrati listu pogodaka: `{case_id,
  case_name, role: 'pošiljalac'|'primalac', broj_transakcija}`.
- Ruta: `GET /api/v1/cases/{case_id}/address-lookup?address=...` (ili slično).
- Frontend: mala značka/panel na Graf stranici (ili u "Detalji transakcije" panelu na
  Taint analizi) — "⚠ Adresa se pojavljuje u 2 druga slučaja", sa linkovima na te slučajeve.
- Custody: ovo je **pasivan uvid** (kao Kontrolna tabla), ne pokreće se `custody`
  potpisivanje — samo se prikazuje info, ne "pristupa se dokazu" u smislu lanca dokaza.

**Potencijalna zamka:** performanse ako ima mnogo slučajeva sa velikom evidencijom —
možda vredi keširati skup adresa po slučaju umesto da se svaki put ponovo parsira CSV.

---

## 2. Detekcija strukturiranja (smurfing)

**Problem koji rešava:** Klasičan AML crveni signal — veliki iznos se namerno razbije na
mnogo malih transakcija, često tik ispod praga prijave (npr. dosledno 8.000–9.400 umesto
10.000+). Ovo se tematski savršeno uklapa u scenario "sumnjivog pranja novca" koji je već
demo-slučaj u aplikaciji.

**Zašto je srednji posao:** Treba nov analitički modul (grupisanje transakcija po
pošiljaocu/vremenskom prozoru, prepoznavanje obrasca "mnogo malih uplata koje se sabiraju
u okrugao/veliki iznos, svaka ispod praga"), plus nova ruta i nova frontend stranica — po
uzoru na `dex_swap_analysis`/`behavioral_analysis` (isti oblik: `GET` pasivna varijanta +
`POST .../run` deliberatna varijanta sa `custody` potpisivanjem, vidi LANAC-DOKAZA.md).

**Skica implementacije:**
- Backend: `app/analytics/structuring_detection.py` — heuristika: za svaku adresu,
  posmatraj izlazne (ili ulazne) transakcije unutar kliznog vremenskog prozora (npr. 72h);
  ako N transakcija (N ≥ neki prag, npr. 4) ima sličnu veličinu i sve su ispod
  konfigurabilnog praga prijave, a zbir prelazi taj prag — označi kao grupu strukturiranja.
  Parametri (prag, veličina prozora, min. broj transakcija) bi trebalo da budu podesivi u
  UI, isto kao `max_gap_seconds` kod DEX Swap analize.
- Ruta: `POST /api/v1/cases/{case_id}/structuring-analysis/run`.
- Frontend: nova stranica "Strukturiranje" — lista detektovanih grupa (izvor, broj
  transakcija, vremenski raspon, ukupan iznos, prag koji se izbegava), PDF izvoz po istom
  obrascu kao ostali izveštaji.
- Testovi: sintetički scenariji (kao `test_scenario_created` na stranici Testovi) —
  "14 uplata od ~9000 tik ispod praga od 10000 treba da se prepozna kao strukturiranje".

**Potencijalna zamka:** lažni pozitivi — legitimna platna dinamika (plate, rate) može
ličiti na strukturiranje. Vredi od početka razmišljati o pragovima koji se mogu podesiti
po slučaju, ne fiksnim konstantama u kodu.

---

## 3. Centralnost čvorova (hub detekcija)

**Problem koji rešava:** Koje adrese su "čvorišta" kroz koja prolazi neproporcionalno
mnogo toka sredstava — potencijalni mikseri, menjačnice ili druge tačke konsolidacije.
Trenutno graf boji čvorove po riziku/zaprljanosti, ali ne i po tome KOLIKO je neki čvor
strukturno važan za povezivanje ostatka grafa.

**Zašto je malo-do-srednje posla:** `networkx` je već zavisnost projekta (koristi se za
sam graf), a ima gotove funkcije — `betweenness_centrality`, `degree_centrality`,
`pagerank` — koje rade direktno nad već izgrađenim grafom. Nije potreban nov algoritam,
samo poziv gotove funkcije i novi režim bojenja na postojećoj Graf stranici.

**Skica implementacije:**
- Backend: u `app/analytics/graph_building.py` (ili novi mali modul) — funkcija koja nad
  već sastavljenim `networkx.DiGraph` pozove `nx.betweenness_centrality(graph)` (pažnja:
  na velikom grafu ovo je O(V·E) — možda vredi ograničiti na `k`-uzorak čvorova za
  aproksimaciju kod velikih slučajeva, `nx.betweenness_centrality(graph, k=500)`), i vrati
  rezultat kao dodatno polje po čvoru (`centrality_score`), normalizovano 0–1.
  Ruta: dodatni parametar na postojećoj `GET /cases/{id}/graph` (`?color_by=centrality`)
  ili posebna ruta.
- Frontend: na Graf stranici, dugme/dropdown "Boji po: Rizik | Zaprljanost | Centralnost" —
  isti mehanizam bojenja kao za taint %, samo druga vrednost kao ulaz u gradijent boje.
- Nije potrebna nova custody-gated akcija — može se tretirati kao deo postojećeg
  "Analiziraj graf" pokretanja (dodatno polje u već postojećem odgovoru), bez novog
  potpisivanja.

**Potencijalna zamka:** `betweenness_centrality` je skup za velike grafove — proveriti
performanse na najvećem demo slučaju pre nego što se ponudi kao podrazumevana opcija.

---

## Preporuka

Ako treba birati jedno za sledeći korak: **#1 (unakrsna provera kroz slučajeve)** —
najmanje rizika, najbrže se završava, i rešava stvaran, svakodnevni problem istražitelja
("da li sam ovu adresu već negde video"), za razliku od #2 i #3 koji su više "nice to
have" analitički dodaci.
