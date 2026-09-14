"""Runs the full analytics plugin pipeline (taint analysis, chain hopping, peel chains,
wallet clustering, ...) over a case's evidence graph, optionally scoped to one evidence
file - the "Pokreni taint analizu" / "Analiziraj graf" endpoint.

Called two different ways: passively, by the Kontrolna tabla's summary tiles and by the
Graf page's automatic (uncoloured) preview on selection - no `custody` needed there - and
deliberately, by the Taint Analysis page's "Pokreni taint analizu" button and the Graf
page's own "Analiziraj graf" button, both of which always supply one. When
`request.custody` is present, running the analysis is treated as re-accessing every
transaction AND every evidence file it processes, and that access is recorded in both
chains of custody before the response is returned.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.analytics.case_graph import clean_evidence_frames, combine_frames, graph_summary
from app.analytics.graph_building import build_transaction_graph, transaction_graph_to_node_link_json
from app.analytics.plugins.manager import run_plugin_pipeline
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_analytics_run.models import RunAnalyticsRequest
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.post('/{case_id}/analytics/run')
def run_case_analytics(
    case_id: str,
    evidence: str | None = None,
    request: RunAnalyticsRequest | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)

    # Built from the per-evidence-file frames (rather than via build_case_graph) so each
    # row can be tagged with the specific evidence file it came from for the custody log -
    # build_case_graph itself only returns the already-concatenated frame.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)
    graph = build_transaction_graph(combined_frame)
    seed_addresses = request.seed_addresses if request else None
    plugin_results = run_plugin_pipeline(dataframe=combined_frame, graph=graph, seed_addresses=seed_addresses)

    # Which analysis produced a given finding is itself part of the chain of custody: two
    # analysts running the same case over a different evidence scope, or with a different
    # seed list, legitimately get different percentages - without this record there is no
    # way to reconstruct afterwards which run a disputed number actually came from.
    #
    # 'custody_recorded' and the two row counts answer, right here in the general activity
    # log, the question "was this particular run also written into the lanac dokaza, and
    # at which granularity" - without them, a reader would have to cross-reference the
    # separate custody log files to tell a passive graph preview apart from a deliberate,
    # signed access to the same evidence.
    has_custody = bool(request and request.custody)
    write_audit_log(
        action='analytics_run',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'evidence_scope': evidence or 'combined',
            'seed_addresses': seed_addresses or [],
            'seed_count': len(seed_addresses or []),
            'rows': int(len(combined_frame)),
            'node_count': int(graph.number_of_nodes()),
            'custody_recorded': has_custody,
            'custody_transaction_rows': int(len(combined_frame)) if has_custody else 0,
            'custody_evidence_files': len(per_evidence_frames) if has_custody else 0,
        },
    )

    if request and request.custody:
        record_custody_access(
            case=case,
            per_evidence_frames=per_evidence_frames,
            custody=request.custody,
            user=str(current_user['username']),
        )

    payload = transaction_graph_to_node_link_json(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    payload['rows'] = int(len(combined_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    payload['analytics'] = plugin_results
    payload['summary'] = graph_summary(graph)
    return payload
