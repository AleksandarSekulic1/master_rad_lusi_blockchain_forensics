"""Shared chain-of-custody recording, used by every case-scoped analysis that treats
running it as a deliberate access to the evidence: case_analytics_run (taint analysis /
"Analiziraj graf"), case_pathfinding ("FIND PATH"), case_behavioral_analysis,
case_dex_swap_analysis and case_token_approval_analysis. Each of those accepts an optional
`TransactionCustodyEntry` on its "run" request and, when present, calls
`record_custody_access` before returning - see LANAC-DOKAZA.md for why this is one shared
mechanism rather than a parallel log per analysis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, Field

from app.evidence.custody_evidence_log import append_evidence_custody_batch
from app.evidence.custody_log import append_custody_batch
from app.evidence.tx_identity import transaction_id


class TransactionCustodyEntry(BaseModel):
    """What the analyst is asserting by deliberately running an analysis: who they are and
    why they are accessing this evidence right now. When present, every field is required
    - a caller cannot send a token "empty" custody object just to satisfy the shape; it is
    either a real entry or omitted entirely.
    """

    ime_prezime: str = Field(min_length=1)
    opis_radnje: str = Field(min_length=1)
    signature_image: str = Field(min_length=1)
    identifikator_predmeta: str | None = None
    identifikator_dokaznog_materijala: str | None = None
    proizvodjac: str | None = None
    model: str | None = None
    serijski_broj: str | None = None


def _clean_scalar(value: object) -> object | None:
    """pandas leaves missing cells as NaN/pd.NA depending on column dtype - both need to
    become a real None before going into the custody log, or a missing tx hash would be
    stored as the literal string "<NA>" instead of being absent."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def record_custody_access(
    *,
    case: dict[str, object],
    per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]],
    custody: TransactionCustodyEntry,
    user: str,
    extra_transaction_fields: dict[str, dict[str, object]] | None = None,
) -> None:
    """One deliberate access is recorded at TWO granularities at once, both useful for a
    different question a reader might have:

    - per transaction (`custody_log`): was THIS specific transaction looked at, by whom,
      why - one row per transaction row in scope, appended to that transaction's own chain.
    - per evidence file (`custody_evidence_log`): was THIS evidence file (the exhibit
      itself, like a seized hard drive) accessed, by whom, why - one row per evidence file
      in scope, regardless of how many transactions it contains.

    Both share the same run/date/analyst/reason/signature - they were genuinely accessed
    together, by the same act of running the analysis (see LANAC-DOKAZA.md).

    `extra_transaction_fields` (optional, keyed by `tx_id`) merges additional fields into
    ONE SPECIFIC transaction's custody row, on top of the generic fields every row already
    gets below - used by case_token_approval_analysis to attach a structured
    TOKEN_APPROVAL evidence item (owner/spender/token/status/risk/...) to its own approval
    transaction's row, without changing this shared helper's behaviour for any other
    caller (Taint/Graph/Pathfinding/DEX Swap/Behavioral never pass this - it stays None for
    them, exactly as before - see TOKEN-APPROVAL-IMPLEMENTATION.md #18).
    """
    run_id = uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    case_name = str(case.get('name') or '')
    identifikator_predmeta = custody.identifikator_predmeta or case_name or str(case.get('id') or '')

    transaction_batch: list[dict[str, object]] = []
    evidence_batch: list[dict[str, object]] = []
    for evidence_entry, frame in per_evidence_frames:
        stored_name = str(evidence_entry.get('stored_name') or '')
        file_name = str(evidence_entry.get('file_name') or stored_name)
        identifikator_dokaznog_materijala = custody.identifikator_dokaznog_materijala or file_name

        shared_fields = {
            'run_id': run_id,
            'timestamp': timestamp,
            'case_id': case['id'],
            'case_name': case_name,
            'evidence_stored_name': stored_name,
            'evidence_file_name': file_name,
            'identifikator_predmeta': identifikator_predmeta,
            'identifikator_dokaznog_materijala': identifikator_dokaznog_materijala,
            'proizvodjac': custody.proizvodjac or 'N/A',
            'model': custody.model or 'N/A',
            'serijski_broj': custody.serijski_broj or 'N/A',
            'ime_prezime': custody.ime_prezime,
            'opis_radnje': custody.opis_radnje,
            'user': user,
            'signature_image': custody.signature_image,
        }

        evidence_batch.append({
            **shared_fields,
            'evidence_sha256': evidence_entry.get('sha256'),
            'evidence_currency': evidence_entry.get('currency'),
            'evidence_row_count': int(len(frame)),
        })

        for row in frame.to_dict('records'):
            amount = row.get('amount')
            tx_timestamp = row.get('timestamp')
            tx_id = transaction_id(row, stored_name)
            extra = (extra_transaction_fields or {}).get(tx_id)
            transaction_batch.append({
                **shared_fields,
                'tx_id': tx_id,
                'tx_hash': _clean_scalar(row.get('metadata')),
                'sender_address': _clean_scalar(row.get('sender_address')),
                'recipient_address': _clean_scalar(row.get('recipient_address')),
                'amount': float(amount) if pd.notna(amount) else None,
                'currency': _clean_scalar(row.get('currency')),
                'tx_timestamp': tx_timestamp.isoformat() if pd.notna(tx_timestamp) else None,
                **(extra or {}),
            })

    append_custody_batch(transaction_batch)
    append_evidence_custody_batch(evidence_batch)
