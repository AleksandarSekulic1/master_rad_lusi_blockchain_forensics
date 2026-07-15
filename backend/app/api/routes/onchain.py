from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.paths import RAW_DIR
from app.services.case_management import append_evidence, resolve_case, store_case_evidence
from app.services.onchain_ingestion import NETWORK_CHAIN_IDS, fetch_address_transactions


router = APIRouter(prefix='/onchain', tags=['onchain'])

_ADDRESS_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')


class FetchTransactionsRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)
    network: str = Field(default='mainnet')
    case_id: str | None = None


@router.post('/fetch')
def fetch_transactions(
    request: FetchTransactionsRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    if not _ADDRESS_PATTERN.match(request.address):
        raise HTTPException(status_code=400, detail='Adresa mora biti u formatu 0x + 40 heksadecimalnih karaktera.')

    if request.network not in NETWORK_CHAIN_IDS:
        raise HTTPException(status_code=400, detail=f'Mreža mora biti jedna od: {", ".join(NETWORK_CHAIN_IDS)}.')

    try:
        dataframe = fetch_address_transactions(request.address, request.network)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if dataframe.empty:
        raise HTTPException(status_code=404, detail='Nema pronađenih transakcija za ovu adresu na izabranoj mreži.')

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    stored_name = f'{timestamp}_onchain_{request.network}_{request.address}.csv'
    stored_path = RAW_DIR / stored_name
    dataframe.to_csv(stored_path, index=False)

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size
    user = str(current_user['username'])

    case = resolve_case(request.case_id, analyst=user)
    store_case_evidence(str(case['id']), stored_path, stored_name)
    evidence_entry = append_evidence(
        str(case['id']),
        original_name=f'onchain_{request.network}_{request.address}.csv',
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        analyst=user,
    )

    audit_entry = write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action=f'onchain_fetch_{request.network}',
        user=user,
        case_id=str(case['id']),
    )

    preview_frame = dataframe.head(5)

    return {
        'file_name': stored_name,
        'sha256': sha256_hash,
        'audit_log': audit_entry,
        'rows_total': int(len(dataframe)),
        'preview': preview_frame.to_dict(orient='records'),
        'case': evidence_entry['case'],
        'evidence': evidence_entry,
    }
