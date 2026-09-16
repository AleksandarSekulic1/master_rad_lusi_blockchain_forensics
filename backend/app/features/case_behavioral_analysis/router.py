"""Behavioral / Time-of-Day Analysis: UTC hour-of-day and day-of-week transaction pattern
for one address, from a case's transaction graph, plus a heuristic UTC-offset/region
compatibility estimate layered on top (see app/analytics/timezone_heuristics.py for what
that estimate does and does not claim).

GET is the read-only, passive variant (no custody dialog, no audit log entry). POST /run
is its deliberate-access counterpart: identical result, but accepts a `custody` entry and,
when present, records it in both chains of custody before returning.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics.behavioral_analysis import analyze_time_of_day
from app.analytics.case_graph import build_case_graph, clean_evidence_frames, combine_frames
from app.analytics.graph_building import build_transaction_graph
from app.analytics.timezone_heuristics import estimate_timezone_compatibility
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_behavioral_analysis.models import BehavioralAnalysisRunRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/behavioral-analysis')
def get_case_behavioral_analysis(
    case_id: str,
    address: str = Query(min_length=1),
    evidence: str | None = None,
) -> dict[str, object]:
    """First version - UTC only, no timezone/continent inference. Read-only: this only
    re-reads the case's own already-built graph structure (no new analytics pipeline run),
    so there is no custody dialog and no write_audit_log call here, unlike the
    deliberate-access POST /run below."""
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)

    _, graph = build_case_graph(evidence_paths)

    try:
        result = analyze_time_of_day(graph, address.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Heuristic UTC-offset/region compatibility estimate, layered on top of the raw
    # hourly_distribution analyze_time_of_day() already computed - see
    # timezone_heuristics.py's module docstring for what this claim is (and is not).
    result['timezone_estimate'] = estimate_timezone_compatibility(result['hourly_distribution'], result['total_transactions'])

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.post('/{case_id}/behavioral-analysis/run')
def run_case_behavioral_analysis(
    case_id: str,
    request: BehavioralAnalysisRunRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Deliberate-access counterpart of GET /behavioral-analysis: identical result, but
    this route accepts a `custody` entry and, when present, records it in both chains of
    custody (`record_custody_access`) before returning - so running the analysis from the
    UI leaves the same audit trail as Taint / Pathfinding / DEX Swaps. The read-only GET
    route stays for passive/embedded use.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Per-evidence-file frames (like case_pathfinding) so record_custody_access can tag
    # each custody row with the evidence file it came from.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)
    graph = build_transaction_graph(combined_frame)

    try:
        result = analyze_time_of_day(graph, request.address.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['timezone_estimate'] = estimate_timezone_compatibility(
        result['hourly_distribution'], result['total_transactions']
    )
    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()

    has_custody = bool(request.custody)
    write_audit_log(
        action='behavioral_analysis_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'address': request.address.strip(),
            'evidence_scope': evidence or 'combined',
            'total_transactions': result['total_transactions'],
            'custody_recorded': has_custody,
            'custody_transaction_rows': int(len(combined_frame)) if has_custody else 0,
            'custody_evidence_files': len(per_evidence_frames) if has_custody else 0,
        },
    )

    if request.custody:
        record_custody_access(
            case=case,
            per_evidence_frames=per_evidence_frames,
            custody=request.custody,
            user=str(current_user['username']),
        )

    return result
