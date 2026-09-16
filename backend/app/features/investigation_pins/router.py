"""REST API for pinned nodes.

An investigator pins an address to mark it important and keep it fixed on the graph
(step 5). It is persisted (step 10) so the pin survives a reload, and it belongs to an
investigation - stored only under ``data/investigations/<id>/pinned_nodes.json``, never in
the transaction graph.

Nested under the investigation container: ``/investigations/{investigation_id}/pins``.
Any authenticated user, same as the rest of the investigator layer.

This module, together with ``models.py``, ``repository.py`` and ``service.py`` in this
same package, is the complete "pinned nodes" feature slice - everything needed to add,
change or remove this capability lives here, in one place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.investigation_pins import service
from app.features.investigation_pins.models import PinNodeRequest, PinnedNode
from app.features.investigation_pins.service import PinnedNodeNotFoundError
from app.investigations.service import InvestigationCaseNotFoundError

router = APIRouter(prefix='/investigations/{investigation_id}/pins', tags=['investigator-pins'])

_NOT_FOUND = (InvestigationCaseNotFoundError, PinnedNodeNotFoundError)


@router.get('')
def get_pins(investigation_id: str) -> dict[str, object]:
    try:
        pins = service.list_pins(investigation_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {'investigation_id': investigation_id, 'pins': pins}


@router.put('')
def put_pin(
    investigation_id: str,
    request: PinNodeRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> PinnedNode:
    """Pin an address, or update the stored position of one that is already pinned."""
    try:
        pin = service.set_pin(investigation_id, request, pinned_by=str(current_user['username']))
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_pin_set',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'address': pin.address},
    )
    return pin


@router.delete('', status_code=204)
def delete_pin(
    investigation_id: str,
    address: str = Query(min_length=1, description='Exact address to unpin.'),
    current_user: dict[str, object] = Depends(get_current_user),
) -> None:
    try:
        service.clear_pin(investigation_id, address)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_pin_cleared',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'address': address.strip()},
    )
