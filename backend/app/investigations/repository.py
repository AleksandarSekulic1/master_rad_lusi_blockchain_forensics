"""Persistence for investigation cases.

Flat-file JSON on disk, the same approach the rest of this project uses (there is no
database - see `app/services/case_management.py`, `app/services/report_registry.py`). This
module is pure storage: it knows nothing about validation, timestamps or id generation -
that is the service layer's job (`app/investigations/service.py`).

Storage layout (mirrors `case_management.py`):

    data/investigations/
    ├── index.json                      lightweight list, newest-updated first
    └── <investigation_id>/
        └── investigation.json          the full InvestigationCase record
        (notes / pinned-nodes / links files land in this same directory later)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.paths import INVESTIGATIONS_DIR


def _root() -> Path:
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return INVESTIGATIONS_DIR


def _index_path() -> Path:
    return _root() / 'index.json'


def _investigation_dir(investigation_id: str) -> Path:
    return _root() / investigation_id


def investigation_dir(investigation_id: str) -> Path:
    """Public accessor for the per-investigation directory, so sibling child-collection
    modules (notes, and later pinned nodes / links) can put their own files under it
    without each re-deriving the storage layout."""
    return _investigation_dir(investigation_id)


def _record_path(investigation_id: str) -> Path:
    return _investigation_dir(investigation_id) / 'investigation.json'


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


# --- Index -------------------------------------------------------------------------------

def load_index() -> list[dict[str, Any]]:
    payload = _read_json(_index_path(), {'investigations': []})
    if not isinstance(payload, dict):
        return []
    entries = payload.get('investigations')
    return entries if isinstance(entries, list) else []


def save_index(entries: list[dict[str, Any]]) -> None:
    ordered = sorted(entries, key=lambda item: str(item.get('updated_at', '')), reverse=True)
    _write_json(_index_path(), {'investigations': ordered})


# --- Records -----------------------------------------------------------------------------

def exists(investigation_id: str) -> bool:
    return _record_path(investigation_id).exists()


def read_record(investigation_id: str) -> dict[str, Any] | None:
    payload = _read_json(_record_path(investigation_id), None)
    return payload if isinstance(payload, dict) else None


def write_record(record: dict[str, Any]) -> None:
    _write_json(_record_path(str(record['id'])), record)


def delete_record(investigation_id: str) -> bool:
    """Removes the whole per-investigation directory. Returns False when there was nothing
    to delete, so the caller can turn that into a 404 rather than a silent success."""
    directory = _investigation_dir(investigation_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True
