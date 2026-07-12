from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.analytics.ingestion.csv_ingestion import clean_transaction_csv
from app.evidence.audit_log.logger import write_audit_log
from app.evidence.hashing.service import calculate_sha256


router = APIRouter(prefix='/upload', tags=['upload'])


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


@router.post('/csv')
async def upload_csv(
    file: UploadFile = File(...),
    user: str = Form(default='system'),
) -> dict[str, object]:
    if not file.filename:
        raise HTTPException(status_code=400, detail='CSV fajl mora imati naziv.')

    if not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail='Dozvoljen je samo CSV fajl.')

    repo_root = _repo_root()
    raw_dir = repo_root / 'data' / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    safe_name = Path(file.filename).name
    stored_name = f'{timestamp}_{safe_name}'
    stored_path = raw_dir / stored_name

    with stored_path.open('wb') as destination:
        shutil.copyfileobj(file.file, destination)

    sha256_hash = calculate_sha256(stored_path)
    audit_entry = write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action='csv_upload',
        user=user,
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
    }
