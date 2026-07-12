from __future__ import annotations

from datetime import datetime
from math import log1p
from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.blacklist_check.service import _normalize_address
from app.analytics.graph_building.service import build_transaction_graph
from app.analytics.plugins.base import BasePlugin


def _classify_score(score: int) -> str:
    if score >= 80:
        return 'critical'
    if score >= 60:
        return 'high'
    if score >= 35:
        return 'medium'
    if score > 0:
        return 'low'
    return 'none'


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _gather_node_metrics(graph: nx.DiGraph, node: str) -> dict[str, Any]:
    incoming_edges = list(graph.in_edges(node, data=True))
    outgoing_edges = list(graph.out_edges(node, data=True))
    total_in_amount = sum(float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0) for _, _, attrs in incoming_edges)
    total_out_amount = sum(float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0) for _, _, attrs in outgoing_edges)
    transaction_count = sum(int(attrs.get('transaction_count', 0) or 0) for _, _, attrs in incoming_edges + outgoing_edges)

    counterparties = {
        target if source == node else source
        for source, target, _ in incoming_edges + outgoing_edges
        if source != target
    }

    timestamps = []
    for _, _, attrs in incoming_edges + outgoing_edges:
        timestamps.extend([_parse_timestamp(attrs.get('first_seen')), _parse_timestamp(attrs.get('last_seen'))])

    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    active_span_seconds = 0
    if timestamps:
        active_span_seconds = int((max(timestamps) - min(timestamps)).total_seconds())

    return {
        'in_degree': graph.in_degree(node),
        'out_degree': graph.out_degree(node),
        'transaction_count': transaction_count,
        'total_in_amount': total_in_amount,
        'total_out_amount': total_out_amount,
        'total_volume': total_in_amount + total_out_amount,
        'counterparty_count': len(counterparties),
        'active_span_seconds': active_span_seconds,
    }


def _proximity_map(graph: nx.DiGraph, blacklisted_nodes: set[str], max_depth: int = 2) -> dict[str, int]:
    proximity: dict[str, int] = {}
    if not blacklisted_nodes:
        return proximity

    undirected = graph.to_undirected(as_view=True)
    for source in blacklisted_nodes:
        if source not in undirected:
            continue
        distances = nx.single_source_shortest_path_length(undirected, source, cutoff=max_depth)
        for node, distance in distances.items():
            if node == source:
                continue
            current_distance = proximity.get(node)
            if current_distance is None or distance < current_distance:
                proximity[node] = int(distance)

    return proximity


class RiskScoringPlugin(BasePlugin):
    name = 'risk_scoring'
    description = 'Assigns a 0-100 risk score to each address using forensics heuristics.'

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        working_graph = graph
        if working_graph is None:
            if dataframe is None:
                raise ValueError('Risk scoring requires a graph or a transaction DataFrame.')
            working_graph = build_transaction_graph(dataframe)

        blacklisted_nodes = {
            _normalize_address(node)
            for node, attrs in working_graph.nodes(data=True)
            if bool(attrs.get('blacklist_flag'))
        }
        proximity = _proximity_map(working_graph, blacklisted_nodes)

        node_metrics = {node: _gather_node_metrics(working_graph, node) for node in working_graph.nodes}
        max_volume = max((metrics['total_volume'] for metrics in node_metrics.values()), default=0.0)

        results: list[dict[str, Any]] = []
        for node, metrics in node_metrics.items():
            node_attrs = working_graph.nodes[node]

            if bool(node_attrs.get('blacklist_flag')):
                score = 100
                reasons = ['blacklisted address']
            else:
                score = 0
                reasons: list[str] = []

                distance = proximity.get(node)
                if distance == 1:
                    score += 35
                    reasons.append('one hop away from a blacklisted address')
                elif distance == 2:
                    score += 15
                    reasons.append('two hops away from a blacklisted address')

                total_volume = float(metrics['total_volume'])
                if max_volume > 0 and total_volume > 0:
                    volume_component = int(round(20 * (log1p(total_volume) / log1p(max_volume))))
                    if volume_component > 0:
                        score += volume_component
                        reasons.append(f'volume pressure {volume_component}/20')

                transaction_count = int(metrics['transaction_count'])
                activity_component = min(15, transaction_count * 3)
                if activity_component > 0:
                    score += activity_component
                    reasons.append(f'activity burst {activity_component}/15')

                active_span_seconds = int(metrics['active_span_seconds'])
                if transaction_count >= 3 and active_span_seconds <= 3600:
                    score += 15
                    reasons.append('dense transfers within one hour')
                elif transaction_count >= 5 and active_span_seconds <= 86400:
                    score += 5
                    reasons.append('compact transfer window')

                counterparty_count = int(metrics['counterparty_count'])
                if counterparty_count >= 5:
                    score += 10
                    reasons.append('high counterparty spread')
                elif counterparty_count >= 3:
                    score += 5
                    reasons.append('moderate counterparty spread')

                if metrics['in_degree'] > 0 and metrics['out_degree'] > 0 and abs(metrics['in_degree'] - metrics['out_degree']) <= 1:
                    score += 5
                    reasons.append('balanced in/out pattern')

            score = max(0, min(100, int(score)))
            risk_band = _classify_score(score)

            node_attrs['risk_score'] = score
            node_attrs['risk_band'] = risk_band
            node_attrs['risk_reasons'] = reasons

            results.append(
                {
                    'address': node,
                    'risk_score': score,
                    'risk_band': risk_band,
                    'reasons': reasons,
                    **metrics,
                }
            )

        results.sort(key=lambda item: (-item['risk_score'], item['address']))

        return {
            'plugin': self.name,
            'description': self.description,
            'scored_addresses': len(results),
            'high_risk_addresses': [item for item in results if item['risk_score'] >= 70],
            'results': results,
        }


def run_risk_scoring(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
) -> dict[str, Any]:
    return RiskScoringPlugin().run(dataframe=dataframe, graph=graph)