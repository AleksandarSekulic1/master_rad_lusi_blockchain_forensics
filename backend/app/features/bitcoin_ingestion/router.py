from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.features.bitcoin_ingestion.models import FetchBitcoinTransactionsRequest
from app.features.bitcoin_ingestion.service import fetch_address_transactions
from app.paths import RAW_DIR
from app.services.case_management import CaseClosedError, append_evidence, require_open_case, store_case_evidence


router = APIRouter(prefix='/bitcoin', tags=['bitcoin'])

# Base58Check (P2PKH/P2SH): starts with 1 or 3, 25-34 characters total.
_BASE58_PATTERN = re.compile(r'^[13][a-km-zA-HJ-NP-Z1-9]{24,33}$')
# Bech32 (P2WPKH/P2WSH/P2TR): starts with bc1, 39-59 characters total.
_BECH32_PATTERN = re.compile(r'^bc1[a-z0-9]{36,56}$')

_DISCLAIMER = (
    'Pošiljalac je izveden po "common input ownership" heuristici (prva ulazna adresa '
    'predstavlja čitavu transakciju) - standardna forenzička pretpostavka, ne dokazana '
    'činjenica. Nepotvrđene (mempool) transakcije nisu uključene.'
)


@router.post('/fetch')
def fetch_transactions(
    request: FetchBitcoinTransactionsRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    address = request.address.strip()

    if not (_BASE58_PATTERN.match(address) or _BECH32_PATTERN.match(address)):
        raise HTTPException(
            status_code=400,
            detail='Adresa mora biti validan Bitcoin format (Base58: počinje sa 1/3, ili Bech32: počinje sa bc1).',
        )

    try:
        case = require_open_case(request.case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseClosedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        dataframe = fetch_address_transactions(address)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if dataframe.empty:
        raise HTTPException(status_code=404, detail='Nema potvrđenih transakcija za datu adresu.')

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    stored_name = f'{timestamp}_bitcoin_address_{address}.csv'
    stored_path = RAW_DIR / stored_name
    dataframe.to_csv(stored_path, index=False)

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size
    user = str(current_user['username'])

    store_case_evidence(str(case['id']), stored_path, stored_name)
    evidence_entry = append_evidence(
        str(case['id']),
        original_name=f'bitcoin_address_{address}.csv',
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        analyst=user,
        # Blockstream's txs endpoint only ever returns native BTC movements.
        currency='BTC',
    )

    audit_entry = write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action='bitcoin_fetch_address',
        user=user,
        case_id=str(case['id']),
        case_name=str(case.get('name') or ''),
        details={'address': address, 'rows_fetched': int(len(dataframe))},
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
        'resolved_query': address,
        'disclaimer': _DISCLAIMER,
    }
