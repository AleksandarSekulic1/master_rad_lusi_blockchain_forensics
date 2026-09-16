"""REST API for investigator notes.

A note is attached to EITHER an address/graph node OR a transaction/edge (identified by
the project's existing `tx_id` - `app/evidence/tx_identity.py`). The two kinds share this
one endpoint set and are told apart by the note's `target_type` field.

Nested under the investigation container from step 1:
`/investigations/{investigation_id}/notes`. Same access level as the rest of the
investigator layer (any authenticated user - the router is mounted with the shared
`get_current_user` dependency in `app/api/router.py`).

Notes are investigator observations. They are stored only under
`data/investigations/<id>/notes.json` and are never written into the evidence case, the
transaction graph, or any analysis output.

This module, together with `models.py`, `repository.py` and `service.py` in this same
package, is the complete "investigator notes" feature slice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.investigation_notes import service
from app.features.investigation_notes.models import (
    InvestigatorNote,
    InvestigatorNoteCreate,
    InvestigatorNoteUpdate,
)
from app.features.investigation_notes.service import InvestigatorNoteNotFoundError
from app.investigations.service import InvestigationCaseNotFoundError

router = APIRouter(prefix='/investigations/{investigation_id}/notes', tags=['investigator-notes'])

# Both "missing investigation" and "missing note" are a 404 to the client; catching them
# together keeps every handler's error branch a single line.
_NOT_FOUND = (InvestigationCaseNotFoundError, InvestigatorNoteNotFoundError)

_TARGET_TYPES = ('address', 'transaction')


def _audit_details(investigation_id: str, note: InvestigatorNote) -> dict[str, object]:
    return {
        'investigation_id': investigation_id,
        'note_id': note.id,
        'target_type': note.target_type,
        'address': note.address,
        'tx_id': note.tx_id,
    }


@router.get('')
def get_notes(
    investigation_id: str,
    address: str | None = Query(default=None, description='Exact address / graph-node id to filter by.'),
    tx_id: str | None = Query(
        default=None,
        description='Exact transaction id to filter by - the same identifier the chain of custody uses '
        '(a tx hash, or a "row-..." fallback).',
    ),
    target_type: str | None = Query(default=None, description="Filter by kind only: 'address' or 'transaction'."),
) -> dict[str, object]:
    if target_type is not None and target_type not in _TARGET_TYPES:
        raise HTTPException(status_code=400, detail="target_type mora biti 'address' ili 'transaction'.")
    if sum(value is not None for value in (address, tx_id, target_type)) > 1:
        raise HTTPException(
            status_code=400,
            detail='Navedite najviše jedan filter: address, tx_id ili target_type.',
        )

    try:
        notes = service.list_notes(
            investigation_id,
            address=address,
            tx_id=tx_id,
            target_type=target_type,  # already validated to 'address' | 'transaction' | None
        )
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        'investigation_id': investigation_id,
        'address': address,
        'tx_id': tx_id,
        'target_type': target_type,
        'notes': notes,
    }


@router.post('')
def post_note(
    investigation_id: str,
    request: InvestigatorNoteCreate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigatorNote:
    try:
        note = service.create_note(investigation_id, request, author=str(current_user['username']))
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_created',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, note),
    )
    return note


@router.get('/{note_id}')
def get_note(investigation_id: str, note_id: str) -> InvestigatorNote:
    try:
        return service.get_note(investigation_id, note_id)
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
        note = service.update_note(investigation_id, note_id, request)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_updated',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, note),
    )
    return note


@router.delete('/{note_id}', status_code=204)
def delete_note_route(
    investigation_id: str,
    note_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> None:
    try:
        # Read it first so the deleted note's target can go into the activity log - once
        # it is gone there is nothing left to resolve the id against.
        note = service.get_note(investigation_id, note_id)
        service.delete_note(investigation_id, note_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_note_deleted',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, note),
    )
