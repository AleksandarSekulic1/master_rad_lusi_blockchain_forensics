# Graf — 6 analiza koje ga obogaćuju (ukratko)

Stranica **Graf** prikazuje evidenciju slučaja kao čvorove (adrese) i grane (transakcije).
Pored samog crtanja, šest plugin-ova iz `backend/app/analytics/plugins/` upisuje dodatne
oznake na svaki čvor/granu — sve se pokreću zajedno preko `POST /cases/{id}/analytics/run`
(`backend/app/analytics/plugins/manager.py`), uvek u ovom redosledu:

```
blacklist_check → taint_analysis → wallet_clustering → risk_scoring → peel_chains → chain_hopping → anomaly_detection
```

Redosled nije proizvoljan: **risk_scoring** čita `blacklist_flag` koji je tek postavio
**blacklist_check** (blacklistovana adresa automatski dobija skor 100, a njeni susedi na
1-2 koraka dobijaju bonus poena) — da su zamenjeni, risk scoring ne bi imao šta da pročita.

Rezultat svake analize: broj se vidi na **Dashboard-u** (kartice "Entiteti visokog rizika",
"Klasteri", "Na crnoj listi" — samo za ove tri), a **detalji po čvoru** se vide na **Graf**
stranici klikom na čvor, u panelu "Rizik i oznake" / "Identitet i klaster".

---

## 1. Blacklist check

**Kako radi:** poredi svaku adresu iz grafa sa malom, hardkodovanom listom
(`blacklist_check.py`, `DEFAULT_BLACKLIST_ENTRIES`) — trenutno 3 simulirane demo adrese plus
jedna **stvarna** OFAC-sankcionisana Bitcoin adresa (Garantex Europe OÜ — vidi
`BITCOIN-UVOZ.md`). **Ne čita** `known_entities.json` (to je posebna, veća lista za
enrichment/lookup na drugim mestima) — ovo je namerno mala, brzo proveriva lista.

**Zašto je potrebno:** direktan, nedvosmislen dokaz — ako se adresa poklopi sa sankcionom
listom, to nije heuristika nego činjenica. Ostale analize (risk scoring, seed suggestion)
se oslanjaju na ovaj rezultat kao najjači signal koji imaju.

**Gde se vidi:** Dashboard kartica "Na crnoj listi"; na Grafu — panel "Crne liste" (izvor +
naziv), čvor dobija upozoravajuću ivicu.

---

## 2. Risk scoring

**Kako radi:** svakoj adresi dodeljuje skor 0-100. Blacklistovana adresa → automatski 100.
Ostale se boduju zbirom faktora: blizina blacklistovanoj adresi (1 hop = +35, 2 hop-a = +15),
"pritisak" prometa (logaritamski, u odnosu na najveći promet u grafu, do 20 poena), gustina
aktivnosti (do 15), veliki broj transakcija u kratkom vremenskom prozoru (do 15), broj
različitih kontrastrana (do 10), i uravnotežen in/out obrazac (+5). Skor ≥70 = "visok rizik".

**Zašto je potrebno:** analitičar sa slučajem od stotine adresa ne može ručno da pregleda
svaku — skor daje prioritet, na šta prvo pogledati. Nije dokaz krivice, nego triage alat.

**Gde se vidi:** Dashboard kartica "Entiteti visokog rizika"; na Grafu — broj u gornjem delu
panela čvora, plus razlog za svaki poen ("volume pressure 20/20" itd.).

---

## 3. Wallet clustering

**Kako radi:** dve heuristike. (1) **multi_input** — dve različite adrese šalju **isti
iznos, istoj adresi, u istom trenutku** (ili dele isti transaction hash ako evidencija to
nosi) → tretiraju se kao isti vlasnik, po istom principu kao Bitcoin-ova *common input
ownership heuristic*, samo primenjeno na dve odvojene uplate umesto na ulaze jedne
transakcije. (2) **behavioral_similarity** — dve adrese imaju ≥75% preklapanja u skupu
suseda (šalju/primaju od istih trećih adresa) → verovatno isti vlasnik, iako nemaju
zajedničku transakciju.

**Zašto je potrebno:** ako jedno lice kontroliše pet adresa, prebacivanje novca među njima
nije "razblaživanje" nego premeštanje u istom džepu — bez klastera bi svaki takav transfer
lažno izgledao kao predaja trećem licu. (Napomena: `TAINT-ANALIZA.md` odeljak 9 objašnjava
zašto se klasteri **ne** koriste u obračunu taint procenta na Ethereum-u — heuristika je
slabija bez pravog UTXO multi-input signala, pa je pogrešan klaster rizičniji od nijednog.)

**Gde se vidi:** Dashboard kartica "Klasteri"; na Grafu — panel "Klaster" (ID + broj
članova), čvorovi istog klastera dobijaju isprekidan prsten iste boje.

---

## 4. Peel chains

**Kako radi:** traži adresu koja primi veći iznos (podrazumevano ≥100) i **u istom
"dahu"** (podesiv vremenski prozor) ga podeli na dva izlaza — jedan veliki "nastavak" i
jedan manji "peel" (podrazumevano ≤35% primljenog). Prati taj nastavak dalje kroz sledeće
adrese dok obrazac traje (min. 3 koraka da bi se prijavio kao lanac); svaki peel-korak mora
propustiti ≥65% iznosa dalje da bi se računao kao deo istog lanca.

**Zašto je potrebno:** "guljenje" (peeling) manjih iznosa iz glavne sume je jedan od
najpoznatijih obrazaca pranja novca (klasično na Bitcoin-u, ali prenosivo i na Ethereum) —
automatski prepoznat lanac usmerava analitičara pravo na njega umesto da ga traži ručno
kroz stotine grana.

**Gde se vidi:** na Grafu — panel "Peel uloga" (seed/relay/terminal) + broj poverenja
(confidence), čvorovi lanca dobijaju poseban oblik (dijamant) na grafu.

---

## 5. Chain hopping

**Kako radi:** označava čvor kao "bridge" ili "swap" tačku na dva načina — (1) ime/adresa
sadrži ključnu reč (`bridge`, `hop`, `wormhole`... za bridge; `swap`, `dex`, `router`,
`uniswap`, `exchange`... za swap) ili je na konfigurisanoj listi poznatih adresa, ili (2)
bez imena — čisto po obrascu ponašanja: ≥2 ulazna, ≥1 izlazni tok, ≥2 transakcije, i
odnos izlaz/ulaz između 0.5 i 1.5 (novac "prođe kroz", ne zadržava se).

**Zašto je potrebno:** mostovi i menjačnice su tačke gde trag često postaje teže prativ (roba
menja oblik/lanac) — obeležavanje tih tačaka govori analitičaru gde je trag "skočio" i gde
treba tražiti nastavak na drugom lancu/formatu.

**Gde se vidi:** na Grafu — panel "Skok lanca" (bridge/swap + razlog prepoznavanja).

---

## 6. Anomaly detection

**Kako radi:** za svaku adresu računa 16 numeričkih osobina (stepen ulaza/izlaza, broj
transakcija, ukupan promet, broj kontrastrana, raspon aktivnosti, odnos ulaz/izlaz,
prosečan/najveći iznos, standardna devijacija iznosa, "skok" najvećeg iznosa u odnosu na
prosek, stopa aktivnosti) i propušta ih kroz **Isolation Forest** (sklearn) — model koji uči
šta je "tipično" u OVOM grafu i izdvaja statističke outliere, bez unapred zadatih pravila.

**Zašto je potrebno:** za razliku od ostalih pet analiza (koje traže POZNAT obrazac), ova
hvata **nepoznato** — ponašanje koje odudara od ostatka grafa a ne uklapa se ni u jedno od
gornjih pravila. Koristan poslednji sloj za slučajeve koje nijedna imenovana heuristika nije
pokrila.

**Zašto nema tekstualno polje u panelu:** rezultat je pokazatelj, ne kategorija — prikazuje
se vizuelno (puna zlatna ivica na čvoru + stavka u legendi grafa), da se ne meša sa
kategoričkim oznakama iz ostalih pet analiza.

**Gde se vidi:** na Grafu — puna zlatna ivica oko čvora + legenda.
