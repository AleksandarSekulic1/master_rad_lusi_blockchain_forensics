from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.analytics.ingestion import clean_transaction_csv, detect_currencies
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.paths import RAW_DIR
from app.services.case_management import CaseClosedError, append_evidence, require_open_case, store_case_evidence


router = APIRouter(prefix='/upload', tags=['upload'])


@router.post('/csv')
async def upload_csv(
    file: UploadFile = File(...),
    case_id: str = Form(...),
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    user = str(current_user['username'])
    if not file.filename:
        raise HTTPException(status_code=400, detail='CSV fajl mora imati naziv.')

    if not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail='Dozvoljen je samo CSV fajl.')

    try:
        case = require_open_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseClosedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_dir = RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    safe_name = Path(file.filename).name
    stored_name = f'{timestamp}_{safe_name}'
    stored_path = raw_dir / stored_name

    with stored_path.open('wb') as destination:
        shutil.copyfileobj(file.file, destination)

    # The taint model sums and divides `amount` values, so a file mixing ETH and USDT rows
    # would yield percentages that are arithmetically meaningless while looking precise.
    # That cannot be corrected after the fact - every later figure would be wrong - so the
    # file is rejected here rather than accepted with a warning.
    currencies = detect_currencies(stored_path)
    if len(currencies) > 1:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=(
                f'Evidencija sadrži više valuta ({", ".join(currencies)}). Taint procenat bi bio besmislen jer '
                'se iznosi različitih valuta sabiraju kao da su isti. Razdvojite transakcije u posebne fajlove, '
                'po jednu valutu u svakom.'
            ),
        )
    currency = currencies[0] if currencies else None

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size

    store_case_evidence(str(case['id']), stored_path, stored_name)
    evidence_entry = append_evidence(
        str(case['id']),
        original_name=safe_name,
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        analyst=user,
        currency=currency,
    )

    audit_entry = write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action='csv_upload',
        user=user,
        case_id=str(case['id']),
        case_name=str(case.get('name') or ''),
        details={'original_name': safe_name, 'size_bytes': size_bytes, 'currency': currency},
    )

    cleaned_frame = clean_transaction_csv(stored_path)
    preview_frame = cleaned_frame.head(5).copy()

    if not preview_frame.empty:
        preview_frame['timestamp'] = preview_frame['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S%z')

    preview_frame = preview_frame.where(pd.notnull(preview_frame), None)

    return {
        'file_name': stored_name,
        'sha256': sha256_hash,
        'audit_log': audit_entry,
        'rows_total': int(len(cleaned_frame)),
        'preview': preview_frame.to_dict(orient='records'),
        'case': evidence_entry['case'],
        'evidence': evidence_entry,
    }
