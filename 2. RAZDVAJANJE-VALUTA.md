# Automatsko razdvajanje evidencije po valuti

## Problem

Taint model sabira i deli kolonu `amount` da bi izračunao procenat zaprljanosti. Ako jedan
CSV meša valute (npr. ETH i USDT u istom fajlu), taj zbir je aritmetički besmislen — iznosi
različitih valuta se sabiraju kao da su isti broj.

Ranije je aplikacija takav upload jednostavno **odbijala** (HTTP 400) i tražila od korisnika
da ručno razdvoji fajl u posebne CSV-ove, po jednu valutu u svakom, pa da ih ponovo uploaduje
jedan po jedan.

## Rešenje

Upload sa više valuta se sada **automatski deli** na serveru — jedan evidencijski fajl po
valuti — bez ikakve ručne intervencije korisnika.

## Backend

- **`backend/app/analytics/ingestion.py`** — nova funkcija `split_by_currency(file_path)`:
  čita sirov CSV, prepoznaje kolonu valute (`currency`/`valuta`/`token`/`symbol`/`asset`,
  case-insensitive), i grupiše **originalne redove** (netaknute kolone, isti header) po
  velikim slovima normalizovanoj valuti. Redovi koji uopšte ne deklarišu valutu idu u
  poseban `None`-bucket (fajl `..._UNSPECIFIED.csv`) — ne pripisuju se nasumično nekoj od
  ostalih valuta.

- **`backend/app/api/routes/upload.py`** — ruta `POST /api/v1/upload/csv`: kad
  `detect_currencies()` nađe više od jedne valute, umesto `raise HTTPException(400, ...)`
  poziva se nova `_split_and_store_by_currency()`. Ona za svaki razdvojeni deo ponavlja isti
  postupak kao za običan upload: snimanje, SHA-256 heš, upis u evidenciju slučaja
  (`store_case_evidence` + `append_evidence`), upis u dnevnik revizije (`csv_upload_split`).
  Originalni pomešani fajl se **nikad ne čuva** kao dokaz — samo njegovi razdvojeni delovi.

  Odgovor rute sad ima oblik:
  ```json
  {
    "split": true,
    "source_file_name": "original.csv",
    "files": [
      { "file_name": "..._ETH.csv", "currency": "ETH", "rows_total": 2, "sha256": "...", "evidence": {...} },
      { "file_name": "..._USDC.csv", "currency": "USDC", "rows_total": 1, "sha256": "...", "evidence": {...} }
    ],
    "rows_total": 3,
    "case": {...}
  }
  ```

## Frontend

- **`models/blockchain-forensics.models.ts`** — `UploadCsvResponse` proširen sa
  `split?`, `source_file_name?`, `files?: UploadCsvSplitFile[]`; stara polja
  (`file_name`, `sha256`, `audit_log`, `preview`) postala opciona jer ih split odgovor
  ne popunjava.
- **`dashboard.component.ts`** — `uploadEvidence()` grana poruku statusa na osnovu
  `uploadResult.split`; nova `splitSummaryLabel()` sastavlja sažetak tipa:
  *„Evidencija je sadržala 3 valute — automatski razdvojena u 3 fajla: ETH (2), USDC (1),
  DAI (1)."*
- **`dashboard.component.html`/`.scss`** — novi blok `.upload-split-meta` koji nabraja svaki
  nastali fajl (valuta, broj redova, SHA-256), prikazuje se umesto starog jednofajlnog
  `.upload-meta` bloka kad je `uploadResult.split === true`.

Posle uploada se, kao i pre, učitava kombinovani graf/analitika preko svih dokaza slučaja
zajedno — razlika je samo u tome što Taint analiza po pojedinačnom dokazu sad ispravno vidi
tačno jednu valutu po fajlu.

## Testirano

- `python -m pytest` u `backend/` — 289 testova prolazi.
- Ručni smoke test preko `TestClient`-a: CSV sa ETH/USDC/DAI/bez-valute redovima →
  4 evidencijska fajla, svaki sa tačnim brojem redova.
