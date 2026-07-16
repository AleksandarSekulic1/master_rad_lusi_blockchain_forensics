# Uvoz transakcija direktno sa blockchain-a

Pored ručnog učitavanja CSV fajlova, Lusi v1.0 sada ume da povuče transakcije direktno sa blockchain-a preko besplatnog Etherscan API-ja, na dva načina:

- **Adresa novčanika** → povlači se **kompletna istorija transakcija** te adrese.
- **Heš pojedinačne transakcije** (npr. kopiran sa Etherscan-a, iz izveštaja o incidentu) → sistem prepoznaje format i nudi izbor: povući samo tu jednu transakciju, ili pronaći pošiljaoca i povući **celu njegovu istoriju** (preporučeno za istragu).

Ovaj dokument objašnjava kako da to podesiš i isprobaš.

## Kako ovo radi (u kratkim crtama)

- Ne postoji "login na Ethereum" — blockchain je javan, nema naloga/lozinke. Ti samo uneseš adresu ili heš transakcije, a naš server preko Etherscan API-ja povuče odgovarajuće podatke.
- **Čitanje istorije transakcija je uvek besplatno**, bez obzira na mrežu — gas (naknada) se plaća samo kada se *šalje* transakcija, ne kada se ona *čita*. Znači ne trošimo ni pravi ni test-novac, samo čitamo javne podatke.
- Povučene transakcije se transformišu u isti format kao ručno učitan CSV, prolaze kroz potpuno isti pipeline (graph building, risk scoring, clustering, peel chains, chain hopping, anomaly detection...) i čuvaju se u Depou dokaza sa SHA-256 heš otiskom i audit log zapisom — kao da je fajl ručno učitan, radi lanca dokaza.

## 1. Napravi besplatan Etherscan API key

1. Idi na [etherscan.io](https://etherscan.io) i napravi besplatan nalog.
2. Nakon prijave, idi na **My Profile → API Keys** i klikni **Add**.
3. Kopiraj generisani API key (jedan key radi za sve mreže — mainnet i sve testnet-ove, preko njihovog V2 API-ja).

Besplatan tier ima limit od otprilike 5 poziva u sekundi, što je više nego dovoljno za ovaj alat (jedan poziv po jednoj adresi).

## 2. Podesi API key u projektu

**Lokalno (bez Dockera):** u `backend/.env` (napravi ga na osnovu `backend/.env.example` ako ne postoji) dodaj:

```
ETHERSCAN_API_KEY=tvoj-api-key-ovde
```

**Preko Docker Compose-a:** napravi `.env` fajl u korenu projekta (pored `docker-compose.yml`) sa:

```
ETHERSCAN_API_KEY=tvoj-api-key-ovde
```

Docker Compose automatski čita taj fajl i prosleđuje ga u kontejner. Restartuj backend (`docker compose up --build backend`) da bi promena stupila na snagu.

Ako key nije podešen, sistem će vratiti jasnu grešku ("ETHERSCAN_API_KEY nije podešen na serveru") umesto da nešto tiho ne radi.

## 3. Testiranje — mainnet (prave, istorijske transakcije)

Najlakši način da testiraš je da uzmeš neku poznatu adresu sa dosta prometa i pratiš je kroz Kontrolnu tablu:

1. Uloguj se u aplikaciju.
2. (Opciono) Otvori stranicu **Slučajevi** i izaberi/napravi slučaj u koji želiš da se dokaz svrsta.
3. Na **Kontrolnoj tabli**, u sekciji "Prijem dokaza", pronađi polje **"ili povuci sa blockchain-a"**.
4. Unesi adresu (primer — Vitalik Buterin-ova javna adresa, ima veliku i raznovrsnu istoriju):
   ```
   0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
   ```
5. Izaberi mrežu **Ethereum mainnet** i klikni **"Povuci transakcije"**.
6. Sistem povlači istoriju, pravi CSV evidence zapis, računa SHA-256 heš i automatski pokreće graph building + analitiku — isto kao posle ručnog CSV uploada.

Za realan forenzički scenario, možeš potražiti javno poznate adrese povezane sa stvarnim hakovima/prevarama (npr. preko izveštaja o poznatim incidentima) i analizirati njihovu stvarnu istoriju transakcija istim putem.

## 4. Pretraga po hešu transakcije

Ako imaš samo heš pojedinačne transakcije (npr. iz nekog izveštaja o incidentu, ili kopiran sa Etherscan-a — dugačak niz od 66 karaktera koji počinje sa `0x`), možeš ga direktno nalepiti u isto polje umesto adrese:

1. Nalepi heš (primer, stvarna transakcija iz 2015. godine):
   ```
   0x9b629147b75dc0b275d478fa34d97c5d4a26926457540b15a5ce871df36c23fd
   ```
2. Sistem automatski prepoznaje da je u pitanju heš (a ne adresa) i ispod polja se pojavljuju dve opcije:
   - **"Celu istoriju pošiljaoca" (preporučeno)** — pronalazi ko je poslao tu transakciju i povlači kompletnu istoriju te adrese, čime dobijaš pravi materijal za istragu (ko je još taj pošiljalac kontaktirao, koliki mu je promet, itd.), a ne samo izolovan jedan red.
   - **"Samo ovu transakciju"** — povlači isključivo taj jedan red (2 čvora, 1 veza u grafu) — korisno ako te zanima samo brza provera te jedne transakcije.
3. Klikni **"Povuci transakcije"**.

## 5. Testiranje — Sepolia (testnet, ako želiš potpuno kontrolisan scenario)

Ako želiš da sam kreiraš kontrolisan set transakcija (npr. da simuliraš tok "ukradenog novca" kroz nekoliko adresa radi demonstracije), koristi Sepolia testnet:

1. Instaliraj MetaMask (ili sličan wallet) i prebaci ga na Sepolia test mrežu.
2. Uzmi besplatan test-ETH sa nekog Sepolia faucet-a (npr. `sepoliafaucet.com` ili Alchemy/Infura faucet — potraži trenutno aktivan).
3. Napravi par transakcija između svojih test-adresa (besplatno, test-ETH nema vrednost).
4. U aplikaciji izaberi mrežu **Sepolia (testnet)** i unesi jednu od svojih test-adresa.

Ove transakcije su podjednako "prave" (potvrđene na blockchain-u, sa pravim hešom) kao i mainnet transakcije — razlika je samo što je novac na testnet-u bezvredan.

## 6. Šta se dešava u pozadini (za tehnički deo rada)

- Backend: `backend/app/services/onchain_ingestion.py`:
  - `fetch_address_transactions()` — poziva Etherscan V2 API modul `account`/`txlist` (`/v2/api?chainid=...`), pretvara odgovor u isti tabelarni format koji koristi CSV ingestion (`sender_address`, `recipient_address`, `amount`, `timestamp`, `metadata`), preskače neuspele (revertovane) transakcije.
  - `fetch_transaction_by_hash()` — poziva Etherscan-ov `proxy`/`eth_getTransactionByHash` modul (direktan JSON-RPC passthrough); odgovor uključuje `blockTimestamp` direktno, pa je dovoljan jedan API poziv.
  - `fetch_single_transaction_frame()` / `fetch_expanded_sender_history()` — obrađuju heš u jedan od dva režima opisana gore.
- Ruta: `POST /api/v1/onchain/fetch` (`backend/app/api/routes/onchain.py`) — automatski prepoznaje da li je uneta adresa (42 karaktera) ili heš transakcije (66 karaktera) na osnovu regex-a, primenjuje izabrani `mode`, snima rezultat kao CSV u `data/raw/`, računa SHA-256, povezuje ga sa slučajem (isti mehanizam kao `/upload/csv`) i upisuje audit log zapis sa akcijom `onchain_fetch_<mreža>_<režim>`.
- Frontend: `ApiService.fetchOnchainTransactions()` poziva tu rutu; rezultat se obrađuje potpuno isto kao odgovor na CSV upload (isti `loadDerivedViews()` poziv), tako da graf i analitika rade bez ikakvih izmena.
