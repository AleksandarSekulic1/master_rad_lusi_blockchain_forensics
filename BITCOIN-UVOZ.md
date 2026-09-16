# Uvoz Bitcoin (UTXO) transakcija

## Kako je urađen uvoz (ukratko)

Bitcoin nema "pošiljaoca" i "primaoca" kao Ethereum — svaka transakcija troši jedan ili
više prethodnih izlaza (UTXO) kao ulaze i pravi jedan ili više novih izlaza. Da se ceo
postojeći sistem ne bi menjao, svaka Bitcoin transakcija se pri uvozu **normalizuje** u isti
oblik koji sistem već koristi za Ethereum (`sender_address, recipient_address, amount,
timestamp`):

> **Prva ulazna adresa** = "pošiljalac" (*common input ownership heuristic* — standardna
> forenzička pretpostavka: ko god troši više ulaza u istoj transakciji, po pravilu je isti
> novčanik). **Svaka izlazna adresa** = po jedan red primaoca.

Izvor podataka: **Blockstream API** (`blockstream.info/api`), besplatan, bez ključa. Uvoz
živi u posebnom folderu `backend/app/features/bitcoin_ingestion/`, Ethereum deo koda nije
dirat ni na jednom mestu.

Test slučaj: **"BITCOIN"** — sadrži ručno napravljen demo scenario (dve peel-chain "šeme
pranja novca") **i** pravu, uživo povučenu istoriju jedne stvarno sankcionisane adrese.

---

## Testiranje korak po korak — šta otvoriti, šta vidiš, i zašto

### 1. Graf

**Otvori:** slučaj **"BITCOIN"** → stranica **Graf**.

**Vidiš:** 46 čvorova, 47 grana.

**Zašto:** to je zbir ručnog demo CSV-a (26 čvorova — dve odvojene "žrtve" čiji se novac
pere kroz peel-chain, sa jednim zajedničkim reiskorišćenim mule-wallet-om) i prave on-chain
istorije jedne adrese (21 transakcija), učitanih kao dve odvojene evidencije u isti slučaj.
Graf ih prikazuje spojene, jer downstream ne pravi razliku između "ručno uneto" i "povučeno
sa lanca" — obe evidencije su isti CSV oblik.

Klikni na pojedine čvorove da vidiš detalje:

| Klikni na čvor | Vidiš u panelu | Zašto |
|---|---|---|
| `3PeelSeed1xxxxxxxxxxxxxxxxxxxxxxxx` | **Peel uloga: seed**, povišen risk score | Prima veliki iznos (500) i odmah ga u istom "dahu" deli na dva izlaza — jedan veliki nastavak, jedan manji "peel" — klasičan peel-chain obrazac. Isti `peel_chains` plugin kao za Ethereum, samo čita normalizovane BTC redove. |
| `3BridgeSwapHopxxxxxxxxxxxxxxxxxxxxx` | **Skok lanca: bridge** | `chain_hopping` plugin prepoznaje ključnu reč "bridge"/"hop" u imenu čvora — isti mehanizam kojim bi prepoznao npr. "Tornado.Cash" na Ethereum-u. |
| `1CoConspiratorAxxxxxxxxxxxxxxxxxxxx` | **Klaster: 2 člana** | `wallet_clustering` plugin je video da ova adresa i `1CoConspiratorBxxx...` šalju **identičan iznos, istoj adresi, u istom trenutku** — forenzički obrazac zajedničkog vlasnika (isti duh kao common input ownership, samo primenjen na dve odvojene uplate umesto na ulaze jedne transakcije). |
| `3lpoy53k625zvee47zasig5jgkaxj27kh1` | **Crne liste: OFAC · Garantex Europe OÜ (designated 2022-04-05)** | Ovo **nije** simulirana demo adresa — ovo je stvarna Bitcoin adresa navedena u pravoj OFAC SDN sankciji od 5.4.2022. godine (izvor: `ofac.treasury.gov/recent-actions/20220405`). Dokaz da blacklist provera radi identično za Bitcoin kao za Ethereum, kad podatak postoji u listi. |

### 2. Taint analiza

**Otvori:** stranica **Taint analiza** → slučaj "BITCOIN" → seed adresa:
```
bc1qvictim1000000000000000000000000000000
```

**Vidiš:** 100% zaraženosti na `3PeelSeed1...`, procenat opada niz peel-granu (deo ide na
"mule" adresu kao gubitak), a ostatak stiže do sankcionisane adrese
`bc1qsanctioned10000000000000000000000000`.

**Zašto:** haircut model prati **proporciju** zaraženog iznosa kroz svaki hop hronološkim
redom — potpuno mu je svejedno da li `amount` kolona potiče iz ETH ili iz satošija
pretvorenih u BTC pri uvozu. Isti kod, ista matematika.

### 3. Path finding

**Otvori:** stranica **Path finding** → From: `bc1qvictim1000000000000000000000000000000`,
To: `bc1qsanctioned10000000000000000000000000`.

**Vidiš:** putanja `victim1 → PeelSeed1 → PeelRelay1 → PeelRelay2 → sanctioned1` (4 hop-a).

**Zašto:** BFS pretraga radi nad grafom kao apstraktnom strukturom (čvorovi/grane) — nema
pojma "ovo je Bitcoin adresa", samo prati grane.

### 4. Behavioral analiza

**Otvori:** stranica **Behavioral analiza** → izaberi bilo koju BTC adresu iz slučaja
(npr. Garantex adresu `3lpoy53k625zvee47zasig5jgkaxj27kh1`, koja ima 4 stvarne transakcije).

**Vidiš:** raspodelu po satu/danu u nedelji, na osnovu njenih stvarnih timestamp-ova.

**Zašto:** ova analiza čita isključivo `timestamp` kolonu — potpuno joj je svejedno kog je
lanca adresa.

### 5. DEX Swap analiza

**Otvori:** stranica **DEX Swap analiza** → pokreni nad slučajem "BITCOIN".

**Vidiš:** "0 detektovano", bez greške.

**Zašto:** DEX (decentralizovane berze) postoje samo na lancima sa pametnim ugovorima.
Bitcoin nema taj koncept — 0 je **ispravan** odgovor, ne kvar.

### 6. Token Approval analiza

**Otvori:** stranica **Token Approval analiza** → pokreni nad slučajem "BITCOIN".

**Vidiš:** "0 korelacija", bez greške.

**Zašto:** `approve()`/`permit()` su ERC-20/EIP-2612 koncepti (odobravanje trošenja tokena).
Bitcoin nema tokene ni allowance mehanizam — isto, 0 je ispravno.

### 7. Napredna pretraga grafa (Neo4j)

**Otvori:** stranica **Slučajevi** → kartica "BITCOIN" → dugme za naprednu pretragu grafa
→ unesi `bc1qvictim1000000000000000000000000000000`, 2 koraka.

**Vidiš:** susede — `3PeelSeed1...` na 1 koraku, `1MuleWallet1...` i `3PeelRelay1...` na
2 koraka.

**Zašto:** Neo4j indeksira graf identično bez obzira na poreklo — upit je čist Cypher nad
`sender_address`/`recipient_address` poljima, iste kolone za svaki lanac.

### 8. Uživo povlačenje sa Bitcoin mreže (Dashboard)

**Otvori:** **Kontrolna tabla** → sekcija "Sa blockchain-a" → mreža **Bitcoin mainnet** →
adresa:
```
3Lpoy53K625zVeE47ZasiG5jGkAxJ27kh1
```
→ **"Povuci transakcije"**.

**Vidiš:** 4 nove transakcije povučene i dodate u Depo dokaza slučaja, graf se osveži.

**Zašto:** server poziva Blockstream API za tu adresu, dobija njene potvrđene transakcije,
normalizuje ih po pravilu sa vrha dokumenta i snima kao BTC evidenciju — isti tok kao ručni
CSV upload, samo automatizovan.

---

## Poznata ograničenja (namerno, za prvu verziju)

- Nema pretrage po hešu pojedinačne Bitcoin transakcije — samo "adresa → cela istorija".
- Nepotvrđene (mempool) transakcije se ne uvoze.
- "Prva ulazna adresa = pošiljalac" je heuristika, ne dokazana činjenica — otuda
  `disclaimer` polje u odgovoru API-ja kad se povlači uživo.
- DEX Swap i Token Approval stranice trenutno tiho vraćaju prazan rezultat za Bitcoin,
  umesto da eksplicitno kažu "ne primenjuje se na ovaj lanac" — manje UX poboljšanje za
  kasnije.
