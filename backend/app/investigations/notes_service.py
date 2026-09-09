"""Business logic for investigator notes.

Sits between the API routes and `notes_repository.py`. Generates ids/timestamps, keeps
`updated_at` moving without ever touching `created_at`, and always verifies the parent
investigation exists first (reusing step 1's service), so a note can never be created
against, or read from, an investigation that is not there.

A note targets EITHER an address/node OR a transaction/edge - see
`app/investigations/notes_models.py`. The transaction identifier is the project's existing
`tx_id` (`app/evidence/tx_identity.py`); this layer only stores and matches it as an
opaque string, exactly as it does an address.
"""

from __future__ import annotations

from app.investigations import notes_repository
from app.investigations.notes_models import (
    InvestigatorNote,
    InvestigatorNoteCreate,
    InvestigatorNoteUpdate,
    NoteTargetType,
    utc_now_iso,
)
from app.investigations.service import get_investigation


class InvestigatorNoteNotFoundError(FileNotFoundError):
    """A note id that does not exist within the given investigation. Subclasses
    FileNotFoundError, same convention as InvestigationCaseNotFoundError."""


def _coerce_row(row: dict[str, object]) -> dict[str, object]:
    """Tolerate note rows written before this step (step 3 stored address notes only, with
    no `target_type` / `tx_id` keys). There is no persisted step-3 data, so this is purely
    defensive - it lets an old row load as the address note it always was."""
    coerced = dict(row)
    coerced.setdefault('address', None)
    coerced.setdefault('tx_id', None)
    if not coerced.get('target_type'):
        coerced['target_type'] = 'transaction' if coerced.get('tx_id') else 'address'
    return coerced


def _load_models(investigation_id: str) -> list[InvestigatorNote]:
    return [InvestigatorNote(**_coerce_row(row)) for row in notes_repository.load_notes(investigation_id)]


def _persist(investigation_id: str, notes: list[InvestigatorNote]) -> None:
    notes_repository.save_notes(investigation_id, [note.model_dump() for note in notes])


def list_notes(
    investigation_id: str,
    *,
    address: str | None = None,
    tx_id: str | None = None,
    target_type: NoteTargetType | None = None,
) -> list[InvestigatorNote]:
    """Notes for one investigation, newest created first.

    At most one filter is honoured (the route enforces "at most one"):
      - `address`      -> that node's notes,
      - `tx_id`        -> that transaction/edge's notes,
      - `target_type`  -> every node note, or every transaction note.
    None of them -> every note in the investigation.

    Identifier matches are exact and case-sensitive (surrounding whitespace trimmed), and
    scoped to the matching `target_type`, so an address and a tx_id that happen to be the
    same string never cross over.
    """
    get_investigation(investigation_id)  # -> InvestigationCaseNotFoundError -> HTTP 404

    notes = _load_models(investigation_id)
    if address is not None:
        target = address.strip()
        notes = [note for note in notes if note.target_type == 'address' and note.address == target]
    elif tx_id is not None:
        target = tx_id.strip()
        notes = [note for note in notes if note.target_type == 'transaction' and note.tx_id == target]
    elif target_type is not None:
        notes = [note for note in notes if note.target_type == target_type]
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
        target_type=data.target_type,
        address=data.address,
        tx_id=data.tx_id,
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
