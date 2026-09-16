"""Pathfinding Analysis: unweighted BFS over a case's own transaction graph, toward one of
two kinds of destination - a specific address, or the nearest known exchange (see
app/analytics/path_finding.py).

Running a search is itself a deliberate access to every transaction it traverses, same as
"Pokreni taint analizu"/"Analiziraj graf" - so this route accepts an optional `custody`
entry and, when present, records it in both chains of custody before returning.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.analytics.case_graph import clean_evidence_frames, combine_frames
from app.analytics.graph_building import build_transaction_graph
from app.analytics.path_finding import bfs_shortest_path, find_path_to_nearest_of
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.case_pathfinding.models import CasePathfindingRequest
from app.services.address_enrichment import get_known_entity
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.custody_recording import record_custody_access

router = APIRouter(prefix='/cases', tags=['cases'])


@router.post('/{case_id}/pathfinding')
def run_case_pathfinding(
    case_id: str,
    request: CasePathfindingRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """- 'specific_address' (default): the exact address the caller picked - the original
      first-version behaviour, response stays exactly {found, path, hops}, unchanged.
    - 'nearest_cex': the nearest address, by hop count, that the LOCAL known_entities
      registry (app.services.address_enrichment.get_known_entity) flags as an exchange -
      never a guess based on an address's name/label, only what that offline registry
      actually says. Response additionally carries destination_address/destination_label,
      and a message when nothing is found, so the reader can tell "no CEX anywhere in
      this evidence" apart from "one exists but isn't reachable from here".

    Deliberately minimal beyond that: no weighting, no multiple paths, no cash-out-point
    detection yet.
    """
    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    # Built from per-evidence-file frames (like case_analytics_run) rather than via
    # build_case_graph, so record_custody_access can tag each row with the evidence file
    # it came from - functionally identical graph, just exposes the per-file split too.
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)
    graph = build_transaction_graph(combined_frame)

    mode = request.destination_mode
    if mode == 'specific_address':
        if not request.to_address:
            raise HTTPException(status_code=400, detail='Polje "to" je obavezno za destination_mode "specific_address".')
        result: dict[str, object] = bfs_shortest_path(graph, request.from_address, request.to_address)
        target_for_log: str | None = request.to_address
    elif mode == 'nearest_cex':
        # Only addresses the LOCAL registry actually flags as an exchange, and only among
        # nodes present in THIS graph - never an assumption based on how an address looks.
        cex_addresses = {
            str(node)
            for node in graph.nodes
            if (entity := get_known_entity(str(node))) is not None and entity['category'] == 'exchange'
        }
        nearest = find_path_to_nearest_of(graph, request.from_address, cex_addresses)
        destination = nearest.get('destination')
        destination_entity = get_known_entity(str(destination)) if destination else None
        result = {
            'found': nearest['found'],
            'path': nearest['path'],
            'hops': nearest['hops'],
            'destination_address': destination,
            'destination_label': destination_entity['name'] if destination_entity else None,
        }
        if not nearest['found']:
            result['message'] = (
                'Nijedna poznata CEX adresa nije prisutna u ovoj evidenciji.'
                if not cex_addresses
                else 'Nijedna poznata CEX adresa nije dostupna (nema puta) od izabrane polazne adrese u ovoj evidenciji.'
            )
        target_for_log = str(destination) if destination else None
    else:
        raise HTTPException(status_code=400, detail=f'Nepoznat ili još nepodržan destination_mode: {mode}')

    # Same 'path_finding' action the old standalone /graph/path-finding route already
    # writes (see app/api/routes/graph.py) - reused rather than inventing a new action
    # name, so it picks up the existing label/colour/summary in Log aktivnosti and the
    # activity report for free, now with a case_id attached.
    has_custody = bool(request.custody)
    write_audit_log(
        action='path_finding',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={
            'source_address': request.from_address,
            'target_address': target_for_log,
            'destination_mode': mode,
            'evidence_scope': evidence or 'combined',
            'found': result['found'],
            'hops': result['hops'],
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
