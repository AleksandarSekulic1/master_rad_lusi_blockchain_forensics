"""Stable identifier for one individual transaction row, used to key its chain of custody.

A chain of custody entry has to keep pointing at the SAME transaction every time it is
written, across separate analysis runs, possibly days apart - otherwise "row 3 of this
form" and "row 4" would silently describe two different transfers. Real evidence (an
on-chain pull) already carries a transaction hash for this. Demo/manual CSV evidence often
does not, so a deterministic fallback is derived from the fields that DO identify a
transfer - sender, recipient, amount, timestamp - salted with the evidence file, so the
same transfer recorded in two different evidence files does not collide onto one id.
"""

from __future__ import annotations

import hashlib
from typing import Any


def transaction_id(row: dict[str, Any], evidence_stored_name: str) -> str:
    """Row is expected to carry the normalized columns from `app.analytics.ingestion`
    (sender_address, recipient_address, amount, timestamp, metadata) - `metadata` is where
    a CSV's tx_hash/hash column ends up after normalization (see ingestion.COLUMN_ALIASES).
    """
    tx_hash = str(row.get('metadata') or row.get('tx_hash') or row.get('hash') or '').strip()
    if tx_hash and tx_hash.lower() != 'nan':
        return tx_hash

    basis = '|'.join([
        str(row.get('sender_address') or ''),
        str(row.get('recipient_address') or ''),
        str(row.get('amount') or ''),
        str(row.get('timestamp') or ''),
        evidence_stored_name,
    ])
    return 'row-' + hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]
