"""REST API for investigator notes on blockchain addresses / graph nodes.

Nested under the investigation container from step 1:
`/investigations/{investigation_id}/notes`. Same access level as the rest of the
investigator layer (any authenticated user - the router is mounted with the shared
`get_current_user` dependency in `app/api/router.py`).

Notes are investigator observations. They are stored only under
`data/investigations/<id>/notes.json` and are never written into the evidence case, the
transaction graph, or any analysis output.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.investigations import notes_service
from app.investigations.notes_models import (
    InvestigatorNote,
    InvestigatorNoteCreate,
    InvestigatorNoteUpdate,
)
from app.investigations.notes_service import InvestigatorNoteNotFoundError
from app.investigations.service import InvestigationCaseNotFoundError


router = APIRouter(prefix='/investigations/{investigation_id}/notes', tags=['investigator-notes'])

# Both "missing investigation" and "missing note" are a 404 to the client; catching them
# together keeps every handler's error branch a single line.
_NOT_FOUND = (InvestigationCaseNotFoundError, InvestigatorNoteNotFoundError)


@router.get('')
def get_notes(
    investigation_id: str,
    address: str | None = Query(default=None, description='Exact address to filter by; omit for every note in the investigation.'),
) -> dict[str, object]:
    try:
        notes = notes_service.list_notes(investigation_id, address=address)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {'investigation_id': investigation_id, 'address': address, 'notes': notes}


@router.post('')
def post_note(
    investigation_id: str,
    request: InvestigatorNoteCreate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigatorNote:
    try:
        note = notes_service.create_note(investigation_id, request, author=str(current_user['username']))
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_created',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'note_id': note.id, 'address': note.address},
    )
    return note


@router.get('/{note_id}')
def get_note(investigation_id: str, note_id: str) -> InvestigatorNote:
    try:
        return notes_service.get_note(investigation_id, note_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch('/{note_id}')
def patch_note(
    investigation_id: str,
    note_id: str,
    request: InvestigatorNoteUpdate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigatorNote:
    try:
        note = notes_service.update_note(investigation_id, note_id, request)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_updated',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'note_id': note_id, 'address': note.address},
    )
    return note


@router.delete('/{note_id}', status_code=204)
def delete_note_route(
    investigation_id: str,
    note_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> None:
    try:
        # Read it first so the deleted note's address can go into the activity log - once
        # it is gone there is nothing left to resolve the id against.
        note = notes_service.get_note(investigation_id, note_id)
        notes_service.delete_note(investigation_id, note_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_deleted',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'note_id': note_id, 'address': note.address},
    )
