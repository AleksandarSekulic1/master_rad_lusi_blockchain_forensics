from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.analytics.ingestion import clean_transaction_csv, detect_currencies, split_by_currency
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
    # That cannot be corrected after the fact - every later figure would be wrong - so a
    # mixed file is split into one evidence file per currency here (see
    # _split_and_store_by_currency below) rather than accepted, or rejected and left for
    # the analyst to split by hand outside the app.
    currencies = detect_currencies(stored_path)
    if len(currencies) > 1:
        return _split_and_store_by_currency(stored_path, safe_name, str(case['id']), user)
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


def _split_and_store_by_currency(
    stored_path: Path,
    original_name: str,
    case_id: str,
    user: str,
) -> dict[str, object]:
    """Splits a multi-currency upload into one evidence file per currency (see
    ingestion.split_by_currency) and stores/hashes/logs each exactly like a normal
    single-currency upload above. The combined file the analyst actually dropped is
    never itself stored as evidence - only its per-currency parts are, since only those
    are internally consistent for the taint model.
    """
    groups = split_by_currency(stored_path)
    stored_path.unlink(missing_ok=True)

    raw_dir = stored_path.parent
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix or '.csv'

    files: list[dict[str, object]] = []
    case_summary: dict[str, object] | None = None

    for label in sorted(groups, key=lambda value: (value is None, value or '')):
        subset = groups[label]
        label_tag = label or 'UNSPECIFIED'
        split_original_name = f'{stem}_{label_tag}{suffix}'
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        split_stored_name = f'{timestamp}_{split_original_name}'
        split_path = raw_dir / split_stored_name
        subset.to_csv(split_path, index=False)

        sha256_hash = calculate_sha256(split_path)
        size_bytes = split_path.stat().st_size

        store_case_evidence(case_id, split_path, split_stored_name)
        evidence_entry = append_evidence(
            case_id,
            original_name=split_original_name,
            stored_name=split_stored_name,
            size_bytes=size_bytes,
            sha256_hash=sha256_hash,
            analyst=user,
            currency=label,
        )
        case_summary = evidence_entry.get('case')  # type: ignore[assignment]

        write_audit_log(
            file_name=split_stored_name,
            sha256_hash=sha256_hash,
            action='csv_upload_split',
            user=user,
            case_id=case_id,
            case_name=str((case_summary or {}).get('name') or ''),
            details={'source_file': original_name, 'currency': label, 'size_bytes': size_bytes},
        )

        cleaned_frame = clean_transaction_csv(split_path)
        files.append({
            'file_name': split_stored_name,
            'currency': label,
            'rows_total': int(len(cleaned_frame)),
            'sha256': sha256_hash,
            'evidence': evidence_entry,
        })

    return {
        'split': True,
        'source_file_name': original_name,
        'files': files,
        'rows_total': sum(int(item['rows_total']) for item in files),
        'case': case_summary,
    }
