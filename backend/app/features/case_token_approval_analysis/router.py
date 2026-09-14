"""Token Approval / Ice Phishing Analysis: extracts and classifies ERC-20
approve()/EIP-2612 permit() grants and their transferFrom() usage from a case's evidence
(see app/analytics/token_approval_analysis.py and TOKEN-APPROVAL-IMPLEMENTATION.md for
exactly which fields are read vs. derived vs. reported as unavailable).

Three read-only GET views (analysis / history / correlation) plus one deliberate-access
POST /run that, on top of correlating usage, attaches a structured per-transaction
TOKEN_APPROVAL evidence item to the chain of custody (see service.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics.case_graph import clean_evidence_frames, combine_frames
from app.analytics.token_approval_analysis import (
    DEFAULT_LARGE_AMOUNT_THRESHOLD,
    DEFAULT_LONG_ACTIVE_PERIOD_SECONDS,
    DEFAULT_MULTIPLE_TRANSFER_THRESHOLD,
    DEFAULT_RAPID_USE_SECONDS,
    DEFAULT_UNLIMITED_THRESHOLD,
    MAX_LARGE_AMOUNT_THRESHOLD,
    MAX_LONG_ACTIVE_PERIOD_SECONDS,
    MAX_MULTIPLE_TRANSFER_THRESHOLD,
    MAX_RAPID_USE_SECONDS,
    MAX_UNLIMITED_THRESHOLD,
    MIN_LARGE_AMOUNT_THRESHOLD,
    MIN_LONG_ACTIVE_PERIOD_SECONDS,
    MIN_MULTIPLE_TRANSFER_THRESHOLD,
    MIN_RAPID_USE_SECONDS,
    MIN_UNLIMITED_THRESHOLD,
    analyze_token_approvals,
    build_token_approval_history,
    correlate_approval_usage,
)
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_token_approval_analysis import service
from app.features.case_token_approval_analysis.models import TokenApprovalAnalysisRunRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/token-approval-analysis')
def get_case_token_approval_analysis(
    case_id: str,
    address: str | None = Query(default=None),
    evidence: str | None = None,
    unlimited_threshold: float = Query(default=DEFAULT_UNLIMITED_THRESHOLD, ge=MIN_UNLIMITED_THRESHOLD, le=MAX_UNLIMITED_THRESHOLD),
    rapid_use_seconds: int = Query(default=DEFAULT_RAPID_USE_SECONDS, ge=MIN_RAPID_USE_SECONDS, le=MAX_RAPID_USE_SECONDS),
    large_amount_threshold: float = Query(default=DEFAULT_LARGE_AMOUNT_THRESHOLD, ge=MIN_LARGE_AMOUNT_THRESHOLD, le=MAX_LARGE_AMOUNT_THRESHOLD),
    multiple_transfer_threshold: int = Query(default=DEFAULT_MULTIPLE_TRANSFER_THRESHOLD, ge=MIN_MULTIPLE_TRANSFER_THRESHOLD, le=MAX_MULTIPLE_TRANSFER_THRESHOLD),
    long_active_period_seconds: int = Query(default=DEFAULT_LONG_ACTIVE_PERIOD_SECONDS, ge=MIN_LONG_ACTIVE_PERIOD_SECONDS, le=MAX_LONG_ACTIVE_PERIOD_SECONDS),
) -> dict[str, object]:
    """`address` is optional - omitted, every approval group found in the evidence is
    returned; given, the result is scoped to groups where that address is the owner OR the
    spender (404 if it never appears in the evidence at all).

    Reads the case's cleaned per-transaction DataFrame directly (NOT the shared
    transaction graph) - the shared graph deliberately does not carry the extra fields
    (event_type, token_address, spender_address...) this analysis needs.

    Read-only: no custody dialog, no audit log entry.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    normalized_address = address.strip() if address else None

    try:
        result = analyze_token_approvals(
            combined_frame,
            target_address=normalized_address,
            unlimited_threshold=unlimited_threshold,
            rapid_use_seconds=rapid_use_seconds,
            large_amount_threshold=large_amount_threshold,
            multiple_transfer_threshold=multiple_transfer_threshold,
            long_active_period_seconds=long_active_period_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.get('/{case_id}/token-approval-history')
def get_case_token_approval_history(
    case_id: str,
    address: str = Query(min_length=1),
    evidence: str | None = None,
    unlimited_threshold: float = Query(default=DEFAULT_UNLIMITED_THRESHOLD, ge=MIN_UNLIMITED_THRESHOLD, le=MAX_UNLIMITED_THRESHOLD),
    rapid_use_seconds: int = Query(default=DEFAULT_RAPID_USE_SECONDS, ge=MIN_RAPID_USE_SECONDS, le=MAX_RAPID_USE_SECONDS),
    large_amount_threshold: float = Query(default=DEFAULT_LARGE_AMOUNT_THRESHOLD, ge=MIN_LARGE_AMOUNT_THRESHOLD, le=MAX_LARGE_AMOUNT_THRESHOLD),
    multiple_transfer_threshold: int = Query(default=DEFAULT_MULTIPLE_TRANSFER_THRESHOLD, ge=MIN_MULTIPLE_TRANSFER_THRESHOLD, le=MAX_MULTIPLE_TRANSFER_THRESHOLD),
    long_active_period_seconds: int = Query(default=DEFAULT_LONG_ACTIVE_PERIOD_SECONDS, ge=MIN_LONG_ACTIVE_PERIOD_SECONDS, le=MAX_LONG_ACTIVE_PERIOD_SECONDS),
) -> dict[str, object]:
    """Token Approval history for ONE address: reconstructs APPROVE -> allowance change ->
    eventual REVOCATION -> current status per grant (see
    analytics/token_approval_analysis.build_token_approval_history and
    TOKEN-APPROVAL-IMPLEMENTATION.md #13). Unlike get_case_token_approval_analysis above,
    `address` is REQUIRED - a "history" only makes sense for a specific address.

    Same read-only treatment as get_case_token_approval_analysis: no custody dialog, no
    audit log entry.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    normalized_address = address.strip()

    try:
        result = build_token_approval_history(
            combined_frame,
            address=normalized_address,
            unlimited_threshold=unlimited_threshold,
            rapid_use_seconds=rapid_use_seconds,
            large_amount_threshold=large_amount_threshold,
            multiple_transfer_threshold=multiple_transfer_threshold,
            long_active_period_seconds=long_active_period_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.get('/{case_id}/token-approval-correlation')
def get_case_token_approval_correlation(
    case_id: str,
    address: str | None = Query(default=None),
    evidence: str | None = None,
    unlimited_threshold: float = Query(default=DEFAULT_UNLIMITED_THRESHOLD, ge=MIN_UNLIMITED_THRESHOLD, le=MAX_UNLIMITED_THRESHOLD),
    rapid_use_seconds: int = Query(default=DEFAULT_RAPID_USE_SECONDS, ge=MIN_RAPID_USE_SECONDS, le=MAX_RAPID_USE_SECONDS),
    large_amount_threshold: float = Query(default=DEFAULT_LARGE_AMOUNT_THRESHOLD, ge=MIN_LARGE_AMOUNT_THRESHOLD, le=MAX_LARGE_AMOUNT_THRESHOLD),
    multiple_transfer_threshold: int = Query(default=DEFAULT_MULTIPLE_TRANSFER_THRESHOLD, ge=MIN_MULTIPLE_TRANSFER_THRESHOLD, le=MAX_MULTIPLE_TRANSFER_THRESHOLD),
    long_active_period_seconds: int = Query(default=DEFAULT_LONG_ACTIVE_PERIOD_SECONDS, ge=MIN_LONG_ACTIVE_PERIOD_SECONDS, le=MAX_LONG_ACTIVE_PERIOD_SECONDS),
) -> dict[str, object]:
    """Correlates each individual approve()/permit() grant with its later transferFrom()
    usage: OWNER -> APPROVAL -> SPENDER -> transferFrom -> token transfer (see
    analytics/token_approval_analysis.correlate_approval_usage and
    TOKEN-APPROVAL-IMPLEMENTATION.md #14). `address` is optional - omitted, every grant in
    the evidence is correlated; given, scoped to grants where that address is the owner or
    spender (404 if it never appears in the evidence at all).

    Same read-only treatment as get_case_token_approval_analysis/-history: no custody
    dialog, no audit log entry.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    normalized_address = address.strip() if address else None

    try:
        result = correlate_approval_usage(
            combined_frame,
            target_address=normalized_address,
            unlimited_threshold=unlimited_threshold,
            rapid_use_seconds=rapid_use_seconds,
            large_amount_threshold=large_amount_threshold,
            multiple_transfer_threshold=multiple_transfer_threshold,
            long_active_period_seconds=long_active_period_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['case_id'] = case_id
    result['evidence'] = evidence
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    return result


@router.post('/{case_id}/token-approval-analysis/run')
def run_case_token_approval_analysis(
    case_id: str,
    request: TokenApprovalAnalysisRunRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Deliberate variant of get_case_token_approval_correlation above: identical
    correlation, but treated as a deliberate access to every transaction in the evidence
    scope (like "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps' ANALYZE -
    see LANAC-DOKAZA.md), so it accepts an optional `custody` entry and, when present,
    records it in both chains of custody via the same, unmodified-in-shape
    `record_custody_access()` every other analysis already uses.

    Beyond that shared per-transaction/per-evidence-file record, EACH individual approve()/
    permit() row's own custody entry additionally carries a structured `token_approval_
    evidence` item (see service.token_approval_custody_enrichment) - Owner/Spender/Token/
    Allowance/Approval timestamp/Transaction hash/Status/First transferFrom/Total
    transferred/Risk/Reasons, explicitly split into `blockchain_facts` (read directly from
    the evidence), `computed_indicators` (deterministically derived - counts, matching,
    status) and `heuristic_conclusions` (the risk assessment, always disclaimed) - see
    TOKEN-APPROVAL-IMPLEMENTATION.md #18 for the full field-by-field rationale. This makes
    Token Approval Analysis a full peer of Taint/Graph/Pathfinding/Behavioral/DEX Swap in
    the chain of evidence, not a passive-only analysis - using the SAME custody_log.jsonl/
    custody_evidence_log.jsonl files and the SAME "Lanac dokaza" page/PDF export, never a
    parallel log.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Built from the per-evidence-file frames (like case_analytics_run/case_pathfinding/
    # case_dex_swap_analysis) rather than via combine_frames(clean_evidence_frames(...))
    # alone, so each row can be tagged with the specific evidence file it came from, both
    # for the custody log (existing behaviour) and for the tx_id-keyed enrichment below.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)

    normalized_address = request.address.strip() if request.address else None

    try:
        result = correlate_approval_usage(
            combined_frame,
            target_address=normalized_address,
            unlimited_threshold=request.unlimited_threshold,
            rapid_use_seconds=request.rapid_use_seconds,
            large_amount_threshold=request.large_amount_threshold,
            multiple_transfer_threshold=request.multiple_transfer_threshold,
            long_active_period_seconds=request.long_active_period_seconds,
        )
    except ValueError as exc:
        # FAILED - written even though nothing was found/analysed, so an investigator can
        # later see that a Token Approval run was ATTEMPTED for this address and why it
        # did not complete (see TOKEN-APPROVAL-IMPLEMENTATION.md #19.2) - same "log the
        # attempt, not just the success" discipline the rest of this section documents.
        write_audit_log(
            action='token_approval_analysis_run',
            user=str(current_user['username']),
            case_id=case_id,
            case_name=str(case.get('name') or ''),
            details={
                'status': 'FAILED',
                'address': normalized_address,
                'evidence_scope': evidence or 'combined',
                'error': str(exc),
            },
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    has_custody = bool(request.custody)
    findings_recorded = 0

    if request.custody:
        # Unfiltered (target_address=None) and tagged with evidence-file identity, so
        # EVERY approval row genuinely touched by this run gets its own chain-of-evidence
        # entry - not only the ones matching `request.address` - same "the whole scanned
        # evidence was accessed" principle LANAC-DOKAZA.md §2 already applies to every
        # other analysis (see TOKEN-APPROVAL-IMPLEMENTATION.md #18.3 for why this is a
        # second, separate computation from `result` above rather than reusing it).
        tagged_frame = service.combine_frames_with_evidence_tag(per_evidence_frames)
        try:
            full_result = correlate_approval_usage(
                tagged_frame,
                target_address=None,
                unlimited_threshold=request.unlimited_threshold,
                rapid_use_seconds=request.rapid_use_seconds,
                large_amount_threshold=request.large_amount_threshold,
                multiple_transfer_threshold=request.multiple_transfer_threshold,
                long_active_period_seconds=request.long_active_period_seconds,
            )
        except ValueError:
            full_result = {'correlations': [], 'groups': []}
        extra_transaction_fields = service.token_approval_custody_enrichment(full_result)
        findings_recorded = len(extra_transaction_fields)

    write_audit_log(
        action='token_approval_analysis_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'status': 'SUCCESS',
            'address': normalized_address,
            'evidence_scope': evidence or 'combined',
            'correlation_count': result['correlation_count'],
            **service.token_approval_summary_counts(result),
            'custody_recorded': has_custody,
            'custody_transaction_rows': int(len(combined_frame)) if has_custody else 0,
            'custody_evidence_files': len(per_evidence_frames) if has_custody else 0,
            'token_approval_findings_recorded': findings_recorded,
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
    return result
