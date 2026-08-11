# Taint analiza — algoritam, ograničenja i provera ispravnosti

Ovaj dokument opisuje **kako radi sam algoritam**: model praćenja sredstava, algoritam za predlog čvorova, greške koje su pronađene i ispravljene, i kako se sve to proverava.

Uputstva za korišćenje pojedinačnih funkcija u interfejsu (filteri, vremenska traka, PDF izveštaj) nalaze se u `BLOCKCHAIN-UVOZ.md`, sekcije 6–11.

---

## 1. Model praćenja: proporcionalni („haircut")

Kada se na jednoj adresi pomešaju zaprljana i čista sredstva, **svaki naredni odliv nosi isti procenat zaprljanosti kao balans sa kojeg je poslat**.

```
adresa primi 1000 ukradenih + 500 čistih  →  balans je 66.67% zaprljan
svaka naredna isplata sa te adrese nosi tačno 66.67% zaprljanih sredstava
```

### Zašto baš taj model

Izbor modela **nije neutralan** — isti podaci daju različite procente pod različitim modelima, pa se rezultat ne može direktno porediti sa nalazom dobijenim drugom metodom:

| Model | Pretpostavka | Posledica |
|---|---|---|
| **Proporcionalni (haircut)** | odliv nosi srazmeran udeo | izabran ovde |
| FIFO | prvi primljeni novac prvi odlazi | koristi se u nekim jurisdikcijama zbog sudske prakse |
| LIFO | poslednji priliv prvi odlazi | obrnuta pretpostavka |
| Poison / taint-by-contact | svaki dodir = trajno 100% | obuhvata ogroman broj nevezanih adresa |

Proporcionalni je izabran jer ne širi sumnju na adrese čija je stvarna izloženost zanemarljiva, a zadržava trag i posle višestrukog mešanja.

### Ključne osobine koje algoritam mora da poštuje

1. **Čist priliv razblažuje** procenat srazmerno.
2. **Odliv ne menja procenat pošiljaoca** — odnosi i prljavo i čisto u istoj srazmeri.
3. **Hronologija ide kroz ceo graf**, ne granu po granu — transakcije se obrađuju po stvarnom vremenu.
4. **Raspodela po izvorima putuje sa novcem** — zna se ne samo „koliko je prljavo" nego i „čije je".
5. **Procenat nikada ne prelazi 100%.**

Svaka od ovih osobina je pokrivena testom (sekcija 5).

---

## 2. Algoritam za predlog čvorova

### 2.1 Šta je bilo pogrešno u prvoj verziji

Prva verzija je koristila detektor statističkih anomalija (`IsolationForest`) i uzimala sve što on označi. Izmereno na stvarnim podacima, to je bilo **neupotrebljivo za taint analizu**:

**Broj predloga je bio izmišljen.** Formula za parametar `contamination` uvek daje `0.05`, bez obzira na broj adresa:

```python
contamination = min(0.2, max(0.05, 1.0 / max(len(training_frame), 20)))
```

`max(0.05, ...)` uvek pobedi, pa je rezultat konstantno 5%. Od 631 adrese uvek ~32 predloga — bez obzira da li ima 2 sumnjive adrese ili 200.

**Odziv na podacima sa poznatim odgovorom: 1 od 4.** Na `demo_taint_dilution.csv`, gde su izvori namerno poznati:

| Prava seed adresa | Rezultat | Ocena rizika |
|---|---|---|
| `0xExchangeHacker` | pronađen | 17 |
| `0xThief` | **propušten** | 21 |
| `0xHacker1` | **propušten** | 20 |
| `0xHacker2` | **propušten** | 19 |

**Uzrok nije podešavanje nego struktura problema:** izvor ukradenog novca po pravilu izgleda dosadno — lopovljev novčanik obično uradi jednu stvar, pošalje novac dalje. To je najmanje neuobičajeno ponašanje koje postoji. Mikser, koji prima od više strana, statistički štrči — pa je dobio ocenu 54, a lopovi 19–21.

**Predlagao je najveće adrese.** Na slučaju „test 1", od 32 predloga **21 je bio među 32 adrese sa najvećim prometom**. Na pravom Ethereumu to su berze i pametni ugovori. Postaviti berzanski novčanik kao „izvor ukradenog novca" znači označiti sve njegove isplate kao zaprljane — pogrešan zaključak razmera hiljada adresa.

**Granica je bila proizvoljna.** `0xMixer` (ocena 54) je ušao, `0xLaunderingHub` (53) nije. Jedan poen.

### 2.2 Odbačeno rešenje (i zašto)

Razmatrano je determinističko pravilo: *„izvor je adresa koja šalje a nikad ne prima unutar dokaza"*. Testirano pre primene:

| Podaci | Rezultat |
|---|---|
| Demo scenario (11 adresa) | **4/4 — nijedan propust** |
| Realni „test 1" (631 adresa) | **621 od 631 adresa (98.4%)** |

Na demou savršeno, na pravim podacima beskorisno — jer je realna evidencija povučena kao istorija jedne adrese, pa svi ostali u njoj figurišu samo kao pošiljaoci. Pravilo je odbačeno.

### 2.3 Šta algoritam radi sada

**Princip:** prva verzija je prvo odlučila *koliko* adresa da izdvoji, pa tražila koje — zato je uvek nešto „lupila", morala je da popuni kvotu. Sada se prvo traže konkretni obrasci, pa koliko se nađe, toliko. **Ništa se ne predlaže bez navedenog razloga.**

Predlozi su podeljeni po ulozi, jer nisu zamenjivi:

**Kandidati za izvor** — jedina osnova za seed adresu:

| Provera | Definicija |
|---|---|
| Crna lista predmeta | adresa je označena kao poznato zlonamerna u samim podacima |
| OFAC sankcionisane | poklapanje sa lokalnom bazom sankcionisanih adresa |

**Tačke pranja** — važni nalazi, ali **pogrešni kao seed**:

| Provera | Definicija |
|---|---|
| Poznati mikseri | poklapanje sa bazom miksera (Tornado.Cash i sl.) |
| Peel chain | lanac u kome se pri svakom koraku odvaja mali deo, ostatak ide dalje |
| Chain hopping | prebacivanje sredstava između mreža |
| Brzi prolaz | prosleđeno 90–110% ukupno primljenog (ništa nije zadržano), prvi odliv u roku od 60 min |
| Buđenje uspavane adrese | ≥90 dana bez aktivnosti, pa ponovna aktivnost |
| Usitnjavanje | ≥5 sličnih odliva (±10%) u roku od 24h |

Statistička anomalija je **izbačena iz predloga** — ostaje kao informacija na čvoru, ali više ne predlaže seed adrese.

### 2.4 Rezultat izmene

| Slučaj | Pre | Posle |
|---|---|---|
| „test 1" (631 adresa) | 32 predloga, bez objašnjenja | **1 izvor + 5 tačaka pranja**, svaki sa razlogom |
| Demo (25 adresa) | mešavina | **1 izvor + 8 tačaka pranja** |

Adrese koje su ispravno **ispale**: `0xNormalUser1` (zadržao 90% sredstava), `0xMixer` (50%), `0xLaunderingHub` (20%) — nijedna nije prolaz.

Primer izlaza:

```
KANDIDAT ZA IZVOR
  0xbad000...        Na crnoj listi predmeta

TAČKE PRANJA (nisu izvori)
  0xPeelChainStart   Brzi prolaz — primljeno 200.0000, prosleđeno 199.8000 (zadržano 0.1%)
  0xPeelOstatak1     Brzi prolaz — primljeno 196.8000, prosleđeno 196.6000 (zadržano 0.1%)
  0xArbitrumBridge   Chain hopping
```

### 2.5 Prazan rezultat — dva različita značenja

Kada nema nijednog predloga, aplikacija razlikuje dva potpuno različita zaključka:

- **Sivo, neutralno** — podaci su mogli da pokažu obrasce, ali nijedan se nije pojavio.
- **Žuto, upozorenje** — *„Nula nalaza zbog oblika evidencije, ne zato što je čisto."*

Drugi slučaj nastaje kada je evidencija povučena kao **istorija jedne adrese**. Primer sa „test 1", pojedinačna on-chain evidencija:

```
620 adresa, 619 veza
adresa koje i primaju i šalju:  0
čvorište:  619 ulaznih, 0 izlaznih
```

Svih 619 adresa pošalje jednom čvorištu i trag se prekida. Lančani obrasci (brzi prolaz, peel chain, usitnjavanje) **strukturno ne mogu da se pojave** — ne zbog pragova, nego zato što nema šta da se nađe. Bez ovog upozorenja analitičar bi iz golog „nema nalaza" zaključio da je novac čist, a podaci na to pitanje uopšte ne mogu da odgovore.

Rešenje: koristiti **kombinovani prikaz** svih dokaza, ili pri povlačenju sa blockchain-a režim **„proširi pošiljaoce"**.

### 2.6 Šta algoritam ne može

**Ne može da pronađe poreklo sredstava.** *„Ova sredstva su ukradena"* nije svojstvo transakcije — ista šema transfera izgleda identično bila to krađa, pozajmica ili kupovina. Forenzičar seed adresu ne izvodi iz grafa nego iz **prijave oštećenog, sudskog naloga ili izveštaja berze**.

Seed adrese moraju doći iz predmeta. Algoritam ih može dopuniti, ali ne zameniti. Zato dugme nosi naziv **„Predloži za pregled"**, a ne „Pronađi izvore", i **ne popunjava** automatski spisak izvora.

---

## 3. Pronađene i ispravljene greške

### 3.1 Procenat preko 100% (kritično)

U izveštaju za „test 1" pisalo je *„Najveći procenat zaprljanosti: 111.11%"* — matematički nemoguća vrednost u forenzičkom dokumentu.

Hronologija koja je do toga dovela:

```
rang 388  0xNormalUser1 ŠALJE 5   → balans -5
rang 389  ŠALJE još 5             → balans -10
rang 392  PRIMA 50 zaprljanih     → 50/40  = 125%
rang 393  PRIMA još 50            → 100/90 = 111.11%
```

**Uzrok — dve povezane greške:**
1. Adresa je potrošila sredstva koja je imala **pre početka evidencije**. Model to ne vidi, pa je balans otišao u minus i imenilac postao manji od zaprljanog iznosa.
2. Pri slanju više nego što je primljeno, proporcija je bila veća od 1 — **stvarao se zaprljan iznos ni iz čega** i množio kroz lanac.

**Ispravka:** balans se ne spušta ispod nule (višak potiče od sredstava van evidencije, koja su nepoznata a ne negativna), a prosleđeno zaprljano ne može premašiti ono što na adresi stvarno postoji.

**Provera posle ispravke:** najveći procenat na istom slučaju je sada `100.00%`, nijedna adresa preko 100%.

### 3.2 Nemoguć procenat u pravilu „brzi prolaz"

Pravilo je prijavljivalo *„prosleđeno 150% primljenog"* jer je sabiralo sve odlive u vremenskom prozoru, uključujući i sredstva primljena ranije. Prepisano da meri **koliko je adresa zadržala ukupno**, sa gornjom granicom — adresa koja pošalje znatno više nego što je primila troši sopstvena sredstva, što nije prolaz.

### 3.3 Zabuna u legendi grafa

Anomalija i klaster su oba bili prikazani **isprekidano**, pa se zlatni prsten redovno čitao kao klaster. To su dva potpuno različita nalaza — jedan je statistička naznaka, drugi tvrdnja da dve adrese pripadaju istom licu.

Sada: **anomalija = zlatna puna ivica**, **klaster = tirkizan isprekidan prsten**. Isprekidano znači isključivo klaster.

### 3.4 Šta NIJE bila greška

Sumnjalo se da je peel chain detektor pokvaren jer na „test 1" vraća 0 lanaca, iako postoje adrese nazvane `0xPeelChainStart`. Provereno: detektor radi ispravno — na demo podacima nalazi 3 lanca. Na „test 1" tih lanaca prosto nema u obliku koji definicija traži; te adrese hvata pravilo brzog prolaza.

---

## 4. Klasteri u taint analizi

**Konceptualno su bitni.** Ako jedno lice kontroliše pet adresa, prebacivanje novca između njih nije razblaživanje nego premeštanje u istom džepu. Bez klastera lopov može da razbije sredstva po sopstvenim adresama i svaki korak izgleda kao transfer trećem licu — procenat pada, a novac nije nigde otišao.

**Praktično su na Ethereumu nepouzdani.** Najjača heuristika za klasterovanje (zajednički ulazi u transakciji) dolazi iz Bitcoin UTXO modela i na Ethereumu **ne postoji** — svaka transakcija ima jednog pošiljaoca. Ostaju slabije, ponašajne heuristike koje daju lažne pogotke, a pogrešan klaster je opasniji od nijednog: spojio bi adresu nevinog lica sa osumnjičenim i taint bi „procurio" na njega.

**Odluka:** klasteri se **ne uvode u obračun taint procenta**. Ako se koriste, treba da budu hipoteza koju analitičar potvrđuje, prikazana odvojeno — nikad tiho spojena u računicu, jer bi se time u izveštaj uvela nedokaziva pretpostavka koja menja sve procente.

Napomena: na slučaju „test 1" detektor klastera vraća **0 klastera**.

---

## 5. Provera ispravnosti (testovi)

Svi testovi su vidljivi i pokretljivi iz aplikacije — dugme **„Testovi"** u meniju (samo administrator). Detaljno o samoj stranici: `BLOCKCHAIN-UVOZ.md`, sekcija 10.

### 5.1 Šta je pokriveno

| Fajl | Testova | Pokriva |
|---|---|---|
| `test_taint_analysis.py` | 21 | haircut matematiku, raspodelu po izvorima, podatke za vremensku traku, hronologiju, ponašanje seed adresa, zaprljane skokove |
| `test_seed_suggestion.py` | 17 | svako pravilo za predlog čvorova, razdvajanje izvor/pranje, prazan rezultat |
| `test_activity_report.py` | 24 | izveštaj aktivnosti (vidi `BLOCKCHAIN-UVOZ.md` sekciju 11) |

**Ukupno 62 testa.**

### 5.2 Zaštite od povratka ispravljenih grešaka

Za svaku grešku iz sekcije 3 postoji test koji je čuva:

| Test | Šta sprečava |
|---|---|
| „Procenat nikada ne prelazi 100%" | povratak greške 111.11% |
| „Slanje više nego što je primljeno ne stvara novi taint" | umnožavanje zaprljanog iznosa kroz lanac |
| „Adresa koja zadrži većinu sredstava se ne označava" | mikser pogrešno označen kao prolaz |
| „Adresa koja pošalje više nego što je primila se ne označava" | nemoguć procenat „150% prosleđeno" |
| „Svaki zapis istorije nosi svoju raspodelu po izvorima" | filter po izvoru tiho netačan tokom vremenske trake |

### 5.3 Dokaz da testovi zaista hvataju greške

Test koji prolazi i na ispravnom i na pokvarenom kodu je bezvredan. Algoritam je namerno pokvaren tri puta i provereno je da testovi to primete:

| Namerna greška | Rezultat |
|---|---|
| Uklonjena haircut proporcija | 3 testa pala |
| Uklonjeno hronološko sortiranje | 4 testa pala |
| Uklonjena raspodela po izvorima po rangu | 1 test pao |

Posle svake provere kôd je vraćen u prvobitno stanje i provereno da ponovo prolazi.

### 5.4 Kako pokrenuti

**Iz aplikacije:** „Testovi" → **„Pokreni sistemske testove"**. Klik na test otvara objašnjenje na srpskom i njegov stvarni izvorni kod.

**Iz terminala:**

```bash
docker compose exec backend python -m pytest tests/ -v
```

---

## 6. Ručno testiranje algoritma

### 6.1 Predlog čvorova — očekivani rezultati

**Demo (`demo_taint_dilution.csv`, kombinovani prikaz):**

1. Otvori Taint analizu, izaberi **„Sve transakcije (kombinovano)"**, klikni **„Predloži za pregled"**.
2. U zelenoj grupi („Kandidati za izvor") mora biti `0xbad000...` sa razlogom **„Na crnoj listi predmeta"** — ovim se potvrđuje da definitivan signal ulazi u pravu kategoriju.
3. U žutoj grupi („Tačke pranja") moraju biti peel chain adrese i chain hopping adrese — ovim se potvrđuje da se obrasci pranja prepoznaju, ali **ne** nude kao izvori.
4. `0xMixer` i `0xLaunderingHub` **ne smeju** biti u spisku — zadržali su 50% odnosno 20% sredstava, dakle nisu prolaz. Ovim se potvrđuje da pravilo o zadržavanju radi.
5. Klikni **„Prikaži izvršene provere"** — mora se videti svih 8 provera, uključujući one sa 0 pogodaka. Ovim se potvrđuje razlika između „čisto" i „nije ni gledano".

**Realna evidencija (jedna on-chain istorija):**

6. Izaberi pojedinačnu evidenciju `onchain_mainnet_address_...csv` i klikni „Predloži za pregled".
7. Rezultat mora biti **0 predloga**, ali sa **žutim upozorenjem** da je razlog oblik evidencije, a ne čistoća — ovim se potvrđuje da se dva različita značenja praznog rezultata ne mešaju.

### 6.2 Provera da procenat ne prelazi 100%

8. Pokreni analizu na „test 1" sa kombinovanom evidencijom i seed adresama koje uključuju `0xbad000...`.
9. U tabeli „Najviše zaprljane adrese" **nijedan** procenat ne sme biti veći od 100% — ovim se potvrđuje ispravka iz 3.1. (Pre ispravke je `0xNormalUser1` prikazivao 111.11%.)

### 6.3 Provera legende grafa

10. Otvori Graf, pronađi čvor sa zlatnom ivicom — ivica mora biti **puna**, a u legendi mora pisati „zlatna puna ivica". Isprekidan prsten (tirkizan) sme označavati isključivo klaster. Ovim se potvrđuje ispravka iz 3.3.

---

## 7. Gde se šta nalazi u kodu

| Šta | Fajl |
|---|---|
| Haircut algoritam | `backend/app/analytics/plugins/taint_analysis.py` |
| Predlog čvorova (pravila) | `backend/app/analytics/seed_suggestion.py` |
| Ruta za predlog | `GET /api/v1/cases/{case_id}/seed-suggestions` |
| Peel chain detektor | `backend/app/analytics/plugins/peel_chains.py` |
| Baza poznatih entiteta | `backend/app/services/known_entities.json` |
| Testovi | `backend/tests/` |
| Prikaz na ekranu | `frontend/src/app/features/taint-analysis/` |
