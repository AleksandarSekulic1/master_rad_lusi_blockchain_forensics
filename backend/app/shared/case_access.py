"""Shared "load this case, 404 if missing" helpers.

Every case-scoped feature slice (case_management, case_graph, case_behavioral_analysis,
case_dex_swap_analysis, case_token_approval_analysis, case_seed_suggestion,
case_analytics_run, case_pathfinding) needs the exact same three steps before it can do
anything: load the case (or 404), resolve its evidence CSV paths (or 404), and optionally
narrow that to one evidence file (or 404 if it does not exist in the case). Kept here once
rather than duplicated per slice.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.services.case_management import get_case, get_case_evidence_paths


def get_case_or_404(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def get_case_evidence_paths_or_404(case: dict[str, object]) -> list[tuple[dict[str, object], object]]:
    try:
        return get_case_evidence_paths(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def filter_evidence_paths(
    evidence_paths: list[tuple[dict[str, object], object]],
    stored_name: str | None,
) -> list[tuple[dict[str, object], object]]:
    if not stored_name:
        return evidence_paths

    filtered = [(entry, path) for entry, path in evidence_paths if entry.get('stored_name') == stored_name]
    if not filtered:
        raise HTTPException(status_code=404, detail=f'Evidence {stored_name} not found in case')
    return filtered
