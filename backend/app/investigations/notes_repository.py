"""Persistence for investigator notes.

One JSON file per investigation - `data/investigations/<investigation_id>/notes.json` -
holding every note for that investigation across all addresses:

    { "notes": [ { <InvestigatorNote fields> }, ... ] }

Same flat-file approach as the rest of the project (there is no database). Pure dict I/O:
no validation, no id/timestamp handling - that is `notes_service.py`'s job. The file lives
inside the per-investigation directory (from `app/investigations/repository.py`), so
deleting an investigation removes its notes with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.investigations.repository import investigation_dir

_NOTES_FILE = 'notes.json'


def _notes_path(investigation_id: str) -> Path:
    return investigation_dir(investigation_id) / _NOTES_FILE


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_notes(investigation_id: str) -> list[dict[str, Any]]:
    payload = _read_json(_notes_path(investigation_id), {'notes': []})
    if not isinstance(payload, dict):
        return []
    notes = payload.get('notes')
    return notes if isinstance(notes, list) else []


def save_notes(investigation_id: str, notes: list[dict[str, Any]]) -> None:
    _write_json(_notes_path(investigation_id), {'notes': notes})
