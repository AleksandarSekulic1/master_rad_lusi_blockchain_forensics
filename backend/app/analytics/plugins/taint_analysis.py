from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.plugins.base import BasePlugin


def _normalize_seed_addresses(seed_addresses: list[str] | None) -> set[str]:
    if not seed_addresses:
        return set()
    return {str(address).strip() for address in seed_addresses if str(address).strip()}


class TaintAnalysisPlugin(BasePlugin):
    name = 'taint_analysis'
    description = (
        'Propagates a proportional ("haircut") taint percentage - broken down per individual '
        'seed - from seed addresses through the graph, processing every transaction in strict '
        'chronological order.'
    )

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        seed_addresses: list[str] | None = None,
        seed_from_blacklist: bool = True,
        **context: Any,
    ) -> dict[str, Any]:
        if graph is None:
            raise ValueError('Taint analysis requires a graph.')

        seeds = _normalize_seed_addresses(seed_addresses)
        if seed_from_blacklist:
            seeds |= {node for node, attrs in graph.nodes(data=True) if attrs.get('blacklist_flag')}

        # Zero/negative-amount transactions never change any balance below, so they're
        # dropped from the ranked sequence entirely here (not just skipped mid-loop) -
        # otherwise the timeline slider would have "dead" positions where scrubbing does
        # nothing, which real on-chain data (dust/contract-interaction transfers) does
        # contain.
        events = [event for event in self._collect_chronological_events(graph) if event[3] > 0]

        # Balans i "prljavi" deo balansa po adresi, posmatrano samo unutar ove evidencije -
        # nema pristupa prilivima van uvezenih transakcija, pa je ovo "closed-world"
        # pretpostavka (videti napomenu u opisu plugina/tezi o ograničenju).
        balance: dict[str, float] = {}
        # Zaprljani deo balansa RAZLOŽEN po pojedinačnom izvoru (seed adresi), ne samo
        # jedan zbirni broj - omogućava da se za svaki čvor/transakciju kaže "od čega je
        # sastavljen" taint (npr. "52.72% = 30% od seed-a A + 22.72% od seed-a B").
        tainted_balance: dict[str, dict[str, float]] = {}
        # Poslednji poznati (zbirni i razloženi) taint pre nego što je balans pao na
        # (blizu) nule - inače bi deljenje sa nula-balansom izbrisalo istorijski trag da
        # je adresa uopšte bila umešana.
        peak_taint_pct: dict[str, float] = {}
        peak_breakdown: dict[str, dict[str, float]] = {}
        tainted_hops: list[dict[str, Any]] = []
        # Complete per-node taint % history, one entry per event that actually touches
        # that node - unlike tainted_hops (only hops carrying nonzero tainted value),
        # this also captures dilution: a node's % can drop purely because CLEAN money
        # arrived, which is not itself a "tainted hop" but does change the node's state.
        # A timeline scrubber built only from tainted_hops would show stale values at
        # exactly those moments.
        node_taint_series: dict[str, list[dict[str, Any]]] = {}

        def total_tainted(node: str) -> float:
            return sum(tainted_balance.get(node, {}).values())

        def taint_pct(node: str) -> float:
            bal = balance.get(node, 0.0)
            if bal <= 1e-12:
                return peak_taint_pct.get(node, 100.0 if node in seeds else 0.0)
            return 100.0 * total_tainted(node) / bal

        def breakdown_pct(node: str) -> dict[str, float]:
            bal = balance.get(node, 0.0)
            if bal <= 1e-12:
                if node in peak_breakdown:
                    return peak_breakdown[node]
                return {node: 100.0} if node in seeds else {}
            return {
                seed: round(100.0 * amount / bal, 2)
                for seed, amount in tainted_balance.get(node, {}).items()
                if amount > 1e-9
            }

        # First chronological appearance of each node/edge - lets the frontend's timeline
        # scrubber reveal elements in the exact same order the simulation itself uses,
        # rather than re-deriving that order independently in JS and risking drift.
        node_first_rank: dict[str, int] = {}
        edge_first_rank: dict[str, int] = {}

        for rank, (timestamp, source, target, amount, tx_metadata) in enumerate(events, start=1):
            node_first_rank.setdefault(source, rank)
            node_first_rank.setdefault(target, rank)
            edge_first_rank.setdefault(f'{source}__{target}', rank)

            source_balance = balance.get(source, 0.0)
            source_tainted = tainted_balance.get(source, {})
            if source_balance > 1e-12:
                transferred_by_seed = {
                    seed: amount * (seed_amount / source_balance) for seed, seed_amount in source_tainted.items()
                }
            elif source in seeds:
                # Untouched seed spending for the first time - the whole transfer is
                # attributed to itself, not to whatever (nonexistent) balance it had.
                transferred_by_seed = {source: amount}
            else:
                transferred_by_seed = {}

            tainted_amount = sum(transferred_by_seed.values())

            balance[source] = source_balance - amount
            new_source_tainted = dict(source_tainted)
            for seed, seed_amount in transferred_by_seed.items():
                new_source_tainted[seed] = new_source_tainted.get(seed, 0.0) - seed_amount
            tainted_balance[source] = new_source_tainted
            source_pct_after = taint_pct(source)
            source_breakdown_after = breakdown_pct(source)
            peak_taint_pct[source] = max(peak_taint_pct.get(source, 0.0), source_pct_after)
            if balance[source] > 1e-12:
                peak_breakdown[source] = source_breakdown_after
            node_taint_series.setdefault(source, []).append(
                {
                    'rank': rank,
                    'taint_percentage': round(source_pct_after, 2),
                    'direction': 'out',
                    'counterparty': target,
                    'amount': amount,
                    'tainted_amount': tainted_amount,
                    'timestamp': timestamp.isoformat(),
                    # Per-seed split of this node's balance right after this event - lets the
                    # frontend's "Filter po izvoru" apply correctly while scrubbing the
                    # timeline, not just in the final/full view (previously only tainted_hops
                    # had a historically-accurate per-source snapshot; node_taint_series only
                    # had the aggregate).
                    'taint_by_source': source_breakdown_after,
                }
            )

            balance[target] = balance.get(target, 0.0) + amount
            new_target_tainted = dict(tainted_balance.get(target, {}))
            for seed, seed_amount in transferred_by_seed.items():
                new_target_tainted[seed] = new_target_tainted.get(seed, 0.0) + seed_amount
            if target in seeds:
                # Seed adresa "ponovo ubrizgava" pun taint (pripisan sebi samoj) na svaki
                # dolazni transfer - u slučaju da isti akter dobije svež ukraden novac iz
                # više različitih incidenata, prethodna mešavina izvora više nije bitna.
                new_target_tainted = {target: balance[target]}
            tainted_balance[target] = new_target_tainted
            target_pct_after = taint_pct(target)
            target_breakdown_after = breakdown_pct(target)
            peak_taint_pct[target] = max(peak_taint_pct.get(target, 0.0), target_pct_after)
            if balance[target] > 1e-12:
                peak_breakdown[target] = target_breakdown_after
            node_taint_series.setdefault(target, []).append(
                {
                    'rank': rank,
                    'taint_percentage': round(target_pct_after, 2),
                    'direction': 'in',
                    'counterparty': source,
                    'amount': amount,
                    'tainted_amount': tainted_amount,
                    'timestamp': timestamp.isoformat(),
                    'taint_by_source': target_breakdown_after,
                }
            )

            if tainted_amount > 1e-9:
                hop_breakdown = {
                    seed: round(100.0 * seed_amount / amount, 2)
                    for seed, seed_amount in transferred_by_seed.items()
                    if seed_amount > 1e-9
                }
                tainted_hops.append(
                    {
                        'rank': rank,
                        'source': source,
                        'target': target,
                        'timestamp': timestamp.isoformat(),
                        'amount': amount,
                        'tainted_amount': tainted_amount,
                        'taint_pct_at_hop': round(100.0 * tainted_amount / amount, 2),
                        'source_taint_pct_after': round(source_pct_after, 2),
                        'target_taint_pct_after': round(target_pct_after, 2),
                        # What fraction of THIS hop's tainted amount came from each seed -
                        # e.g. {'0xSeedA': 60.0, '0xSeedB': 40.0} when two sources mixed
                        # together before this specific transfer.
                        'taint_by_source': hop_breakdown,
                        # Whatever the source CSV's tx_hash/hash column carried for this
                        # exact transaction (see ingestion.py's COLUMN_ALIASES) - None when
                        # the evidence never had one, so the frontend can fall back to
                        # "n/a" instead of showing a missing/undefined value.
                        'tx_metadata': tx_metadata,
                    }
                )

        for node in graph.nodes:
            graph.nodes[node]['taint_percentage'] = round(taint_pct(node), 2)
            graph.nodes[node]['is_taint_seed'] = node in seeds
            graph.nodes[node]['taint_by_source'] = breakdown_pct(node)

        results = sorted(
            (
                {
                    'address': node,
                    'taint_percentage': graph.nodes[node]['taint_percentage'],
                    'is_taint_seed': graph.nodes[node]['is_taint_seed'],
                    'taint_by_source': graph.nodes[node]['taint_by_source'],
                }
                for node in graph.nodes
            ),
            key=lambda item: -item['taint_percentage'],
        )

        return {
            'plugin': self.name,
            'description': self.description,
            'seed_addresses': sorted(seeds),
            'tainted_node_count': sum(1 for item in results if item['taint_percentage'] > 0),
            'tainted_hops': tainted_hops,
            'results': results,
            'node_first_rank': node_first_rank,
            'edge_first_rank': edge_first_rank,
            'node_taint_series': node_taint_series,
            'timeline_max_rank': len(events),
            # Enough per-rank detail (who sent what to whom, and when) for the frontend
            # to build a caption/subtitle and to pan the camera to the right edge during
            # playback, without needing a second request or re-deriving it from `graph`.
            'timeline_events': [
                {
                    'rank': rank,
                    'source': source,
                    'target': target,
                    'amount': amount,
                    'timestamp': timestamp.isoformat(),
                    'tx_metadata': tx_metadata,
                }
                for rank, (timestamp, source, target, amount, tx_metadata) in enumerate(events, start=1)
            ],
        }

    @staticmethod
    def _collect_chronological_events(graph: nx.DiGraph) -> list[tuple[pd.Timestamp, str, str, float, Any]]:
        """Flattens every individual transaction across every edge into one time-ordered
        stream - taint has to follow real chronological order across the whole graph, not
        edge-by-edge, since transactions on different edges interleave in time."""

        events: list[tuple[pd.Timestamp, str, str, float, Any]] = []
        for source, target, attrs in graph.edges(data=True):
            transactions = attrs.get('transactions') or [
                {'amount': attrs.get('total_amount', 0.0), 'timestamp': attrs.get('first_seen'), 'metadata': None}
            ]
            for tx in transactions:
                timestamp = pd.to_datetime(tx.get('timestamp'), utc=True, errors='coerce')
                if pd.isna(timestamp):
                    continue
                events.append((timestamp, source, target, float(tx.get('amount', 0.0) or 0.0), tx.get('metadata')))

        events.sort(key=lambda event: event[0])
        return events


def run_taint_analysis(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
    seed_addresses: list[str] | None = None,
    seed_from_blacklist: bool = True,
) -> dict[str, Any]:
    return TaintAnalysisPlugin().run(
        dataframe=dataframe,
        graph=graph,
        seed_addresses=seed_addresses,
        seed_from_blacklist=seed_from_blacklist,
    )
