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
        'Propagates a proportional ("haircut") taint percentage from seed addresses through the '
        'graph, processing every individual transaction in strict chronological order.'
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

        events = self._collect_chronological_events(graph)

        # Balans i "prljavi" deo balansa po adresi, posmatrano samo unutar ove evidencije -
        # nema pristupa prilivima van uvezenih transakcija, pa je ovo "closed-world"
        # pretpostavka (videti napomenu u opisu plugina/tezi o ograničenju).
        balance: dict[str, float] = {}
        tainted_balance: dict[str, float] = {}
        # Poslednji poznati taint % pre nego što je balans pao na (blizu) nule - inače bi
        # deljenje sa nula-balansom izbrisalo istorijski trag da je adresa uopšte bila umešana.
        peak_taint_pct: dict[str, float] = {}
        tainted_hops: list[dict[str, Any]] = []

        def taint_pct(node: str) -> float:
            bal = balance.get(node, 0.0)
            if bal <= 1e-12:
                return peak_taint_pct.get(node, 100.0 if node in seeds else 0.0)
            return 100.0 * tainted_balance.get(node, 0.0) / bal

        for timestamp, source, target, amount in events:
            if amount <= 0:
                continue

            source_balance = balance.get(source, 0.0)
            source_ratio = (
                tainted_balance.get(source, 0.0) / source_balance
                if source_balance > 1e-12
                else (1.0 if source in seeds else 0.0)
            )
            tainted_amount = amount * source_ratio

            balance[source] = source_balance - amount
            tainted_balance[source] = tainted_balance.get(source, 0.0) - tainted_amount
            peak_taint_pct[source] = max(peak_taint_pct.get(source, 0.0), taint_pct(source))

            balance[target] = balance.get(target, 0.0) + amount
            tainted_balance[target] = tainted_balance.get(target, 0.0) + tainted_amount
            if target in seeds:
                # Seed adresa "ponovo ubrizgava" pun taint na svaki dolazni transfer i - u
                # slučaju da isti akter dobije svež ukraden novac iz više različitih incidenata.
                tainted_balance[target] = balance[target]
            peak_taint_pct[target] = max(peak_taint_pct.get(target, 0.0), taint_pct(target))

            if tainted_amount > 1e-9:
                tainted_hops.append(
                    {
                        'source': source,
                        'target': target,
                        'timestamp': timestamp.isoformat(),
                        'amount': amount,
                        'tainted_amount': tainted_amount,
                        'taint_pct_at_hop': round(100.0 * tainted_amount / amount, 2),
                    }
                )

        for node in graph.nodes:
            graph.nodes[node]['taint_percentage'] = round(taint_pct(node), 2)
            graph.nodes[node]['is_taint_seed'] = node in seeds

        results = sorted(
            (
                {
                    'address': node,
                    'taint_percentage': graph.nodes[node]['taint_percentage'],
                    'is_taint_seed': graph.nodes[node]['is_taint_seed'],
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
        }

    @staticmethod
    def _collect_chronological_events(graph: nx.DiGraph) -> list[tuple[pd.Timestamp, str, str, float]]:
        """Flattens every individual transaction across every edge into one time-ordered
        stream - taint has to follow real chronological order across the whole graph, not
        edge-by-edge, since transactions on different edges interleave in time."""

        events: list[tuple[pd.Timestamp, str, str, float]] = []
        for source, target, attrs in graph.edges(data=True):
            transactions = attrs.get('transactions') or [
                {'amount': attrs.get('total_amount', 0.0), 'timestamp': attrs.get('first_seen')}
            ]
            for tx in transactions:
                timestamp = pd.to_datetime(tx.get('timestamp'), utc=True, errors='coerce')
                if pd.isna(timestamp):
                    continue
                events.append((timestamp, source, target, float(tx.get('amount', 0.0) or 0.0)))

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
