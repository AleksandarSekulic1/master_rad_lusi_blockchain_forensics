"""REST API for the investigator layer's container entity: the investigation case.

CRUD only, for now. Investigator notes, pinned nodes and off-chain address links get
their own sub-routes under `/investigations/{id}/...` in later steps.

Open to any authenticated user (the router is mounted with the shared `get_current_user`
dependency in `app/api/router.py`), same access level as the evidence `cases` router.
Every write is also recorded in the app-wide activity log, matching how every other
state-changing route in this project behaves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.investigations import service
from app.investigations.models import (
    InvestigationCase,
    InvestigationCaseCreate,
    InvestigationCaseUpdate,
)
from app.investigations.service import InvestigationCaseNotFoundError


router = APIRouter(prefix='/investigations', tags=['investigations'])


def _get_or_404(investigation_id: str) -> InvestigationCase:
    try:
        return service.get_investigation(investigation_id)
    except InvestigationCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('')
def get_investigations() -> dict[str, list[InvestigationCase]]:
    return {'investigations': service.list_investigations()}


@router.post('')
def post_investigation(
    request: InvestigationCaseCreate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigationCase:
    case = service.create_investigation(request)
    write_audit_log(
        action='investigation_case_created',
        user=str(current_user['username']),
        details={'investigation_id': case.id, 'name': case.name},
    )
    return case


@router.get('/{investigation_id}')
def get_investigation_detail(investigation_id: str) -> InvestigationCase:
    return _get_or_404(investigation_id)


@router.patch('/{investigation_id}')
def patch_investigation(
    investigation_id: str,
    request: InvestigationCaseUpdate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigationCase:
    _get_or_404(investigation_id)
    case = service.update_investigation(investigation_id, request)
    write_audit_log(
        action='investigation_case_updated',
        user=str(current_user['username']),
        details={
            'investigation_id': case.id,
            'updated_fields': sorted(request.model_dump(exclude_unset=True).keys()),
        },
    )
    return case


@router.delete('/{investigation_id}', status_code=204)
def delete_investigation_route(
    investigation_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> None:
    # Read the name before deleting - once the record is gone there is nothing left to
    # resolve the id against, and "investigation X was deleted" should stay readable.
    case = _get_or_404(investigation_id)
    service.delete_investigation(investigation_id)
    write_audit_log(
        action='investigation_case_deleted',
        user=str(current_user['username']),
        details={'investigation_id': investigation_id, 'name': case.name},
    )
