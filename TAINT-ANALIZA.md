# Taint analiza — sve implementirano, sa koracima testiranja

Kompletna dokumentacija taint analize na jednom mestu: šta je implementirano, zašto, i kako se svaka funkcija proverava.

Uputstva za uvoz podataka (Etherscan, CSV), log aktivnosti i stranicu „Testovi" nalaze se u `BLOCKCHAIN-UVOZ.md`.

**Sadržaj**

| Deo | Šta pokriva |
|---|---|
| [1. Zašto taint analiza](#1-zašto-taint-analiza) | forenzički smisao |
| [2. Model praćenja](#2-model-praćenja-proporcionalni-haircut) | haircut model i zašto baš on |
| [3. Osnovno testiranje](#3-osnovno-testiranje-algoritma) | tačni brojevi na demo podacima |
| [4. Predlog čvorova](#4-predlog-čvorova-za-analizu) | pravila umesto statistike |
| [5. Funkcije prikaza](#5-funkcije-prikaza-i-analize) | filteri, traka, detalji, entiteti |
| [6. PDF izveštaj](#6-pdf-izveštaj) | sadržaj, metodologija, potpis i pečat |
| [7. Valuta evidencije](#7-valuta-evidencije) | zaštita od mešanja ETH/USDT |
| [8. Oblik evidencije](#8-upozorenje-o-obliku-evidencije) | jednoslojni podaci |
| [9. Klasteri](#9-klasteri) | zašto se ne koriste u obračunu |
| [10. Ispravljene greške](#10-pronađene-i-ispravljene-greške) | šta je pucalo i kako je rešeno |
| [11. Testovi](#11-provera-ispravnosti-testovi) | 60 automatskih testova |
| [12. Gde je šta u kodu](#12-gde-se-šta-nalazi-u-kodu) | putanje |

---

## 1. Zašto taint analiza

Zamisli da neko ukrade novac i počne da ga prosleđuje kroz gomilu novčanika, mešajući ga usput sa tuđim, čistim parama — baš da zamrsi trag.

**Taint %** odgovara na jedno pitanje: *„Od svega što se sad nalazi na ovoj adresi, koliki deo je zapravo taj ukradeni novac?"* — npr. „40% para na ovoj adresi potiče od te krađe, ostalih 60% je tuđ novac koji se slučajno našao u istom loncu."

Zašto je to korisno u istrazi:

1. **Ne moraju se ručno proveravati stotine adresa.** Na velikom grafu program odmah pokaže koje su najviše umešane.
2. **Pokazuje gde novac izlazi napolje.** Lopova obično već znamo; zanima nas gde se ukradeni novac pretvara nazad u pravi novac (berza) — tu ga vlasti mogu zaustaviti.
3. **Daje broj, ne samo „da/ne".** „31% je prljavo" je jači dokaz od „umešana je", jer se vidi razlika između nekoga ko je skoro sigurno umešan i nekoga ko je samo dotaknut velikim čistim tokom.
4. **Ne baca sve u isti koš.** Ako si nevin a slučajno koristio isti mikser kao lopov, ne bi bilo pošteno da te tretiraju kao lopova — zato se računa stvaran udeo.

---

## 2. Model praćenja: proporcionalni („haircut")

Kada se na jednoj adresi pomešaju zaprljana i čista sredstva, **svaki naredni odliv nosi isti procenat zaprljanosti kao balans sa kojeg je poslat**.

```
adresa primi 1000 ukradenih + 500 čistih  →  balans je 66.67% zaprljan
svaka naredna isplata sa te adrese nosi tačno 66.67% zaprljanih sredstava
```

### Zašto baš taj model

Izbor modela **nije neutralan** — isti podaci daju različite procente pod različitim modelima, pa se rezultat ne može direktno porediti sa nalazom dobijenim drugom metodom:

| Model | Pretpostavka | Posledica |
|---|---|---|
| **Proporcionalni (haircut)** | odliv nosi srazmeran udeo | **izabran ovde** |
| FIFO | prvi primljeni novac prvi odlazi | koristi se u nekim jurisdikcijama zbog sudske prakse |
| LIFO | poslednji priliv prvi odlazi | obrnuta pretpostavka |
| Poison / taint-by-contact | svaki dodir = trajno 100% | obuhvata ogroman broj nevezanih adresa |

Proporcionalni je izabran jer ne širi sumnju na adrese čija je stvarna izloženost zanemarljiva, a zadržava trag i posle višestrukog mešanja.

### Pet osobina koje algoritam mora da poštuje

1. **Čist priliv razblažuje** procenat srazmerno.
2. **Odliv ne menja procenat pošiljaoca** — odnosi i prljavo i čisto u istoj srazmeri.
3. **Hronologija ide kroz ceo graf**, ne granu po granu.
4. **Raspodela po izvorima putuje sa novcem** — zna se ne samo „koliko je prljavo" nego i „čije je".
5. **Procenat nikada ne prelazi 100%.**

Svaka je pokrivena automatskim testom (sekcija 11).

---

## 3. Osnovno testiranje algoritma

### 3.1 Kontrolisan scenario — tačni brojevi

**Šta se dokazuje:** da je matematika haircut modela tačna, na podacima sa unapred poznatim odgovorom. Tri tvrdnje:

- zaprljanost se **razblažuje** kad se pomeša sa čistim sredstvima — ne 100% (poison model), ne 0% (nema praćenja), nego tačno **1000/1500 = 66.67%**
- procenat **putuje dalje nepromenjen** kroz sledeći prenos
- model **ne zavisi od izbora izvora** — zamena izvora predvidljivo menja rezultat

U slučaju **„Demo: Sumnjiva laundering sema"** postoji dokaz `demo_taint_dilution.csv` napravljen baš za ovo (skripta `backend/scripts/seed_demo_taint_evidence.py`), sa tri nezavisna scenarija na različitim adresama:

```
sender_address,recipient_address,amount,timestamp
0xThief,0xMixer,1000,2026-03-01T00:00:00Z
0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z
0xMixer,0xExitWallet,750,2026-03-01T00:10:00Z
0xHacker1,0xLaunderingHub,600,2026-04-01T00:00:00Z
0xHacker2,0xLaunderingHub,400,2026-04-01T00:05:00Z
0xLaunderingHub,0xFinalDestination,800,2026-04-01T00:10:00Z
0xExchangeHacker,0xExchangeMule,200,2026-06-01T00:00:00Z
0xExchangeMule,0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be,200,2026-06-01T00:05:00Z
```

**Test A — razblaživanje jednim izvorom:**

1. **Slučajevi** → izaberi „Demo: Sumnjiva laundering sema".
2. **Taint analiza** → u „Prikaz transakcija" izaberi `demo_taint_dilution.csv`.
3. Klikni čvor `0xThief` → **„Pokreni taint analizu (1)"**.
4. Očekivano: `0xThief` = 100%, `0xMixer` i `0xExitWallet` = **66.67%**, `0xCleanUser` = 0%.

**Test B — model ne zavisi od izbora izvora:**

5. Izaberi `0xCleanUser` kao seed umesto `0xThief` i pokreni ponovo.
6. Očekivano: `0xCleanUser` = 100%, `0xMixer`/`0xExitWallet` padaju na **33.33%** (500/1500), `0xThief` = 0%. Ovim se potvrđuje da brojevi nisu „zakucani".

**Test C — raspodela po pojedinačnom izvoru:**

7. Izaberi **oba** `0xHacker1` i `0xHacker2` → **„Pokreni taint analizu (2)"**.
8. Očekivano: `0xLaunderingHub` = 100%, panel „Poreklo po izvoru" pokazuje **60% od `0xHacker1`, 40% od `0xHacker2`**. Na grani `0xLaunderingHub → 0xFinalDestination` piše **„60%+40%"**.

Ovim se dokazuje da algoritam prati ne samo *koliko* je zaprljano nego i *od koga*.

### 3.2 Test na realnim podacima (slučaj „test 1")

Slučaj **„test 1"** sadrži prave transakcije povučene sa Etherscan-a za adresu `0x28b1Dc1a5E3699A428BC51d234DFab7C9CB2a183` (~620 čvorova). Specifičnost: **sve transakcije su uplate KA toj adresi** — to je „fan-in" graf.

1. **Taint analiza** → slučaj „test 1".
2. Klikni **bilo koji čvor osim same adrese `0x28b1...`** (neki „list" na obodu).
3. Pokreni analizu.
4. Očekivano: taj čvor = 100%, a centralna adresa pokazuje **mali ali nenulti procenat** — tačno koliko taj pošiljalac čini od ukupnog priliva. Ostali ostaju na 0%, jer centralna adresa u ovoj evidenciji ništa ne šalje dalje.
5. Izaberi **više** čvorova kao seed → procenat na `0x28b1...` se sabira proporcionalno.

Ovo je i sam po sebi koristan nalaz: pokazuje kako se taint drastično razblažuje kad sredstva uđu u adresu koja prima od stotina izvora (tipično za berzu), za razliku od uskog peel-chain toka gde ostaje skoro nerazblažen.

> Ova evidencija pokreće i upozorenje o obliku podataka — vidi sekciju 8.

---

## 4. Predlog čvorova za analizu

### 4.1 Šta je bilo pogrešno u prvoj verziji

Prva verzija je koristila detektor statističkih anomalija (`IsolationForest`) i uzimala sve što on označi. Izmereno na stvarnim podacima — **neupotrebljivo za taint analizu**:

**Broj predloga je bio izmišljen.** Formula uvek daje `0.05`:

```python
contamination = min(0.2, max(0.05, 1.0 / max(len(training_frame), 20)))
```

`max(0.05, ...)` uvek pobedi → od 631 adrese uvek ~32 predloga, bez obzira da li ima 2 sumnjive ili 200.

**Odziv na podacima sa poznatim odgovorom: 1 od 4.**

| Prava seed adresa | Rezultat | Ocena rizika |
|---|---|---|
| `0xExchangeHacker` | pronađen | 17 |
| `0xThief` | **propušten** | 21 |
| `0xHacker1` | **propušten** | 20 |
| `0xHacker2` | **propušten** | 19 |

**Uzrok nije podešavanje nego struktura problema:** izvor ukradenog novca po pravilu izgleda dosadno — lopovljev novčanik obično uradi jednu stvar, pošalje novac dalje. To je najmanje neuobičajeno ponašanje koje postoji. Mikser, koji prima od više strana, statistički štrči — pa je dobio ocenu 54, a lopovi 19–21.

**Predlagao je najveće adrese.** Na „test 1", od 32 predloga **21 je bio među 32 adrese sa najvećim prometom**. Na pravom Ethereumu to su berze i pametni ugovori. Postaviti berzanski novčanik kao izvor znači označiti sve njegove isplate kao zaprljane.

**Granica je bila proizvoljna.** `0xMixer` (54) je ušao, `0xLaunderingHub` (53) nije. Jedan poen.

### 4.2 Odbačeno rešenje

Razmatrano je pravilo: *„izvor je adresa koja šalje a nikad ne prima unutar dokaza"*. Testirano pre primene:

| Podaci | Rezultat |
|---|---|
| Demo scenario (11 adresa) | **4/4 — nijedan propust** |
| Realni „test 1" (631 adresa) | **621 od 631 adresa (98.4%)** |

Na demou savršeno, na pravim podacima beskorisno. **Odbačeno.**

### 4.3 Šta radi sada

**Princip:** prva verzija je prvo odlučila *koliko* adresa da izdvoji, pa tražila koje — zato je uvek nešto „lupila". Sada se prvo traže konkretni obrasci, pa koliko se nađe, toliko. **Ništa se ne predlaže bez navedenog razloga.**

**Kandidati za izvor** — jedina osnova za seed adresu:

| Provera | Definicija |
|---|---|
| Crna lista predmeta | adresa označena kao poznato zlonamerna u samim podacima |
| OFAC sankcionisane | poklapanje sa lokalnom bazom sankcionisanih adresa |

**Tačke pranja** — važni nalazi, ali **pogrešni kao seed**:

| Provera | Definicija |
|---|---|
| Poznati mikseri | poklapanje sa bazom miksera (Tornado.Cash i sl.) |
| Peel chain | lanac u kome se pri svakom koraku odvaja mali deo |
| Chain hopping | prebacivanje između mreža |
| Brzi prolaz | prosleđeno 90–110% ukupno primljenog (ništa zadržano), prvi odliv u roku od 60 min |
| Buđenje uspavane adrese | ≥90 dana bez aktivnosti, pa ponovna aktivnost |
| Usitnjavanje | ≥5 sličnih odliva (±10%) u roku od 24h |

Statistička anomalija je **izbačena iz predloga** — ostaje informacija na čvoru, ali više ne predlaže seed adrese.

### 4.4 Rezultat izmene

| Slučaj | Pre | Posle |
|---|---|---|
| „test 1" (631 adresa) | 32 predloga bez objašnjenja | **1 izvor + 5 tačaka pranja**, svaki sa razlogom |
| Demo (25 adresa) | mešavina | **1 izvor + 8 tačaka pranja** |

Ispravno **ispale**: `0xNormalUser1` (zadržao 90%), `0xMixer` (50%), `0xLaunderingHub` (20%) — nijedna nije prolaz.

### 4.5 Prazan rezultat — dva različita značenja

- **Sivo, neutralno** — podaci su mogli da pokažu obrasce, ali nijedan se nije pojavio.
- **Žuto, upozorenje** — *„Nula nalaza zbog oblika evidencije, ne zato što je čisto"* (vidi sekciju 8).

### 4.6 Šta algoritam ne može

**Ne može da pronađe poreklo sredstava.** *„Ova sredstva su ukradena"* nije svojstvo transakcije — ista šema transfera izgleda identično bila to krađa, pozajmica ili kupovina. Forenzičar seed adresu ne izvodi iz grafa nego iz **prijave oštećenog, sudskog naloga ili izveštaja berze**.

Zato dugme nosi naziv **„Predloži za pregled"**, a ne „Pronađi izvore", i **ne popunjava** automatski spisak izvora.

### 4.7 Testiranje

**Demo, kombinovani prikaz:**

1. Izaberi **„Sve transakcije (kombinovano)"** → **„Predloži za pregled"**.
2. U **zelenoj** grupi („Kandidati za izvor") mora biti `0xbad000...` sa razlogom **„Na crnoj listi predmeta"** — potvrđuje da definitivan signal ulazi u pravu kategoriju.
3. U **žutoj** grupi („Tačke pranja") moraju biti peel chain i chain hopping adrese — potvrđuje da se obrasci prepoznaju, ali **ne** nude kao izvori.
4. `0xMixer` i `0xLaunderingHub` **ne smeju** biti u spisku (zadržali 50% odnosno 20%) — potvrđuje da pravilo o zadržavanju radi.
5. Klikni **„Prikaži izvršene provere"** — mora se videti svih 8 provera, uključujući one sa 0 pogodaka. Potvrđuje razliku između „čisto" i „nije ni gledano".

**Pojedinačna on-chain evidencija:**

6. Izaberi `onchain_mainnet_address_...csv` → „Predloži za pregled".
7. Očekivano: **0 predloga** uz **žuto upozorenje** da je razlog oblik evidencije, a ne čistoća.

---

## 5. Funkcije prikaza i analize

Sve su testirane na `demo_taint_dilution.csv`, da brojevi budu proverljivi.

### 5.1 Vremenska traka (reprodukcija toka novca)

**Šta radi:** dugme **„Vremenska traka"** otvara traku ispod grafa koja reprodukuje analizu **transakciju po transakciju, hronološki kroz ceo graf**. Čvorovi i grane se pojavljuju onim redom kojim su se stvarno desili, a boje i procenti se menjaju u hodu.

Kontrole:

| Kontrola | Šta radi |
|---|---|
| **▶ Pusti / ⏸ Pauza** | automatska reprodukcija |
| **⏭ Sledeća zaprljana** | preskače na sledeću transakciju koja stvarno prenosi zaprljana sredstva (preskače čiste) |
| **Klizač** | ručno pomeranje na bilo koju transakciju |
| **Brzina** | 0.25x do 2x |
| **Titlovi** | tekstualni opis trenutne transakcije ispod grafa |
| **Prati kameru** | graf se sam pomera na transakciju koja se upravo dešava |

Pored klizača piše `Transakcija 5 / 8` i datum te transakcije.

**Zašto je korisno:** konačni rezultat pokazuje *stanje*, ali ne i *redosled*. Traka pokazuje kako je novac stvarno tekao — u kom trenutku je koja adresa postala zaprljana i kada se procenat spustio. Za odbranu rada je to najuverljiviji prikaz, jer se vidi da algoritam poštuje hronologiju, a ne da samo sabira.

**Važno pravilo:** kad je traka **isključena**, uvek se prikazuje **konačan, potpun rezultat**. Delimično stanje se vidi samo dok je traka aktivno uključena.

**Testiranje** (demo, seed `0xThief`):

1. Pokreni analizu, klikni **„Vremenska traka"**.
2. Klizač na **1/8**: vidi se samo `0xThief → 0xMixer`, `0xMixer` = **100%**.
3. Klizač na **2/8** (priliv od `0xCleanUser`): `0xMixer` pada na **66.67%** — ovim se potvrđuje da traka prikazuje istorijsko stanje, ne konačno.
4. Klikni **▶ Pusti** → reprodukcija ide sama; **⏸ Pauza** je zaustavlja.
5. Uključi **Titlove** → ispod grafa se ispisuje opis trenutne transakcije.
6. Isključi traku → svi procenti se vraćaju na konačne vrednosti.

### 5.2 Filter praga (sakrivanje čvorova bez doprinosa)

**Šta radi:** dugme **„Sakrij ispod praga"** uklanja sa grafa sve čvorove čiji je taint procenat **na ili ispod** zadatog praga (polje „ispod __ %", podrazumevano 0). Sakrivanjem čvora nestaju i njegove grane.

**Zašto je korisno:** na grafu od 600 adresa gde je 10 zaprljanih, ostalih 590 su vizuelni šum. Prag veći od nule je bitan kad ima mnogo izvora — tada malo šta stoji na tačno 0%, ali mnogo adresa nosi zanemarljiv delić.

**Testiranje** (realni slučaj sa mnogo čvorova):

1. Pokreni analizu → klikni **„Sakrij ispod praga"** sa pragom 0.
2. Očekivano: ostaju samo čvorovi sa procentom > 0.
3. Podigni prag na npr. **5** → nestaju i adrese sa zanemarljivim procentom.
4. Izvezi PDF → **grafički prikaz u izveštaju sadrži tačno ono što je bilo na ekranu**, a u sekciji „Podesavanja prikaza" piše aktivni prag. Ovim se potvrđuje da izveštaj ne prikazuje nešto drugo od onoga što je analitičar video.

### 5.3 Isticanje putanje (klik na čvor)

**Šta radi:** klik na čvor ističe **putanju zaprljanog novca** kroz taj čvor — ostatak grafa se zatamni. Ponovni klik na istaknutu granu pomera fokus na drugi kraj te veze (pošiljalac ↔ primalac), pa se lanac može pratiti korak po korak.

**Testiranje:** klikni `0xMixer` → istaknuta je putanja `0xThief → 0xMixer → 0xExitWallet`, ostalo je zatamnjeno. U PDF izveštaju, pod „Podesavanja prikaza", stoji „Istaknuta putanja: Cvor 0xMixer".

### 5.4 Detalji čvora i provera ko je iza adrese

**Šta radi:** klik na čvor otvara panel sa taint procentom, oznakom da li je izvor, raspodelom po izvorima i spiskom zaprljanih transakcija. Ispod toga panel **„Ko je zapravo iza ove adrese (on-chain podaci)"** dovlači podatke sa Etherscan-a:

| Podatak | Čemu služi |
|---|---|
| ENS ime | čitljiv identitet umesto heksadecimalne adrese |
| Tip adrese | obična adresa (EOA) ili pametni ugovor |
| Poznat entitet | berza / mikser / **⚠ OFAC sankcionisano** |
| Trenutni balans | koliko je sredstava još na adresi |
| Prva aktivnost na lancu | koliko je adresa stara |
| **Izvor sredstava** | prva ikada on-chain transakcija — ko je „upalio" ovaj novčanik |
| **⚠ Dust finansiranje** | novčanik aktiviran sitnim iznosom „taman za gas" — obrazac aktiviranja mule adrese |
| **⚠ Direktna veza sa crnom listom** | izvor finansiranja je sam na crnoj listi |

**Zašto je korisno:** taint procenat kaže *koliko*, ovaj panel kaže *ko i otkud*. „Adresa je otvorena pre tri dana, finansirana sitnim iznosom sa adrese na crnoj listi" je nalaz koji taint sam po sebi ne daje.

**Testiranje:** klikni bilo koji čvor u realnom slučaju → panel se popunjava (traži Etherscan API ključ; bez njega polja ostaju prazna, što je očekivano).

### 5.5 Rangirane liste sa proširivanjem

**Šta radi:** desni panel prikazuje **„Najviše zaprljane adrese"** (prvih 15) i **„Verovatne tačke unovčavanja"** (prvih 5, poznati entiteti na vrhu). Kada ih ima više, dugme „Prikaži još N" otvara ceo spisak. Klik na adresu je pronalazi i fokusira na grafu.

**Zašto su liste ograničene:** bez ograničenja bi na realnom slučaju panel imao stotine redova, a i PDF izveštaj bi postao neupotrebljiv. Prikazuje se ono najvažnije, uz jasan broj koliko je izostavljeno.

**Testiranje:** na realnom slučaju proveri da dugme „Prikaži još" navodi tačan broj preostalih i da klik na adresu skače na taj čvor na grafu.

### 5.6 Filter po pojedinačnom izvoru

**Šta radi:** kad ima više seed adresa, panel **„Filter po izvoru"** omogućava da privremeno isključiš jedan ili više izvora iz prikaza — bez ponovnog pokretanja analize. Procenti, boje, natpisi na granama i isticanje putanje se momentalno preračunaju.

**Zašto je korisno:** kad se dve nezavisne kriminalne aktivnosti spoje na istoj adresi, ukupan procenat kaže „koliko je ukupno prljavo", ali ne i „šta bih video da pratim SAMO prvi upad".

**Testiranje:**

1. Izaberi **oba** `0xHacker1` i `0xHacker2` → pokreni analizu (100%, raspodela 60/40).
2. U panelu klikni `0xHacker2` da ga isključiš.
3. Očekivano: `0xLaunderingHub` i `0xFinalDestination` pokazuju **60%**, `0xHacker2` pada na 0%.
4. Isključi i `0xHacker1` → svi procenti padaju na 0%.
5. **„Prikaži sve izvore"** → vraća se kombinovani prikaz (60%+40%).

### 5.7 Detalji transakcije (klik na granu)

**Šta radi:** klik na strelicu otvara panel **„Detalji transakcije"** sa spiskom **svake pojedinačne transakcije** između te dve adrese — iznos, vreme, tx heš (ako ga evidencija ima), procenat zaprljanosti baš te transakcije, i raspodela po izvorima.

**Zašto je korisno:** natpis na strelici je sažet — ako je bilo više transakcija, prikazuje samo poslednju. Ovaj panel daje revizijski preciznu evidenciju, uključujući tx heš (bitno za povezivanje sa stvarnim blockchain zapisom).

**Testiranje:**

1. Pokreni analizu sa `0xThief` kao seed.
2. Klikni strelicu `0xThief → 0xMixer`.
3. Očekivano: **1 transakcija** — iznos 1000, vreme `2026-03-01T00:00:00Z`, zaprljanost **100%**, identifikator **„n/a"** (demo CSV nema tx heš; kod on-chain podataka tu stoji stvarni heš).

### 5.8 Objašnjenje procenta (kompletna istorija razblaživanja)

**Šta radi:** kad izabereš čvor, sekcija **„Objašnjenje procenta"** prikazuje **baš svaku** transakciju koja je promenila balans te adrese (i zaprljane i čiste), sa procentom **pre** i **posle**. Pad je jasno obeležen („razblaženo ovim prilivom").

**Zašto je korisno:** lista „Zaprljane transakcije" pokazuje samo one koje su DONELE prljav novac — ne objašnjava zašto je procenat nekad OPAO. Pad se dešava kad adresa primi čist novac, što se ranije nigde nije videlo.

**Testiranje** (seed `0xThief`, izabran čvor `0xMixer`) — očekivana istorija tim redosledom:

1. Prima 1000 od `0xThief` → **0% → 100%**.
2. Prima 500 od `0xCleanUser` → **100% → 66.67%** (`-33.33% — razblaženo ovim prilivom`).
3. Šalje 750 ka `0xExitWallet` → **66.67% → 66.67%** (delta 0%) — odliv ne menja procenat, što je očekivano ponašanje haircut modela.

### 5.9 Kretanje procenta kroz vreme (grafikon)

**Šta radi:** iznad „Objašnjenja procenta" prikazuje se mali step-grafikon procenta izabranog čvora kroz sve događaje. Prelaskom mišem preko tačke vidi se pre/posle procenat i suprotna strana; klikom se vremenska traka pomera tačno na tu transakciju.

**Zašto je korisno:** iz tabele se teško vidi *oblik* promene — grafikon odmah pokazuje da li je procenat postepeno opadao (razblaživanje) ili naglo skočio (nov priliv).

**Testiranje:**

1. Izaberi `0xMixer` (scenario iz 5.8).
2. Očekivano: linija koja skače na 100%, pa pada na 66.67% i ostaje ravna — **stepenasta**, ne kosa, jer se procenat ne menja postepeno između transakcija.
3. Klikni na drugu tačku → vremenska traka se uključuje i skače na tu transakciju.

### 5.10 Povezivanje tačaka unovčavanja sa poznatim entitetima

**Šta radi:** svaka adresa u listi „Verovatne tačke unovčavanja" proverava se protiv lokalne offline baze (`known_entities.json` — preko 700 stvarnih Binance/Coinbase/Kraken adresa, Tornado.Cash instanci, OFAC adresa). Pogodak dobija obojenu oznaku („berza: Binance", „⚠ OFAC sankcionisano") i **izdvaja se na vrh liste**.

**Zašto je korisno:** „primio prljav novac i nikad ga nije poslao dalje" ne kaže GDE je novac završio. Kad se adresa poklopi sa imenovanom berzom, to je znatno jači nalaz — postoji operater i jurisdikcija koju organi mogu kontaktirati.

**Testiranje** (treći scenario u demo fajlu; `0x3f5ce5fb...f0be` je stvarna Binance adresa):

1. Klikni `0xExchangeHacker` kao seed → pokreni analizu.
2. `0xExchangeMule` i `0x3f5c...f0be` obe na **100%** — potvrđuje da propagacija kroz više skokova radi.
3. Pored `0x3f5c...f0be` mora stajati plava oznaka **„berza: Binance"** — potvrđuje da je provera pogodila pravu adresu iz baze.
4. Ta adresa mora biti **prva** na listi — potvrđuje da sortiranje po poznatom entitetu radi.

Provera ne zove Etherscan (lokalna pretraga), pa radi trenutno bez obzira na broj adresa.

### 5.11 Filter po izvoru radi i tokom vremenske trake

**Šta radi:** panel „Filter po izvoru" ranije je nestajao čim se uključi vremenska traka — tokom skrolovanja se uvek prikazivao doprinos SVIH izvora. Sada filter radi i tokom trake, sa **istorijski tačnim** procentom na trenutnoj poziciji.

**Tehnički razlog ranijeg ograničenja:** grane (`tainted_hops`) su oduvek imale tačnu raspodelu po izvoru za svaku transakciju. Čvorovi (`node_taint_series`) su čuvali samo zbirni procenat po događaju — backend sada uz svaki događaj čuva i raspodelu po izvorima.

**Testiranje** (seed `0xHacker1` + `0xHacker2`):

1. Pokreni analizu i uključi vremensku traku. Panel „Filter po izvoru" mora ostati vidljiv — potvrđuje da više nije skriven.
2. Isključi `0xHacker2`.
3. Traka na **transakciju 4/8**: `0xLaunderingHub` = **100%** (stiglo samo Hacker1-ovo).
4. Traka na **5/8**: `0xLaunderingHub` = **60%**, a ne 100% — **ključni trenutak** koji dokazuje da filter računa istorijsku raspodelu.
5. Traka na **6/8**: `0xFinalDestination` = **60%**, strelica pokazuje **„60%"** (ne „60%+40%").
6. **„Prikaži sve izvore"** → svuda ponovo 100%.

---

## 6. PDF izveštaj

### 6.1 Sadržaj izveštaja

Izvezeni PDF sadrži, redom: zaglavlje sa podacima o slučaju i valutom, kratku napomenu o metodologiji, kartice sa rezimeom, ključne nalaze, spisak izvora, heševe evidencije, podešavanja prikaza, **grafički prikaz mreže** (tačno onakav kakav je bio na ekranu, uključujući aktivni filter praga), rangiranu listu zaprljanih adresa, raspodelu po izvorima, tačke unovčavanja, **detaljnu istoriju razblaživanja**, detalje izabrane transakcije, zaključak, metodologiju i ograničenja, i **potpis sa pečatom**.

**Ograničenja obima** (da izveštaj ostane čitljiv i na velikom slučaju):

| Sekcija | Ograničenje |
|---|---|
| Tačke unovčavanja | najviše **5** (poznati entiteti prvo, pa po procentu) |
| Istorija razblaživanja | najviše **20** transakcija po adresi |
| Detalji izabrane transakcije | najviše **20** transakcija |

Kad se nešto preseče, ispod stoji napomena koliko je izostavljeno i gde se kompletni podaci nalaze.

**Testiranje — mali slučaj:**

1. Pokreni test iz 5.10 (seed `0xExchangeHacker`) i izvezi PDF.
2. U „Verovatne tacke unovcavanja" tačno **2** reda, **bez** „+X dodatnih" napomene — potvrđuje da napomena izostaje kad nema viška.
3. U „Detaljna istorija razblazivanja" pored `0x3f5c...f0be` mora pisati `[berza: Binance]`, sa **1** redom istorije (Prijem 200, zaprljano 200, 0% → 100%).

**Testiranje — veliki slučaj:**

4. Pokreni analizu na „test 1" i izvezi PDF.
5. Za adresu sa mnogo transakcija mora biti tačno **20** redova + napomena tipa `+ 355 dodatnih transakcija (dalja neto promena procenta: +11.96 p.p.)` — potvrđuje da cap radi na stvarnoj evidenciji.
6. Klikni na granu **pre** izvoza → u PDF-u se pojavljuje „Detalji izabrane transakcije". Bez selektovane grane sekcija se **ne** pojavljuje.

### 6.2 Metodologija i ograničenja u izveštaju

**Šta radi:** PDF na dva mesta objašnjava kako se tumače brojevi:

1. **Kratka napomena na prvoj strani** (uokvireno, **pre svih rezultata**) — model, closed-world, šta znači 0%.
2. **Puna sekcija na kraju** — primenjeni model sa poređenjem (FIFO/LIFO/poison), ograničenje zatvorenog sveta, tumačenje 0%, i šest ostalih ograničenja.

**Zašto je bitno:** izbor modela nije neutralan, a `0%` ne znači „adresa je čista" nego „u ovoj evidenciji nema traga". Te rečenice su ranije postojale samo u komentarima u kodu, a čita ih neko ko kôd nikada neće videti.

| Ograničenje koje izveštaj navodi | Zašto |
|---|---|
| Haircut model | rezultat nije uporediv sa FIFO/LIFO/poison metodom |
| Closed-world | procenat može biti **i viši i niži** od stvarnog |
| 0% ≠ čisto | odsustvo dokaza nije dokaz odsustva |
| Zavisnost od izbora izvora | drugačiji izvori → drugačiji procenti |
| Adresa ≠ identitet | jedna berzanska adresa pripada hiljadama korisnika |
| Valute / cross-chain / zaokruživanje | granice u kojima brojevi imaju smisla |

**Testiranje:**

1. Izvezi PDF. Na **prvoj strani**, ispod „GENERISANO", mora stajati uokvirena napomena sa rečju „haircut" — potvrđuje da čitalac vidi upozorenje **pre** ijednog procenta.
2. Na kraju mora postojati sekcija „Metodologija i ogranicenja" sa četiri podnaslova.
3. Naslov sekcije ne sme ostati sam na dnu strane bez teksta ispod.

> **Napomena o slovima:** u ovom PDF-u nema kvačica („razblazuje" umesto „razblažuje"). PDF taint analize se generiše u browseru (jsPDF), čiji osnovni fontovi ne podržavaju č/ć/š/ž/đ, a ugrađivanje Unicode fonta bi znatno povećalo aplikaciju. Izveštaj aktivnosti se generiše na serveru i **ima** ispravna slova.

### 6.3 Potpis, pečat i provera verodostojnosti

**Šta radi:** klik na „Izvezi PDF izveštaj" više ne izvozi odmah — otvara se prozor u kome se analitičar **potpisuje mišem** na beloj površini (kao u Paint-u), potvrđuje izjavu čekboksom, pa se tek onda generiše dokument.

U PDF se dodaje nova strana **„Potpis i overa"** sa:
- nacrtanim potpisom i imenom analitičara
- **pečatom aplikacije Lusi** (okrugli, crtan vektorski — ostaje oštar na svakom zumu): `LUSI` / `DIGITALNA FORENZIKA` / `OVERENO` / datum
- **kontrolnim brojem** u obliku `LUSI-2026-3VM6-SMK5`
- **otiskom sadržaja** (SHA-256)

**Zašto dve stvari umesto jedne:**

| Element | Šta stvarno dokazuje |
|---|---|
| Nacrtani potpis | **izjava** analitičara — „ja sam izradio ovaj izveštaj i stojim iza njega" |
| Pečat + kontrolni broj + otisak | **da li je sadržaj menjan** — proverava se računski |

Potpis **ne** dokazuje nepromenjenost: to je slika u PDF-u i ostaje netaknut i kad neko izmeni sadržaj. To piše i u samom izveštaju, zajedno sa granicom: provera potvrđuje da se **podaci** poklapaju sa registrovanim, ne da je fajl bajt-po-bajt isti (za to bi trebao kriptografski potpis dokumenta, npr. PAdES — van obima ovog rada).

**Kako radi:** otisak se računa nad podacima koje bi neko mogao osporiti — slučaj, izvori, svi procenti, heševi evidencije, tačke unovčavanja. Registruje se na serveru **pre** generisanja PDF-a (kontrolni broj mora postojati da bi bio odštampan u dokument koji identifikuje), a kasnija provera poredi otisak sa zabeleženim.

**Testiranje:**

1. Pokreni bilo koju analizu i klikni **„Izvezi PDF izveštaj"** — mora se otvoriti prozor sa belom površinom, a ne odmah preuzeti fajl.
2. Klikni „Potpiši i izvezi" bez potpisa — dugme mora biti **onemogućeno**. Potvrđuje da se prazan potpis ne prihvata.
3. Potpiši se mišem, ali **ne** čekiraj izjavu — dugme i dalje onemogućeno. Potvrđuje da su oba uslova obavezna.
4. Čekiraj izjavu → dugme se otključava → izvezi.
5. U PDF-u, na poslednjoj strani, mora biti tvoj potpis, okrugli Lusi pečat, kontrolni broj i otisak.
6. Klikni „Obriši potpis" pa ponovo potpiši — potvrđuje da se površina čisti.

**Testiranje provere (tehnički, preko Swagger-a na `http://localhost:8000/docs`):**

7. `GET /api/v1/reports/verify?code=<kontrolni broj>&content_hash=<otisak iz PDF-a>` → `matches: true`.
8. Isti poziv sa izmenjenim otiskom → `matches: false` uz poruku da je izveštaj izmenjen.
9. Nepostojeći kontrolni broj → `found: false` — razlikuje se od pada provere sadržaja.

Sam potpis se beleži i u logu aktivnosti (akcija `report_signed`) sa kontrolnim brojem.

---

## 7. Valuta evidencije

Haircut model **sabira i deli** iznose — to ima smisla samo ako su svi u istoj jedinici. Fajl koji meša 10 ETH i 500 USDT dao bi procenat koji izgleda precizno, a aritmetički je besmislen.

**Kako je rešeno:**

Opciona kolona u CSV-u: `currency`, `valuta`, `token`, `symbol`, `asset`. Vrednosti se normalizuju (`eth`, `ETH `, `Eth` → `ETH`).

| Stanje fajla | Ponašanje |
|---|---|
| Jedna valuta | prihvata se, valuta se beleži uz evidenciju |
| **Više valuta** | **fajl se odbija** (HTTP 400) uz spisak pronađenih |
| Nema kolone | prihvata se, beleži se `nije navedena` |

Odbijanje je strože od upozorenja jer se greška kasnije ne može ispraviti — **svaki** procenat bi bio pogrešan. Odbijeni fajl se briše sa diska.

**„Nije navedena" nije isto što i ETH.** Pretpostaviti da jeste značilo bi upisati nedokazivu tvrdnju u forenzički zapis.

**On-chain povlačenje** uvek upisuje `ETH`, jer koristi Etherscan-ov `txlist` (samo native transferi), nikad `tokentx`.

**Upozorenje u kombinovanom prikazu:** pojedinačni fajl više ne može mešati valute, ali kombinovani prikaz može spojiti ETH fajl i USDT fajl. Tada se iznad Taint analize pojavljuje žuta traka. U PDF-u, uz `EVIDENCIJA`, stoji polje **`VALUTA`**.

**Testiranje:**

1. Napravi CSV sa kolonom `currency`, jedan red `ETH` i jedan `USDT`. Otpremi — mora biti **odbijen** uz poruku koja navodi obe valute.
2. Ispravi da obe budu `ETH` → mora **proći**, uz evidenciju se beleži `ETH`.
3. Otpremi fajl **bez** kolone → prolazi, valuta ostaje `nije navedena`.
4. Izvezi PDF → u zaglavlju mora postojati red `VALUTA`.

---

## 8. Upozorenje o obliku evidencije

**Problem koji rešava:** evidencija povučena kao istorija jedne adrese sadrži samo njene protivstranke — ne i šta su one radile dalje. U takvim podacima:

- **svaki list izgleda kao „tačka unovčavanja"** — iako su te adrese skoro sigurno slale novac dalje, to prosto nije povučeno
- **sve stoji na 100%** — ništa se nije mešalo, pa nema razblaživanja

Primer sa stvarnim slučajem:

```
176 adresa, 175 veza
i primaju i šalju:   2  (1.1%)
samo primaju:      166  (94.3%)
```

Izveštaj je tu prijavljivao **„162 verovatnih tačaka unovčavanja"** — aritmetički tačno, forenzički pogrešno. To nisu mesta gde je novac napustio mrežu, nego **ivica prikupljenih podataka**.

**Kako je rešeno:** algoritam meri koliko adresa i prima i prosleđuje. Ako ih je manje od 5% (na grafu od bar 20 adresa), i ekran i PDF prikazuju upozorenje:

> ⚠ Evidencija prati novac samo jedan skok. Od 176 adresa, samo 2 i prima i prosleđuje. Zbog toga „tačke unovčavanja" nisu nalaz nego ivica prikupljenih podataka...

**Rešenje za analitičara:** koristiti **kombinovani prikaz** svih dokaza, ili pri povlačenju sa blockchain-a režim **„proširi pošiljaoce"**.

**Testiranje:**

1. Izaberi pojedinačnu on-chain evidenciju (istorija jedne adrese) i pokreni analizu → mora se pojaviti **žuto upozorenje** iznad rezultata.
2. Izvezi PDF → isto upozorenje mora stajati u uokvirenom bloku iznad tabele tačaka unovčavanja.
3. Pokreni analizu na demo slučaju (ima prave lance) → upozorenje se **ne** sme pojaviti.

---

## 9. Klasteri

**Konceptualno su bitni.** Ako jedno lice kontroliše pet adresa, prebacivanje novca između njih nije razblaživanje nego premeštanje u istom džepu. Bez klastera lopov može da razbije sredstva po sopstvenim adresama i svaki korak izgleda kao transfer trećem licu.

**Praktično su na Ethereumu nepouzdani.** Najjača heuristika za klasterovanje (zajednički ulazi u transakciji) dolazi iz Bitcoin UTXO modela i na Ethereumu **ne postoji** — svaka transakcija ima jednog pošiljaoca. Ostaju slabije heuristike koje daju lažne pogotke, a pogrešan klaster je opasniji od nijednog: spojio bi adresu nevinog lica sa osumnjičenim i taint bi „procurio" na njega.

**Odluka:** klasteri se **ne uvode u obračun taint procenta**. Ako se koriste, treba da budu hipoteza koju analitičar potvrđuje, prikazana odvojeno.

Napomena: na slučaju „test 1" detektor klastera vraća **0 klastera**. Ono što na grafu izgleda kao grupe je raspored crtanja, ne otkriveni klasteri.

**Razlikovanje oznaka na grafu** (ranije su se mešale, jer su obe bile isprekidane):

| Oznaka | Boja | Stil |
|---|---|---|
| Anomalija | zlatna | **puna** ivica |
| Klaster | tirkizna | **isprekidan** prsten |

---

## 10. Pronađene i ispravljene greške

### 10.1 Procenat preko 100% (kritično)

U izveštaju je pisalo *„Najveći procenat zaprljanosti: 111.11%"* — nemoguća vrednost u forenzičkom dokumentu.

```
rang 388  0xNormalUser1 ŠALJE 5   → balans -5
rang 389  ŠALJE još 5             → balans -10
rang 392  PRIMA 50 zaprljanih     → 50/40  = 125%
rang 393  PRIMA još 50            → 100/90 = 111.11%
```

**Dve povezane greške:**
1. Adresa je potrošila sredstva koja je imala **pre početka evidencije** → balans u minus → imenilac manji od zaprljanog iznosa.
2. Pri slanju više nego što je primljeno, proporcija je bila veća od 1 → **stvarao se zaprljan iznos ni iz čega** i množio kroz lanac.

**Ispravka:** balans se ne spušta ispod nule, a prosleđeno zaprljano ne može premašiti ono što na adresi postoji. Provereno: najveći procenat na istom slučaju je sada `100.00%`.

### 10.2 Nemoguć procenat u pravilu „brzi prolaz"

Pravilo je prijavljivalo *„prosleđeno 150% primljenog"* jer je sabiralo sve odlive u vremenskom prozoru, uključujući ranije primljena sredstva. Prepisano da meri **koliko je adresa zadržala ukupno**, sa gornjom granicom.

### 10.3 Zabuna u legendi grafa

Anomalija i klaster su oba bili prikazani isprekidano. Sada: anomalija = **zlatna puna** ivica, klaster = **tirkizan isprekidan** prsten.

### 10.4 Šta NIJE bila greška

Sumnjalo se da je peel chain detektor pokvaren jer na „test 1" vraća 0 lanaca, iako postoje adrese nazvane `0xPeelChainStart`. Provereno: detektor radi — na demo podacima nalazi 3 lanca. Na „test 1" tih lanaca nema u obliku koji definicija traži; te adrese hvata pravilo brzog prolaza.

---

## 11. Provera ispravnosti (testovi)

Svi testovi su vidljivi i pokretljivi iz aplikacije — dugme **„Testovi"** (samo administrator). Klikom na test vidi se objašnjenje na srpskom i njegov stvarni izvorni kod.

### 11.1 Šta je pokriveno

| Fajl | Testova | Pokriva |
|---|---|---|
| `test_taint_analysis.py` | 24 | haircut matematika, raspodela po izvorima, podaci za vremensku traku, hronologija, seed adrese, zaprljani skokovi, oblik evidencije |
| `test_seed_suggestion.py` | 17 | svako pravilo za predlog čvorova, razdvajanje izvor/pranje, prazan rezultat |
| `test_currency_validation.py` | 8 | prepoznavanje valute, odbijanje pomešanih, kompatibilnost sa starim fajlovima |
| `test_report_registry.py` | 11 | otisak sadržaja, kontrolni broj, provera izmenjenog izveštaja |

**Ukupno 60 testova za taint analizu** (uz 24 za izveštaj aktivnosti — vidi `BLOCKCHAIN-UVOZ.md`).

### 11.2 Zaštite od povratka ispravljenih grešaka

| Test | Šta sprečava |
|---|---|
| „Procenat nikada ne prelazi 100%" | povratak greške 111.11% |
| „Slanje više nego što je primljeno ne stvara novi taint" | umnožavanje zaprljanog iznosa kroz lanac |
| „Adresa koja zadrži većinu sredstava se ne označava" | mikser pogrešno označen kao prolaz |
| „Adresa koja pošalje više nego što je primila se ne označava" | nemoguć procenat „150% prosleđeno" |
| „Svaki zapis istorije nosi svoju raspodelu po izvorima" | filter po izvoru tiho netačan tokom trake |
| „Izmenjen izveštaj pada na proveri" | neotkrivena izmena dokumenta |

### 11.3 Dokaz da testovi zaista hvataju greške

Test koji prolazi i na ispravnom i na pokvarenom kodu je bezvredan. Algoritam je namerno pokvaren tri puta:

| Namerna greška | Rezultat |
|---|---|
| Uklonjena haircut proporcija | 3 testa pala |
| Uklonjeno hronološko sortiranje | 4 testa pala |
| Uklonjena raspodela po izvorima po rangu | 1 test pao |

Posle svake provere kôd je vraćen u prvobitno stanje.

### 11.4 Kako pokrenuti

**Iz aplikacije:** „Testovi" → **„Pokreni sistemske testove"**.

**Iz terminala:**

```bash
docker compose exec backend python -m pytest tests/ -v
```

---

## 12. Gde se šta nalazi u kodu

| Šta | Fajl |
|---|---|
| Haircut algoritam | `backend/app/analytics/plugins/taint_analysis.py` |
| Predlog čvorova (pravila) | `backend/app/analytics/seed_suggestion.py` |
| Registar izveštaja (potpis/overa) | `backend/app/services/report_registry.py` |
| Provera valute | `backend/app/analytics/ingestion.py` |
| Peel chain detektor | `backend/app/analytics/plugins/peel_chains.py` |
| Baza poznatih entiteta | `backend/app/services/known_entities.json` |
| Testovi | `backend/tests/` |
| Prikaz i PDF | `frontend/src/app/features/taint-analysis/` |

**Rute:**

| Ruta | Namena |
|---|---|
| `POST /api/v1/cases/{id}/analytics/run` | pokretanje analize |
| `GET /api/v1/cases/{id}/seed-suggestions` | predlog čvorova |
| `POST /api/v1/reports/register` | registracija izveštaja pri potpisivanju |
| `GET /api/v1/reports/verify` | provera verodostojnosti |
