"""Business logic for investigator notes.

Sits between the API routes and `notes_repository.py`. Generates ids/timestamps, keeps
`updated_at` moving without ever touching `created_at`, and always verifies the parent
investigation exists first (reusing step 1's service), so a note can never be created
against, or read from, an investigation that is not there.
"""

from __future__ import annotations

from app.investigations import notes_repository
from app.investigations.notes_models import (
    InvestigatorNote,
    InvestigatorNoteCreate,
    InvestigatorNoteUpdate,
    utc_now_iso,
)
from app.investigations.service import get_investigation


class InvestigatorNoteNotFoundError(FileNotFoundError):
    """A note id that does not exist within the given investigation. Subclasses
    FileNotFoundError, same convention as InvestigationCaseNotFoundError."""


def _load_models(investigation_id: str) -> list[InvestigatorNote]:
    return [InvestigatorNote(**row) for row in notes_repository.load_notes(investigation_id)]


def _persist(investigation_id: str, notes: list[InvestigatorNote]) -> None:
    notes_repository.save_notes(investigation_id, [note.model_dump() for note in notes])


def list_notes(investigation_id: str, address: str | None = None) -> list[InvestigatorNote]:
    """Notes for one investigation, newest created first. With `address`, only that
    address's notes (exact match, whitespace-trimmed, case-sensitive - same spelling rule
    the model enforces on create). Without it, every note in the investigation."""
    get_investigation(investigation_id)  # -> InvestigationCaseNotFoundError -> HTTP 404

    notes = _load_models(investigation_id)
    if address is not None:
        target = address.strip()
        notes = [note for note in notes if note.address == target]
    return sorted(notes, key=lambda note: (note.created_at, note.id), reverse=True)


def get_note(investigation_id: str, note_id: str) -> InvestigatorNote:
    get_investigation(investigation_id)
    for note in _load_models(investigation_id):
        if note.id == note_id:
            return note
    raise InvestigatorNoteNotFoundError(f'Beleška nije pronađena: {note_id}')


def create_note(investigation_id: str, data: InvestigatorNoteCreate, *, author: str) -> InvestigatorNote:
    get_investigation(investigation_id)

    now = utc_now_iso()
    note = InvestigatorNote(
        investigation_id=investigation_id,
        address=data.address,
        text=data.text,
        author=author,
        created_at=now,
        updated_at=now,
    )
    notes = _load_models(investigation_id)
    notes.append(note)
    _persist(investigation_id, notes)
    return note


def update_note(investigation_id: str, note_id: str, changes: InvestigatorNoteUpdate) -> InvestigatorNote:
    get_investigation(investigation_id)

    notes = _load_models(investigation_id)
    for index, note in enumerate(notes):
        if note.id != note_id:
            continue
        note.text = changes.text
        note.updated_at = utc_now_iso()
        notes[index] = note
        _persist(investigation_id, notes)
        return note
    raise InvestigatorNoteNotFoundError(f'Beleška nije pronađena: {note_id}')


def delete_note(investigation_id: str, note_id: str) -> None:
    get_investigation(investigation_id)

    notes = _load_models(investigation_id)
    remaining = [note for note in notes if note.id != note_id]
    if len(remaining) == len(notes):
        raise InvestigatorNoteNotFoundError(f'Beleška nije pronađena: {note_id}')
    _persist(investigation_id, remaining)
