from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.analytics.case_graph import build_case_graph, graph_summary
from app.analytics.graph_building import transaction_graph_to_node_link_json
from app.analytics.plugins.manager import run_plugin_pipeline
from app.api.deps import get_current_user
from app.services.case_management import (
    create_case,
    delete_case,
    get_case,
    get_case_evidence_paths,
    list_cases,
    set_case_status,
)


def _get_case_or_404(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


router = APIRouter(prefix='/cases', tags=['cases'])


class CreateCaseRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class SetCaseStatusRequest(BaseModel):
    status: str = Field(pattern='^(open|closed)$')


@router.get('')
def get_cases() -> dict[str, object]:
    return {'cases': list_cases()}


@router.post('')
def post_case(request: CreateCaseRequest, current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    return create_case(name=request.name, analyst=str(current_user['username']), description=request.description)


@router.get('/{case_id}')
def get_case_detail(case_id: str) -> dict[str, object]:
    return _get_case_or_404(case_id)


@router.get('/{case_id}/evidence')
def get_case_evidence(case_id: str) -> dict[str, object]:
    case = _get_case_or_404(case_id)
    return {'case_id': case_id, 'evidence': case.get('evidence', [])}


@router.patch('/{case_id}/status')
def patch_case_status(case_id: str, request: SetCaseStatusRequest) -> dict[str, object]:
    _get_case_or_404(case_id)
    return set_case_status(case_id, request.status)


@router.delete('/{case_id}', status_code=204)
def delete_case_route(case_id: str) -> None:
    try:
        delete_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _case_evidence_paths_or_404(case: dict[str, object]) -> list[tuple[dict[str, object], object]]:
    try:
        return get_case_evidence_paths(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _filter_evidence_paths(
    evidence_paths: list[tuple[dict[str, object], object]],
    stored_name: str | None,
) -> list[tuple[dict[str, object], object]]:
    if not stored_name:
        return evidence_paths

    filtered = [(entry, path) for entry, path in evidence_paths if entry.get('stored_name') == stored_name]
    if not filtered:
        raise HTTPException(status_code=404, detail=f'Evidence {stored_name} not found in case')
    return filtered


@router.get('/{case_id}/graph')
def get_case_graph(case_id: str, evidence: str | None = None) -> dict[str, object]:
    """Transaction graph for the case. Pass `evidence` (stored_name) to scope it to a single evidence file,
    otherwise it is combined across ALL evidence in the case."""
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    payload = transaction_graph_to_node_link_json(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    payload['rows'] = int(len(combined_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    return payload


@router.post('/{case_id}/analytics/run')
def run_case_analytics(case_id: str, evidence: str | None = None) -> dict[str, object]:
    """Runs the full analytics pipeline over the case's evidence graph, optionally scoped to one evidence file."""
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    plugin_results = run_plugin_pipeline(dataframe=combined_frame, graph=graph)

    payload = transaction_graph_to_node_link_json(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    payload['rows'] = int(len(combined_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    payload['analytics'] = plugin_results
    payload['summary'] = graph_summary(graph)
    return payload
