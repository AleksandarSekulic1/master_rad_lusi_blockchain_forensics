"""DEX Swap Analysis: best-effort detection of "wallet -> known/likely DEX contract, then
that same contract -> the same wallet" pairs in a case's evidence, flagged as a 'Detected'
or 'Potential' swap depending on how strong the match is (see
app/analytics/dex_swap_analysis.py and DEX-SWAP-ANALIZA.md).

GET is the read-only, passive variant used by the Graph page's DEX swap overlay. POST /run
is its deliberate-access counterpart, used by the DEX Swap Analysis page's own ANALYZE
button: identical detection, but accepts a `custody` entry and, when present, records it
in both chains of custody before returning.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics.case_graph import clean_evidence_frames, combine_frames
from app.analytics.dex_swap_analysis import (
    DEFAULT_MAX_GAP_SECONDS,
    MAX_MAX_GAP_SECONDS,
    MIN_MAX_GAP_SECONDS,
    detect_dex_swaps,
)
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_dex_swap_analysis.models import DexSwapAnalysisRunRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/dex-swap-analysis')
def get_case_dex_swap_analysis(
    case_id: str,
    address: str | None = Query(default=None),
    evidence: str | None = None,
    max_gap_seconds: int = Query(default=DEFAULT_MAX_GAP_SECONDS, ge=MIN_MAX_GAP_SECONDS, le=MAX_MAX_GAP_SECONDS),
) -> dict[str, object]:
    """`address` is optional - omitted, every candidate swap in the evidence is returned;
    given, the result is scoped to that one address (and a 404 if it never appears in the
    evidence at all).

    Reads the case's cleaned per-transaction DataFrame directly (NOT the shared
    transaction graph) because the shared graph deliberately does not carry a
    per-transaction currency/token field - see DEX-SWAP-ANALIZA.md #2.

    Read-only: only re-reads already-cleaned evidence, no new custody dialog, no audit log
    entry.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    normalized_address = address.strip() if address else None

    try:
        result = detect_dex_swaps(combined_frame, target_address=normalized_address, max_gap_seconds=max_gap_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.post('/{case_id}/dex-swap-analysis/run')
def run_case_dex_swap_analysis(
    case_id: str,
    request: DexSwapAnalysisRunRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Deliberate variant of GET .../dex-swap-analysis above: identical detection, but
    treated as a deliberate access to every transaction in the evidence scope (like
    "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf" - see LANAC-DOKAZA.md), so it
    accepts an optional `custody` entry and, when present, records it in both chains of
    custody (`record_custody_access`) before returning.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Built from the per-evidence-file frames (like case_analytics_run/case_pathfinding)
    # rather than via combine_frames(clean_evidence_frames(...)) alone, so each row can be
    # tagged with the specific evidence file it came from for the custody log.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)

    normalized_address = request.address.strip() if request.address else None

    try:
        result = detect_dex_swaps(combined_frame, target_address=normalized_address, max_gap_seconds=request.max_gap_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    has_custody = bool(request.custody)
    write_audit_log(
        action='dex_swap_analysis_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'address': normalized_address,
            'evidence_scope': evidence or 'combined',
            'max_gap_seconds': request.max_gap_seconds,
            'total_events': result['total_events'],
            'detected_count': result['detected_count'],
            'potential_count': result['potential_count'],
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
