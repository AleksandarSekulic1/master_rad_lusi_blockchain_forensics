"""Chain of custody at the EVIDENCE FILE level - "Obrazac evidencije rukovanja dokaznim
materijalom" applied to a whole imported CSV/on-chain export, the way the paper form
applies to a whole exhibit (a hard drive), rather than to each file recorded on it.

This is the coarser sibling of `app.evidence.custody_log` (which tracks the same kind of
access per INDIVIDUAL transaction). Both are kept side by side deliberately - see
LANAC-DOKAZA.md - each answers a different question a reader might have ("was THIS
transaction looked at" vs "was THIS evidence file accessed, and when, by whom, why"), and
a single deliberate access (running the analysis pipeline over a case's evidence) writes
to both at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.paths import LOGS_DIR


def _evidence_custody_log_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / 'custody_evidence_log.jsonl'


def append_evidence_custody_batch(entries: list[dict[str, Any]]) -> None:
    """Writes every row from one analysis run in a single file handle - a combined-scope
    run can touch several evidence files at once, all from the same act of accessing."""
    if not entries:
        return

    path = _evidence_custody_log_path()
    with path.open('a', encoding='utf-8') as log_file:
        for entry in entries:
            # 'scope' is stamped here rather than trusted from the caller, so every line
            # in this file is unambiguously self-describing ("this row is about a WHOLE
            # evidence file") even read in isolation, outside the app - e.g. straight from
            # the .jsonl file, or once mixed into a combined export.
            record = {'id': uuid4().hex, 'scope': 'evidence_file', **entry}
            log_file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def load_evidence_custody_entries(
    *, case_id: str | None = None, evidence_stored_name: str | None = None,
) -> list[dict[str, Any]]:
    path = _evidence_custody_log_path()
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
                # One damaged line must not make the rest of the log unreadable.
                continue
            if case_id is not None and entry.get('case_id') != case_id:
                continue
            if evidence_stored_name is not None and entry.get('evidence_stored_name') != evidence_stored_name:
                continue
            entries.append(entry)

    return entries


def custody_chain_for_evidence(case_id: str, evidence_stored_name: str) -> dict[str, Any] | None:
    """The full Образац for one evidence file: its descriptive snapshot plus every access
    row, oldest first, numbered the way the paper form numbers them (Бр. 1, 2, 3...)."""
    entries = sorted(
        load_evidence_custody_entries(case_id=case_id, evidence_stored_name=evidence_stored_name),
        key=lambda entry: str(entry.get('timestamp') or ''),
    )
    if not entries:
        return None

    first, last = entries[0], entries[-1]
    numbered = [{**entry, 'redni_broj': index} for index, entry in enumerate(entries, start=1)]

    return {
        'case_id': case_id,
        'case_name': last.get('case_name'),
        'evidence_stored_name': evidence_stored_name,
        'evidence_file_name': last.get('evidence_file_name'),
        'evidence_sha256': first.get('evidence_sha256'),
        'evidence_currency': first.get('evidence_currency'),
        'evidence_row_count': last.get('evidence_row_count'),
        # Header fields reflect the MOST RECENT access, same reasoning as the
        # per-transaction chain - a correction should be what prints on the form.
        'identifikator_predmeta': last.get('identifikator_predmeta'),
        'identifikator_dokaznog_materijala': last.get('identifikator_dokaznog_materijala'),
        'proizvodjac': last.get('proizvodjac'),
        'model': last.get('model'),
        'serijski_broj': last.get('serijski_broj'),
        'entries': numbered,
    }


def list_case_evidence(case_id: str) -> list[dict[str, Any]]:
    """One row per evidence file that has been accessed at least once in this case, most
    recently accessed first - the browsing list for the evidence-level custody view."""
    by_file: dict[str, dict[str, Any]] = {}
    for entry in load_evidence_custody_entries(case_id=case_id):
        stored_name = str(entry.get('evidence_stored_name') or '')
        if not stored_name:
            continue
        bucket = by_file.setdefault(stored_name, {
            'evidence_stored_name': stored_name,
            'evidence_file_name': entry.get('evidence_file_name'),
            'evidence_sha256': entry.get('evidence_sha256'),
            'evidence_currency': entry.get('evidence_currency'),
            'evidence_row_count': entry.get('evidence_row_count'),
            'access_count': 0,
            'last_accessed_at': None,
        })
        bucket['access_count'] += 1
        timestamp = str(entry.get('timestamp') or '')
        if bucket['last_accessed_at'] is None or timestamp > str(bucket['last_accessed_at']):
            bucket['last_accessed_at'] = entry.get('timestamp')

    return sorted(by_file.values(), key=lambda item: str(item.get('last_accessed_at') or ''), reverse=True)
