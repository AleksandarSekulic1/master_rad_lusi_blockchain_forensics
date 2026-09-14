"""Rule-based, explained suggestions for taint analysis seed addresses (see
app/analytics/seed_suggestion.py).

Runs the analytics pipeline first so the pattern plugins (peel chain, chain hopping) have
annotated the graph, then applies the suggestion rules on top.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.analytics.case_graph import build_case_graph
from app.analytics.plugins.manager import run_plugin_pipeline
from app.analytics.seed_suggestion import suggest_seeds
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404

router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('/{case_id}/seed-suggestions')
def get_seed_suggestions(case_id: str, evidence: str | None = None) -> dict[str, object]:
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    run_plugin_pipeline(dataframe=combined_frame, graph=graph, seed_addresses=None)

    payload = suggest_seeds(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    return payload
