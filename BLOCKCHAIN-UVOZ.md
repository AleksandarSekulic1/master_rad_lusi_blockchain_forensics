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

## 6. Taint analiza

Taint analiza (stranica **Taint analiza** u navigaciji) prati kako se „prljava" sredstva proporcionalno šire kroz
graf, počevši od jednog ili više izabranih („seed") čvorova.

> Kompletna dokumentacija — forenzički smisao, haircut model, predlog čvorova, sve funkcije prikaza, PDF izveštaj sa
> potpisom i pečatom, provera valute, pronađene greške i **svi koraci testiranja** — nalazi se u zasebnom dokumentu
> **[`TAINT-ANALIZA.md`](TAINT-ANALIZA.md)**.

## 7. Šta se dešava u pozadini (za tehnički deo rada)

- Backend: `backend/app/services/onchain_ingestion.py`:
  - `fetch_address_transactions()` — poziva Etherscan V2 API modul `account`/`txlist` (`/v2/api?chainid=...`), pretvara odgovor u isti tabelarni format koji koristi CSV ingestion (`sender_address`, `recipient_address`, `amount`, `timestamp`, `metadata`), preskače neuspele (revertovane) transakcije.
  - `fetch_transaction_by_hash()` — poziva Etherscan-ov `proxy`/`eth_getTransactionByHash` modul (direktan JSON-RPC passthrough); odgovor uključuje `blockTimestamp` direktno, pa je dovoljan jedan API poziv.
  - `fetch_single_transaction_frame()` / `fetch_expanded_sender_history()` — obrađuju heš u jedan od dva režima opisana gore.
- Ruta: `POST /api/v1/onchain/fetch` (`backend/app/api/routes/onchain.py`) — automatski prepoznaje da li je uneta adresa (42 karaktera) ili heš transakcije (66 karaktera) na osnovu regex-a, primenjuje izabrani `mode`, snima rezultat kao CSV u `data/raw/`, računa SHA-256, povezuje ga sa slučajem (isti mehanizam kao `/upload/csv`) i upisuje audit log zapis sa akcijom `onchain_fetch_<mreža>_<režim>`.
- Frontend: `ApiService.fetchOnchainTransactions()` poziva tu rutu; rezultat se obrađuje potpuno isto kao odgovor na CSV upload (isti `loadDerivedViews()` poziv), tako da graf i analitika rade bez ikakvih izmena.


## 8. Log aktivnosti (chain of custody)

Ovo nije funkcija taint analize nego cele aplikacije — beleži se **svaka** radnja analitičara, od otpremanja dokaza do pokretanja analize.

### 8.1 Šta se beleži i zašto

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

### 8.2 Stranica "Log" — testiranje

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

### 8.3 Veza sa izveštajem slučaja

Postojeći backend izveštaj slučaja (`/api/v1/exports/cases/{id}/report.csv` i `.pdf`) već čita isti log za svoj "chain of custody" deo, pa se **pokrenute analize sada automatski pojavljuju i tamo** — ranije su u tom delu izveštaja postojali samo zapisi o otpremanju dokaza.

## 9. Testovi ispravnosti (stranica „Testovi")

Dugme **„Testovi"** u meniju (samo administrator) otvara stranicu sa dve vrste provera. Podela nije kozmetička nego suštinska, i objašnjena je u 10.1 i 10.2.

### 9.1 Sistemski testovi — nepromenljivi

**Šta radi:** fiksni skup testova nad taint algoritmom, u verzionisanom kodu (`backend/tests/`). Stranica ih prikazuje grupisano, pokreće na klik i prikazuje rezultat (prošlo / palo, trajanje). **Klikom na svaki test** otvara se objašnjenje na srpskom šta test dokazuje i **njegov stvarni izvorni kod**, a kod testa koji je pao i tačna poruka o grešci.

**Zašto se ne mogu menjati kroz aplikaciju:** kada bi dokaz ispravnosti mogao da se izmeni dok ne prođe, prestao bi da bude dokaz. Zato stranica ove testove **samo čita i pokreće** — ne postoji način da se kroz interfejs izmene.

**Kako nazivi ostaju sinhronizovani sa kodom:** naziv testa na stranici je **prva linija docstring-a same test funkcije**, a naslov grupe je docstring klase. Ne postoji odvojena tabela prevoda koja bi vremenom mogla da se raziđe od koda — izmena testa automatski menja i ono što piše u aplikaciji.

**Šta je pokriveno (glavne grupe):** razblaživanje (haircut model), raspodela po izvorima, podaci za vremensku traku, hronologija, ponašanje seed adresa, zaprljani skokovi — plus grupe za izveštaj aktivnosti (sekcija 11).

**Testiranje:**

1. Otvori „Testovi" i klikni **„Pokreni sistemske testove"** — treba da se pojavi zelena traka sa brojem testova koji su prošli i trajanjem — ovim se potvrđuje da se testovi stvarno izvršavaju na serveru, a ne da je rezultat unapred upisan.
2. Klikni na bilo koji test — otvara se objašnjenje na srpskom i blok **„Šta test tačno proverava"** sa pravim kodom — ovim se potvrđuje da se prikazani kod čita iz stvarnog test fajla.
3. Kao ne-admin nalog pokušaj da otvoriš `/tests` — pristup mora biti odbijen — ovim se potvrđuje da je stranica zaista ograničena na administratora.

> **Dokaz da testovi zaista hvataju greške:** test koji prolazi i na ispravnom i na pokvarenom kodu je bezvredan. Zato je algoritam namerno kvaren tri puta i provereno je da testovi to primete: uklanjanje haircut proporcije → 3 testa pala; uklanjanje hronološkog sortiranja → 4 testa pala; uklanjanje raspodele po izvorima po rangu (izmena iz 8.6) → 1 test pao. Nakon svake provere kôd je vraćen u prvobitno stanje.

### 9.2 Validacioni scenariji — mogu se kreirati, menjati i brisati

**Šta radi:** scenario je **opis podataka, ne kod** — spisak transakcija, izvori (seed adrese) i procenti koji se očekuju. Aplikacija ih propušta kroz **pravi** taint algoritam i uporedi rezultat. Kod pada se prikazuje tabela **očekivano vs. dobijeno** po adresi.

**Zašto je ovo bezbedno za izmenu, a sistemski testovi nisu:** scenario ne sadrži kod koji se izvršava — samo podatke koji se propuštaju kroz postojeći algoritam. Zato admin može slobodno da ih kreira, menja i briše bez rizika da kroz interfejs pokrene proizvoljan kod na serveru.

**Praktična vrednost:** vremenom nastaje **biblioteka referentnih slučajeva na kojima je alat validiran** — što je za rad iz digitalne forenzike jak argument.

**Testiranje:**

1. Klikni **„Novi scenario"**, unesi transakcije `0xThief → 0xMixer, 1000` i `0xCleanUser → 0xMixer, 500`, izvor `0xThief`, i očekivanje `0xMixer = 50%` (namerno pogrešno). Sačuvaj i pokreni — scenario mora **pasti**, uz prikaz `očekivano 50%, dobijeno 66.67%` — ovim se potvrđuje da poređenje stvarno radi, a ne da sve prolazi.
2. Klikni „Izmeni" i postavi `66.67%` — sada mora **proći** — ovim se potvrđuje da izmena scenarija ima efekta.
3. Unesi adresu koja ne postoji u transakcijama — mora se prikazati poruka *„Adresa se ne pojavljuje u rezultatu analize"* umesto tihog prolaza — ovim se potvrđuje da greška u kucanju ne može da prođe kao uspešan test.
4. Obriši scenario — nestaje sa spiska; u „Log aktivnosti" ostaje zapis sa **nazivom** obrisanog scenarija.

### 9.3 Testovi se automatski pojavljuju na stranici

Pokretač testova skenira sve `test_*.py` fajlove u `backend/tests/`, pa **svaki novi test fajl automatski osvane na stranici**, grupisan po svom docstring-u — bez ikakve izmene u frontendu. Zahvaljujući tome, provere koje se pišu tokom razvoja ostaju trajno vidljive umesto da budu privremene skripte koje se obrišu.

Ograničenje: ovako se mogu pokriti logika, matematika i pravila pristupa. Vizuelne provere (prelom teksta, raspored u PDF-u) i Docker build ostaju ručne — test koji tvrdi „izgleda lepo" ne bi ništa dokazivao.

## 10. Izveštaj aktivnosti (izvoz iz loga)

**Šta radi:** dugme **„Izveštaj"** na strani „Log aktivnosti" otvara panel za izvoz zapisa u **PDF** ili **CSV**.

- **Period** — tri opcije: `Sve aktivnosti` (od početka korišćenja sistema), `Jedan dan`, `Od — do`
- **Korisnici** — običan korisnik dobija isključivo svoje akcije; **administrator** bira jednog, više njih u bilo kojoj kombinaciji, ili sve
- **Brojač uživo** — pre klika se prikazuje koliko zapisa period obuhvata; kada je **0**, dugmad su onemogućena uz poruku da se izveštaj ne može generisati

**Zašto se generiše na serveru:** dokument tvrdi „ovo su sve akcije u periodu X", pa podaci moraju doći iz samog log fajla, a ne iz onoga što je browser slučajno učitao. Zbog toga ovaj izveštaj ima i **ispravna slova č/ć/š/ž/đ** (server ugrađuje Unicode font), za razliku od taint PDF-a iz sekcije 8.7.

**Vremenska zona — najvažniji detalj:** log čuva vreme u UTC, a korisnik bira dane onako kako ih vidi na ekranu, u lokalnom vremenu. Da se filtrira po UTC danu, akcija u 00:30 po lokalnom vremenu (UTC+2) bi ispala iz izveštaja za taj dan, jer je u UTC-u zabeležena kao 22:30 prethodnog dana. Zato se filtrira po **lokalnom** danu, a izveštaj u zaglavlju navodi korišćenu zonu (npr. `UTC+02:00`).

**Šta izveštaj sadrži:** zaglavlje (ko, kada, period + zona, obuhvaćeni korisnici, redosled), četiri kartice sa rezimeom (ukupno akcija / korisnika / dana sa aktivnošću / slučajeva), raspodelu po tipu akcije i po korisniku sa trakama, i hronologiju **grupisanu po danima** (svaki dan ima zaglavlje sa datumom, danom u nedelji i brojem akcija).

**Testiranje:**

1. Otvori „Log aktivnosti" → **„Izveštaj"** → ostavi `Sve aktivnosti`. Ispod se prikazuje ukupan broj zapisa — ovim se potvrđuje da brojač radi pre generisanja.
2. Izaberi `Jedan dan` i datum na koji **ima** aktivnosti → preuzmi PDF. U zaglavlju mora pisati `Jedan dan: <datum>` i korišćena vremenska zona — ovim se potvrđuje da je period tačno onaj koji si izabrao.
3. Izaberi datum na koji **nema** aktivnosti → brojač pokazuje `0 zapisa`, poruka objašnjava zašto, a dugmad su siva — ovim se potvrđuje da se prazan izveštaj sprečava **pre** klika, a ne greškom posle.
4. Kao **admin**, izaberi dva konkretna korisnika → broj zapisa mora biti zbir njihovih pojedinačnih brojeva — ovim se potvrđuje da filter po korisnicima radi tačno.
5. Preuzmi i **CSV** za isti period → mora imati isti broj redova (plus zaglavlje) i sadržati **i lokalno i UTC vreme** — ovim se potvrđuje da se izveštaj može proveriti nezavisno od vremenske zone.
6. Vrati se na „Log aktivnosti" → na vrhu je novi zapis **„Izvezen izveštaj aktivnosti"** sa detaljima tipa `PDF · 42 zapisa · jedan dan: 10.08.2026.` — ovim se potvrđuje da se i sam izvoz beleži, sa tačnim vremenskim okvirom koji je biran.

**Provera bezbednosti (tehnički):** ulogovan kao analitičar, ručno pozovi `GET /api/v1/activity-log/report.pdf?users=admin` — izveštaj i dalje sadrži **samo tvoje** akcije, jer opseg određuje uloga iz tokena, a ne parametar iz upita.

> Napomena o starim zapisima: unosi napravljeni pre nego što su polja `case_name` i `details` uvedena prikazuju se sa „naziv nije zabeležen" i praznim detaljima. To je namerno — tada ti podaci nisu beleženi, pa bi bilo kakva dopuna bila izmišljena.
