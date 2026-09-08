# DEX Swap Analysis

Dokumentacija novog, samostalnog modula koji pokušava da automatski prepozna **DEX swap
događaje** — situacije kada adresa pošalje jedan token na poznat/verovatan DEX smart
contract, a zatim u kratkom vremenskom periodu primi drugi token nazad — namerno odvojeno
od Graph/Taint/Pathfinding/Behavioral stranica. Ovo je **heuristika**, ne dokaz: svaki
nalaz nosi eksplicitan nivo pouzdanosti (`Detected` ili `Potential`) i disclaimer.

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
| [10. Gde je šta u kodu](#10-gde-je-šta-u-kodu) | putanje |

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
(`demo_dex_swap_analysis.csv`, 12 redova) u isti deljeni demo slučaj (`46ae7f91db9b`) koji
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

## 10. Gde je šta u kodu

| Šta | Fajl |
|---|---|
| Algoritam | `backend/app/analytics/dex_swap_analysis.py` (`detect_dex_swaps`, `classify_dex_node`) |
| Kuriran spisak DEX adresa | `backend/app/analytics/known_dex_contracts.json` |
| Ruta | `backend/app/api/routes/cases.py` (`get_case_dex_swap_analysis`) |
| Testovi | `backend/tests/test_dex_swap_analysis.py` |
| Demo podaci | `backend/scripts/seed_demo_dex_swap_evidence.py` |
| Frontend stranica (§8) | `frontend/src/app/features/dex-swap-analysis/` |
| Graph integracija (§9) | `frontend/src/app/features/graph-visualization/graph-visualization.component.ts` (`loadDexSwapOverlay`, `renderSwapOverlay`, `buildSwapEdgeElements`, `swapConfidenceLevel`) |
| API poziv | `frontend/src/app/core/services/api.service.ts` (`getDexSwapAnalysis`) |
| Tipovi | `frontend/src/app/models/blockchain-forensics.models.ts` (`DexSwapEvent`, `DexSwapAnalysisResult`, `DexSwapDataCompleteness`, `DexSwapNodeConsidered`) |

**Ruta:**

| Ruta | Namena |
|---|---|
| `GET /api/v1/cases/{id}/dex-swap-analysis` | Kandidati za DEX swap (Detected/Potential) u evidenciji slučaja, opciono ograničeno na jednu adresu |
