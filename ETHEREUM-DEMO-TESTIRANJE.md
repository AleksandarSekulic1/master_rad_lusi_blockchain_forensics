# Ručno testiranje — Ethereum demo slučaj

## O čemu je reč (ukratko)

Postoji već pripremljen demo slučaj koji pokriva **baš svaku** analizu u sistemu, sastavljen
od 9 odvojenih evidencijskih fajlova (svaki napravljen za jednu konkretnu heuristiku), koji
se svi spajaju u jedan graf:

**Otvori:** slučaj **"Demo: Sumnjiva laundering šema (hakovan novčanik)"** (id `46ae7f91db9b`).

Ovaj dokument prolazi kroz svaku stranicu aplikacije redom: šta otvoriti, koju adresu uneti,
šta ćeš videti, i zašto je to tako.

---

## Testiranje korak po korak

### 1. Graf

**Otvori:** stranica **Graf** → izabrani slučaj gore.

**Vidiš:** 39 čvorova, 39 grana (zbir svih 9 evidencijskih fajlova).

Klikni na pojedine čvorove:

| Klikni na čvor | Vidiš u panelu | Zašto |
|---|---|---|
| `0xPeelSeed` | **Peel uloga: seed**, visok risk score | Prima 500 od `0xVictimWallet` i odmah deli na dva izlaza (350 nastavak + 150 "peel") — `peel_chains` plugin prepoznaje lanac `PeelSeed → PeelRelay1 → PeelRelay2` (3 koraka, confidence 80). |
| `0xUniswapRouter` | **Skok lanca: swap** | `chain_hopping` prepoznaje reč "uniswap"/"router"/"swap" u imenu — isto važi za `0xSushiRouter`, `0xBridgeRouterHop` (bridge), i sve adrese sa "exchange" u imenu (`0xCashOutExchangeWallet`, `0xExchangeHacker`, `0xExchangeMule`, `0xExchangeCounterparty`) — ukupno 7 tačaka. |
| `0xbad0000000000000000000000000000000000001` | **Crne liste: OFAC** (simulirana adresa), risk score **100** | Ovo je jedina hardkodovana "simulaciona" OFAC adresa u `blacklist_check` plugin-u (za razliku od stvarne Garantex adrese dodate za Bitcoin demo — vidi `BITCOIN-UVOZ.md`). |
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

### 7. Napredna pretraga grafa (Neo4j)

**Otvori:** stranica **Slučajevi** → kartica demo slučaja → dugme za naprednu pretragu
grafa → unesi `0xVictimWallet`, 2 koraka.

**Vidiš:** na 1 koraku — `0xDrainerContract`, `0xDrainerWallet`, `0xPeelSeed`,
`0xSweepContract` (VictimWallet je istovremeno "žrtva" i u peel-chain **i** u
token-approval scenariju — namerno deljena adresa da poveže dve priče). Na 2 koraka —
`0xMuleWallet1`, `0xPeelRelay1`, `0xVictimWallet2`.

---

## Poenta ovog demo slučaja

Svih 9 fajlova zajedno pokrivaju **svaku** analizu u sistemu barem jednim jasnim,
namerno-dizajniranim primerom — uključujući i granične slučajeve koji **ne smeju** da se
lažno prijave (bounce transakcija u DEX Swap-u, oprezan korisnik u Token Approval-u). Isti
princip kao Bitcoin demo (`BITCOIN-UVOZ.md`): svaka tvrdnja se može stvarno pokrenuti i
proveriti, ne samo pročitati.
