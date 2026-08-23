"""Per-transaction chain of custody - "Obrazac evidencije rukovanja dokaznim materijalom"
applied to individual blockchain transactions instead of a physical exhibit.

Every time an analyst runs the taint analysis over a case's evidence, that is treated as
accessing EVERY transaction the run touched - one row gets appended to each of those
transactions' own custody log (see `app.evidence.tx_identity.transaction_id` for how a
transaction keeps the same identity across separate runs). This module only manages that
log; the actual write happens from the analytics-run route, and the PDF that mirrors the
paper form is built in `app.exports.custody_report`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.paths import LOGS_DIR

# Fields an analyst types/edits on each access - suggested back from prior entries so the
# same case or the same physical device does not have to be retyped identically every time.
SUGGESTABLE_FIELDS = (
    'identifikator_predmeta',
    'identifikator_dokaznog_materijala',
    'proizvodjac',
    'model',
    'serijski_broj',
)


def _custody_log_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / 'custody_log.jsonl'


def append_custody_batch(entries: list[dict[str, Any]]) -> None:
    """Writes every row from one analysis run in a single file handle - a run can touch
    hundreds of transactions at once, and opening/closing the file per row would be both
    slow and, on some platforms, a source of interleaved writes under load."""
    if not entries:
        return

    path = _custody_log_path()
    with path.open('a', encoding='utf-8') as log_file:
        for entry in entries:
            # 'scope' is stamped here rather than trusted from the caller, so every line
            # in this file is unambiguously self-describing ("this row is about ONE
            # transaction") even read in isolation, outside the app - e.g. straight from
            # the .jsonl file, or once mixed into a combined export.
            record = {'id': uuid4().hex, 'scope': 'transaction', **entry}
            log_file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def load_custody_entries(*, case_id: str | None = None, tx_id: str | None = None) -> list[dict[str, Any]]:
    path = _custody_log_path()
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with path.open('r', encoding='utf-8') as log_file:
        for line in log_file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # One damaged line must not make the rest of the log unreadable - the
                # remaining rows are still valid evidence of access.
                continue
            if case_id is not None and entry.get('case_id') != case_id:
                continue
            if tx_id is not None and entry.get('tx_id') != tx_id:
                continue
            entries.append(entry)

    return entries


def custody_chain_for_transaction(case_id: str, tx_id: str) -> dict[str, Any] | None:
    """The full Образац for one transaction: its descriptive snapshot plus every access
    row, oldest first, numbered the way the paper form numbers them (Бр. 1, 2, 3...)."""
    entries = sorted(
        load_custody_entries(case_id=case_id, tx_id=tx_id),
        key=lambda entry: str(entry.get('timestamp') or ''),
    )
    if not entries:
        return None

    first, last = entries[0], entries[-1]
    numbered = [{**entry, 'redni_broj': index} for index, entry in enumerate(entries, start=1)]

    return {
        'case_id': case_id,
        'case_name': last.get('case_name'),
        'tx_id': tx_id,
        'tx_hash': last.get('tx_hash'),
        'sender_address': first.get('sender_address'),
        'recipient_address': first.get('recipient_address'),
        'amount': first.get('amount'),
        'currency': first.get('currency'),
        'tx_timestamp': first.get('tx_timestamp'),
        'evidence_stored_name': last.get('evidence_stored_name'),
        'evidence_file_name': last.get('evidence_file_name'),
        # Header fields reflect the MOST RECENT access - an analyst correcting a device
        # detail (e.g. "N/A" -> the real serial number once it is known) should have that
        # correction be what prints on the form, not the very first guess.
        'identifikator_predmeta': last.get('identifikator_predmeta'),
        'identifikator_dokaznog_materijala': last.get('identifikator_dokaznog_materijala'),
        'proizvodjac': last.get('proizvodjac'),
        'model': last.get('model'),
        'serijski_broj': last.get('serijski_broj'),
        'entries': numbered,
    }


def list_case_transactions(case_id: str) -> list[dict[str, Any]]:
    """One row per distinct transaction that has been accessed at least once in this case,
    most recently accessed first - the browsing list behind the "Lanac dokaza" page."""
    by_tx: dict[str, dict[str, Any]] = {}
    for entry in load_custody_entries(case_id=case_id):
        tx_id = str(entry.get('tx_id') or '')
        if not tx_id:
            continue
        bucket = by_tx.setdefault(tx_id, {
            'tx_id': tx_id,
            'tx_hash': entry.get('tx_hash'),
            'sender_address': entry.get('sender_address'),
            'recipient_address': entry.get('recipient_address'),
            'amount': entry.get('amount'),
            'currency': entry.get('currency'),
            'tx_timestamp': entry.get('tx_timestamp'),
            'evidence_file_name': entry.get('evidence_file_name'),
            'access_count': 0,
            'last_accessed_at': None,
        })
        bucket['access_count'] += 1
        timestamp = str(entry.get('timestamp') or '')
        if bucket['last_accessed_at'] is None or timestamp > str(bucket['last_accessed_at']):
            bucket['last_accessed_at'] = entry.get('timestamp')

    return sorted(by_tx.values(), key=lambda item: str(item.get('last_accessed_at') or ''), reverse=True)


def field_suggestions(case_id: str) -> dict[str, list[str]]:
    """Distinct prior values per editable field, most recent first, so re-accessing the
    same evidence (or reusing the same physical device across accesses) offers what was
    typed before instead of asking the analyst to retype it identically."""
    entries = sorted(
        load_custody_entries(case_id=case_id),
        key=lambda entry: str(entry.get('timestamp') or ''),
        reverse=True,
    )

    suggestions: dict[str, list[str]] = {field: [] for field in SUGGESTABLE_FIELDS}
    seen: dict[str, set[str]] = {field: set() for field in SUGGESTABLE_FIELDS}
    for entry in entries:
        for field in SUGGESTABLE_FIELDS:
            value = str(entry.get(field) or '').strip()
            if not value or value.upper() == 'N/A' or value in seen[field]:
                continue
            seen[field].add(value)
            suggestions[field].append(value)

    return suggestions


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
