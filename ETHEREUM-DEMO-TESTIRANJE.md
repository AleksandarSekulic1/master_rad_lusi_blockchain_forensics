# Ručno testiranje — Ethereum demo slučaj

## O čemu je reč (ukratko)

Postoji već pripremljen demo slučaj koji pokriva **baš svaku** analizu u sistemu, sastavljen
od 10 ručno napravljenih evidencijskih fajlova (svaki za jednu konkretnu heuristiku) **plus**
jedna prava, uživo povučena on-chain evidencija (stvaran hakerski incident — vidi odeljak 9)
— svih 11 se spaja u jedan graf.

Flow of Funds / Layering analiza (odeljak 10) je jedina analiza koja **ne dobija svoj
poseban CSV** — i namerno ne treba joj jedan. Ona ne uvodi novu heuristiku niti nov obrazac
podataka, već **prati postojeće grane grafa** (kroz koliko god od tih 11 fajlova treba) i
**unakrsno poziva** analize koje ostalih 10 fajlova već pokrivaju (crna lista, risk scoring,
peel chains, chain hopping, wallet clustering, DEX swap, token approval, opciono Taint/
Sybil). Postojeći peel-chain scenario (`demo_case.csv`, odeljci 1-3) je već tačno onakav
podatak kakav ovoj analizi treba — nov fajl bi samo duplirao ono što već postoji.

**Otvori:** slučaj **"Demo: Sumnjiva laundering šema (hakovan novčanik)"** (id `46ae7f91db9b`).

Ovaj dokument prolazi kroz svaku stranicu aplikacije redom: šta otvoriti, koju adresu uneti,
šta ćeš videti, i zašto je to tako.

---

## Testiranje korak po korak

### 1. Graf

**Otvori:** stranica **Graf** → izabrani slučaj gore.

**Vidiš:** 264 čvora, 266 grana (zbir svih 10 ručnih fajlova + 430 pravih transakcija Ronin
Bridge hakerske adrese — odeljak 9).

Klikni na pojedine čvorove:

| Klikni na čvor | Vidiš u panelu | Zašto |
|---|---|---|
| `0xPeelSeed` | **Peel uloga: seed**, visok risk score | Prima 500 od `0xVictimWallet` i odmah deli na dva izlaza (350 nastavak + 150 "peel") — `peel_chains` plugin prepoznaje lanac `PeelSeed → PeelRelay1 → PeelRelay2` (3 koraka, confidence 80). |
| `0xUniswapRouter` | **Skok lanca: swap** | `chain_hopping` prepoznaje reč "uniswap"/"router"/"swap" u imenu — isto važi za `0xSushiRouter`, `0xBridgeRouterHop` (bridge), i sve adrese sa "exchange" u imenu (`0xCashOutExchangeWallet`, `0xExchangeHacker`, `0xExchangeMule`, `0xExchangeCounterparty`) — ukupno 7 tačaka. |
| `0xbad0000000000000000000000000000000000001` | **Crne liste: OFAC** (simulirana adresa), risk score **100** | Hardkodovana "simulaciona" demo adresa u `blacklist_check` plugin-u. |
| `0x098b716b8aaf21512996dc57eb0615e2383e2f96` | **Crne liste: OFAC** — "Ronin Bridge Exploiter / Lazarus Group", risk score **100** | Ovo **nije** simulirana adresa — stvarna, OFAC-sankcionisana adresa (designacija 2022-04-14) hakera Ronin Bridge-a. Vidi odeljak 9. |
| `0xCoConspirator1` | **Klaster: 2 člana** (sa `0xCoConspirator2`) | Obe adrese šalju **isti iznos (25), istoj adresi, u istom trenutku** — `multi_input` heuristika. |
| `0xAsiaHoursWallet` | **Klaster: 2 člana** (sa `0xNightOwlWallet`) | Ove dve nemaju zajedničku transakciju — spojene su preko `behavioral_similarity` heuristike (preklapaju im se skupovi suseda ≥75%), ne preko deljene transakcije. |
| `0xInvestorWallet` | **Klaster: 2 člana** (sa `0xUniswapRouter`) | Isto — `multi_input`, jer u kratkom prozoru razmenjuju 100 USDC u oba smera po istom obrascu kao ostatak seta. Dobar primer da heuristika ume i da preširoko uhvati (Investor i DEX router nisu isti "vlasnik") — heuristike su indikator za proveru, ne dokaz. |

### 2. Taint analiza

**Otvori:** stranica **Taint analiza** → seed adresa:
```
0xVictimWallet
```

**Vidiš:** taint se širi kroz 17 od 39 čvorova, opada niz `PeelSeed → PeelRelay1 →
PeelRelay2 → 0xbad...001` (blacklistovana adresa).

**Dugme "Predloži za pregled"** (iznad rezultata) — pokreće `seed-suggestions` proveru nad
celom evidencijom, nezavisno od unete seed adrese:

| Predložena adresa | Razlog |
|---|---|
| `0xbad0000000000000000000000000000000000001` | Na crnoj listi predmeta (poreklo/origin kandidat) |
| `0xPeelSeed`, `0xPeelRelay1`, `0xPeelRelay2` | Peel chain, koraci 1-3 |
| `0xBridgeRouterHop`, `0xExchangeMule` | Chain hopping + "brzi prolaz" (primljeno ≈ prosleđeno za par minuta) |
| `0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045` | **Buđenje uspavane adrese** — 91 dan bez aktivnosti, pa ponovna aktivnost 01.09.2026. |

Poslednji red je poenta `demo_dormancy_reactivation.csv` fajla: ista (stvarna) Ethereum
adresa prima 5 ETH krajem lanca pranja u junu (`demo_case.csv`), pa **ćuti 91 dan**, pa
iznenada prosleđuje 4.8 ETH na `0xCashOutExchangeWallet` u septembru — klasičan obrazac
"sačekaj da se prašina slegne, pa unovči".

### 3. Path finding

**Otvori:** stranica **Path finding** → From: `0xVictimWallet`,
To: `0xbad0000000000000000000000000000000000001`.

**Vidiš:** putanja `VictimWallet → PeelSeed → PeelRelay1 → PeelRelay2 → 0xbad...001`
(4 hop-a).

Probaj i drugi pravac — From: `0xPeelSeed`, To:
`0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045` → putanja ide kroz
`MuleWallet1 → BridgeRouterHop → DeadDropWallet` (4 hop-a) — druga grana istog lanca.

### 4. Behavioral analiza

**Otvori:** stranica **Behavioral analiza** → dve adrese za poređenje:

| Adresa | Broj transakcija | Timezone procena | Zašto |
|---|---|---|---|
| `0xNightOwlWallet` | 11 | **Nisko poverenje**, UTC-11 – UTC+12 (skoro ceo raspon) | Ime sugeriše obrazac, ali sa samo 11 transakcija raspoređenih neujednačeno, heuristika nema dovoljno signala da suzi opseg. |
| `0xAsiaHoursWallet` | 13 | **Srednje poverenje**, UTC+5 – UTC+12 (Azija/Okeanija) | Dosledan obrazac aktivnosti u istim satima kroz više dana daje uži, pouzdaniji opseg. |

Namerno je uparen jedan slab i jedan jak primer — da se vidi da alat **ne izmišlja
preciznost** kad je nema (`confidence: Low` vs `Medium`), plus da je uvek prisutan
disclaimer da je ovo heuristički indikator, ne dokaz lokacije.

### 5. DEX Swap analiza

**Otvori:** stranica **DEX Swap analiza** → pokreni nad slučajem.

**Vidiš:** 2 događaja, jedan **Detected**, jedan **Potential**:

| Tip | Ulaz → Izlaz | Zašto ta oznaka |
|---|---|---|
| **Detected** | 10 ETH → 25.000 USDC preko `0xUniswapRouter` | Oba kraka dele **isti transaction hash** (`0xswap0001`) — najjači mogući signal. |
| **Potential** | 2 ETH → 3200 DAI, 70s kasnije | Nema deljenog tx hash-a — uparen je samo po adresi i vremenskoj bliskosti (unutar podesivog prozora), pa je oznaka slabija. |

Ostale transakcije u istom fajlu (npr. `0xInvestorWallet ↔ 0xPeerWalletB`, isti iznos u oba
smera) su namerno **ne**-swap primeri (bounce iste valute) — provereno da ih algoritam
ispravno **ne** prijavljuje.

### 6. Token Approval analiza

**Otvori:** stranica **Token Approval analiza** → pokreni nad slučajem.

**Vidiš:** 5 grupa (owner → spender parova), sa različitim nivoima rizika:

| Owner → Spender | Token | Rizik | Zašto |
|---|---|---|---|
| `0xVictimWallet2 → 0xDrainerContract` | USDC | **HIGH** | Neograničen allowance (`is_unlimited: true`), povučen preko `transferFrom` unutar sat vremena od odobrenja — klasičan ice-phishing obrazac. |
| `0xVictimWallet → 0xDrainerContract` | USDC | **HIGH** | Isti spender odobren od **dva različita** vlasnika — strukturni signal mogućeg drainer ugovora sa više žrtava. |
| `0xVictimWallet → 0xSweepContract` | USDC, DAI | **MEDIUM** | Isti spender odobren za **2 različita tokena** — "potpiši ovde" phishing obrazac (širok pristup odjednom), ali bez `transferFrom` još uvek. |
| `0xCarefulTrader → 0xUniswapRouter` | DAI | **LOW** | Odobrenje **nije** neograničeno, iskorišćeno, pa **opozvano** (`approve(0)`) posle upotrebe — uzoran, oprezan obrazac. |

Namerno je uparen jedan "žrtva" i jedan "oprezan korisnik" primer sa istim DEX router-om,
da se pokaže da isti spender kod dva različita ponašanja vlasnika ne dobija automatski
istu ocenu.

### 7. Sybil & Bot Network analiza

*(Pozadina i heuristika: `14. SYBIL-ANALIZA.md`. Implementacija/fajlovi:
`SYBIL-ANALYSIS-IMPLEMENTATION.md`.)*

**Otvori:** stranica **Sybil & Bot mreže** → „Prikaz transakcija" → izaberi
`demo_sybil_analysis.csv` (ne kombinovano — vidi `14. SYBIL-ANALIZA.md` §2/§7 zašto).
Klikni **ANALIZIRAJ** → razlog pristupa + potpis.

Prvo (potpisano) ANALIZIRAJ uvek skenira sa najširim mogućim parametrima (min. 2 adrese,
prozor 3600s) — polja u „Napredna podešavanja" posle toga samo sužavaju prikaz, uživo,
bez novog potpisa (šta tačno znače ta polja — vidi `14. SYBIL-ANALIZA.md` §10).

**Vidiš:** tačno **2 klastera**:

| Klaster | Kontrakt / funkcija | Adrese | Risk score |
|---|---|---|---|
| **SYBIL-1** | `0xAirdropClaimContract` / `claimAirdrop` | `0xBotWallet1..5` | **88, critical** |
| **SYBIL-2** | `0xMintContract` / `mint` | `0xBotWallet1-3` | **76, high** |

**Probaj i (opciono, menja se uživo — bez novog potpisa):**

**A) Prag:**
1. Min. broj adresa → **3**.
2. Dobijaš isti prikaz kao u tabeli (2 klastera) — `0xRegularUserA`/`0xRegularUserB` ne
   zadovoljavaju ovaj prag pa nestaju iz prikaza.

**B) Filter po adresi:**
1. Adresa → `0xBotWallet1`.
2. Prikaz se odmah osveži → oba klastera (SYBIL-1 i SYBIL-2), jer ta adresa učestvuje u
   oba.

**C) Filter po kontraktu:**
1. Adresa → prazno (dugme × pored polja).
2. Kontrakt → `0xMintContract`.
3. Prikaz se odmah osveži → samo jedan klaster.

**Lanac dokaza:** posle potpisanog ANALIZIRAJ, u **Lanac dokaza** → „Po transakciji" —
bedž **👥 SYBIL** i poseban panel (Blockchain činjenice / Heuristički zaključci).

### 8. Napredna pretraga grafa (Neo4j)

**Otvori:** stranica **Slučajevi** → kartica demo slučaja → dugme za naprednu pretragu
grafa → unesi `0xVictimWallet`, 2 koraka.

**Vidiš:** na 1 koraku — `0xDrainerContract`, `0xDrainerWallet`, `0xPeelSeed`,
`0xSweepContract` (VictimWallet je istovremeno "žrtva" i u peel-chain **i** u
token-approval scenariju — namerno deljena adresa da poveže dve priče). Na 2 koraka —
`0xMuleWallet1`, `0xPeelRelay1`, `0xVictimWallet2`.

### 9. Prava on-chain evidencija — Ronin Bridge hak (2022)

Svih 10 fajlova do sad su ručno napravljeni demo scenariji. Za metodologiju predloga teme
("Praktično dokazivanje — testiranje na stvarnim, istorijskim podacima o poznatom
incidentu") ovome je dodata i **prava** evidencija:

**Otvori:** **Kontrolna tabla** → sekcija "Sa blockchain-a" → mreža **Ethereum mainnet** →
adresa:
```
0x098b716b8aaf21512996dc57eb0615e2383e2f96
```
→ **"Povuci transakcije"** (ili već učitano — vidi Depo dokaza slučaja).

**Šta je ovo:** adresa hakera koji je 23.03.2022. opljačkao **Ronin Bridge** (Axie Infinity),
oko 625 miliona dolara — jedan od najvećih kripto-hakova ikad, pripisan **Lazarus Group**-i
(Severna Koreja). OFAC je ovu tačnu adresu sankcionisao 14.04.2022.

**Vidiš:** 430 stvarnih transakcija, od 23.03.2022. nadalje. Pokretanjem analize (seed = ova
adresa):
- **Blacklist check** → **stvaran** pogodak, `sources: ["OFAC"]` (dodato u
  `blacklist_check.py`, isti obrazac kao za Bitcoin/Garantex — vidi `GRAPH.md`).
- **Risk scoring** → **100/critical** (blacklistovana adresa automatski dobija maksimalan
  skor).
- **Taint analiza** (seed = ova adresa) → širi se kroz 25 čvorova stvarne mreže u koju je
  haker rasturio ukradena sredstva.
- Ostale analize (peel chains, chain hopping, wallet clustering, anomaly detection, Sybil &
  Bot Network) i dalje rade bez greške na kombinovanom (ručni demo + pravi) grafu od 264
  čvora — vidi odeljak 7 za tačan broj Sybil klastera koje TA kombinacija proizvodi.

**Zašto je ovo važno:** ostalih 10 fajlova dokazuje da algoritam radi na kontrolisanim,
poznatim ulazima (jedinični test, u suštini). Ova adresa dokazuje da alat radi i na
**stvarnim, neuređenim** on-chain podacima pravog hakerskog incidenta — potpuno isti kod,
bez ijedne izmene za ovu priliku.

### 10. Flow of Funds / Layering analiza

*(Implementacija/fajlovi, integracija sa ostalim analizama, PDF, Log/Audit:
`FLOW-OF-FUNDS-IMPLEMENTATION.md`.)*

**Otvori:** stranica **Tok sredstava** → „Prikaz transakcija" → ostavi **Sve transakcije
(kombinovano)** → polazna adresa:
```
0xPeelSeed
```
→ Smer: **Unapred (kuda su otišla)** → Broj nivoa: **4** (podrazumevano, ne diraj) → **POKRENI
PRAĆENJE** → razlog pristupa + potpis.

**Zašto baš „kombinovano", a ne jedan fajl** (za razliku od Sybil analize u odeljku 7, koja
MORA da ostane na jednom fajlu — vidi `14. SYBIL-ANALIZA.md` §2/§7): Flow of Funds prati
STVARNE grane grafa kuda god vode, kroz koliko god fajlova. Ograničavanje na jedan fajl bi
ovde VEŠTAČKI presekao lanac tačno na granici fajla — poslednji korak ovog istog primera
(`0xbad...001 → 0xInvestorWallet`, tabela ispod) dolazi iz sasvim drugog fajla
(`demo_taint_dilution.csv`), ne iz onog gde lanac počinje (`demo_case.csv`). Nema razloga da
se bilo šta suzi — to je i jedina opcija koju ova stranica nudi za obim evidencije.

**Zašto baš `0xPeelSeed`, a ne `0xVictimWallet`** (iako je `0xVictimWallet` "prava" polazna
tačka pljačke): `0xVictimWallet` je namerno DELJENA adresa između dva nepovezana demo
scenarija — peel-chain (ovaj primer) i token-approval (odeljak 6). Praćenje TOKA od
`0xVictimWallet` bi na 1. nivou odmah pokazalo četiri odredišta iz dve različite priče
odjednom (peel-chain ka `0xPeelSeed` sa iznosom 500, i tri odobrenja tokena ka
`0xDrainerContract`/`0xDrainerWallet`/`0xSweepContract` sa iznosima reda veličine 500.000 —
probaj sam, to je tačno onaj slučaj gde je pošteno da alat pokaže SVE što vidi, ne ono što
analitičar već očekuje). `0xPeelSeed` je tačka gde peel-chain priča stvarno počinje — isti
seed koji Graf i Taint odeljci (1, 2) već koriste, bez mešanja sa nesrodnim scenarijem.

**Vidiš:** 9 agregiranih tokova kroz tačno 4 nivoa (nije skraćeno):

| Nivo | Tok | Imovina | Iznos | Broj tx | Zašto |
|---|---|---|---|---|---|
| 1 | PeelSeed → MuleWallet1 | UNKNOWN | 150 | 1 | „peel" grana (manji deo) |
| 1 | PeelSeed → PeelRelay1 | UNKNOWN | 350 | 1 | nastavak lanca (veći deo) |
| 2 | PeelRelay1 → PeelRelay2 | UNKNOWN | 250 | 1 | nastavak |
| 2 | PeelRelay1 → MuleWallet2 | UNKNOWN | 90 | 1 | još jedan „peel" |
| 2 | MuleWallet1 → BridgeRouterHop | UNKNOWN | 140 | 1 | druga grana istog lanca (vidi Path finding, odeljak 3) |
| 3 | PeelRelay2 → 0xbad...001 | **ETH** | 200 | **2** | dve odvojene transakcije (120+80) agregirane u JEDAN tok |
| 3 | BridgeRouterHop → DeadDropWallet | UNKNOWN | 135 | 1 | |
| 4 | 0xbad...001 → 0xInvestorWallet | ETH | 10 | 1 | tok NASTAVLJA i iz blacklistovane adrese, ne staje tu |
| 4 | DeadDropWallet → 0xd8dA...045 | ETH | 5 | 1 | ista adresa koja se „budi" posle 91 dana (odeljak 2) |

**Zašto su neki tokovi `UNKNOWN` a neki `ETH`:** nijedan red u ovoj evidenciji ne deklariše
`currency` kolonu, pa se imovina nagađa iz OBLIKA adrese. `0xbad...001`, `0xInvestorWallet` i
`0xd8dA...045` su ispravno oblikovane Ethereum adrese (`0x` + 40 hex znakova), pa dobijaju
`ETH`; pseudo-labele kao `PeelSeed`/`MuleWallet1` nisu, pa ostaju `UNKNOWN` — pošteno
označeno kao nepoznato, nikad izmišljeno.

**Klikni na tok `PeelRelay2 → 0xbad...001`** (bočni panel, desno od dijagrama):

- **Direktne blockchain činjenice**: Na crnoj listi (OFAC) — „Simulated OFAC sanctioned
  address" (ista adresa/nalaz kao u odeljku 1).
- **Agregovani rezultati**: Ukupno primljeno (ETH): 200, poslato: 10.
- **Heuristički zaključci**: Risk scoring **100/100 (critical)** — razlog „blacklisted
  address".
- Dugme **„Prikaži u Lancu dokaza"** uz svaku od dve transakcije (120 i 80) — svaka se
  nezavisno otvara na svom zapisu.

**Probaj (opciono, sve ispod menja samo PRIKAZ već preuzetih podataka, bez novog potpisa,
osim tačke A):**

**A) Čekiraj „Taint analizom (isti seed-ovi)" PRE pokretanja.** `0xbad...001` dobija
**Taint: 100%** (seed ovde je `0xPeelSeed`, NE `0xVictimWallet` kao u Taint odeljku (2) —
namerno drugačiji seed, videćeš drugačiji, ali unutar sebe dosledan procenat ako probaš oba).
`0xInvestorWallet` (4 skoka dalje, posle prolaska kroz nesrodne transakcije u
`demo_taint_dilution.csv`) dobija **Taint: 0.03%** — razblaženo, ali ne nula. Ovo NIJE nov
model — poziva se isti `taint_analysis.py` koji Taint stranica koristi, samo sa seed-ovima
koje si već uneo ovde (`seed_from_blacklist` je namerno isključen, vidi
`FLOW-OF-FUNDS-IMPLEMENTATION.md` §5 — procenat prati SAMO ovaj tok, ne i nepovezanu
blacklist adresu negde drugde u slučaju).

**B) Klikni na tok ka `0xInvestorWallet`** (nivo 4, sa uključenim Taint-om iz tačke A) —
najbogatiji primer unakrsnih nalaza u celom demo slučaju:
- **4 odvojena `flow_totals` bedža** (DAI/ETH/USDC/USDT), nikad sabrana preko valuta.
- **`dex_swap_detected`** (agregovano — isti tx hash `0xswap0001` na oba kraka) i
  **`dex_swap_potential`** (heuristika — 70s vremenska podudarnost) — isti događaji koje
  DEX Swap analiza (odeljak 5) već prijavljuje kao „Detected"/„Potential".
- **`wallet_cluster: cluster_5`** — ista `0xInvestorWallet ↔ 0xUniswapRouter` veza iz Graf
  odeljka (1), red o klasteru koji "ume i da preširoko uhvati".
- **Risk score 66/100 (high)**, razlog „one hop away from a blacklisted address" — Risk
  Scoring plugin primećuje blizinu `0xbad...001` sam, bez ijednog dodatnog poziva.

**C) Nivo prikaza → Kategorije.** Broj tokova ostaje 9 — nijedna od ovih pseudo-adresa nije u
kuriranom registru poznatih entiteta (`known_entities.json`), pa nema šta da se spoji. Ovo
je namerno POŠTENO ponašanje (nikad izmišljen entitet), ne propust — uporedi sa tačkom D.

**D) Ponovi sa polaznom adresom `0x098b716b8aaf21512996dc57eb0615e2383e2f96`** (prava Ronin
Bridge adresa, odeljak 9), 2 nivoa. Ovog puta se VIDI razlika: 31 agregiran tok na nivou
adresa, ali samo **25** na nivou kategorija — ova adresa (i nekoliko odredišta) su u
kuriranom registru poznatih entiteta pa se spajaju pod kategoriju **„sanctioned"**. Isti kod,
druga adresa — razlika dolazi isključivo iz toga da li je adresa STVARNO u registru, nikad
iz pogađanja.

**E) Izvezi PDF izveštaj.** Sadrži sve gore, plus tabelu „Layering nivoi" (4 nivoa), tabelu
„Ukupan analizirani volumen" (odvojeno po imovini), i eksplicitnu rečenicu koja kaže da li je
Taint/Sybil bio uključen u TO pokretanje — bez obzira ima li heurističkih nalaza za prikaz.

---

## Poenta ovog demo slučaja

Deset ručnih fajlova zajedno pokrivaju **svaku** analizu u sistemu barem jednim jasnim,
namerno-dizajniranim primerom — uključujući i granične slučajeve koji **ne smeju** da se
lažno prijave (bounce transakcija u DEX Swap-u, oprezan korisnik u Token Approval-u, adrese
ispod praga i van vremenskog prozora u Sybil & Bot Network analizi). Jedanaesti, pravi
(Ronin Bridge), dokazuje da isti kod radi i van kontrolisanih uslova — i, uzgred, pokazuje
da Sybil heuristika hvata svaku sinhronizovanu konvergenciju na stvarnim podacima, ne samo
namerno-dizajnirane demo primere (odeljak 7). Isti princip kao Bitcoin demo
(`BITCOIN-UVOZ.md`): svaka tvrdnja se može stvarno pokrenuti i proveriti, ne samo
pročitati.

Flow of Funds (odeljak 10) je dvanaesti dokaz istog principa, ali na drugom nivou — ne
uvodi trinaesti fajl, već pokazuje da se već postojećih 11 mogu čitati ZAJEDNO, kroz više
nivoa odjednom, sa svakim ranije uvedenim nalazom (crna lista, risk score, peel chain,
DEX swap, wallet cluster, opciono Taint/Sybil) prikazanim na jednom mestu i jasno razdvojenim
na činjenicu/agregat/heuristiku (§10 `FLOW-OF-FUNDS-IMPLEMENTATION.md`). Isti obrazac i tu:
tačke B i D iznad se mogu stvarno kliknuti i proveriti, brojevi u tabeli nisu prepisani iz
glave — povučeni su direktno iz pokrenute analize nad ovim istim slučajem.
