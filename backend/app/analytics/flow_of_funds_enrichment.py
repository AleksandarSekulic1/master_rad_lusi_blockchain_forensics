"""Cross-references a Flow of Funds trace against the SAME existing analyses the rest of the
app already runs over a case's evidence - Graph analysis' own plugin pipeline (blacklist
check, risk scoring, peel chains, chain hopping, wallet clustering), DEX Swap Analysis, Token
Approval Analysis, and - opt-in, since both need an explicit reason to run - Taint Analysis
and Sybil Analysis. Every one of those modules is only ever IMPORTED and CALLED here, exactly
as their own feature routers already call them - nothing about Graph/Taint/Pathfinding (or
any other existing analysis) is modified.

The point of this module is to answer one question per address a Flow of Funds trace
touches: "what does the rest of the app already know about this address, and how sure can I
be about it?" - answered as three explicitly separated buckets, never blended into one
generic "info" list, so a reader never mistakes a model's guess for an established fact:

  - 'facts': directly established from a source OUTSIDE this case's own inference - a
    curated known-entity or blacklist registry match (app.services.address_enrichment,
    app.analytics.plugins.blacklist_check). Wrong only if the registry itself is wrong, not
    because of anything this analysis inferred from the transaction graph.
  - 'aggregated': plain arithmetic/counting straight over this case's own real transactions
    - no scoring model, no pattern heuristic. Total received/sent, and events that are only
    ever reported when independently corroborated by hard data (a DEX swap pairing confirmed
    by the SAME transaction hash on both legs; a token-approval event that genuinely
    happened, before any risk judgement is applied to it).
  - 'heuristics': a MODEL's conclusion - could be wrong, always tagged with which plugin
    produced it (risk_scoring, peel_chains, chain_hopping, wallet_clustering, a DEX swap
    pairing inferred only from timing proximity, a token approval's risk rating,
    taint_analysis's haircut-model percentage, sybil_analysis's clustering).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from app.analytics.dex_swap_analysis import detect_dex_swaps
from app.analytics.flow_of_funds import _filter_by_period, _row_asset
from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.blacklist_check import run_blacklist_check
from app.analytics.plugins.chain_hopping import run_chain_hopping
from app.analytics.plugins.peel_chains import run_peel_chains
from app.analytics.plugins.risk_scoring import run_risk_scoring
from app.analytics.plugins.taint_analysis import run_taint_analysis
from app.analytics.plugins.wallet_clustering import run_wallet_clustering
from app.analytics.sybil_analysis import detect_sybil_clusters
from app.analytics.token_approval_analysis import analyze_token_approvals
from app.services.address_enrichment import get_known_entity

_RISKY_APPROVAL_LEVELS = ('MEDIUM', 'HIGH')


def _empty_bucket() -> dict[str, list[dict[str, Any]]]:
    return {'facts': [], 'aggregated': [], 'heuristics': []}


def enrich_flow_of_funds_nodes(
    addresses: list[str],
    dataframe: pd.DataFrame,
    seed_addresses: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    include_taint: bool = False,
    include_sybil: bool = False,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Returns `{address: {'facts': [...], 'aggregated': [...], 'heuristics': [...]}}` for
    every address in `addresses` (exactly the ids already in a trace result's own `nodes`
    list - always real addresses, even when the Sankey view itself is collapsed to
    entities/categories - see flow-of-funds.component.ts for how the frontend re-joins these
    onto a collapsed node via `contributing_addresses`).

    Scoped to the SAME time window as the trace it's enriching (`start_time`/`end_time`,
    applied the same way `trace_flow_of_funds` applies them - see `_filter_by_period`), so an
    annotation never cites a transaction that fell outside what the analyst is actually
    looking at.

    `include_taint`/`include_sybil` default OFF - both are heavier, and both are already
    deliberate, separately-triggered analyses elsewhere in the app (their own pages, their
    own "run" action); this function does not run them for free on every trace, only on
    request, matching the "USD kada postoji pouzdana istorijska vrednost"-style honesty this
    endpoint's earlier version already applies (Chain of Evidence for the addresses is best
    with clear caveats).

    Reuses `pd.DataFrame` reads and `build_transaction_graph` exactly like every plugin
    already does independently - never mutates or imports from path_finding.py or
    taint_analysis.py's SHAPE (the taint plugin itself is called, not reimplemented).
    """
    annotations: dict[str, dict[str, list[dict[str, Any]]]] = {address: _empty_bucket() for address in addresses}
    address_set = set(addresses)

    def add(address: str, bucket: str, badge: dict[str, Any]) -> None:
        entry = annotations.get(address)
        if entry is not None:
            entry[bucket].append(badge)

    # --- facts: curated external registries - independent of this case's own data ---
    for address in addresses:
        entity = get_known_entity(address)
        if entity:
            add(address, 'facts', {'type': 'known_entity', 'name': entity['name'], 'category': entity['category']})

    period_frame = _filter_by_period(dataframe, start_time, end_time)
    if period_frame.empty:
        return annotations

    graph = build_transaction_graph(period_frame)

    blacklist_result = run_blacklist_check(dataframe=period_frame, graph=graph)
    for match in blacklist_result.get('matches', []):
        for node_id in match.get('nodes', []):
            add(str(node_id), 'facts', {
                'type': 'blacklist',
                'sources': list(match.get('sources', [])),
                'label': match.get('label'),
            })

    # --- aggregated: arithmetic straight over this case's own transactions ---
    # Deliberately NOT graph.nodes[address]['total_received']/'total_sent'
    # (app.analytics.graph_building._annotate_node_flow_totals) - that sums every edge's
    # `total_amount` regardless of asset, which is exactly the "amounts summed across
    # incompatible units" mistake this whole module's own unit-safety rule (see
    # flow_of_funds.py's module docstring, point 1) exists to prevent. A node active in more
    # than one asset gets one flow_totals badge PER asset here instead of one blindly-summed
    # number that would misrepresent a mixed-asset node as a single, trustworthy figure.
    has_currency = 'currency' in period_frame.columns
    totals_by_address_asset: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {'received': 0.0, 'sent': 0.0}))
    for row in period_frame.itertuples(index=False):
        sender = str(getattr(row, 'sender_address'))
        recipient = str(getattr(row, 'recipient_address'))
        amount = float(getattr(row, 'amount'))
        currency = getattr(row, 'currency', None) if has_currency else None
        asset = _row_asset(currency, sender, recipient)
        if sender in address_set:
            totals_by_address_asset[sender][asset]['sent'] += amount
        if recipient in address_set:
            totals_by_address_asset[recipient][asset]['received'] += amount

    for address in addresses:
        for asset, totals in sorted(totals_by_address_asset.get(address, {}).items()):
            add(address, 'aggregated', {
                'type': 'flow_totals',
                'asset': asset,
                'total_received': totals['received'],
                'total_sent': totals['sent'],
                'net_flow': totals['received'] - totals['sent'],
            })

    # --- heuristics: the SAME plugin pipeline the Graph page's "Analiziraj graf" button
    # runs (see app/features/case_analytics_run) - called directly here rather than through
    # run_plugin_pipeline, so the (required) blacklist_check -> {risk_scoring, taint_analysis}
    # ordering (see app/analytics/plugins/manager.py's DEFAULT_PIPELINE) is explicit. ---
    run_wallet_clustering(dataframe=period_frame, graph=graph)
    run_risk_scoring(dataframe=period_frame, graph=graph)
    run_peel_chains(dataframe=period_frame, graph=graph)
    run_chain_hopping(dataframe=period_frame, graph=graph)

    for address in addresses:
        if not graph.has_node(address):
            continue
        node_attrs = graph.nodes[address]

        if node_attrs.get('risk_score') is not None:
            add(address, 'heuristics', {
                'type': 'risk_score',
                'score': int(node_attrs.get('risk_score', 0) or 0),
                'band': node_attrs.get('risk_band'),
                'reasons': list(node_attrs.get('risk_reasons', []) or []),
            })
        if node_attrs.get('cluster_id'):
            add(address, 'heuristics', {
                'type': 'wallet_cluster',
                'cluster_id': node_attrs.get('cluster_id'),
                'cluster_size': node_attrs.get('cluster_size'),
            })
        if node_attrs.get('peel_chain_flag'):
            add(address, 'heuristics', {
                'type': 'peel_chain',
                'chain_id': node_attrs.get('peel_chain_id'),
                'role': node_attrs.get('peel_chain_role'),
                'confidence': node_attrs.get('peel_chain_score'),
            })
        if node_attrs.get('chain_hop_flag'):
            add(address, 'heuristics', {
                'type': 'chain_hop',
                'service_type': node_attrs.get('chain_hop_type'),
                'reasons': list(node_attrs.get('chain_hop_reasons', []) or []),
            })

    # --- DEX Swap Analysis: 'Detected' (paired by a SHARED tx hash on both legs - hard
    # data, not an inference) counts as aggregated; 'Potential' (timing proximity only, a
    # genuine guess) counts as heuristic. ---
    try:
        dex_result: dict[str, Any] | None = detect_dex_swaps(period_frame)
    except ValueError:
        dex_result = None
    if dex_result:
        for event in dex_result.get('events', []):
            user_address = str(event.get('user_address') or '')
            if user_address not in address_set:
                continue
            if event.get('confidence') == 'Detected':
                add(user_address, 'aggregated', {
                    'type': 'dex_swap_detected',
                    'dex_name': event.get('dex_name'),
                    'input_transaction_hash': event.get('input_transaction_hash'),
                    'output_transaction_hash': event.get('output_transaction_hash'),
                })
            else:
                add(user_address, 'heuristics', {
                    'type': 'dex_swap_potential',
                    'dex_name': event.get('dex_name'),
                    'time_gap_seconds': event.get('time_gap_seconds'),
                })

    # --- Token Approval Analysis: the approval EVENT itself (an owner really did approve a
    # spender) is aggregated fact-of-the-data; the RISK rating of that approval is a
    # heuristic judgement layered on top. ---
    try:
        approval_result: dict[str, Any] | None = analyze_token_approvals(period_frame)
    except ValueError:
        approval_result = None
    if approval_result:
        for group in approval_result.get('groups', []):
            owner = str(group.get('owner') or '')
            spender = str(group.get('spender') or '')
            is_risky = group.get('risk_level') in _RISKY_APPROVAL_LEVELS

            for address, role, counterparty in ((owner, 'owner', spender), (spender, 'spender', owner)):
                if address not in address_set:
                    continue
                add(address, 'aggregated', {
                    'type': 'token_approval',
                    'role': role,
                    'counterparty': counterparty,
                    'status': group.get('current_status'),
                })
                if is_risky:
                    add(address, 'heuristics', {
                        'type': 'token_approval_risk',
                        'role': role,
                        'risk_level': group.get('risk_level'),
                        'risk_score': group.get('risk_score'),
                        'indicators': [indicator.get('code') for indicator in group.get('risk_indicators', []) or []],
                    })

    # --- Sybil Analysis: opt-in (case-wide clustering pass, a deliberate extra step). ---
    if include_sybil:
        try:
            sybil_result: dict[str, Any] | None = detect_sybil_clusters(period_frame)
        except ValueError:
            sybil_result = None
        if sybil_result:
            for cluster in sybil_result.get('clusters', []):
                for address in cluster.get('addresses', []) or []:
                    if address in address_set:
                        add(address, 'heuristics', {
                            'type': 'sybil_cluster',
                            'cluster_id': cluster.get('cluster_id'),
                            'risk_level': cluster.get('risk_level'),
                        })

    # --- Taint Analysis: opt-in, seeded from the SAME addresses this trace started from -
    # never from the case's blacklist (seed_from_blacklist=False), so the percentage shown
    # answers "how much of THIS trace's own funds reached this node", not a number diluted
    # by an unrelated blacklist seed elsewhere in the case. ---
    if include_taint and seed_addresses:
        try:
            taint_result: dict[str, Any] | None = run_taint_analysis(
                dataframe=period_frame, graph=graph, seed_addresses=seed_addresses, seed_from_blacklist=False,
            )
        except ValueError:
            # Same "an optional cross-reference degrades gracefully, it never takes the
            # whole trace down with it" discipline as the DEX/Token Approval/Sybil calls
            # above - a taint hiccup on some edge case in the data should never turn an
            # otherwise-successful Flow of Funds trace into a 500.
            taint_result = None
        for entry in (taint_result or {}).get('results', []) or []:
            address = str(entry.get('address') or '')
            percentage = entry.get('taint_percentage') or 0
            if address in address_set and percentage > 0:
                add(address, 'heuristics', {
                    'type': 'taint',
                    'percentage': percentage,
                    'is_seed': bool(entry.get('is_taint_seed', False)),
                })

    return annotations
