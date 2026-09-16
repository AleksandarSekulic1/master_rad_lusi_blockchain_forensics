# Uvoz Bitcoin (UTXO) transakcija

Pored Ethereum-a (account-based model), Lusi v1.0 sada ume da povuče i analizira **Bitcoin**
transakcije (UTXO model) — kompletan plan i sve arhitektonske odluke su zapisane u
[`BITCOIN-UTXO-PLAN.md`](BITCOIN-UTXO-PLAN.md); ovaj dokument je korisničko uputstvo posle
što je implementacija završena i testirana.

## Kako ovo radi (u kratkim crtama)

Bitcoin nema "pošiljaoca" i "primaoca" u istom smislu kao Ethereum — svaka transakcija troši
jedan ili više prethodnih izlaza (UTXO) kao ulaze i pravi jedan ili više novih izlaza. Da se
ceo postojeći pipeline (graph building, svih 6 plugin-ova, taint analiza, custody log, Neo4j
pretraga, dashboard search, svi izvozi) ne bi menjao, svaka Bitcoin transakcija se pri uvozu
**normalizuje** u isti oblik koji sistem već koristi za Ethereum:

> **Prva ulazna adresa** (čiji je UTXO potrošen) = "pošiljalac" — standardna forenzička
> pretpostavka poznata kao *common input ownership heuristic* (Meiklejohn i dr., 2013).
> **Svaka izlazna adresa** = po jedan red primaoca (change izlazi nazad ka pošiljaocu se NE
> filtriraju — ostaju u grafu kao normalna grana, jer to i jesu). OP_RETURN izlazi (bez
> adrese) i nepotvrđene (mempool) transakcije se preskaču.

Posledica: **ništa postojeće nije menjano**. Ceo downstream gleda isti CSV/DataFrame oblik
(`sender_address, recipient_address, amount, timestamp, metadata`) bez obzira da li je
poreklo Ethereum ili Bitcoin.

Izvor podataka je **Blockstream Esplora API** (`https://blockstream.info/api`) — javan,
besplatan, bez API ključa (za razliku od Etherscan-a).

## 1. Testiranje — povlačenje prave adrese sa mainnet-a

1. Uloguj se u aplikaciju.
2. Otvori/napravi slučaj na strani **Slučajevi**.
3. Na **Kontrolnoj tabli**, u sekciji "Sa blockchain-a", izaberi mrežu **Bitcoin mainnet**.
4. Unesi Bitcoin adresu, npr. (OFAC-sankcionisana adresa Garantex Europe OU, designacija
   2022-04-05 — vidi odeljak 3 ispod):
   ```
   3Lpoy53K625zVeE47ZasiG5jGkAxJ27kh1
   ```
5. Klikni **"Povuci transakcije"**. Sistem povlači potvrđenu istoriju te adrese preko
   Blockstream API-ja, normalizuje je po pravilu iznad, pravi CSV evidence zapis, računa
   SHA-256 i pokreće graph building + analitiku — isto kao posle ručnog CSV uploada.

Polje za adresu validira **Base58** (počinje sa `1` ili `3`, 25-34 znaka) ili **Bech32**
(počinje sa `bc1`, 39-59 znakova) format; radio dugmići "cela istorija"/"samo transakcija"
(tx-hash mod) se ne prikazuju za Bitcoin — prva verzija podržava samo "adresa → istorija".

## 2. Testiranje — ručni CSV (kontrolisan scenario za demonstraciju)

Za scenario koji sigurno "upali" sve analize (peel chains, chain hopping, wallet clustering,
taint, risk scoring, anomaly detection), učitaj ručno napravljen CSV kroz postojeći
"Iz CSV fajla" tok — isti format kao gore, sa `currency=BTC` kolonom. Primer manjeg
scenarija (peel-chain lanac + jedna clustering grupa):

```
sender_address,recipient_address,amount,timestamp,currency
bc1qvictim0000000000000000000000000000000,3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,500.0,2026-02-01T10:00:00Z,BTC
3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,3PeelRelay1xxxxxxxxxxxxxxxxxxxxxxx,350.0,2026-02-01T10:15:00Z,BTC
3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,1MuleWalletxxxxxxxxxxxxxxxxxxxxxxx,150.0,2026-02-01T10:20:00Z,BTC
```

Napomena: `amount` mora biti dovoljno veliki (≥100) da bi ga peel-chain plugin uopšte
razmotrio kao "seed" iznos (`min_seed_amount` je generički prag, ne specifičan za valutu —
ista stvar bi važila i za sitne ETH iznose).

Adrese ne moraju biti stvarne za ovaj tok (format se ne validira pri CSV uploadu, samo pri
unosu u polje za live-fetch) — bitno je da CSV oblik odgovara pravilu iz odeljka "Kako ovo
radi".

## 3. Blacklist provera — stvarna OFAC adresa

`blacklist_check` plugin (`backend/app/analytics/plugins/blacklist_check.py`) ima sopstvenu,
malu, hardkodovanu listu adresa (ne čita `known_entities.json`, koji služi za drugu stvar —
enrichment/lookup na Dashboard-u i Path finding-u). Dodata je jedna **stvarna** adresa u obe
liste:

- **`3Lpoy53K625zVeE47ZasiG5jGkAxJ27kh1`** — navedena kao "Digital Currency Address - XBT"
  identifikator u OFAC SDN designaciji **Garantex Europe OÜ** od **2022-04-05**
  (izvor: `ofac.treasury.gov/recent-actions/20220405`).

Ova adresa ima samo 4 potvrđene transakcije na mainnet-u (proverено preko Blockstream API-ja)
— dovoljno malo da bude praktična za demo, dovoljno da blacklist provera vrati **stvaran**
pogodak umesto praznog rezultata. Testirano: povlačenjem njene istorije preko odeljka 1 i
pokretanjem analize, `blacklist_check.matched_count` prelazi sa 0 na 1, sa
`sources: ["OFAC"]`.

## 4. Šta se ne primenjuje na Bitcoin (i zašto to nije greška)

| Analiza | Ponašanje |
|---|---|
| Taint, Path finding, Peel chains, Chain hopping, Wallet clustering, Anomaly detection, Risk scoring, Blacklist check | rade identično kao za Ethereum — generičke su, gledaju samo graf/brojeve |
| Behavioral / Timezone analiza | radi identično — samo vremenski pečati |
| Dashboard pretraga adrese, Neo4j napredna pretraga grafa | rade identično |
| Svi izvozi (PDF/CSV/GraphML/GEXF/PNG/SVG) | rade identično |
| **DEX Swap analiza** | vraća "0 detektovano" — DEX-ovi postoje samo na lancima sa pametnim ugovorima; ovo je ispravno ponašanje, ne greška |
| **Token Approval analiza** | vraća prazno — `approve()`/`permit()` su ERC-20/EIP-2612 koncepti kojih nema na Bitcoin-u; isto, ispravno ponašanje |
| ENS ime, tip "contract vs wallet" (enrichment) | vraćaju "nepoznato" za Bitcoin adrese — već dizajnirano da to gracioznо radi |

## 5. Šta se dešava u pozadini (za tehnički deo rada)

- Backend: `backend/app/features/bitcoin_ingestion/` — samostalan VSA slice, potpuno odvojen
  od `backend/app/features/onchain/` (Ethereum), koji ostaje netaknut:
  - `service.py` — `fetch_address_transactions()` poziva Blockstream Esplora
    (`GET /address/{address}/txs`, pa `GET /address/{address}/txs/chain/{last_seen_txid}`
    za paginaciju), `transaction_to_rows()` normalizuje svaku transakciju po common input
    ownership heuristici, satošije deli sa 100.000.000 za BTC.
  - `models.py` — `FetchBitcoinTransactionsRequest(address, case_id)`.
  - `router.py` — `POST /api/v1/bitcoin/fetch`, isti obrazac kao `/onchain/fetch`
    (`require_open_case`, `store_case_evidence`, `append_evidence` sa `currency='BTC'`,
    `write_audit_log` sa akcijom `bitcoin_fetch_address`), plus regex validacija
    Base58/Bech32 formata i `disclaimer` polje u odgovoru (heuristika, ne dokazana činjenica).
- Testovi: `backend/tests/test_bitcoin_ingestion.py` — normalizacija (2 ulaza/2 izlaza),
  coinbase transakcija (nema `prevout`), OP_RETURN izlaz, nepotvrđena transakcija,
  paginacija preko `txs/chain/{last_seen_txid}`, mrežna greška — sve sa mock-ovanim HTTP
  pozivom, bez zavisnosti od žive Blockstream instance.
- Frontend: `OnchainNetwork` tip dobija `'bitcoin_mainnet'`; `ApiService.fetchBitcoinTransactions()`
  poziva novu rutu; `dashboard.component.ts#fetchOnchainTransactions()` grana se na mrežu —
  za Bitcoin validira Base58/Bech32 i poziva novi endpoint, inače ponašanje ostaje
  identično kao pre.

## 6. Poznata ograničenja (namerno, za prvu verziju)

- Nema podrške za pretragu po hešu pojedinačne Bitcoin transakcije — samo "adresa → cela
  istorija" (isto obrazloženje kao kod Ethereum tx-hash moda: pojedinačna transakcija ima
  malu forenzičku vrednost bez konteksta pošiljaoca).
- Nepotvrđene (mempool) transakcije se ne uvoze — forenzički alat treba da radi nad
  ustaljenim činjenicama.
- "Prva ulazna adresa = pošiljalac" je heuristika, ne apsolutna istina (isto kao timezone
  heuristika) — zato `disclaimer` polje u odgovoru API-ja.
- DEX Swap i Token Approval stranice trenutno samo tiho vraćaju prazan rezultat za
  Bitcoin-only slučaj, umesto da eksplicitno kažu "ne primenjuje se na ovaj lanac" — manje
  UX poboljšanje za kasnije, nije blokirajuće.
