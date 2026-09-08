"""Persistence for investigator notes.

One JSON file per investigation - ``data/investigations/<investigation_id>/notes.json`` -
shaped ``{ "notes": [ { <InvestigatorNote fields> }, ... ] }``, holding every note
(address + transaction) for that investigation.

Pure dict I/O via the shared collection helpers in ``app/investigations/repository.py`` -
no validation, no id/timestamp handling (that is ``notes_service.py``'s job). The file
lives inside the per-investigation directory, so deleting an investigation removes its
notes with it. Nothing here touches the transaction graph or the evidence case.
"""

from __future__ import annotations

from typing import Any

from app.investigations.repository import read_collection, write_collection

_FILE = 'notes.json'
_KEY = 'notes'


def load_notes(investigation_id: str) -> list[dict[str, Any]]:
    return read_collection(investigation_id, _FILE, _KEY)


def save_notes(investigation_id: str, notes: list[dict[str, Any]]) -> None:
    write_collection(investigation_id, _FILE, _KEY, notes)
