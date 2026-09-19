"""Flow of Funds / Layering Analysis: traces aggregated flows outward (or backward) from one
or more seed addresses across a case's own transaction graph, several hops ("levels") at a
time - see app/analytics/flow_of_funds.py for the algorithm and why it never returns raw
per-transaction rows.

Same GET (passive) / POST .../run (deliberate, custody-gated) split as
case_dex_swap_analysis: GET is read-only for a live/preview UI, POST /run is the deliberate
variant used by an actual "Run" button and, when a `custody` entry is present, records it in
both chains of custody before returning.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics.case_graph import clean_evidence_frames, combine_frames
from app.analytics.flow_of_funds import (
    DEFAULT_LEVELS,
    DEFAULT_MAX_FLOWS,
    MAX_LEVELS,
    MAX_MAX_FLOWS,
    MIN_LEVELS,
    MIN_MAX_FLOWS,
    trace_flow_of_funds,
)
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_flow_of_funds.models import FlowOfFundsRunRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])

_VALID_DIRECTIONS = ('forward', 'backward')


@router.get('/{case_id}/flow-of-funds')
def get_case_flow_of_funds(
    case_id: str,
    source: list[str] = Query(default=[]),
    direction: str = Query(default='forward'),
    max_levels: int = Query(default=DEFAULT_LEVELS, ge=MIN_LEVELS, le=MAX_LEVELS),
    min_amount: float = Query(default=0.0, ge=0.0),
    max_flows: int = Query(default=DEFAULT_MAX_FLOWS, ge=MIN_MAX_FLOWS, le=MAX_MAX_FLOWS),
    evidence: str | None = None,
) -> dict[str, object]:
    """Read-only: only re-reads already-cleaned evidence, no new custody dialog, no audit
    log entry. `source` may be repeated (`?source=0xA&source=0xB`) to trace from several
    seed addresses at once.
    """
    if not source:
        raise HTTPException(status_code=400, detail='Polje "source" je obavezno (bar jedna adresa).')
    if direction not in _VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f'Nepoznat direction: {direction}')

    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    try:
        result = trace_flow_of_funds(
            combined_frame,
            source_addresses=source,
            direction=direction,
            max_levels=max_levels,
            min_amount=min_amount,
            max_flows=max_flows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.post('/{case_id}/flow-of-funds/run')
def run_case_flow_of_funds(
    case_id: str,
    request: FlowOfFundsRunRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Deliberate variant of GET .../flow-of-funds above: identical trace, but treated as a
    deliberate access to every transaction in the evidence scope it walks (like "Pokreni
    taint analizu"/"FIND PATH"/"Analiziraj graf" - see LANAC-DOKAZA.md), so it accepts an
    optional `custody` entry and, when present, records it in both chains of custody before
    returning.
    """
    if request.direction not in _VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f'Nepoznat direction: {request.direction}')

    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Built from the per-evidence-file frames (like case_analytics_run/case_pathfinding/
    # case_dex_swap_analysis) rather than combine_frames(clean_evidence_frames(...)) alone,
    # so each row can be tagged with the specific evidence file it came from for custody.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)

    try:
        result = trace_flow_of_funds(
            combined_frame,
            source_addresses=request.source_addresses,
            direction=request.direction,
            max_levels=request.max_levels,
            min_amount=request.min_amount,
            max_flows=request.max_flows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    has_custody = bool(request.custody)
    write_audit_log(
        action='flow_of_funds_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'source_addresses': request.source_addresses,
            'direction': request.direction,
            'max_levels': request.max_levels,
            'evidence_scope': evidence or 'combined',
            'flow_count': result['flow_count'],
            'levels_reached': result['levels_reached'],
            'truncated': result['truncated'],
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

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result
