# DEX Swap Analysis

Dokumentacija novog, samostalnog modula koji pokušava da automatski prepozna **DEX swap
događaje** — situacije kada adresa pošalje jedan token na poznat/verovatan DEX smart
contract, a zatim u kratkom vremenskom periodu primi drugi token nazad — namerno odvojeno
od Graph/Taint/Pathfinding/Behavioral stranica. Ovo je **heuristika**, ne dokaz: svaki
nalaz nosi eksplicitan nivo pouzdanosti (`Detected` ili `Potential`) i disclaimer.

> **Samo želiš da testiraš (korak po korak, sa gotovim demo podacima)?** Idi direktno na
> **[§14. Testiranje korak po korak — kompletan vodič](#14-testiranje-korak-po-korak--kompletan-vodič)**
> na kraju ovog dokumenta — sve ostalo ispod je tehničko objašnjenje ZAŠTO je tako
> napravljeno, ne uputstvo kako da se proveri da radi.

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Zašto zasebna analiza](#1-zašto-zasebna-analiza) | odnos prema Graph/Taint/Pathfinding/Behavioral |
| [2. Zašto se ne koristi deljeni graf](#2-zašto-se-ne-koristi-deljeni-graf) | ključno arhitektonsko ograničenje i rešenje |
| [3. Heuristika](#3-heuristika) | klasifikacija DEX čvora, dva prolaza uparivanja, uslovi |
| [4. Mere protiv false positive-a](#4-mere-protiv-false-positive-a) | šest konkretnih kočnica, sa primerima |
| [5. API](#5-api) | ruta, parametri, oblik odgovora |
| [6. Demo podaci i ručna provera](#6-demo-podaci-i-ručna-provera) | šest namerno izolovanih parova, tačni brojevi |
| [7. Šta nedostaje za pouzdaniju detekciju](#7-šta-nedostaje-za-pouzdaniju-detekciju) | pošteno o ograničenjima podataka |
| [8. Frontend stranica](#8-frontend-stranica) | `/dex-swaps` — Address + ANALYZE, lista swap kartica |
| [9. Graph integracija](#9-graph-integracija) | isprekidane SWAP veze preko postojećeg grafa, klik-detalji, High/Medium/Low |
| [10. Taint preko swap-a](#10-taint-preko-swap-a) | zašto se taint ne sme pustiti kroz DEX čvor kao kroz običan, i šta radimo umesto toga |
| [11. PDF izveštaj](#11-pdf-izveštaj) | potpisan izveštaj sa kontrolnim brojem, isti obrazac kao Taint/Pathfinding |
| [12. Lanac dokaza](#12-lanac-dokaza) | ANALYZE kao deliberatan pristup, isti obrazac kao Taint/Pathfinding/Graf |
| [13. Gde je šta u kodu](#13-gde-je-šta-u-kodu) | putanje |
| [14. Testiranje korak po korak — kompletan vodič](#14-testiranje-korak-po-korak--kompletan-vodič) | **🡒 počni ovde ako samo želiš da testiraš** |

---

## 1. Zašto zasebna analiza

| | Graph | Taint | Pathfinding | Behavioral | DEX Swap Analysis |
|---|---|---|---|---|---|
| Pitanje | Kako izgleda cela mreža? | Koliki deo sredstava potiče od izvora X? | Kojim redosledom su sredstva stigla od A do B? | Kada je adresa aktivna? | Da li je ovaj par transakcija zapravo JEDAN swap? |
| Ulaz | evidencija slučaja | seed čvorovi | dve adrese | jedna adresa | evidencija slučaja (opciono jedna adresa) |
| Rezultat | graf čvorova/grana | % zaprljanosti | jedna putanja | raspodela po satu/danu | lista kandidata za swap, sa nivoom pouzdanosti |

Sve analize čitaju istu, već očišćenu evidenciju jednog slučaja, ali ovo je fizički
odvojena stranica/ruta/modul, bez deljenog stanja sa ostale četiri.

## 2. Zašto se ne koristi deljeni graf

`app.analytics.graph_building.build_transaction_graph` (deljen od strane Graph/Taint/
Pathfinding/Behavioral) namerno agregira transakcije po granama i čuva samo `{amount,
timestamp, metadata}` — **bez `currency` polja**. To znači da graf ne može da razlikuje
"10 ETH" od "25 000 USDC" ni u principu.

Zato ovaj modul **ne dira `graph_building.py`** (da ne bi uticao na ostale module), nego
radi direktno nad `combined_frame` — istim DataFrame-om koji `case_graph.combine_frames`
već proizvodi iz `ingestion.clean_transaction_csv`, koji **zadržava** `currency` kolonu
po redu, kad postoji. Cena ovog izbora: modul ne koristi (i ne dobija) čvorove/grane koje
su drugi analitički moduli već označili (npr. `chain_hop_flag`), jer ne prolazi kroz
zajednički graf.

## 3. Heuristika

`app/analytics/dex_swap_analysis.py` → `detect_dex_swaps(transactions, target_address=None, max_gap_seconds=300)`

### 3.1 Klasifikacija DEX čvora

Za svaku jedinstvenu adresu u evidenciji, `classify_dex_node()` proverava, tim redom:

1. **Tačan pogodak** u `known_dex_contracts.json` (novi fajl — deset realnih, javno
   poznatih Ethereum DEX router/pool adresa: Uniswap V2/V3/Universal Router, SushiSwap,
   1inch V4/V5, 0x Exchange Proxy, Curve 3pool, Balancer Vault). Case-insensitive
   poređenje (adrese su hex, veličina slova nije semantička).
2. **"Brand" ključna reč** u samom tekstu adrese (`uniswap`, `sushiswap`, `curve`,
   `balancer`, `1inch`, `kyberswap`, ...) — za demo/ručne CSV-ove koji koriste čitljive
   pseudo-adrese (`0xUniswapRouter`) umesto pravih 0x adresa.
3. **Generička ključna reč** (`dex`, `router`, `aggregator`) — slabiji signal, uvek
   posebno obeležen (`keyword_match_generic`) da bi analitičar znao da mu treba dodatna
   provera.

Adresa koja ne pogodi nijedno od ovoga se **uopšte ne razmatra** kao mogući DEX — nema
"strukturne" (in-degree/out-degree) klasifikacije kao kod `chain_hopping.py`, namerno:
takav signal je previše širok za tvrdnju "ovo je DEX" (vidi §4).

**Namerno IZOSTAVLJENO iz obe liste:** `exchange` (kolidiralo bi sa `0xExchangeCounterparty`,
adresom koja već postoji u Behavioral demo evidenciji istog slučaja) i goli `swap`
(kolidiralo bi sa običnim korisničkim adresama/labelama koje sadrže tu reč, npr. wallet
nazvan "...SwapTrader...", a nije DEX — sve marke koje su stvarno bitne već pokriva
brand lista).

### 3.2 Uparivanje — dva prolaza

**Prolaz 1 — isti transaction hash.** Ako dva reda u evidenciji dele isti, stvarno
deklarisan `metadata`/`tx_hash` (ne izmišljen fallback ID — vidi `tx_identity.py`), i
jedan ide `wallet → DEX` a drugi `DEX → wallet` (ista adresa), to je najjači raspoloživ
signal u ovom modelu podataka — evidencija je eksplicitno zabeležila oba transfera kao
deo jedne on-chain transakcije. → **`Detected Swap`**.

**Prolaz 2 — vremenski prozor.** Za sve što Prolaz 1 nije rešio: za svaki DEX čvor, za
svaku adresu koja mu je nešto poslala, traži se najranija još neiskorišćena transakcija
`DEX → ta ista adresa`, koja stiže POSLE (ili istovremeno sa) odlaznom transakcijom i
unutar `max_gap_seconds` (podrazumevano **300s / 5 minuta**, podesivo 10–3600s query
parametrom). → **`Potential Swap`**.

Svaka transakcija se upotrebljava u **najviše jednom** paru (greedy, hronološki) — nema
duplog brojanja.

### 3.3 Kad se par NE proglašava swap-om

- `currency` deklarisana i **ista** na oba kraka (npr. ETH → ETH) — to je bounce, ne
  swap, bez obzira na DEX čvor i vremensku bliskost.
- `currency` nije deklarisana ni na jednom kraku — par se **i dalje** prijavljuje
  (adresa + vreme je i dalje signal), ali `input_token`/`output_token` su `null`, a
  `data_completeness` to eksplicitno kaže (vidi §7).

## 4. Mere protiv false positive-a

1. **Ista adresa na oba kraka** — `wallet → DEX` i `DEX → wallet` moraju imati identičan
   pošiljalac/primalac. Bez "povezane adrese" (npr. preko wallet clustering-a) u v1 —
   svesno ograničenje, ne previd (vidi §7).
2. **Isti DEX čvor** na oba kraka, ne bilo koji DEX bilo gde.
3. **Vremenska ograda**: povratni krak ne sme doći pre odlaznog, i mora biti unutar
   `max_gap_seconds`.
4. **Isključenje iste valute** (§3.3).
5. **Uska "brand" lista + eksplicitno obeležena "generic" lista** (§3.1) — čak i kad
   generička reč pogodi nešto što nije DEX (npr. `0xBridgeRouterHop` iz postojeće
   peel-chain demo evidencije sadrži "router"), uslov #1 to sam ispravi: taj čvor u
   stvarnim podacima šalje dalje na **drugu** adresu (`0xDeadDropWallet`), ne nazad
   pošiljaocu (`0xMuleWallet1`) — nula događaja, odbrana u dubinu, ne oslanjanje na
   savršenu klasifikaciju čvora.
6. **Case-sensitive poređenje adrese** (`target_address`) — ista konvencija kao
   Pathfinding/Behavioral, nema normalizacije na mala slova pri filtriranju po adresi.

## 5. API

```
GET /api/v1/cases/{case_id}/dex-swap-analysis?address=<opciono>&evidence=<opciono>&max_gap_seconds=<opciono, 10-3600>
```

Read-only ruta, isti tretman kao `/graph` i `/behavioral-analysis`: nema dijaloga za
lanac dokaza i ne piše u `custody_log`, jer samo čita već očišćenu evidenciju. `address`
je **opciono** (za razliku od Behavioral) — izostavljeno, vraćaju se svi kandidati u
evidenciji; zadato, rezultat je ograničen na tu adresu (404 ako se adresa nigde ne
pojavljuje).

Odgovor:
```json
{
  "case_id": "46ae7f91db9b",
  "evidence": null,
  "address": null,
  "total_events": 2,
  "detected_count": 1,
  "potential_count": 1,
  "events": [
    {
      "type": "SWAP",
      "confidence": "Detected",
      "label": "Detected Swap",
      "user_address": "0xInvestorWallet",
      "dex_address": "0xUniswapRouter",
      "dex_name": "0xUniswapRouter",
      "dex_match_basis": "keyword_match_brand: uniswap",
      "input_token": "ETH",
      "input_amount": 10.0,
      "input_transaction_hash": "0xswap0001",
      "input_timestamp": "2026-08-24T09:00:00+00:00",
      "output_token": "USDC",
      "output_amount": 25000.0,
      "output_transaction_hash": "0xswap0001",
      "output_timestamp": "2026-08-24T09:00:00+00:00",
      "time_gap_seconds": 0,
      "match_basis": "shared_transaction_hash",
      "reasons": ["...", "..."]
    }
  ],
  "dex_nodes_considered": [{ "address": "0xUniswapRouter", "name": "0xUniswapRouter", "match_basis": "keyword_match_brand: uniswap" }],
  "data_completeness": { "currency_declared": true, "note": "..." },
  "max_gap_seconds": 300,
  "disclaimer": "DEX swap detekcija je heuristika zasnovana na adresnom obrascu, vremenskoj bliskosti i (kad postoji) deklarisanoj valuti - ne predstavlja kriptografski dokaz da se radi o swap transakciji.",
  "generated_at": "2026-08-25T21:03:07+00:00"
}
```

## 6. Demo podaci i ručna provera

`backend/scripts/seed_demo_dex_swap_evidence.py` dodaje **jedan** fajl
(`demo_dex_swap_analysis.csv`, 13 redova — dvanaest za šest parova plus jedan red koji
priprema taint-seed za §10) u isti deljeni demo slučaj (`46ae7f91db9b`) koji
koriste i ostali moduli, adresa `0xInvestorWallet`. Fajl je namerno seed-ovan direktno
(mimo `/upload/csv`), jer sadrži više valuta — vidi §7 zašto to obična otprema danas ne
bi prihvatila.

Šest parova, svaki demonstrira **tačno jednu** stvar:

| Par | Krakovi | Rezultat | Zašto |
|---|---|---|---|
| 1 | 10 ETH → Uniswap → 25 000 USDC, isti `tx_hash` | **Detected Swap** | Prolaz 1, deljen hash |
| 2 | 2 ETH → Uniswap → 3 200 DAI, 70s razmak, bez hash-a | **Potential Swap** | Prolaz 2, samo vreme+adresa |
| 3 | 1 ETH → Uniswap → 0.98 ETH, 30s razmak | **Ništa** | ista valuta na oba kraka |
| 4 | 4 ETH → SushiRouter → 6000 USDT, **2h** razmak | **Ništa** (na podrazumevanih 300s) | van vremenskog prozora — vraća se pri `max_gap_seconds≥7200` |
| 5 | 3 ETH → SushiRouter → 4500 USDC ide na `0xOtherWallet` | **Ništa** | povratak na DRUGU adresu |
| 6 | 100 USDC → PeerWalletB → 100 USDC nazad | **Ništa** | nijedna adresa nije prepoznata kao DEX |

Pokretanjem `python scripts/seed_demo_dex_swap_evidence.py`, pa `GET
/cases/46ae7f91db9b/dex-swap-analysis?evidence=<stored_name fajla>` (ili bez `evidence`
za kombinovanu evidenciju — ostali fajlovi u slučaju ne dodaju nove event-e), dobija se
tačno `total_events: 2, detected_count: 1, potential_count: 1` — svaki broj gore je
stvarno izračunat pokretanjem prave rute kroz `TestClient`, ne ručno.

Automatski testovi:
```bash
python -m pytest backend/tests/test_dex_swap_analysis.py -v
```
19 testova: klasifikacija DEX čvora (poznata adresa case-insensitive, brand keyword,
`exchange` NIJE DEX, obična adresa nije DEX), Detected vs Potential confidence, van
prozora / širenje prozora, ista valuta se ne prijavljuje, povratak na drugu adresu se ne
prijavljuje, obična razmena bez DEX signala se ne prijavljuje, nedostatak `currency`
kolone i dalje detektuje po adresi+vremenu (token=null), filtriranje po adresi,
nepostojeća adresa → `ValueError`, case-sensitive poređenje, jedna transakcija se ne
upotrebljava dvaput, `max_gap_seconds` se ograničava na [10, 3600], disclaimer je uvek
prisutan.

## 7. Šta nedostaje za pouzdaniju detekciju

Pošteno, po zahtevu — ovo NIJE potpuna zamena za pravu ERC-20 Transfer analizu:

- **`currency` je opciona kolona i retko je popunjena u postojećoj evidenciji.** Kad
  nedostaje, `input_token`/`output_token` su `null` — par se i dalje prijavljuje kao
  `Potential`, ali bez ikakve potvrde da su u pitanju stvarno dva različita tokena.
- **Pravi ERC-20 Transfer eventi se ne povlače.** `onchain_ingestion.fetch_address_transactions`
  koristi samo Etherscan-ov `txlist` (native transfer), nikad `tokentx` — pa ni jedan
  on-chain uvoz u ovom projektu danas ne može sam proizvesti par "ETH out / USDC in" za
  istu transakciju. `known_dex_contracts.json` je koristan tek kad neko ručno unese CSV
  sa `currency` kolonom (ili se doda `tokentx` uvoz — van obima ove verzije).
- **Nema "povezane adrese" logike.** Ako swap izlaz ide na DRUGU adresu (npr. preko
  wallet clustering-a bi se dalo povezati sa istim vlasnikom), ovaj modul to neće
  uparalti — namerno, da ne bi pravio nepouzdanu, fuzzy tvrdnju o vlasništvu.
- **`known_dex_contracts.json` je mali, ručno kuriran spisak** (10 adresa), ne živ
  registar — realne, javno poznate adrese, ali bez garancije potpunosti ili da je svaki
  checksum trenutno tačan; treba proveriti/dopuniti pre pravog forenzičkog korišćenja.
- **Generička ključna reč (`dex`/`router`/`aggregator`) je slab signal**, uvek posebno
  obeležen u `dex_match_basis` — analitičar treba dodatno da proveri takve nalaze.
- **Bez PDF izveštaja i bez dijaloga za lanac dokaza** — read-only pregled, ne pokreće
  novu obradu nad evidencijom, pa se ne beleži u `custody_log` (izveštaj je poseban,
  kasniji korak — vidi zahtev).

## 8. Frontend stranica

Ruta `/dex-swaps`, link **„DEX Swaps"** u glavnom meniju — potpuno odvojena od Graph/
Taint/Pathfinding/Behavioral stranica, isti obrazac kao Behavioral Analysis (aktivan
slučaj + evidence picker, pa **Address** + **ANALYZE**, ništa više na vrhu).

Namerno **bez grafova/statističkih kartica** — glavni (jedini) sadržaj je ravna lista
kartica, jedna po detektovanom događaju, sa jednom istaknutom linijom:

```
INPUT AMOUNT INPUT_TOKEN → DEX_NAME → OUTPUT AMOUNT OUTPUT_TOKEN
```

npr. `10 ETH → 0xUniswapRouter → 25,000 USDC`. Badge iznad kartice je `Detected Swap`
(akcentna boja — isti tx hash na oba kraka) ili `Potential Swap` (priguušena siva —
samo adresa + vreme). Kad `input_token`/`output_token` nedostaje, prikazuje se `?` sa
`title` objašnjenjem — nikad izmišljen simbol. Prazan rezultat: **„No DEX swaps detected
for this address."** Disclaimer iz API odgovora se prikazuje uvek, jednom, ispod liste.

`address` je na ovoj stranici **obavezno polje** (za razliku od same rute, koja ga
prima opciono) — stranica uvek analizira tačno jednu adresu, po zahtevu.

## 9. Graph integracija

DEX swap nalazi se iscrtavaju **preko postojećeg** grafa transakcija na `/graph` stranici
— ne gradi se novi graf, ne menja se `graph_building.py`, `Taint`/`Pathfinding` logika
nije dirana. Realizovano isključivo na frontend-u, kao dodatni sloj cytoscape elemenata.

### 9.1 Šta se crta

Za svaki event iz `GET /cases/{id}/dex-swap-analysis` (pozvano **bez** `address`
parametra — vraća sve kandidate u trenutno izabranoj evidenciji, isti poziv kao
frontend stranica iz §8, samo case-wide), dodaje se **jedna** dodatna grana
`user_address → dex_address`:

- **Isprekidana, ljubičasta** (`#c084fc`) — vizuelno jasno odvojena i od običnih
  transakcionih grana (plava, puna linija) i od bridge/chain-hop grana (takođe
  isprekidane, ali zelen šestougaoni čvor, bez ljubičaste linije).
  Oznaka na grani: `SWAP · 10 ETH → 25,000 USDC`.
- **Providnost/debljina prati pouzdanost** (§9.2) — `High` je najupadljivija, `Low`
  najprigušenija, bez potrebe da se klikne da bi se stekao prvi utisak.
- Realne grane (Wallet A→Uniswap i Uniswap→Wallet A pojedinačno) **ostaju nepromenjene**
  — SWAP grana je trećа, dodatna veza između istog para čvorova, ne zamena.

### 9.2 High / Medium / Low — prevod postojeća dva polja u treće, samo za prikaz

Zahtev traži tronivojski prikaz pouzdanosti; backend ima samo `confidence`
(`Detected`/`Potential`) i `dex_match_basis`. Umesto novog backend polja,
`swapConfidenceLevel()` (frontend, čisto prezentaciono, ne menja API) izvodi treći nivo
iz ta dva postojeća:

| Prikazano | Uslov |
|---|---|
| **High** | `confidence === 'Detected'` (isti tx hash — najjači signal) |
| **Medium** | `Potential`, ali DEX čvor prepoznat pouzdano (`known_address` ili `keyword_match_brand`) |
| **Low** | `Potential` **i** DEX čvor prepoznat samo generičkom rečju (`keyword_match_generic`) |

Ista razlika koju §3.1/§4 već prave između brand i generic ključnih reči — ovde samo
dobija jedno ime za prikaz.

### 9.3 Klik na SWAP vezu

Otvara panel u desnom „Detalji čvora" prostoru (zamenjuje ga dok je swap veza izabrana —
klik na čvor ili drugu granu ga zatvara, isto obrnuto):

```
DEX:                Uniswap
Input:               10 ETH
Output:              25,000 USDC
Timestamp:           2026-08-24 09:00 UTC
Transaction hash:    Tx: 0xswap0001
Confidence:          High (Detected · keyword_match_brand: uniswap)
```

Za `Potential` bez zajedničkog hash-a, red „Transaction hash" prikazuje `Tx in:`/
`Tx out:` odvojeno (nikad tvrdi da je jedan hash zajednički kad nije potvrđeno). Ispod
liste polja, uvek isti disclaimer kao na §8 stranici.

### 9.4 Toggle i vremenska traka

Dugme **„Prikaži/Sakrij DEX swap veze (N)"** pored ostalih filtera u zaglavlju grafa —
uključeno po default-u. Kad je **Vremenska traka** aktivna, SWAP veze **nisu** deo
hronologije (nemaju `chronoRank` kao prave grane) — ostaju vidljive nezavisno od pozicije
trake, isključivo pod kontrolom ovog toggle-a; ako se sakrije čvor (npr. filterom „bez
odliva"), povezana SWAP veza se automatski sakriva zajedno s njim (cytoscape-ovo
podrazumevano ponašanje, bez dodatne logike).

### 9.5 Učitavanje — nezavisno od samog grafa

Overlay se učitava **posebnim** pozivom, paralelno sa običnim `/graph` pozivom, ne kao
njegov deo — ako overlay poziv ne uspe, graf se i dalje normalno prikazuje, samo bez
isprekidanih veza (bez greške na ekranu). Dodavanje/uklanjanje veza kad odgovor stigne NE
pokreće ponovni layout celog grafa (`renderSwapOverlay()` samo doda/ukloni elemente na
već postojećem cytoscape objektu) — layout bi inače nepotrebno „promešao" pozicije
čvorova koje je analitičar možda već ručno rasporedio.

## 10. Taint preko swap-a

Backend `taint_analysis.py` je **potpuno nedirnut** — nula izmena algoritma, grafa ili
nove rute. Ovo je isključivo frontend funkcionalnost na `/graph` stranici, i aktivira se
samo posle "Analiziraj graf".

### 10.1 Zašto se taint ne sme pustiti kroz DEX čvor kao kroz običan

`taint_analysis.py` prati JEDAN bezjedinični `balance`/`tainted_balance` broj po čvoru —
model uopšte ne zna za valutu (ista arhitektonska činjenica kao u §2). Kad bi se taint
pustio kroz Uniswap kao kroz običan čvor:

1. **Mešanje jedinica kvari, ne samo razblažuje, aritmetiku.** Ulazni krak doda "10"
   (ETH) u Uniswap-ov balans; kad izlazni krak (25.000, USDC) stigne, `share =
   min(1.0, amount/source_balance) = min(1.0, 25000/10) = 1.0` — pojede se CEO praćeni
   "prljavi" balans DEX čvora, prenoseći apsolutno **10 jedinica** (ne 25.000), jer
   ETH i USDC brojevi nisu uporedivi 1:1. Rezultat na primaocu: `100 × 10 / 25000 =
   0.04%` — daleko od stvarnih 100%.
2. **Uniswap je deljen čvor** — kroz njega prolaze i tuđi swap-ovi; nepromenjen
   algoritam bi taj udeo računao protiv CELOG agregiranog balansa DEX-a, tačno kao
   generičko razblaživanje kod konsolidatora — ispravno za pravi mixing hub, pogrešno za
   privatnu 1:1 konverziju jednog trgovca.

**Ovo NIJE hipoteza — potvrđeno je stvarnim brojem u demo podacima (§10.4 ispod).**

### 10.2 Rešenje — prenos VEĆ izračunatog broja, bez novog modela

Za svaki detektovan swap, frontend traži u `analytics.taint_analysis.tainted_hops[]`
(deo odgovora `POST /cases/{id}/analytics/run`, već postojeći, nepromenjen) unos koji
odgovara ULAZNOM kraku swap-a — spojeno po `(source, target, amount, timestamp)`,
**isti obrazac koji `taint-analysis.component.ts` već koristi** (`buildEdgeDetails()`,
linija ~818) da poveže `tainted_hops` sa konkretnom transakcijom, pošto nijedan od ta
dva zapisa nema zajednički ID transakcije. Pronađen unos → njegov `taint_pct_at_hop` i
`taint_by_source` JESU preneti taint (već izračunat, ISKLJUČIVO za taj jedan transfer,
pre nego što je ikad dotakao Uniswap). Nije pronađen → **"0% — čista transakcija"**
(ista formulacija koju Taint stranica već koristi za nezaprljan transfer) — pošto
`tainted_hops` beleži SVAKI transfer koji je nosio bilo kakav taint, odsustvo znači
"nula", ne "nedostaju podaci".

Prikazano **samo na swap grani** (`swapCarriedTaint()` u
`graph-visualization.component.ts`):
- Oznaka na grani dobija `· N% tainted` sufiks, plus crveni „halo" (underlay) kad je
  procenat > 0 — vidljivo bez klika.
- Klik-panel dobija novi red **„Taint (preneto sa ulaznog kraka)"**, sa eksplicitnom
  napomenom da je preneto sa ulaznog kraka, ne nezavisno izračunato za DEX ili izlazni
  token — i sa raspodelom po seed-u kad ih ima više (isti prikaz kao Taint stranica).
- Pre „Analiziraj graf": „Taint analiza nije pokrenuta — klikni..." — nula, ne prazno.

### 10.3 Šta OSTAJE vidljivo pogrešno, i zašto je to u redu

Uniswap-ov **sopstveni** `taint_percentage` (vidljiv na zasebnoj `/taint` stranici, ne
na `/graph` čvor-panelu, koji taj broj i inače ne prikazuje) i dalje trpi od §10.1 — to
NIJE popravljeno, niti je trebalo da bude (značilo bi dirati model bez dogovora). Prenos
taint-a preko swap grane je **dodatan, ispravno izračunat, jasno obeležen kanal**
specifično za identifikovane swap parove — ne tvrdi ništa o DEX čvoru samom.

### 10.4 Demo — stvaran primer, ne izmišljen

`seed_demo_dex_swap_evidence.py` dodaje red `0xbad0...0001 → 0xInvestorWallet, 10 ETH`
(podrazumevano crnolistirana adresa — automatski seed za taint, bez ikakve dodatne
konfiguracije) neposredno pre Para 1, tačno replicira primer iz zahteva:

| Swap | Preneti taint | Zašto |
|---|---|---|
| Par 1 — 10 ETH → Uniswap → 25.000 USDC (Detected) | **100%** | Investor je pre swap-a primio TAČNO 10 ETH od crne liste i odmah ih poslao dalje — ceo ulazni krak je 100% zaprljan. |
| Par 2 — 2 ETH → Uniswap → 3.200 DAI (Potential) | **0.04%**, ne 0% | **Namerna, poštena demonstracija §10.1**: Uniswap-ov izlazni krak iz Para 1 (25.000 USDC) je, po nepromenjenom modelu, preneo samo 10 „jedinica" apsolutnog taint-a (ne 100% od 25.000, iz razloga u §10.1/tačka 1) — te 10 jedinica ostaju „zaglavljene" u Investor-ovom sad mešovito-valutnom balansu i cure u SVAKI naredni transfer, uključujući Par 2. Ovo NIJE greška u prenosu — verno je prikazan broj koji nepromenjen algoritam stvarno računa. |

Pokretanjem `python scripts/seed_demo_dex_swap_evidence.py`, pa na `/graph` stranici
izborom `demo_dex_swap_analysis.csv`, klikom „Analiziraj graf" (razlog pristupa +
potpis), pa klikom na SWAP granu — oba broja gore su stvarno izračunata kroz pravu
aplikaciju (Playwright provera), ne ručno.

## 11. PDF izveštaj

Potpuno frontend funkcionalnost — **nema nove backend rute**. Ponovo iskorišćen isti
generički mehanizam koji već koriste Taint i Pathfinding izveštaji
(`POST /api/v1/reports/register` → `GET /api/v1/reports/verify`, vidi
`report_registry.py`), i ista `<app-signature-pad>` komponenta — samo novi poziv iz
`dex-swap-analysis.component.ts`, izgrađen po **identičnom obrascu** kao
`taint-analysis.component.ts`-ov PDF (jspdf + jspdf-autotable), radi vizuelne
doslednosti.

### 11.1 Šta izveštaj sadrži

Tačno ono što je trenutno prikazano na ekranu za analiziranu adresu (ista adresa +
evidencija + `max_gap_seconds` kao poslednji uspešan "ANALYZE"), na tri strane:

1. **Zaglavlje** — Case ID, analizirana adresa, ko je izvezao, evidencija, vremenski
   prozor, vreme generisanja; odmah ispod, kutija sa **istim `disclaimer` tekstom** koji
   API vraća (§5).
2. **Rezime analize** — kartice: ukupno događaja, Detected, Potential, broj razmotrenih
   DEX kontrakata.
3. **Ključni nalazi** — kratak spisak sa kvačicama (broj događaja, da li je valuta
   deklarisana, upozorenje ako postoje događaji sa Low pouzdanošću).
4. **DEX kontrakti razmotreni** — tabela (`dex_nodes_considered`, ista lista kao API
   odgovor).
5. **Detektovani swap događaji** — glavna tabela, jedan red po događaju: DEX,
   Input, Output, **Pouzdanost** (High/Medium/Low — ista `swapConfidenceLevel` logika
   kao Graph overlay, §9.2 — obojena u tabeli po istom ključu boja), Vreme, Tx hash.
6. Ako postoji bar jedan **Low** događaj — posebna upozoravajuća sekcija sa tabelom tih
   događaja i objašnjenjem zašto zaslužuju dodatnu proveru.
7. **Zaključak** — automatski sastavljen pasus (ista svrha kao Taint izveštajev
   `buildConclusionParagraph`).
8. **Metodologija i ograničenja** — sažeta verzija §3/§4/§7 ovog dokumenta: kako radi
   heuristika (dva prolaza), šta NIJE (nije dokaz), i tačno ista lista ograničenja
   podataka (valuta retko popunjena, nema pravog ERC-20 uvoza, nema linked-address
   logike, mali kuriran spisak, generic keyword slab signal, case-sensitive).
9. **Potpis i overa** — nacrtan potpis analitičara + vektorski pečat "LUSI" + kontrolni
   broj + otisak sadržaja (SHA-256) — identičan mehanizam kao Taint/Pathfinding, uključno
   i napomenu da je potpis izjava, ne kriptografski dokaz (vidi `report_registry.py`
   modul-docstring).

### 11.2 Otisak sadržaja

`reportContentPayload()` hešuje: case ID, adresu, evidenciju, `max_gap_seconds`, i za
svaki događaj — adrese, pouzdanost, tokene/iznose, timestamp-ove, osnov uparivanja
(sortirano, da redosled polja nikad ne utiče na heš). Namerno **ne** uključuje
`reasons[]`/opisne tekstove — heš prati brojeve i adrese koje bi neko mogao osporiti, ne
prozu.

### 11.3 Testirano stvarnim izvozom

Pokretanjem prave aplikacije (Playwright): izabrana adresa `0xInvestorWallet`, klik
"Izvezi PDF izveštaj", nacrtan potpis, klik "Potpiši i izvezi PDF" — preuzet je pravi,
validan 3-stranični PDF (`%PDF-1.3` zaglavlje, ~406 KB), sa tačno očekivanim sadržajem
(uključujući ispravno obojenu "High (Detected)" / "Medium (Potential)" kolonu i
generisan, jedinstven kontrolni broj svaki put).

## 12. Lanac dokaza

DEX Swap Analysis se sad ponaša identično Taint/Pathfinding/Graf-u: skeniranje evidencije
u potrazi za swap parovima **jeste** deliberatan pristup svakoj transakciji u tom opsegu,
pa se tako i beleži (vidi LANAC-DOKAZA.md §2). Ništa u samom `detect_dex_swaps()`
algoritmu nije dirano — ovo je isključivo nova ruta plus deljena logika koja već postoji.

### 12.1 Dva endpoint-a, namerno razdvojena

Isti obrazac kao Graf stranica (sirovi graf vs. "Analiziraj graf"):

| Ruta | Ko je zove | Custody |
|---|---|---|
| `GET /cases/{id}/dex-swap-analysis` | Graf stranicin DEX swap overlay (§9) — pasivan, automatski, deo pregleda | Nikad — nepromenjena, i dalje read-only |
| `POST /cases/{id}/dex-swap-analysis/run` | DEX Swaps stranicino „ANALYZE" dugme (§8) | Uvek — `custody` u telu zahteva |

Overlay na Grafu ostaje potpuno nepromenjen (i dalje čita GET rutu) — swap veze se i
dalje vide na grafu bez ijednog dodatnog klika, tačno kao pre. Samo dedikovana DEX Swaps
stranica sada traži razlog pristupa i potpis PRE nego što uopšte pozove backend.

### 12.2 Šta se upisuje

`run_case_dex_swap_analysis` gradi `per_evidence_frames`/`combined_frame` na isti način
kao `run_case_analytics`/`run_case_pathfinding`, i kad `custody` stigne, poziva **isti,
nepromenjen** `_record_custody_access()` helper — nema posebne logike „upiši samo
transakcije koje su ispale u detektovan swap"; upisuje se **cela evidencija u opsegu**
(kombinovana ili jedan fajl), jer je algoritam stvarno pročitao svaki taj red da bi našao
parove, tačno isti princip kao kod Pathfinding-ove BFS pretrage (vidi LANAC-DOKAZA.md §2).

Audit log dobija akciju `dex_swap_analysis_run` (Aktivnost log + izveštaj aktivnosti,
amber boja, ista grupa kao `analytics_run`/`path_finding`) sa `custody_recorded` i brojem
upisanih transakcija/fajlova — isti obrazac kao ostale tri deliberatne rute.

### 12.3 UI tok

Klik na **ANALYZE** više ne zove API direktno — otvara isti deljeni
`CustodyAccessDialogComponent` (razlog pristupa, potpis, checkbox izjave). Tek na
potvrdu se poziva `POST .../dex-swap-analysis/run`; rezultati (kartice) se pojavljuju tek
tada, ne pre. Neuspeh ostaje prikazan UNUTAR dijaloga (ništa uneto se ne gubi), isti
obrazac kao Taint/Pathfinding/Graf.

### 12.4 Testirano stvarnim pokretanjem

Playwright provera: klik ANALYZE → dijalog se otvara, **nula** kartica prikazano pre
potvrde → popunjen razlog, nacrtan potpis, potvrđeno → dijalog se zatvara, kartice se
pojavljuju. Direktnom proverom `logs/custody_log.jsonl` posle: 58 novih redova (cela
evidencija demo slučaja u opsegu), tačno onoliko koliko ima transakcija u kombinovanoj
evidenciji. Stranica „Lanac dokaza" → tab „Po transakciji" odmah pokazuje ažuriran
„Poslednji pristup" za te transakcije; Aktivnost log pokazuje novi red sa tačnim brojem
detektovanih događaja i „lanac dokaza: N transakcija, M fajl(ova)" sažetkom.

### 12.5 Uzgredna popravka — `report_signed` akcija u Log aktivnosti

Dok je ovo testirano, primećeno je da PDF izvoz (§11 — akcija `report_signed`, koju
`POST /reports/register` piše kad se izveštaj potpiše) **nije imao lep prikaz ni za
jednu od tri stranice** koje ga koriste (Taint, Pathfinding, DEX Swaps) — u logu se
video goli tekst `report_signed`, bez ikonice, bez detalja o TIPU izveštaja (jer se
`RegisterReportRequest.summary` — jedini deo koji nosi taj podatak — do sada uopšte nije
upisivao u audit log, samo `verification_code`/`content_hash`). Popravljeno za sve tri
stranice odjednom (deljen mehanizam, deljena popravka):

- `RegisterReportRequest` dobija novo, opciono polje `report_type` (`'taint'` |
  `'pathfinding'` | `'dex_swap'`) — čisto opisno, **nikad** deo otiska sadržaja
  (`compute_content_hash` i dalje hešuje samo `content`, ne `summary`/`report_type`).
- Sve tri stranice (`taint-analysis.component.ts`, `pathfinding.component.ts`,
  `dex-swap-analysis.component.ts`) sada šalju svoj `report_type` uz poziv
  `registerReport()`.
- `write_audit_log` za `report_signed` sada upisuje `report_type` **plus ceo `summary`
  rečnik** koji stranica pošalje (ne samo dva generička polja kao ranije) — svaki
  segment sad vidljiv pod „Prikaži" u Log aktivnosti, isti obrazac kao ostale akcije.
- Lep naziv/ikonica/boja (`'Izvezen potpisan izveštaj (PDF)'`, 🖋, grupa `report`) i
  sažetak u jednom redu (`DEX Swap izveštaj · LUSI-2026-... · 2 događaja`, odn. `Taint
  izveštaj · ... · N zaprljanih adresa, M tačaka unovčavanja`, odn. `Pathfinding
  izveštaj · ... · N skokova`) — dodato i u `activity_report.py` (PDF/CSV izveštaj
  aktivnosti) i u `activity-log.component.ts` (ekran), iste formulacije na oba mesta.

Provereno stvarnim PDF izvozom kroz aplikaciju: red u Log aktivnosti sad glasi „🖋
Izvezen potpisan izveštaj (PDF)" / „DEX Swap izveštaj · LUSI-2026-LZHR-SURE · 2
događaja", a razvijen prikaz („Prikaži") ispisuje svih devet polja pojedinačno
(uključujući `total_events`, `detected_count`, `potential_count`, `address`,
`report_type`, `verification_code`, `content_hash`) — ništa od ranije nije obrisano,
samo dodato.

## 13. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| Algoritam | `backend/app/analytics/dex_swap_analysis.py` (`detect_dex_swaps`, `classify_dex_node`) |
| Kuriran spisak DEX adresa | `backend/app/analytics/known_dex_contracts.json` |
| Ruta | `backend/app/api/routes/cases.py` (`get_case_dex_swap_analysis`) |
| Testovi | `backend/tests/test_dex_swap_analysis.py` |
| Demo podaci | `backend/scripts/seed_demo_dex_swap_evidence.py` |
| Frontend stranica (§8) | `frontend/src/app/features/dex-swap-analysis/` |
| Graph integracija (§9) | `frontend/src/app/features/graph-visualization/graph-visualization.component.ts` (`loadDexSwapOverlay`, `renderSwapOverlay`, `buildSwapEdgeElements`, `swapConfidenceLevel`) |
| Taint preko swap-a (§10) | isti fajl kao gore (`swapCarriedTaint`, `taintAnalysis`, `swapTaintBreakdown`) — čita `TaintAnalysisResult` tip koji već postoji za `/taint` stranicu, ništa novo u backend-u |
| PDF izveštaj (§11) | `frontend/src/app/features/dex-swap-analysis/dex-swap-analysis.component.ts` (`buildDexSwapPdf`, `confirmSignatureAndExport`) — nema nove backend rute, koristi postojeći `/reports/register` |
| Lanac dokaza (§12) | `backend/app/api/routes/cases.py` (`run_case_dex_swap_analysis`, `DexSwapAnalysisRunRequest`) — koristi nepromenjen `_record_custody_access` |
| Aktivnost log oznake (§12.2) | `backend/app/exports/activity_report.py` + `frontend/src/app/features/activity-log/activity-log.component.ts` — akcija `dex_swap_analysis_run` |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`getDexSwapAnalysis` — pasivno, `runDexSwapAnalysis` — deliberatno, `registerReport` — već postojao za Taint/Pathfinding) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`DexSwapEvent`, `DexSwapAnalysisResult`, `DexSwapDataCompleteness`, `DexSwapNodeConsidered`) |

**Rute:**

| Ruta | Namena |
|---|---|
| `GET /api/v1/cases/{id}/dex-swap-analysis` | Kandidati za DEX swap (Detected/Potential) u evidenciji slučaja, opciono ograničeno na jednu adresu — pasivno, bez custody |
| `POST /api/v1/cases/{id}/dex-swap-analysis/run` | Isto, ali deliberatno — `custody` opciono u telu, upisuje lanac dokaza kad je prisutan (vidi §12) |

## 14. Testiranje korak po korak — kompletan vodič

Ovaj deo je namerno **odvojen od tehničkih objašnjenja iznad** — čisto uputstvo, redosled
klikova i tačni brojevi koje treba da vidiš, bez obrazloženja "zašto". Za "zašto", vidi
odgovarajući broj sekcije iz §1–§13 (naveden uz svaki korak ispod).

Sve što sledi koristi **isti demo slučaj** koji koriste i ostali moduli (Taint, Graf,
Pathfinding, Behavioral) — ništa dodatno ne treba da praviš ručno.

### 14.0 Priprema (jednom)

1. Pokreni backend i frontend (`uvicorn app.main:app` iz `backend/`, `ng serve` iz
   `frontend/`).
2. Iz `backend/` foldera pokreni:
   ```bash
   python scripts/seed_demo_dex_swap_evidence.py
   ```
   Ovo dodaje (ili osvežava, ako već postoji) fajl `demo_dex_swap_analysis.csv` u slučaj
   **„Demo: Sumnjiva laundering sema (hakovan novcanik)"** (ID `46ae7f91db9b`) — 13 redova,
   šest namerno izolovanih parova plus jedan red za taint-seed (vidi §6 i §10.4 za tačan
   sadržaj i zašto je baš tako sastavljeno).
3. Prijavi se u aplikaciju (`admin` / `admin123` na demo instalaciji).
4. **Slučajevi** → izaberi **„Demo: Sumnjiva laundering sema (hakovan novcanik)"** kao
   aktivan slučaj (klik na red u tabeli).

### 14.1 Korak 1 — Osnovna detekcija + Lanac dokaza (stranica „DEX Swaps")

*Vidi §8 (stranica) i §12 (lanac dokaza) za pozadinu.*

1. Klikni **„DEX Swaps"** u glavnom meniju.
2. „Prikaz transakcija" → izaberi `demo_dex_swap_analysis.csv` (radi preglednosti — bez
   ovoga radi i nad „Sve transakcije (kombinovano)", samo sa više nepovezanih rezultata iz
   ostale demo evidencije istog slučaja).
3. Address: `0xInvestorWallet` → klikni **ANALYZE**.
4. **Očekivano:** umesto da odmah vidiš rezultat, otvara se dijalog **„Razlog pristupa i
   potpis"** — jer je ovo, kao i Taint/Pathfinding/Graf, deliberatan pristup evidenciji
   (§12.1). Nijedna kartica se ne vidi iza dijaloga.
5. Upiši bilo šta u **„Opis radnje"** (npr. „Provera DEX swap detekcije"), nacrtaj potpis
   mišem u polju, čekiraj izjavu, klikni **„Potpiši i pokreni analizu"**.
6. **Očekivano posle potvrde:**
   - **2 swap-a detected** (pilula pored dugmeta).
   - Kartica 1: badge **DETECTED SWAP**, `0xInvestorWallet → 0xUniswapRouter`,
     **`10 ETH → 0xUniswapRouter → 25,000 USDC`**, `Time: 2026-08-24 09:00 UTC`,
     `Tx: 0xswap0001`.
   - Kartica 2: badge **POTENTIAL SWAP**, `0xInvestorWallet → 0xUniswapRouter`,
     **`2 ETH → 0xUniswapRouter → 3,200 DAI`**, `Time: 2026-08-25 11:15 UTC · gap 70s`.
   - Ispod obe kartice: disclaimer rečenica (heuristika, ne dokaz).

### 14.2 Korak 2 — Graph vizuelizacija (isprekidane SWAP veze)

*Vidi §9 za pozadinu.*

1. Klikni **„Graf"** u meniju (isti slučaj je i dalje aktivan).
2. „Prikaz transakcija" → izaberi opet `demo_dex_swap_analysis.csv`.
3. **Očekivano, automatski (bez klika)**: na grafu se, pored običnih plavih strelica,
   vide **dve isprekidane ljubičaste veze** između `0xInvestorWallet` i `0xUniswapRouter`,
   sa oznakom `SWAP · 10 ETH → 25,000 USDC` i `SWAP · 2 ETH → 3,200 DAI`. Dugme **„Sakrij
   DEX swap veze (2)"** pored ostalih filtera potvrđuje broj.
4. Klikni na jednu od isprekidanih veza (ako ti je teško da pogodiš mišem, uveličaj
   scroll-om najpre). **Očekivano:** desni panel se menja na **„SWAP DOGAĐAJ
   (HEURISTIKA)"** sa poljima DEX / Input / Output / Timestamp / Transaction hash /
   Confidence — za `10 ETH → 25,000 USDC` treba da piše `High (Detected · ...)`.
5. Polje **„Taint (preneto sa ulaznog kraka)"** u istom panelu treba da kaže **„Taint
   analiza nije pokrenuta"** — jer još nisi kliknuo „Analiziraj graf" (sledeći korak).

### 14.3 Korak 3 — Taint preko swap-a

*Vidi §10 za pozadinu, uključujući ZAŠTO je drugi broj ispod 0.04% a ne 0%.*

1. Na istoj Graf stranici, klikni **„Analiziraj graf"** → otvara se isti dijalog kao u
   §14.1 → popuni i potpiši → potvrdi.
2. **Očekivano:** graf se oboji po riziku; `0xbad0...0001` (crna lista) postaje crven
   čvor, `0xInvestorWallet` dobija istaknutu ivicu (visok rizik/taint).
3. Klikni ponovo na granu `10 ETH → 25,000 USDC`. **Očekivano:** polje „Taint" sad
   pokazuje **100%**, sa napomenom da je preneto sa ulaznog kraka. Grana takođe dobija
   crveni „halo" oko isprekidane linije.
4. Klikni na drugu granu (`2 ETH → 3,200 DAI`). **Očekivano: 0.04%**, ne 0% — namerna
   demonstracija ostatka nepromenjenog modela (§10.1/§10.4), ne greška.

### 14.4 Korak 4 — PDF izveštaj

*Vidi §11 za pozadinu.*

1. Vrati se na **„DEX Swaps"** stranicu. **Napomena:** i izbor evidencije i rezultat iz
   koraka 14.1 se **ne pamte** kad odeš na drugu stranicu i vratiš se (obična komponenta,
   ne deljeno stanje) — ponovi korake 2–6 iz §14.1 u celosti (ponovo izaberi
   `demo_dex_swap_analysis.csv` u „Prikaz transakcija", pa adresa `0xInvestorWallet` →
   **ANALYZE** → popuni i potpiši dijalog) da bi kartice ponovo bile na ekranu pre nego
   što nastaviš ovde. Ako preskočiš ponovni izbor evidencije, analiza i dalje radi (nad
   „Sve transakcije (kombinovano)"), ali brojevi u §14.5 koraku 2 ispod neće se poklopiti.
2. Klikni **„Izvezi PDF izveštaj"** → otvara se dijalog **„Potpis analitičara"**.
3. Nacrtaj potpis, čekiraj izjavu, klikni **„Potpiši i izvezi PDF"**.
4. **Očekivano:** preuzima se fajl `<case_id>_dex_swap_report.pdf` — tri strane:
   zaglavlje + ključni nalazi + tabela događaja (strana 1), metodologija/ograničenja
   (strana 2), potpis + pečat „LUSI" + kontrolni broj (strana 3).

### 14.5 Korak 5 — Provera u Log aktivnosti i Lanac dokaza

*Vidi §12 (lanac dokaza) i §12.5 (log aktivnosti) za pozadinu.*

1. Klikni **„Lanac dokaza"** u meniju → tab **„Po transakciji"**. **Očekivano:**
   transakcije iz `demo_dex_swap_analysis.csv` (npr. `0xInvestorWallet→0xUniswapRouter`)
   imaju svež unos u koloni „Poslednji pristup", sa vremenom bliskim koraku 14.1.
2. Klikni **„Log"** u meniju. **Očekivano, odozgo naniže (najnoviji prvi):**
   - **🖋 Izvezen potpisan izveštaj (PDF)** — `DEX Swap izveštaj · LUSI-2026-... · 2
     događaja` (od koraka 14.4).
   - **⇌ Pokrenuta DEX swap analiza** — `0xInvestorWallet · demo_dex_swap_analysis.csv ·
     2 događaja · lanac dokaza: 13 transakcija, 1 fajl(ova)` (od PONOVLJENOG koraka 14.1
     unutar koraka 14.4, ne od prvog pokretanja — to je zato najnovija takva stavka).
     Tačno 13 transakcija zato što je `demo_dex_swap_analysis.csv` bio izabran u „Prikaz
     transakcija", ne „Sve transakcije (kombinovano)" (vidi napomenu u §14.4).
   - **⚙ Pokrenuta analiza** — od „Analiziraj graf" (koraka 14.3).
   - Ispod toga, još jedan **⇌ Pokrenuta DEX swap analiza** red — od PRVOG pokretanja u
     koraku 14.1 (svaki deliberatan pristup dobija svoj red, ništa se ne prepisuje).
3. Klikni **„Prikaži"** na redu „Pokrenuta DEX swap analiza" — **očekivano:** razvijen
   prikaz sa SVAKIM poljem posebno (`address`, `evidence_scope`, `max_gap_seconds`,
   `total_events`, `detected_count`, `potential_count`, `custody_recorded`,
   `custody_transaction_rows`, `custody_evidence_files`).

### 14.6 (Opciono) Provera da se NE prijavljuju lažni pozitivi

*Vidi §4 i §6 za pozadinu — ovo su preostala četiri para iz demo fajla, namerno bez
swap oznake.*

Na „DEX Swaps" stranici (ponovo izaberi `demo_dex_swap_analysis.csv` u „Prikaz
transakcija" — vidi napomenu u §14.4, izbor se ne pamti između poseta stranici), probaj i:

- Address `0xSushiRouter` — **očekivano: 0 swap-ova** (SushiRouter je DEX čvor, ne
  korisnička adresa — analiza traži swap-ove ZA tu adresu kao pošiljaoca/primaoca, ne
  kroz nju kao DEX).
- „Sve transakcije (kombinovano)" + adresa `0xInvestorWallet` → i dalje **tačno 2**
  swap-a (ne 6) — parovi 3–6 su namerno isključeni (ista valuta, prevelik razmak, pogrešna
  povratna adresa, nema DEX signala) — potvrđuje da kočnice iz §4 stvarno rade, ne samo
  na papiru.

### 14.7 Automatski testovi

```bash
python -m pytest backend/tests/test_dex_swap_analysis.py -v
```
19 testova — pokriva tačno iste kočnice kao §14.6, plus klasifikaciju DEX čvora,
Detected/Potential razliku, i granične slučajeve (§6 ima pun spisak).
