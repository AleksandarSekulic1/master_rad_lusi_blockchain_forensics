from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.evidence.audit_log import load_audit_log_entries
from app.exports.service import build_case_artifacts_from_evidence
from app.services.case_management import get_case, get_case_evidence_paths


router = APIRouter(prefix='/exports', tags=['exports'])


def _download_response(content: str | bytes, media_type: str, file_name: str) -> Response:
    headers = {'Content-Disposition': f'attachment; filename="{file_name}"'}
    payload = content if isinstance(content, bytes) else content.encode('utf-8')
    return Response(content=payload, media_type=media_type, headers=headers)


def _case_artifacts(case_id: str) -> tuple[dict[str, object], dict[str, str | bytes]]:
    try:
        case = get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        evidence_paths = get_case_evidence_paths(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit_entries = load_audit_log_entries(case_id=case_id)
    artifacts = build_case_artifacts_from_evidence(case=case, evidence_paths=evidence_paths, audit_entries=audit_entries)
    return case, artifacts


@router.get('/cases/{case_id}/report.csv')
def export_case_csv(case_id: str) -> Response:
    case, artifacts = _case_artifacts(case_id)
    return _download_response(artifacts['csv'], 'text/csv; charset=utf-8', f'{case["id"]}_report.csv')


@router.get('/cases/{case_id}/report.pdf')
def export_case_pdf(case_id: str) -> Response:
    case, artifacts = _case_artifacts(case_id)
    return _download_response(artifacts['pdf'], 'application/pdf', f'{case["id"]}_report.pdf')


@router.get('/cases/{case_id}/graph.graphml')
def export_case_graphml(case_id: str) -> Response:
    case, artifacts = _case_artifacts(case_id)
    return _download_response(artifacts['graphml'], 'application/xml; charset=utf-8', f'{case["id"]}_graph.graphml')


@router.get('/cases/{case_id}/graph.gexf')
def export_case_gexf(case_id: str) -> Response:
    case, artifacts = _case_artifacts(case_id)
    return _download_response(artifacts['gexf'], 'application/xml; charset=utf-8', f'{case["id"]}_graph.gexf')
