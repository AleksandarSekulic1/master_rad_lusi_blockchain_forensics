"""REST API for case CRUD, its evidence locker, and status.

A `Case` is the evidence container (see app/services/case_management.py) - imported
on-chain facts and their custody - NOT the investigator-layer `InvestigationCase`
(app/investigations/). This slice owns the case entity itself: create/list/delete a case,
list/remove its evidence files, and open/close it. Every other case-scoped feature slice
(case_graph, case_behavioral_analysis, case_dex_swap_analysis,
case_token_approval_analysis, case_sybil_analysis, case_seed_suggestion, case_analytics_run,
case_pathfinding) only READS a case through `app.shared.case_access.get_case_or_404` -
none of them own it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_management.models import CreateCaseRequest, SetCaseStatusRequest
from app.services.case_management import create_case, delete_case, list_cases, remove_evidence, set_case_status
from app.shared.case_access import get_case_or_404

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('')
def get_cases(
    search: str | None = Query(default=None, description='Filter po nazivu slučaja (bez razlike u veličini slova).'),
) -> dict[str, object]:
    return {'cases': list_cases(search=search)}


@router.post('')
def post_case(request: CreateCaseRequest, current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    case = create_case(name=request.name, analyst=str(current_user['username']), description=request.description)
    write_audit_log(
        action='case_created',
        user=str(current_user['username']),
        case_id=str(case.get('id') or ''),
        case_name=str(case.get('name') or ''),
    )
    return case


@router.get('/{case_id}')
def get_case_detail(case_id: str) -> dict[str, object]:
    return get_case_or_404(case_id)


@router.get('/{case_id}/evidence')
def get_case_evidence(case_id: str) -> dict[str, object]:
    case = get_case_or_404(case_id)
    return {'case_id': case_id, 'evidence': case.get('evidence', [])}


@router.delete('/{case_id}/evidence/{stored_name}')
def delete_case_evidence(
    case_id: str,
    stored_name: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Removes one evidence file from a case (the inverse of a CSV upload). Returns the
    updated case so the client can refresh its evidence locker in place."""
    case = get_case_or_404(case_id)
    entry = next(
        (e for e in case.get('evidence', []) if isinstance(e, dict) and str(e.get('stored_name')) == stored_name),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f'Evidence {stored_name} not found in case')

    try:
        updated = remove_evidence(case_id, stored_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    write_audit_log(
        action='evidence_removed',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        file_name=str(entry.get('file_name') or ''),
        sha256_hash=str(entry.get('sha256') or ''),
    )
    return updated


@router.patch('/{case_id}/status')
def patch_case_status(
    case_id: str,
    request: SetCaseStatusRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    case = get_case_or_404(case_id)
    updated = set_case_status(case_id, request.status)
    write_audit_log(
        action='case_status_changed',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={'from': str(case.get('status') or ''), 'to': request.status},
    )
    return updated


@router.delete('/{case_id}', status_code=204)
def delete_case_route(case_id: str, current_user: dict[str, object] = Depends(get_current_user)) -> None:
    # Read the name BEFORE deleting - once the case file is gone there is nothing left to
    # resolve the id against, and "case X was deleted" is exactly the kind of entry that
    # must stay readable years later.
    case = get_case_or_404(case_id)
    case_name = str(case.get('name') or '')
    try:
        delete_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='case_deleted',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=case_name,
    )
