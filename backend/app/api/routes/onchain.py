from __future__ import annotations

import re
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.paths import RAW_DIR
from app.services.case_management import append_evidence, resolve_case, store_case_evidence
from app.services.onchain_ingestion import (
    NETWORK_CHAIN_IDS,
    fetch_address_transactions,
    fetch_expanded_sender_history,
    fetch_single_transaction_frame,
)


router = APIRouter(prefix='/onchain', tags=['onchain'])

_ADDRESS_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')
_TX_HASH_PATTERN = re.compile(r'^0x[0-9a-fA-F]{64}$')


class FetchTransactionsRequest(BaseModel):
    query: str = Field(min_length=1)
    network: str = Field(default='mainnet')
    case_id: str | None = None
    mode: str = Field(default='address_history')


def _resolve_dataframe(query: str, network: str, mode: str) -> tuple[pd.DataFrame, str, str]:
    """Returns (dataframe, evidence_label, action_suffix) based on the input format."""
    if _ADDRESS_PATTERN.match(query):
        dataframe = fetch_address_transactions(query, network)
        return dataframe, query, 'address'

    if _TX_HASH_PATTERN.match(query):
        if mode == 'tx_single':
            dataframe = fetch_single_transaction_frame(query, network)
            return dataframe, query, 'tx_single'

        dataframe, sender = fetch_expanded_sender_history(query, network)
        return dataframe, sender, 'tx_expand_sender'

    raise HTTPException(
        status_code=400,
        detail='Unos mora biti adresa (0x + 40 heksadecimalnih karaktera) ili heš transakcije (0x + 64 heksadecimalna karaktera).',
    )


@router.post('/fetch')
def fetch_transactions(
    request: FetchTransactionsRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    query = request.query.strip()

    if request.network not in NETWORK_CHAIN_IDS:
        raise HTTPException(status_code=400, detail=f'Mreža mora biti jedna od: {", ".join(NETWORK_CHAIN_IDS)}.')

    try:
        dataframe, label, action_suffix = _resolve_dataframe(query, request.network, request.mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if dataframe.empty:
        raise HTTPException(status_code=404, detail='Nema pronađenih transakcija za dati unos na izabranoj mreži.')

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    stored_name = f'{timestamp}_onchain_{request.network}_{action_suffix}_{label}.csv'
    stored_path = RAW_DIR / stored_name
    dataframe.to_csv(stored_path, index=False)

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size
    user = str(current_user['username'])

    case = resolve_case(request.case_id, analyst=user)
    store_case_evidence(str(case['id']), stored_path, stored_name)
    evidence_entry = append_evidence(
        str(case['id']),
        original_name=f'onchain_{request.network}_{action_suffix}_{label}.csv',
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        analyst=user,
    )

    audit_entry = write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action=f'onchain_fetch_{request.network}_{action_suffix}',
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
        'resolved_query': label,
    }
