# Plan implementacije: podrška za Bitcoin (UTXO model)

> **Status: implementirano i testirano (Faze 0-3 završene).** Korisničko uputstvo je u
> [`BITCOIN-UVOZ.md`](BITCOIN-UVOZ.md). Ovaj dokument je ostavljen kao istorijski zapis
> plana i arhitektonskih odluka.

Ovo je **plan za implementaciju, ništa još nije napisano u kodu**. Dokument je namerno
samostalan (piše se pred otvaranje nove sesije/chat-a) — sadrži sav kontekst, sve odluke i
korak-po-korak plan sa očekivanim rezultatima, tako da se implementacija može nastaviti bez
da se ponovo prolazi kroz celu diskusiju.

## Zašto ovo radimo

- Predlog teme eksplicitno pominje: *"sistem će moći da analizira UTXO model plaćanja
  (karakterističan za Bitcoin), kao i Account-based model"*.
- Trenutno postoji **samo** Account-based (Ethereum) podrška — potvrđeno pretragom celog
  backend-a, nema nijedne UTXO/Bitcoin reference.
- Mentor je rekao da obim modula nije fiksan/hitan ("dogovorićemo se naknadno"), ali ovo je
  jedina preostala stavka sa originalne liste iz predloga teme (sve ostalo je već urađeno
  — graf baza, izvozi, dashboard pretraga itd. — videti istoriju razgovora / commit poruke).

## Ključna arhitektonska odluka

**NE modelovati UTXO onako kako stvarno jeste** (posebni čvorovi za transakcione izlaze,
ulazi koji troše izlaze...) — to bi zahtevalo izmene u gotovo svakoj analizi (graph
building, svih 6 plugin-ova, taint analizu, custody log) i direktno bi kršilo dosadašnji
princip "male, sigurne, izolovane izmene".

**Umesto toga: normalizacija pri uvozu.** Svaka Bitcoin transakcija se svede na isti oblik
koji ceo pipeline već očekuje — `sender_address, recipient_address, amount, timestamp,
metadata` (identično onome što `onchain_ingestion.py` danas proizvodi za Ethereum) — po
pravilu:

> **Prva ulazna adresa** (adresa čiji je UTXO potrošen) = "pošiljalac" — standardna
> forenzička pretpostavka poznata kao *common input ownership heuristic* (Meiklejohn i dr.,
> 2013): ako neko u istoj transakciji troši više UTXO-a, po pravilu svi pripadaju istom
> novčaniku, pa je svejedno koju od ulaznih adresa uzmemo kao predstavnika. **Svaka izlazna
> adresa** = po jedan red primaoca (osim OP_RETURN izlaza bez adrese — preskaču se; i
> "change" izlazi nazad ka pošiljaocu se NE filtriraju, ostaju u grafu kao normalna grana —
> to je ispravno, samo treba biti svestan da će se često pojavljivati).

Posledica ove odluke: **ništa postojeće se ne dira**. Ceo downstream (NetworkX graf, svih 6
plugin-ova, taint analiza, custody log, Neo4j pretraga, dashboard search, svi izvozi) radi
bez ijedne izmene, jer svi gledaju isti CSV/DataFrame oblik.

## Izvor podataka: Blockstream Esplora API

- **Base URL:** `https://blockstream.info/api`
- **Besplatan, bez registracije, bez API ključa** (za razliku od Etherscan-a koji već
  zahteva ključ) — najmanje trenja od svih razmatranih opcija.
- **Ključna prednost:** odgovor već sadrži razrešene adrese ulaza
  (`vin[].prevout.scriptpubkey_address`), nema potrebe za dodatnim pozivima po prethodnoj
  transakciji (za razliku od nekih starijih API-ja).
- **mempool.space** (`https://mempool.space/api`) je potpuno kompatibilna alternativa
  (isti Esplora API oblik) — koristiti kao rezervu/fallback ako Blockstream ikad uvede
  ograničenje; isti kod, samo druga `BASE_URL` konstanta.

**Endpoint-i koji trebaju:**
- `GET /address/{address}/txs` — do 25 najnovijih (potvrđenih + iz mempool-a)
- `GET /address/{address}/txs/chain/{last_seen_txid}` — paginacija za stariju istoriju
  (isto kao Etherscan-ova `txlist` puna istorija)

**Oblik odgovora (bitna polja):**
```json
{
  "txid": "...",
  "status": { "confirmed": true, "block_time": 1700000000 },
  "vin": [{ "prevout": { "scriptpubkey_address": "bc1q...", "value": 50000 } }],
  "vout": [{ "scriptpubkey_address": "bc1q...", "value": 30000 }]
}
```
- `value` je u **satošijima** — deliti sa `100_000_000` za BTC (isti obrazac kao
  `_wei_to_eth` u `onchain_ingestion.py`).
- `status.block_time` je unix timestamp (sekunde) — isti obrazac kao `_to_iso_timestamp`.
- **Nepotvrđene (mempool) transakcije se preskaču u prvoj verziji** — `status.confirmed ==
  false` nema `block_time`; forenzički alat treba da radi nad ustaljenim činjenicama, ne
  nad transakcijama koje se još mogu promeniti/izbaciti iz mempool-a.
- `currency` polje u DataFrame-u: `'BTC'` (isto mesto gde Ethereum uvoz upisuje `'ETH'`).

## VSA slice — nov, samostalan modul

Nova, potpuno odvojena funkcionalnost — u duhu "dodaj nov modul, ne diraj postojeći":

```
app/features/bitcoin_ingestion/
├── __init__.py
├── models.py    — FetchBitcoinTransactionsRequest (address, case_id)
├── service.py   — poziv Blockstream API-ja + normalizacija u standardni DataFrame oblik
└── router.py    — POST /bitcoin/fetch (isti oblik odgovora kao POST /onchain/fetch:
                   file_name, sha256, audit_log, rows_total, preview, case, evidence,
                   resolved_query)
```

Zašto poseban endpoint/slice a ne grana unutar postojećeg `app/features/onchain/`: da bi
priča "dodaj nov modul za novi lanac bez diranja postojećeg" bila doslovno tačna i
pokazljiva — ceo Bitcoin uvoz živi u jednom folderu, `app/features/onchain/` (Ethereum)
ostaje potpuno netaknut.

**Ne treba nova tabela/model za "Case"** — koristi se identičan `store_case_evidence` /
`append_evidence` iz `app/services/case_management.py` koji Ethereum uvoz već koristi,
bez izmena tamo.

## Kompatibilnost postojećih analiza sa Bitcoin podacima

| Analiza | Radi sa Bitcoin CSV-om? |
|---|---|
| Taint analiza, Path finding, Peel chains, Chain hopping, Wallet clustering, Anomaly detection, Risk scoring | ✅ potpuno identično (generičke, gledaju samo graf/brojeve) |
| Behavioral / Timezone analiza | ✅ identično (radi samo sa vremenskim pečatima) |
| Dashboard pretraga adrese, Neo4j napredna pretraga (graph-search) | ✅ identično |
| Svi izvozi (PDF/CSV/GraphML/GEXF/PNG/SVG) | ✅ identično |
| Blacklist provera | ⚠️ radi mehanički, ali `known_entities.json` trenutno verovatno ima samo Ethereum adrese — treba dodati bar par pravih Bitcoin adresa sa OFAC liste da se test smisleno pokaže |
| **DEX Swap analiza** | ❌ ne primenjuje se — DEX-ovi postoje samo na lancima sa pametnim ugovorima; vratiće "0 detektovano", što je ispravno, ne greška |
| **Token Approval analiza** | ❌ ne primenjuje se — `approve()`/`permit()` su ERC-20/EIP-2612 koncepti kojih nema na Bitcoin-u; isto, vraća prazno, ne greška |
| ENS ime, tip "contract vs wallet" (enrichment) | ⚠️ vraćaju "nepoznato" za Bitcoin adrese — već su dizajnirani da to grациozno rade |

## Frontend izmene (male, u postojećem toku)

Sve u postojećem "Sa blockchain-a" panelu na Dashboard-u — bez nove stranice:

1. `OnchainNetwork` tip (`models/blockchain-forensics.models.ts`) dobija treću vrednost:
   `'bitcoin_mainnet'`.
2. `dashboard.component.html` — jedna nova `<option>` u postojećem `<select>` za mrežu.
3. `dashboard.component.ts` — `fetchOnchainTransactions()` grana na mrežu: za
   `bitcoin_mainnet` poziva NOVU `api.fetchBitcoinTransactions(...)` (novi endpoint), inače
   ostaje isti poziv kao danas.
4. Validacija adrese u polju — kad je izabran Bitcoin, regex se menja (Base58: počinje sa
   `1`/`3`, 25-34 znaka; Bech32: počinje sa `bc1`, 39-59 znakova) umesto `0x...` regex-a.
5. Radio dugmići "cela istorija" / "samo ova transakcija" (tx-hash mod) se **sakrivaju**
   kad je izabran Bitcoin — prva verzija podržava samo "adresa → istorija", ne pojedinačnu
   transakciju po hešu (namerno manji obim).
6. `ApiService` — nova metoda `fetchBitcoinTransactions(address, caseId)`.

## Test slučaj "BITCOIN" — kako se testira

Fazno, da se svaki deo potvrdi pre prelaska na sledeći (isti stil kao Neo4j pilot — svaka
tvrdnja mora biti stvarno pokrenuta, ne samo napisana):

### Faza 0 — dokaz da downstream RADI, pre nego što se piše ijedan red uvoznog koda

Cilj: potvrditi tezu "isti CSV oblik = sve radi bez izmena" **pre** nego što se gradi
Blockstream integracija — najbrži i najjeftiniji test.

1. Napraviti nov slučaj **"BITCOIN"** kroz postojeći UI (stranica Slučajevi).
2. Ručno napraviti mali CSV sa Bitcoin-olikim adresama (Base58/Bech32), oblikovan kao
   peel-chain demo (isti duh kao postojeći Ethereum "Demo: Sumnjiva laundering šema"),
   npr.:
   ```
   sender_address,recipient_address,amount,timestamp
   bc1qvictim0000000000000000000000000000000,3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,0.5,2026-01-01T10:00:00Z
   3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,3PeelRelay1xxxxxxxxxxxxxxxxxxxxxxx,0.35,2026-01-01T10:15:00Z
   3PeelSeedxxxxxxxxxxxxxxxxxxxxxxxxxx,1MuleWalletxxxxxxxxxxxxxxxxxxxxxxx,0.15,2026-01-01T10:20:00Z
   3PeelRelay1xxxxxxxxxxxxxxxxxxxxxxx,bc1qsanctioned0000000000000000000000000,0.30,2026-01-01T10:40:00Z
   ```
   (adrese su placeholder-i ispravnog OBLIKA — pravi format bitan je samo za regex
   validaciju na frontend-u u Fazi 3; za sam CSV upload nije bitno da su to stvarne,
   postojeće adrese)
3. Učitati CSV kroz **postojeći** "Iz CSV fajla" tok (nema izmena koda potrebno za ovaj
   korak).
4. Očekivan rezultat: graf se gradi, prikazuje na stranici Graf, dashboard brojke
   (transakcije/promet/klasteri) se popune — identično kao za bilo koji drugi slučaj.
5. Pokrenuti redom: Taint analizu (seed = victim adresa), Path finding (victim → sankcionisana
   adresa), Wallet clustering, Peel chains, Chain hopping, Neo4j "napredna pretraga grafa",
   Dashboard "brza pretraga adrese". Očekivan rezultat: sve rade i daju smislene nalaze
   (npr. peel chain prepoznaje offset od victim adrese; taint analiza pokazuje % koji
   stiže do sankcionisane adrese).
6. Pokrenuti DEX Swap i Token Approval analize. Očekivan rezultat: obe vraćaju prazan/nulti
   rezultat bez greške (potvrđuje da se "ne primenjuje se" ponaša ispravno, ne rušilački).

**Ako Faza 0 ne prođe kako se očekuje** — stani i istraži pre nastavka; to bi značilo da
neka analiza ipak pravi pretpostavku specifičnu za Ethereum (npr. regex na `0x` prefiks) o
kojoj nismo znali.

### Faza 1 — `app/features/bitcoin_ingestion/` (backend)

1. `service.py`: `fetch_address_transactions(address)` — poziva Blockstream, normalizuje po
   pravilu iz sekcije "Ključna arhitektonska odluka", vraća `pd.DataFrame` sa kolonama
   `['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata']` (identično
   Ethereum obliku; `metadata` = `txid`).
2. `router.py`: `POST /bitcoin/fetch` — isti obrazac kao `app/features/onchain/router.py`
   (`require_open_case`, `store_case_evidence`, `append_evidence`, `write_audit_log`,
   `currency='BTC'`).
3. Registrovati router u `app/api/router.py`.
4. **Test (pytest, sa mock-ovanim HTTP pozivom ka Blockstream-u, ne živim API-jem)**:
   - normalizacija: transakcija sa 2 ulaza / 2 izlaza daje tačno očekivane redove
   - coinbase transakcija (nema `prevout` na ulazu) se ne ruši, preskače se ili se
     posebno obeležava
   - OP_RETURN izlaz (bez adrese) se preskače
   - nepotvrđena transakcija (`confirmed: false`) se preskače
5. **Ručna provera uživo protiv PRAVE Blockstream instance**, na poznatoj javnoj adresi
   (npr. neka istorijski poznata adresa vezana za dobro dokumentovan incident, ili prosto
   bilo koja aktivna adresa) — potvrditi da se broj/oblik redova poklapa sa onim što se
   vidi na `blockstream.info` u pregledaču. Isti nivo provere kao za Neo4j pilot (stvarno
   pokrenuto, ne samo napisano).

### Faza 2 — frontend (dashboard izmene iz sekcije iznad)

1. Tip, dropdown opcija, `ApiService.fetchBitcoinTransactions`, grananje u
   `fetchOnchainTransactions()`, promena regex validacije i sakrivanje tx-hash moda.
2. `npm run build` mora proći bez grešaka.
3. Ručna provera: izabrati "Bitcoin mainnet", uneti poznatu javnu adresu, kliknuti "Povuci
   transakcije" — očekivan rezultat: evidencija se pojavljuje u Depou dokaza slučaja
   "BITCOIN", graf se učita, isti tok kao za Ethereum.

### Faza 3 — spajanje sa demo slučajem i finalna provera

1. U slučaj "BITCOIN" (iz Faze 0) dodati i evidenciju povučenu uživo (Faza 2) — slučaj sad
   ima i ručno-napravljen CSV i pravu on-chain evidenciju, kombinovano.
2. Ponoviti čitav test iz Faze 0, korak 5-6, nad kombinovanom evidencijom.
3. Dodati bar jednu Bitcoin adresu (stvarnu, sa OFAC liste ili slično) u
   `known_entities.json` da se blacklist provera smisleno pokaže i za Bitcoin.
4. Ažurirati ovaj dokument (ili napraviti `BITCOIN-UVOZ.md` po uzoru na
   `BLOCKCHAIN-UVOZ.md`) sa finalnim, korisničkim uputstvom kad sve prođe.

## Rizici / napomene za dalje

- `known_entities.json` bez pravih Bitcoin adresa čini blacklist proveru "tihom" za
  Bitcoin (ništa ne nađe, ali ne zato što je pokvarena, nego zato što lista nema šta da
  pronađe) — dokumentovati ovo jasno da se ne pomeša sa greškom.
- "Prva ulazna adresa = pošiljalac" je heuristika, ne apsolutna istina — isto kao i
  timezone heuristika, treba disclaimer u odgovoru API-ja (`resolved_query`/napomena polje)
  da je reč o pretpostavci, ne dokazanoj činjenici.
- DEX Swap i Token Approval stranice bi trebalo da na UI-ju kažu (kad je slučaj Bitcoin-only)
  da se ne primenjuju, umesto da samo tiho pokažu prazno — manje UX poboljšanje za kasnije,
  nije blokirajuće.

## Sledeći korak

Kad se otvori nova sesija: krenuti od **Faze 0** (najbrža, ne zahteva pisanje koda,
odmah potvrđuje ili obara celu premisu plana pre nego što se uloži vreme u Blockstream
integraciju).
