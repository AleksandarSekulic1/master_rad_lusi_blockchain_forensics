from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.analytics.case_graph import build_case_graph, clean_evidence_frames, combine_frames, graph_summary
from app.analytics.graph_building import build_transaction_graph, transaction_graph_to_node_link_json
from app.analytics.path_finding import bfs_shortest_path, find_path_to_nearest_of
from app.analytics.plugins.manager import run_plugin_pipeline
from app.analytics.seed_suggestion import suggest_seeds
from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.custody_evidence_log import append_evidence_custody_batch
from app.evidence.custody_log import append_custody_batch
from app.evidence.tx_identity import transaction_id
from app.services.address_enrichment import get_known_entity
from app.services.case_management import (
    create_case,
    delete_case,
    get_case,
    get_case_evidence_paths,
    list_cases,
    set_case_status,
)


def _get_case_or_404(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


router = APIRouter(prefix='/cases', tags=['cases'])


class CreateCaseRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class SetCaseStatusRequest(BaseModel):
    status: str = Field(pattern='^(open|closed)$')


class TransactionCustodyEntry(BaseModel):
    """What the analyst is asserting by deliberately running a taint analysis: who they
    are and why they are accessing this evidence right now. When present, every field is
    required - a caller cannot send a token "empty" custody object just to satisfy the
    shape; it is either a real entry or omitted entirely (see RunAnalyticsRequest.custody).
    """

    ime_prezime: str = Field(min_length=1)
    opis_radnje: str = Field(min_length=1)
    signature_image: str = Field(min_length=1)
    identifikator_predmeta: str | None = None
    identifikator_dokaznog_materijala: str | None = None
    proizvodjac: str | None = None
    model: str | None = None
    serijski_broj: str | None = None


class RunAnalyticsRequest(BaseModel):
    # Extra taint-analysis seed addresses (e.g. a known theft address that isn't on any
    # blacklist) on top of whatever taint_analysis already auto-seeds from blacklist_flag.
    seed_addresses: list[str] | None = None
    # Optional at the API level: this same endpoint is also called passively (Dashboard,
    # Graf) just to color/annotate a graph the analyst never deliberately "ran" - only the
    # Taint Analysis page's explicit "Pokreni taint analizu" button represents a genuine,
    # purposeful access, and it is the one caller that always supplies this. When present,
    # every one of ITS fields is required (see TransactionCustodyEntry) - the gate is
    # "all or nothing" per call, never a half-filled entry.
    custody: TransactionCustodyEntry | None = None


@router.get('')
def get_cases() -> dict[str, object]:
    return {'cases': list_cases()}


@router.post('')
def post_case(request: CreateCaseRequest, current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    case = create_case(name=request.name, analyst=str(current_user['username']), description=request.description)
    write_audit_log(
        action='case_created',
        user=str(current_user['username']),
        case_id=str(case.get('id') or ''),
        case_name=str(case.get('name') or ''),
    )
    return case


@router.get('/{case_id}')
def get_case_detail(case_id: str) -> dict[str, object]:
    return _get_case_or_404(case_id)


@router.get('/{case_id}/evidence')
def get_case_evidence(case_id: str) -> dict[str, object]:
    case = _get_case_or_404(case_id)
    return {'case_id': case_id, 'evidence': case.get('evidence', [])}


@router.patch('/{case_id}/status')
def patch_case_status(
    case_id: str,
    request: SetCaseStatusRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    case = _get_case_or_404(case_id)
    updated = set_case_status(case_id, request.status)
    write_audit_log(
        action='case_status_changed',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={'from': str(case.get('status') or ''), 'to': request.status},
    )
    return updated


@router.delete('/{case_id}', status_code=204)
def delete_case_route(case_id: str, current_user: dict[str, object] = Depends(get_current_user)) -> None:
    # Read the name BEFORE deleting - once the case file is gone there is nothing left to
    # resolve the id against, and "case X was deleted" is exactly the kind of entry that
    # must stay readable years later.
    case = _get_case_or_404(case_id)
    case_name = str(case.get('name') or '')
    try:
        delete_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='case_deleted',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=case_name,
    )


def _case_evidence_paths_or_404(case: dict[str, object]) -> list[tuple[dict[str, object], object]]:
    try:
        return get_case_evidence_paths(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _filter_evidence_paths(
    evidence_paths: list[tuple[dict[str, object], object]],
    stored_name: str | None,
) -> list[tuple[dict[str, object], object]]:
    if not stored_name:
        return evidence_paths

    filtered = [(entry, path) for entry, path in evidence_paths if entry.get('stored_name') == stored_name]
    if not filtered:
        raise HTTPException(status_code=404, detail=f'Evidence {stored_name} not found in case')
    return filtered


@router.get('/{case_id}/graph')
def get_case_graph(case_id: str, evidence: str | None = None) -> dict[str, object]:
    """Transaction graph for the case. Pass `evidence` (stored_name) to scope it to a single evidence file,
    otherwise it is combined across ALL evidence in the case."""
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    payload = transaction_graph_to_node_link_json(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    payload['rows'] = int(len(combined_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    return payload


@router.get('/{case_id}/seed-suggestions')
def get_seed_suggestions(case_id: str, evidence: str | None = None) -> dict[str, object]:
    """Rule-based, explained suggestions for taint analysis (see analytics/seed_suggestion.py).

    Runs the analytics pipeline first so the pattern plugins (peel chain, chain hopping)
    have annotated the graph, then applies the suggestion rules on top.
    """
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)

    combined_frame, graph = build_case_graph(evidence_paths)
    run_plugin_pipeline(dataframe=combined_frame, graph=graph, seed_addresses=None)

    payload = suggest_seeds(graph)
    payload['case_id'] = case_id
    payload['evidence'] = evidence
    return payload


def _clean_scalar(value: object) -> object | None:
    """pandas leaves missing cells as NaN/pd.NA depending on column dtype - both need to
    become a real None before going into the custody log, or a missing tx hash would be
    stored as the literal string "<NA>" instead of being absent."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _record_custody_access(
    *,
    case: dict[str, object],
    per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]],
    custody: TransactionCustodyEntry,
    user: str,
) -> None:
    """One deliberate access is recorded at TWO granularities at once, both useful for a
    different question a reader might have:

    - per transaction (`custody_log`): was THIS specific transaction looked at, by whom,
      why - one row per transaction row in scope, appended to that transaction's own chain.
    - per evidence file (`custody_evidence_log`): was THIS evidence file (the exhibit
      itself, like a seized hard drive) accessed, by whom, why - one row per evidence file
      in scope, regardless of how many transactions it contains.

    Both share the same run/date/analyst/reason/signature - they were genuinely accessed
    together, by the same act of running the analysis (see LANAC-DOKAZA.md).
    """
    run_id = uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    case_name = str(case.get('name') or '')
    identifikator_predmeta = custody.identifikator_predmeta or case_name or str(case.get('id') or '')

    transaction_batch: list[dict[str, object]] = []
    evidence_batch: list[dict[str, object]] = []
    for evidence_entry, frame in per_evidence_frames:
        stored_name = str(evidence_entry.get('stored_name') or '')
        file_name = str(evidence_entry.get('file_name') or stored_name)
        identifikator_dokaznog_materijala = custody.identifikator_dokaznog_materijala or file_name

        shared_fields = {
            'run_id': run_id,
            'timestamp': timestamp,
            'case_id': case['id'],
            'case_name': case_name,
            'evidence_stored_name': stored_name,
            'evidence_file_name': file_name,
            'identifikator_predmeta': identifikator_predmeta,
            'identifikator_dokaznog_materijala': identifikator_dokaznog_materijala,
            'proizvodjac': custody.proizvodjac or 'N/A',
            'model': custody.model or 'N/A',
            'serijski_broj': custody.serijski_broj or 'N/A',
            'ime_prezime': custody.ime_prezime,
            'opis_radnje': custody.opis_radnje,
            'user': user,
            'signature_image': custody.signature_image,
        }

        evidence_batch.append({
            **shared_fields,
            'evidence_sha256': evidence_entry.get('sha256'),
            'evidence_currency': evidence_entry.get('currency'),
            'evidence_row_count': int(len(frame)),
        })

        for row in frame.to_dict('records'):
            amount = row.get('amount')
            tx_timestamp = row.get('timestamp')
            transaction_batch.append({
                **shared_fields,
                'tx_id': transaction_id(row, stored_name),
                'tx_hash': _clean_scalar(row.get('metadata')),
                'sender_address': _clean_scalar(row.get('sender_address')),
                'recipient_address': _clean_scalar(row.get('recipient_address')),
                'amount': float(amount) if pd.notna(amount) else None,
                'currency': _clean_scalar(row.get('currency')),
                'tx_timestamp': tx_timestamp.isoformat() if pd.notna(tx_timestamp) else None,
            })

    append_custody_batch(transaction_batch)
    append_evidence_custody_batch(evidence_batch)


@router.post('/{case_id}/analytics/run')
def run_case_analytics(
    case_id: str,
    evidence: str | None = None,
    request: RunAnalyticsRequest | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Runs the full analytics pipeline over the case's evidence graph, optionally scoped to one evidence file.

    This endpoint is called two different ways: passively, by the Kontrolna tabla's
    summary tiles and by the Graf page's automatic (uncoloured) preview on selection - no
    `custody` needed there - and deliberately, by the Taint Analysis page's "Pokreni taint
    analizu" button and the Graf page's own "Analiziraj graf" button, both of which always
    supply one. When `request.custody` is present, running the analysis is treated as
    re-accessing every transaction AND every evidence file it processes, and that access
    is recorded in both chains of custody (see `_record_custody_access`) before the
    response is returned.
    """
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)

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
        _record_custody_access(
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


class CasePathfindingRequest(BaseModel):
    """`from` is always required. `to` is required only for destination_mode
    'specific_address' (the original, first-version behaviour) - for 'nearest_cex' the
    destination is resolved server-side from the case's own graph, so it stays optional
    and is ignored if sent.

    Field names use Python-safe identifiers with aliases for 'from'/'to' (reserved word),
    so the JSON body still looks exactly like {"from": "0x...", "to": "0x..."} -
    populate_by_name also allows calling code (tests, other Python) to pass
    from_address/to_address directly.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_address: str = Field(min_length=1, alias='from')
    to_address: str | None = Field(default=None, alias='to')
    # 'cash_out_point' is deliberately not accepted yet - only "Known CEX" is implemented;
    # requesting it fails clearly (see run_case_pathfinding) instead of being silently
    # accepted and doing nothing useful.
    destination_mode: str = Field(default='specific_address')
    # Same "all fields or none" custody gate as RunAnalyticsRequest.custody - optional at
    # the API level (so direct/test callers without a UI in front of them still work), but
    # the ONLY real caller (the Pathfinding page's "FIND PATH" button) always supplies one,
    # since running a BFS search over the case's evidence is itself a deliberate access to
    # every transaction it traverses, same as "Pokreni taint analizu"/"Analiziraj graf".
    custody: TransactionCustodyEntry | None = None


@router.post('/{case_id}/pathfinding')
def run_case_pathfinding(
    case_id: str,
    request: CasePathfindingRequest,
    evidence: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Pathfinding Analysis: unweighted BFS over the case's own graph (combined evidence,
    or one file via ?evidence=), toward one of two kinds of destination:

    - 'specific_address' (default): the exact address the caller picked - the original
      first-version behaviour, response stays exactly {found, path, hops}, unchanged.
    - 'nearest_cex': the nearest address, by hop count, that the LOCAL known_entities
      registry (app.services.address_enrichment.get_known_entity) flags as an exchange -
      never a guess based on an address's name/label, only what that offline registry
      actually says. Response additionally carries destination_address/destination_label,
      and a message when nothing is found, so the reader can tell "no CEX anywhere in
      this evidence" apart from "one exists but isn't reachable from here".

    Deliberately minimal beyond that: no weighting, no multiple paths, no cash-out-point
    detection yet - see app.analytics.path_finding.
    """
    case = _get_case_or_404(case_id)
    evidence_paths = _filter_evidence_paths(_case_evidence_paths_or_404(case), evidence)
    # Built from per-evidence-file frames (like run_case_analytics) rather than via
    # build_case_graph, so _record_custody_access can tag each row with the evidence file
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
        _record_custody_access(
            case=case,
            per_evidence_frames=per_evidence_frames,
            custody=request.custody,
            user=str(current_user['username']),
        )

    return result
