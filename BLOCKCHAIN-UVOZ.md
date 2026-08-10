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

## 6. Testiranje Taint analize

Taint analiza (stranica **Taint analiza** u navigaciji) prati kako se "prljava" sredstva proporcionalno šire kroz graf, počevši od jednog ili više ručno izabranih ("seed") čvorova, ili automatski od svake adrese koja je već na crnoj listi. Postoje dva načina da je testiraš — kontrolisan sintetički scenario (da proveriš tačne brojeve) i test na pravim, već uvezenim podacima.

### Zašto je ovo uopšte bitno u digitalnoj forenzici

Zamisli da neko ukrade novac i onda ga počne da prosleđuje kroz gomilu različitih novčanika, mešajući ga usput sa tuđim, čistim parama — baš da zamrsi trag ko je šta poslao kome.

**Taint %** je odgovor na jedno prosto pitanje: *"Od svega što se sad nalazi na ovoj adresi, koliki deo je zapravo taj ukradeni novac?"* — npr. "40% para na ovoj adresi potiče od te krađe, ostalih 60% je tuđ, čist novac koji se slučajno našao u istom loncu."

Zašto nam je to korisno u istrazi:

1. **Ne moramo ručno da proveravamo stotine adresa.** Kad imamo veliki graf, program nam odmah pokaže koje adrese su najviše "umešane" — tu prvo gledamo, ne gubimo vreme na sve ostale.
2. **Pomaže da nađemo gde novac izlazi napolje.** Lopova obično već znamo. Ono što nas zanima je gde se taj ukradeni novac na kraju pretvara nazad u pravi novac (npr. na nekoj berzi) — jer tačno tu ga vlasti mogu da zaustave/zamrznu. Taint analiza nam crta tu putanju do te tačke.
3. **Dajemo broj, ne samo "da ili ne".** Umesto da kažemo "ova adresa je umešana" (crno-belo), možemo da kažemo TAČNO koliko — "31% je prljavo". To je jači dokaz na sudu, jer se vidi razlika između nekoga ko je skoro sigurno umešan i nekoga ko je samo malo dotaknut velikim, uglavnom čistim tokom novca.
4. **Zašto ne kažemo prosto "ako je iole dodirnuo prljav novac, cela adresa je 100% kriva"?** Zato što bi to bilo nepravedno — zamisli da si ti potpuno nevin, samo si slučajno koristio isti servis (npr. isti mikser/berzu) gde je i lopov prao svoj novac. Ne bi bilo fer da te sad svi tretiraju kao lopova. Zato računamo pravi, realan udeo, a ne bacamo svakoga u isti koš.

### 6.1 Kontrolisan scenario (tačni brojevi, za proveru algoritma)

**Šta ovim testom dokazujemo:** ovo nije test da li aplikacija "radi" u smislu da nešto iscrta na ekranu — cilj je da se na skupu podataka sa unapred ručno izračunatim, poznatim odgovorom potvrdi da je **matematika proporcionalnog (haircut) modela zaprljanosti tačna**, i to na tri konkretne tvrdnje:

- **Zaprljanost se ispravno razblažuje kad se pomeša sa čistim sredstvima** — kad 1000 "prljavih" i 500 čistih sredstava uđu u isti novčanik (mikser), izlaz ne bi trebalo da bude ni 100% prljav (to bi bio pogrešan, "poison" model), ni 0% (to bi značilo da se zaprljanost uopšte ne prati) — nego tačno proporcionalan udeo, **1000/1500 = 66.67%**.
- **Zaprljanost dalje putuje istim procentom kroz sledeći prenos** — novac koji mikser prosledi dalje (`0xExitWallet`) treba da ponese *isti* procenat koji je mikser imao u trenutku slanja, ne neki nov/proizvoljan broj.
- **Model ne zavisi od toga koji je čvor proizvoljno izabran kao izvor** — ako je algoritam ispravan, zamena izvora treba predvidljivo da promeni koji je čvor 100%, a procenti kod ostalih da se preračunaju u skladu s tim (videti korak niže sa `0xCleanUser`), a ne da ostanu isti ili se slome.

Ako sve tri tvrdnje važe (a dole su i tačno izmerene), to je čvrst dokaz da implementacija algoritma odgovara teorijskom modelu opisanom u radu, a ne da su brojevi slučajno "ispali dobro" na jednom prikazu.

U slučaju **"Demo: Sumnjiva laundering sema (hakovan novcanik)"** već postoji poseban dokaz **`demo_taint_dilution.csv`** napravljen baš za ovo (dodat/proširen skriptom `backend/scripts/seed_demo_taint_evidence.py`) — sadrži dva nezavisna scenarija u istom fajlu, na potpuno različitim adresama, tako da testiranje jednog ne utiče na drugi:

```
sender_address,recipient_address,amount,timestamp
0xThief,0xMixer,1000,2026-03-01T00:00:00Z
0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z
0xMixer,0xExitWallet,750,2026-03-01T00:10:00Z
0xHacker1,0xLaunderingHub,600,2026-04-01T00:00:00Z
0xHacker2,0xLaunderingHub,400,2026-04-01T00:05:00Z
0xLaunderingHub,0xFinalDestination,800,2026-04-01T00:10:00Z
```

**Prve tri linije — razblaživanje jednim izvorom:**

1. Idi na **Slučajevi** → izaberi "Demo: Sumnjiva laundering sema".
2. Idi na **Taint analiza** → u "Prikaz transakcija" izaberi `demo_taint_dilution.csv`.
3. Klikni na čvor `0xThief` (postaje seed) → **"Pokreni taint analizu (1)"**.
4. Očekivano: `0xThief` = 100%, `0xMixer` i `0xExitWallet` = tačno **66.67%** (1000 prljavo / 1500 ukupno u mikseru), `0xCleanUser` = 0%.

Pošto je model proporcionalan (ne zavisi od toga koji je čvor izabran), možeš probati i suprotno — izaberi `0xCleanUser` kao seed umesto `0xThief` i pokreni ponovo: sad će `0xCleanUser` biti 100%, a `0xMixer`/`0xExitWallet` će pasti na **33.33%** (500/1500), dok će `0xThief` ostati na 0%. Ovo je dobar način da se u odbrani rada pokaže da algoritam ispravno reaguje na izbor izvora, a ne da su brojevi "zakucani".

**Poslednje tri linije — raspodela po pojedinačnom izvoru (kad ima više seed-ova odjednom):**

1. U istom `demo_taint_dilution.csv`, izaberi **oba** čvora `0xHacker1` i `0xHacker2` kao seed (2 klika) → **"Pokreni taint analizu (2)"**.
2. Očekivano: `0xLaunderingHub` = 100%, sa panelom "Poreklo po izvoru" koji pokazuje tačno **60% od `0xHacker1`, 40% od `0xHacker2`** (600/1000 i 400/1000). Na grani `0xLaunderingHub → 0xFinalDestination` piše direktno **"60%+40%"** umesto jednog zbirnog broja, jer `0xFinalDestination` nasleđuje identičnu raspodelu.

Ovo dokazuje da algoritam ne samo da tačno računa *koliko* je nešto zaprljano, nego i da ispravno prati *od koga* — bitno kad istraga ima više poznatih sumnjivih adresa istovremeno, ne samo jednu.

### 6.2 Test na realnim podacima (slučaj "test 1")

Slučaj **"test 1"** sadrži prave, sa Etherscan-a povučene transakcije za adresu `0x28b1Dc1a5E3699A428BC51d234DFab7C9CB2a183` (~620 čvorova, ~900 transakcija). Bitna specifičnost ovog konkretnog skupa podataka: **sve transakcije su uplate KA toj adresi** — nijedna nije isplata iz nje (provereno direktno na CSV-u). To znači da je ovo tzv. "fan-in" graf: svaka od ~600 drugih adresa se pojavljuje samo kao jednosmerni pošiljalac.

Šta to znači za taint analizu i šta realno očekivati kad testiraš:

1. Idi na **Taint analiza** → izaberi slučaj "test 1".
2. Klikni na **bilo koji čvor osim same adrese 0x28b1...** (npr. jedan od "listova" na obodu grafa) da ga označiš kao seed.
3. Pokreni analizu.
4. Očekivano: taj čvor = 100% (seed), a centralna adresa `0x28b1...` će pokazati **mali, ali nenulti procenat** — tačno onoliko koliko taj jedan pošiljalac čini od *ukupnog* priliva na tu adresu (npr. ako je taj pošiljalac poslao 2 ETH od ukupno 300 ETH primljenih u ovoj evidenciji, centralna adresa će pokazati ~0.67%). Svi ostali čvorovi ostaju na 0%, jer centralna adresa u ovoj evidenciji nikad ništa dalje ne šalje — taint nema kuda dalje da se širi.
5. Za realističniji scenario "da li su sredstva sa nekoliko poznatih sumnjivih adresa završila na ovoj adresi" — izaberi **više** čvorova kao seed odjednom (klikni na 2-3 različita "lista") i pokreni ponovo; procenat na `0x28b1...` će se sabrati proporcionalno doprinosu svih izabranih pošiljalaca.

Ovo je i sam po sebi koristan forenzički nalaz za tezu: pokazuje kako se taint drastično razblažuje kad sredstva uđu u adresu koja prima od stotina različitih izvora (tipično za berzu/menjačnicu), za razliku od uskog "peel chain" toka gde ostaje skoro nerazblaženo (vidi 6.1 iznad, ili peel-chain scenario u demo slučaju).

## 7. Šta se dešava u pozadini (za tehnički deo rada)

- Backend: `backend/app/services/onchain_ingestion.py`:
  - `fetch_address_transactions()` — poziva Etherscan V2 API modul `account`/`txlist` (`/v2/api?chainid=...`), pretvara odgovor u isti tabelarni format koji koristi CSV ingestion (`sender_address`, `recipient_address`, `amount`, `timestamp`, `metadata`), preskače neuspele (revertovane) transakcije.
  - `fetch_transaction_by_hash()` — poziva Etherscan-ov `proxy`/`eth_getTransactionByHash` modul (direktan JSON-RPC passthrough); odgovor uključuje `blockTimestamp` direktno, pa je dovoljan jedan API poziv.
  - `fetch_single_transaction_frame()` / `fetch_expanded_sender_history()` — obrađuju heš u jedan od dva režima opisana gore.
- Ruta: `POST /api/v1/onchain/fetch` (`backend/app/api/routes/onchain.py`) — automatski prepoznaje da li je uneta adresa (42 karaktera) ili heš transakcije (66 karaktera) na osnovu regex-a, primenjuje izabrani `mode`, snima rezultat kao CSV u `data/raw/`, računa SHA-256, povezuje ga sa slučajem (isti mehanizam kao `/upload/csv`) i upisuje audit log zapis sa akcijom `onchain_fetch_<mreža>_<režim>`.
- Frontend: `ApiService.fetchOnchainTransactions()` poziva tu rutu; rezultat se obrađuje potpuno isto kao odgovor na CSV upload (isti `loadDerivedViews()` poziv), tako da graf i analitika rade bez ikakvih izmena.

## 8. Napredne funkcije Taint analize

Ove funkcije su dodate posle osnovne taint analize opisane u sekciji 6, da bi analiza bila detaljnija i transparentnija — svaka je testirana na `demo_taint_dilution.csv` (isti fajl iz 6.1), da brojevi budu proverljivi.

### 8.1 Filter po pojedinačnom izvoru

**Šta radi:** kad je u analizu uključeno više seed adresa odjednom, panel **"Filter po izvoru"** (pojavljuje se iznad grafa, samo kad ima više od jednog seed-a i vremenska traka je isključena) omogućava da privremeno "isključiš" jedan ili više izvora iz prikaza — bez ponovnog pokretanja cele analize. Procenti, boje čvorova, natpisi na granama i isticanje putanje se momentalno preračunaju da pokažu **samo** doprinos onih izvora koji su ostali uključeni.

**Zašto je ovo korisno:** kad se u istrazi sretnu dve nezavisne kriminalne aktivnosti čiji se novac spoji na istoj adresi (npr. dva različita hakovana novčanika), ukupan procenat ti kaže "koliko je ukupno prljavo", ali ne i "šta bi se videlo da pratim SAMO prvi upad, a ne i drugi". Filter po izvoru ti daje tačno to — izolovan pogled na širenje jednog konkretnog izvora, korak po korak, kao da je jedini.

**Primer (na `demo_taint_dilution.csv`):**

1. Izaberi **oba** `0xHacker1` i `0xHacker2` kao seed → pokreni analizu. Kao u 6.1: `0xLaunderingHub` i `0xFinalDestination` = 100%, raspodela 60%/40%.
2. U panelu "Filter po izvoru", klikni na `0xHacker2` da ga isključiš (ostaje samo `0xHacker1` aktivan).
3. Očekivano: `0xLaunderingHub` i `0xFinalDestination` sada pokazuju tačno **60%** (samo Hacker1-ov deo), a `0xHacker2` sam po sebi pada na 0% i nestaje sa grafa (ako je "Sakrij ispod praga" uključeno).
4. Isključi i `0xHacker1` (nijedan izvor aktivan) → svi procenti padaju na 0%, ceo "prljavi" deo grafa nestaje.
5. Klikni **"Prikaži sve izvore"** da se vratiš na kombinovani prikaz (60%+40%).

### 8.2 Detalji transakcije (klik na granu)

**Šta radi:** klik na strelicu (granu) između dve adrese otvara panel **"Detalji transakcije"** sa spiskom **svake pojedinačne transakcije** koja je ikad prošla između te dve adrese — ne samo zbirni iznos koji se vidi na strelici. Za svaku transakciju se prikazuje: tačan iznos, vremenska oznaka, identifikator transakcije (tx heš — ako ga evidencija ima; u suprotnom piše "n/a"), koliki procenat baš te transakcije je bio zaprljan, i raspodela po izvorima ako ih ima više.

**Zašto je ovo korisno:** natpis na strelici (npr. "60%+40%") je nužno sažet — ako je između dve adrese bilo više odvojenih transakcija u različito vreme, strelica pokazuje samo poslednju. Ovaj panel daje kompletnu, revizijski preciznu evidenciju svake pojedinačne transakcije, uključujući i tx heš kad postoji (bitno ako se izveštaj mora povezati sa stvarnim blockchain zapisom).

**Primer (na `demo_taint_dilution.csv`, seed = `0xThief`):**

1. Pokreni analizu sa `0xThief` kao seed (scenario iz 6.1).
2. Klikni na strelicu `0xThief → 0xMixer`.
3. Očekivano: panel pokaže tačno **1 transakciju** — iznos 1000, vreme `2026-03-01T00:00:00Z`, procenat zaprljanosti **100%** (celih 1000 je prljavo, jer u tom trenutku `0xThief` šalje ceo svoj "prljavi" balans), identifikator transakcije **"n/a"** (ovaj demo CSV nema kolonu sa tx hešom — kod pravih on-chain podataka povučenih preko Etherscan-a, ovde bi stajao stvarni heš).

### 8.3 Objašnjenje procenta (kompletna istorija razblaživanja)

**Šta radi:** kad izabereš čvor, ispod liste "Zaprljane transakcije ovog čvora" nalazi se nova sekcija **"Objašnjenje procenta (kompletna istorija)"** — spisak **baš svake** transakcije koja je ikad promenila balans te adrese (i zaprljane i potpuno čiste), sa procentom **pre** i **posle** svake od njih. Kad neka transakcija spusti procenat, to je jasno obeleženo ("razblaženo ovim prilivom").

**Zašto je ovo korisno:** dosadašnja lista "Zaprljane transakcije" pokazuje samo transakcije koje su DONELE prljav novac — ali ne objašnjava zašto je procenat nekad i OPAO. Pad se dešava kad adresa primi čist (ili manje prljav) novac, što se ranije nigde nije eksplicitno videlo. Ova sekcija čini ceo račun proverljivim korak po korak, umesto da se konačni procenat "pojavi" bez objašnjenja.

**Primer (na `demo_taint_dilution.csv`, seed = `0xThief`, izabran čvor `0xMixer`):**

Očekivana istorija za `0xMixer`, tačno tim redosledom:

1. Prima 1000 od `0xThief` → **0% → 100%** (nema promene naniže, sav prvi priliv je prljav).
2. Prima 500 od `0xCleanUser` → **100% → 66.67%** (`-33.33% — razblaženo ovim prilivom`) — ovo je tačan trenutak razblaživanja, sa istim brojem (66.67%) koji se već proverava u 6.1.
3. Šalje 750 ka `0xExitWallet` → **66.67% → 66.67%** (delta 0%) — odliv ne menja procenat, samo srazmerno "iznosi" i prljavi i čisti deo napolje, što je i teorijski očekivano ponašanje proporcionalnog (haircut) modela.

### 8.4 Povezivanje tačaka unovčavanja sa poznatim entitetima

**Šta radi:** svaka adresa u listi "Verovatne tačke unovčavanja" se proveri protiv lokalne, offline baze poznatih adresa (`backend/app/services/known_entities.json` — preko 700 stvarnih Binance/Coinbase/Kraken/Huobi/OKX/Gemini adresa, Tornado.Cash mikser instanci i OFAC sankcionisanih adresa). Ako neka od njih pogodi poznat entitet, pored nje se pojavljuje obojena oznaka ("berza: Binance", "mikser: Tornado.Cash...", ili crveno "⚠ OFAC sankcionisano"), a ta adresa se automatski izdvaja na vrh liste, ispred onih bez poznatog entiteta.

**Zašto je ovo korisno:** sama činjenica "primio je prljav novac i nikad ga dalje nije poslao" ne kaže ništa o TOME GDE je taj novac zapravo završio. Kad se ta ista adresa poklopi sa stvarnom, imenovanom berzom, to je znatno jači forenzički nalaz — "novac je stigao na Binance nalog" ima realnog operatera i jurisdikciju koju sud/organi mogu stvarno da kontaktiraju, za razliku od anonimne adrese bez daljeg traga.

**Primer (na `demo_taint_dilution.csv`, treći, nezavisan scenario u istom fajlu):**

```
0xExchangeHacker,0xExchangeMule,200,2026-06-01T00:00:00Z
0xExchangeMule,0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be,200,2026-06-01T00:05:00Z
```

Poslednja adresa (`0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be`) nije izmišljena — to je stvarna, poznata Binance adresa iz baze poznatih entiteta.

1. Klikni `0xExchangeHacker` kao seed → pokreni analizu.
2. Očekivano u "Verovatne tačke unovčavanja": `0xExchangeMule` i `0x3f5c...f0be` obe na **100%** (jednostavan lanac, bez razblaživanja) — ovim se potvrđuje da propagacija kroz više uzastopnih skokova i dalje radi ispravno.
3. Pored `0x3f5c...f0be` treba da stoji plava oznaka **"berza: Binance"** — ovim se potvrđuje da je nova known-entity provera stvarno pogodila pravu adresu iz baze, a ne da samo prikazuje prazno polje.
4. Ta adresa treba da bude **prva** na listi, iznad `0xExchangeMule` (koja nema poznat entitet) — ovim se potvrđuje da sortiranje po poznatom entitetu radi, ne samo prikaz oznake.

Pošto ova provera ne zove Etherscan (čist lokalni pretraga po heš-mapi), radi trenutno i bez obzira na broj tačaka unovčavanja u listi.

### 8.5 Obogaćen PDF izveštaj (istorija razblaživanja i detalji transakcije)

**Šta radi:** izvezeni PDF ("Izvezi PDF" dugme) sada, pored postojećih tabela, sadrži i podatke koji su ranije postojali samo na ekranu:

- Lista "Verovatne tačke unovčavanja" (i na ekranu i u PDF-u) je ograničena na najviše **5** adresa (poznati entiteti prvo, pa po procentu) — ako ih ima više, ispod se ispisuje "+ X dodatnih tačaka unovčavanja detektovano...".
- Nova sekcija **"Detaljna istorija razblaživanja — tačke unovčavanja"**: za svaku od (do 5) prikazanih adresa, kompletna hronologija transakcija (smer, suprotna strana, iznos, zaprljan iznos, procenat pre/posle), ograničena na prvih **20** po adresi — ako ih ima više, ispod tabele piše koliko je izostavljeno i kolika je neto promena procenta u tom periodu.
- Nova sekcija **"Detalji izabrane transakcije"**: ako je neka grana bila selektovana na grafu u trenutku klika na "Izvezi PDF", njeni pojedinačni transferi (isto ograničeno na 20) se dodaju u izveštaj. Ako ništa nije selektovano, sekcija se uopšte ne pojavljuje.

**Zašto je ovo korisno:** transakcijski detalji i istorija razblaživanja su ranije postojali SAMO u aplikaciji (klikom na čvor/granu) — izveštaj koji se šalje trećim licima (sudu, kolegama) ih nije sadržao. Bez ograničenja broja adresa/transakcija, ovo bi na realnom slučaju sa stotinama transakcija po adresi napravilo neupotrebljivo dugačak izveštaj — otud dva nivoa ograničenja (top 5 adresa, top 20 transakcija po adresi), sa transparentnom napomenom šta je izostavljeno i gde se kompletni podaci mogu naći (u aplikaciji ili u CSV/GraphML izvozu).

**Primer 1 (na `demo_taint_dilution.csv`, isti scenario kao u 8.4 — mali slučaj):**

1. Ponovi test iz 8.4 (seed `0xExchangeHacker`) i izvezi PDF.
2. U tabeli "Verovatne tačke unovčavanja" treba da stoje tačno **2** reda, bez "+X dodatnih" napomene — ovim se potvrđuje da napomena ispravno izostaje kad je broj tačaka ispod granice od 5.
3. U novoj sekciji "Detaljna istorija razblaživanja", pored `0x3f5c...f0be` treba da piše `[berza: Binance]` odmah u naslovu, sa tačno **1** redom istorije (Prijem od `0xExchangeMule`, iznos 200, zaprljano 200, 0% → 100%) — ovim se potvrđuje da known-entity oznaka i puna istorija sad ulaze i u PDF, ne samo u aplikaciju.

**Primer 2 (na realnom slučaju "test 1", 620 čvorova, kombinovana evidencija):**

1. Pokreni taint analizu na "test 1" evidenciji (svi seedovi) i izvezi PDF.
2. U "Verovatne tačke unovčavanja" treba da stoji tačno **1** red (`0x28b1...2a183`, 52.72%) — ovaj slučaj ima samo jednu tačku unovčavanja, pa cap od 5 ovde ništa ne seče (potvrđuje da se cap ne aktivira lažno kad nije potreban).
3. U "Detaljna istorija razblaživanja" za tu adresu treba da bude tačno **20** redova, a ispod njih napomena `+ 355 dodatnih transakcija (dalja neto promena procenta: +11.96 p.p.)` — ovim se potvrđuje da cap od 20 transakcija po adresi radi na stvarnoj evidenciji sa stotinama transfera, bez rušenja izveštaja.
4. Prvi red istorije treba da pokaže **0% → 100%** (prvi priliv, potpuno zaprljan), a naredni redovi mešavinu čistih priliva (kolona "Zaprljano" = 0.00, procenat opada — razblaživanje) i priliva od seed adresa (procenat raste) — ovim se potvrđuje da je matematika razblaživanja ista ona koja se proverava u 6.1 i 8.3, samo sad primenjena na pravu, veliku evidenciju umesto na skriptovani demo scenario.

**Napomena o "Detalji izabrane transakcije":** klikni na bilo koju granu na grafu PRE nego što klikneš "Izvezi PDF" (npr. `0xThief → 0xMixer` iz 6.1 scenarija) — u PDF-u treba da se pojavi ta sekcija sa istim transakcijama koje se vide u panelu na ekranu. Ako izvezeš PDF bez selektovane grane, sekcija se uopšte ne pojavljuje u dokumentu — ovim se potvrđuje da je uslovno renderovanje ispravno (nema prazne/beskorisne sekcije kad nema šta da se prikaže).

### 8.6 Filter po izvoru radi i tokom vremenske trake

**Šta radi:** panel **"Filter po izvoru"** (8.1) je ranije bio dostupan samo dok je vremenska traka isključena — dok traka radi, procenti/boje/natpisi na granama su uvek prikazivali doprinos SVIH izvora, bez obzira šta je u filteru izabrano. Sada panel ostaje aktivan i tokom skrubovanja, a procenat čvora, boja i natpis na grani se ispravno preračunavaju da pokažu samo izabrani izvor, **baš na trenutnoj poziciji trake** — ne samo u konačnom/punom prikazu.

**Zašto je ovo korisno:** ranije, ako si hteo da vidiš KAKO se tačno šire sredstva baš jednog izvora kroz vreme (a ne oba/sva odjednom), morao si da se osloniš na konačan rezultat — vremenska traka ti nije davala tu istu preciznost tokom same reprodukcije. Sad se ova dva alata (traka + filter) mogu koristiti zajedno — korak po korak posmatranje širenja jednog konkretnog izvora, transakciju po transakciju.

**Šta je bio tehnički razlog za ograničenje i kako je uklonjeno:** grane (`tainted_hops`) su oduvek imale tačnu istorijsku raspodelu po izvoru za svaku pojedinačnu transakciju, pa je taj deo bilo moguće popraviti samo na frontend-u. Čvorovi (`node_taint_series`) su ranije čuvali samo zbirni procenat po događaju, bez raspodele po izvoru u tom trenutku — backend sad uz svaki događaj čuva i tu raspodelu (isti podatak koji se već računa tokom analize, samo sad i zapisan), što frontend-u omogućava da filter primeni tačno onako kako bi izgledalo da je pratio samo taj izvor.

**Primer (na `demo_taint_dilution.csv`, isti scenario kao 8.1 — seed `0xHacker1` + `0xHacker2`):**

1. Pokreni analizu SAMO sa `0xHacker1` i `0xHacker2` kao seed (ne svih 5 iz fajla) i uključi vremensku traku. Panel "Filter po izvoru" treba da ostane vidljiv i dalje — ovim se potvrđuje da panel više nije skriven čim se traka uključi.
2. U filteru isključi `0xHacker2` (ostaje aktivan samo `0xHacker1`).
3. Pomeri traku na **transakciju 4/8** (`Hacker1 → LaunderingHub`): `0xLaunderingHub` treba da pokaže **100%** — u tom trenutku je stiglo samo Hacker1-ovo, pa je filtrirano i nefiltrirano isto — ovim se potvrđuje da rani deo scenarija i dalje radi kao pre popravke.
4. Pomeri na **transakciju 5/8** (`Hacker2 → LaunderingHub`): `0xLaunderingHub` treba SADA da pokaže **60%**, a ne 100% kao pre popravke — ovo je ključni trenutak koji direktno dokazuje da filter tokom trake sada ispravno računa istorijsku (as-of-poziciji), a ne konačnu raspodelu.
5. Pomeri na **transakciju 6/8** (`LaunderingHub → FinalDestination`): `0xFinalDestination` treba da pokaže **60%**, a strelica između njih natpis **"60%"** (ne "60%+40%") — ovim se potvrđuje da su i čvorovi i grane usklađeni, i da se poklapaju sa konačnim rezultatom iz 8.1.
6. Klikni **"Prikaži sve izvore"** i ponovo prođi kroz rangove 4-6 — svuda treba da piše **100%** — ovim se potvrđuje da puni (nefiltrirani) prikaz nije pokvaren popravkom.

## 9. Log aktivnosti (chain of custody)

Za razliku od sekcije 8, ovo nije funkcija taint analize nego cele aplikacije — beleži se **svaka** radnja analitičara, od otpremanja dokaza do pokretanja analize.

### 9.1 Šta se beleži i zašto

**Šta radi:** svaka značajna akcija u aplikaciji upisuje jedan zapis u append-only log (`logs/audit_log.jsonl`). Zapis sadrži vreme, korisnika, tip akcije, slučaj (ID **i naziv**) i parametre specifične za tu akciju:

| Akcija | Kada nastaje | Šta se dodatno beleži |
|---|---|---|
| `csv_upload` | otpremanje CSV dokaza | naziv fajla, SHA-256, veličina |
| `onchain_fetch_*` | povlačenje transakcija sa blockchain-a | mreža, režim, upit, broj transakcija, SHA-256 |
| `analytics_run` | **pokretanje analize (uklj. taint)** | seed adrese, opseg evidencije, broj redova i čvorova |
| `path_finding` | pretraga putanja na grafu | polazna/ciljna adresa, strategija, broj nađenih putanja |
| `case_created` / `case_status_changed` / `case_deleted` | rad sa slučajevima | prethodni → novi status |

**Zašto je ovo bitno:** ranije se beležilo samo *kako je dokaz stigao* (upload/fetch), ali ne i *šta je s njim rađeno*. To je bila stvarna rupa u lancu dokaza: dva analitičara koja pokrenu istu analizu nad različitim opsegom evidencije ili sa različitim seed adresama legitimno dobiju **različite procente** — bez ovog zapisa nije bilo načina da se naknadno rekonstruiše iz kog tačno pokretanja potiče sporni broj.

**Ključni detalj — naziv slučaja se pamti u trenutku akcije**, a ne rezoluje kasnije iz ID-a. Ako se slučaj kasnije preimenuje ili obriše, log i dalje tačno kaže kako se zvao kad je akcija izvršena. Zbog istog principa se u logu i dalje vide korisnici kojih više nema u sistemu — istorija se ne prepisuje kad se nalog ukloni.

### 9.2 Stranica "Log" — testiranje

**Šta radi:** dugme **"Log"** u glavnom meniju (vidljivo svim ulogovanim korisnicima) otvara stranicu sa hronološkim pregledom akcija, najnovije prvo. Akcije su obojene po tipu (plavo = dokazi, žuto = analize, ljubičasto = slučajevi), a klik na "Prikaži" u redu otvara sve sirove parametre te akcije.

**Ko šta vidi:** običan korisnik (analitičar) vidi **isključivo svoje** akcije; administrator vidi akcije **svih** naloga i može ih filtrirati po pojedinačnom korisniku. Ovo ograničenje je sprovedeno **na serveru** — nije stvar prikaza.

**Testiranje kao analitičar (npr. nalog `aco`):**

1. Prijavi se kao ne-admin nalog, pokreni bilo koju taint analizu, pa otvori **"Log"**. Na vrhu liste treba da bude zapis **"Pokrenuta analiza"** sa tvojim korisničkim imenom — ovim se potvrđuje da se pokretanje analize uopšte beleži (ranije se nije).
2. U koloni **"Slučaj"** tog zapisa treba da stoje naziv slučaja i ispod njega ID — ovim se potvrđuje da se zna **za koji je slučaj** analiza pokrenuta, što je i bio glavni zahtev.
3. Klikni **"Prikaži"** na tom redu — treba da se otvore seed adrese, opseg evidencije i broj čvorova — ovim se potvrđuje da se beleže i **parametri** analize, ne samo činjenica da je pokrenuta.
4. Proveri da u listi **nema** akcija drugih korisnika — ovim se potvrđuje da analitičar ne vidi tuđi rad.
5. Otpremi neki CSV ili povuci transakcije sa blockchain-a, pa se vrati na "Log" — nova akcija se pojavljuje sama u roku od ~20 sekundi (indikator **"Uživo"**), bez ručnog osvežavanja — ovim se potvrđuje automatsko osvežavanje. Klikom na "Uživo" se pauzira, klikom na "Osveži" se učitava ručno.

**Testiranje kao administrator:**

6. Prijavi se kao `admin` i otvori "Log" — treba da vidiš izmešane akcije svih naloga, a u zaglavlju da piše da su prikazane akcije **svih korisnika** — ovim se potvrđuje admin opseg.
7. Iz padajućeg menija **"Korisnik"** izaberi jedan nalog — lista se sužava samo na njegove akcije; opcija "Svi korisnici" vraća pun prikaz — ovim se potvrđuje filtriranje po nalogu.
8. Padajući meni sadrži naloge iz iste liste koja se vidi na strani "Administracija" — ovim se potvrđuje da je filter povezan sa stvarnim nalozima, a ne sa proizvoljnim imenima iz loga.

**Provera bezbednosti (opciono, tehnički):** ulogovan kao analitičar, ručno pozovi `GET /api/v1/activity-log?user=admin` (npr. preko Swagger-a na `http://localhost:8000/docs`) — server i dalje vraća **samo tvoje** zapise, jer opseg određuje uloga iz tokena, a ne parametar iz upita. Ovim se potvrđuje da filtriranje nije samo kozmetičko na frontend-u.

### 9.3 Veza sa izveštajem slučaja

Postojeći backend izveštaj slučaja (`/api/v1/exports/cases/{id}/report.csv` i `.pdf`) već čita isti log za svoj "chain of custody" deo, pa se **pokrenute analize sada automatski pojavljuju i tamo** — ranije su u tom delu izveštaja postojali samo zapisi o otpremanju dokaza.
