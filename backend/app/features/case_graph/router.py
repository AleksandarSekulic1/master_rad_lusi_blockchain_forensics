"""Read-only views of a case's transaction graph: the node-link JSON the Graf page renders,
and a raw cleaned-CSV export of the same underlying transactions.

Both endpoints only re-read already-cleaned evidence (no new custody dialog, no audit log
entry) - the deliberate, custody-gated variant that actually runs the analytics pipeline
lives in case_analytics_run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response

from app.analytics.case_graph import build_case_graph, clean_evidence_frames, combine_frames
from app.analytics.graph_building import transaction_graph_to_node_link_json
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/graph')
def get_case_graph(case_id: str, evidence: str | None = None) -> dict[str, object]:
    """Transaction graph for the case. Pass `evidence` (stored_name) to scope it to a single evidence file,
    otherwise it is combined across ALL evidence in the case."""
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    payload = transaction_graph_to_node_link_json(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    payload['rows'] = int(len(combined_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    return payload


@router.get('/{case_id}/transactions/export')
def export_case_transactions(case_id: str, evidence: str | None = None) -> Response:
    """Raw, cleaned per-transaction CSV export of the case's combined evidence (or one
    evidence file when `evidence` is given) - the exact same cleaned rows every analysis
    page reads from (see case_graph.combine_frames), not a re-encoded summary like
    exports.export_case_csv's report.csv (section/field/value key-value dump).

    Read-only, same treatment as get_case_graph above: only re-reads already-cleaned
    evidence, no new custody dialog, no audit log entry. The Dashboard's "Izvoz izveštaja"
    panel exposes this as a plain, unsigned "Izvezi CSV" button - separate from its signed
    PDF report, since raw transaction data isn't itself a presentation document that needs
    an examiner's signature.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))

    csv_text = combined_frame.to_csv(index=False)
    file_name = f'{case_id}_transactions.csv'
    return Response(
        content=csv_text,
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )
