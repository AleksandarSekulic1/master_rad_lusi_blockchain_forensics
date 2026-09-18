"""Sybil & Bot Network Analysis: best-effort detection of groups of DIFFERENT addresses
that call the same smart contract and/or the same function within a short, synchronized
time window (see app/analytics/sybil_analysis.py and SYBIL-ANALIZA.md). This is a
HEURISTIC - it never claims the flagged addresses belong to the same person or entity.

GET is the read-only, passive variant. POST /run is its deliberate-access counterpart:
identical detection, but accepts a `custody` entry and, when present, records it in both
chains of custody before returning - same shape as case_dex_swap_analysis/
case_behavioral_analysis. On top of that shared per-transaction/per-evidence-file record,
a Sybil run additionally attaches a structured `sybil_evidence` item to each transaction
that landed inside a FLAGGED cluster (see service.sybil_custody_enrichment), the same
pattern case_token_approval_analysis already established for its own findings - see
SYBIL-ANALIZA.md #12 for the full field-by-field rationale.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics.case_graph import clean_evidence_frames, combine_frames
from app.analytics.sybil_analysis import (
    DEFAULT_MIN_ADDRESSES,
    DEFAULT_TIME_WINDOW_SECONDS,
    MAX_MIN_ADDRESSES,
    MAX_TIME_WINDOW_SECONDS,
    MIN_MIN_ADDRESSES,
    MIN_TIME_WINDOW_SECONDS,
    detect_sybil_clusters,
)
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_sybil_analysis import service
from app.features.case_sybil_analysis.models import SybilAnalysisRunRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/sybil-analysis')
def get_case_sybil_analysis(
    case_id: str,
    address: str | None = Query(default=None),
    contract: str | None = Query(default=None),
    evidence: str | None = None,
    time_window_seconds: int = Query(default=DEFAULT_TIME_WINDOW_SECONDS, ge=MIN_TIME_WINDOW_SECONDS, le=MAX_TIME_WINDOW_SECONDS),
    min_addresses: int = Query(default=DEFAULT_MIN_ADDRESSES, ge=MIN_MIN_ADDRESSES, le=MAX_MIN_ADDRESSES),
) -> dict[str, object]:
    """`address` and `contract` are both optional - omitted, every candidate Sybil cluster
    in the evidence is returned; given, the result is scoped accordingly (404 if the value
    never appears in the evidence at all).

    Reads the case's cleaned per-transaction DataFrame directly (NOT the shared transaction
    graph) - see sybil_analysis.py's module docstring for why.

    Read-only: only re-reads already-cleaned evidence, no new custody dialog, no audit log
    entry.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    normalized_address = address.strip() if address else None
    normalized_contract = contract.strip() if contract else None

    try:
        result = detect_sybil_clusters(
            combined_frame,
            target_address=normalized_address,
            contract=normalized_contract,
            time_window_seconds=time_window_seconds,
            min_addresses=min_addresses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.post('/{case_id}/sybil-analysis/run')
def run_case_sybil_analysis(
    case_id: str,
    request: SybilAnalysisRunRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Deliberate variant of GET .../sybil-analysis above: identical detection, but treated
    as a deliberate access to every transaction in the evidence scope (like "Pokreni taint
    analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps' ANALYZE - see LANAC-DOKAZA.md), so it
    accepts an optional `custody` entry and, when present, records it in both chains of
    custody (`record_custody_access`) before returning.

    Beyond that shared per-transaction/per-evidence-file record, EACH transaction that
    landed inside a FLAGGED cluster additionally gets a structured `sybil_evidence` item on
    its own custody entry - Sender/Contract/Amount/Timestamp/Transaction hash/Block/
    Function (blockchain facts, read straight from the row) plus the cluster's own
    address count/timing/risk score/reasons (heuristic conclusions) - explicitly split so a
    reader of the chain of evidence can never mistake one kind of claim for the other (see
    service.sybil_custody_enrichment and SYBIL-ANALIZA.md #12).
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Built from the per-evidence-file frames (like case_dex_swap_analysis/case_pathfinding)
    # rather than via combine_frames(clean_evidence_frames(...)) alone, so each row can be
    # tagged with the specific evidence file it came from for the custody log.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)

    normalized_address = request.address.strip() if request.address else None
    normalized_contract = request.contract.strip() if request.contract else None

    try:
        result = detect_sybil_clusters(
            combined_frame,
            target_address=normalized_address,
            contract=normalized_contract,
            time_window_seconds=request.time_window_seconds,
            min_addresses=request.min_addresses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    has_custody = bool(request.custody)
    findings_recorded = 0
    extra_transaction_fields: dict[str, dict[str, object]] = {}

    if request.custody:
        # Unfiltered (target_address=None, contract=None) and tagged with evidence-file
        # identity, so EVERY cluster genuinely found by this run gets its transactions
        # written to the chain of evidence - not only the ones matching request.address/
        # request.contract - same "the whole scanned evidence was accessed" principle
        # LANAC-DOKAZA.md §2 already applies to every other analysis (see
        # TOKEN-APPROVAL-IMPLEMENTATION.md #18.3 for the precedent this mirrors).
        tagged_frame = service.combine_frames_with_evidence_tag(per_evidence_frames)
        full_result = detect_sybil_clusters(
            tagged_frame,
            target_address=None,
            contract=None,
            time_window_seconds=request.time_window_seconds,
            min_addresses=request.min_addresses,
        )
        extra_transaction_fields = service.sybil_custody_enrichment(full_result)
        findings_recorded = len(extra_transaction_fields)

    write_audit_log(
        action='sybil_analysis_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'address': normalized_address,
            'contract': normalized_contract,
            'evidence_scope': evidence or 'combined',
            'time_window_seconds': request.time_window_seconds,
            'min_addresses': request.min_addresses,
            'total_clusters': result['total_clusters'],
            'addresses_flagged': result['addresses_flagged'],
            'custody_recorded': has_custody,
            'custody_transaction_rows': int(len(combined_frame)) if has_custody else 0,
            'custody_evidence_files': len(per_evidence_frames) if has_custody else 0,
            'sybil_findings_recorded': findings_recorded,
        },
    )

    if request.custody:
        record_custody_access(
            case=case,
            per_evidence_frames=per_evidence_frames,
            custody=request.custody,
            user=str(current_user['username']),
            extra_transaction_fields=extra_transaction_fields,
        )

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    result['custody_findings_recorded'] = findings_recorded
    return result
