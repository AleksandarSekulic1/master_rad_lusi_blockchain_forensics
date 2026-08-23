from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.graph_building import build_transaction_graph


def _path_summary(graph: nx.DiGraph, path: list[str]) -> dict[str, Any]:
    total_amount = 0.0
    transaction_count = 0
    edge_details: list[dict[str, Any]] = []

    for source, target in zip(path[:-1], path[1:]):
        edge_data = graph.get_edge_data(source, target, default={})
        total_amount += float(edge_data.get('total_amount', edge_data.get('weight', 0.0)))
        transaction_count += int(edge_data.get('transaction_count', 0))
        edge_details.append({
            'source': source,
            'target': target,
            'total_amount': float(edge_data.get('total_amount', edge_data.get('weight', 0.0))),
            'transaction_count': int(edge_data.get('transaction_count', 0)),
            'first_seen': edge_data.get('first_seen'),
            'last_seen': edge_data.get('last_seen'),
        })

    return {
        'nodes': path,
        'hops': max(len(path) - 1, 0),
        'total_amount': total_amount,
        'transaction_count': transaction_count,
        'edges': edge_details,
    }


def find_transaction_paths(
    cleaned_frame: pd.DataFrame,
    source_address: str,
    target_address: str,
    strategy: str = 'shortest',
    cutoff: int = 6,
    max_paths: int = 20,
) -> dict[str, Any]:
    """Find transaction paths between two addresses."""

    graph = build_transaction_graph(cleaned_frame)

    if source_address not in graph:
        raise ValueError(f'Source address not found in graph: {source_address}')
    if target_address not in graph:
        raise ValueError(f'Target address not found in graph: {target_address}')

    strategy_normalized = strategy.strip().lower()
    paths: list[list[str]] = []

    if strategy_normalized == 'all_simple_paths':
        paths = list(nx.all_simple_paths(graph, source_address, target_address, cutoff=cutoff))[:max_paths]
    elif strategy_normalized == 'most_likely':
        working_graph = graph.copy()
        for source, target, attrs in working_graph.edges(data=True):
            total_amount = max(float(attrs.get('total_amount', attrs.get('weight', 0.0))), 0.0)
            attrs['cost'] = 1.0 / (total_amount + 1e-9)
        shortest_path = nx.shortest_path(working_graph, source_address, target_address, weight='cost')
        paths = [shortest_path]
    else:
        shortest_path = nx.shortest_path(graph, source_address, target_address)
        paths = [shortest_path]

    return {
        'source': source_address,
        'target': target_address,
        'strategy': strategy_normalized,
        'path_count': len(paths),
        'paths': [_path_summary(graph, path) for path in paths],
    }


def _reconstruct_path(parent: dict[str, str], source_address: str, target_address: str) -> list[str]:
    path = [target_address]
    while path[-1] != source_address:
        path.append(parent[path[-1]])
    path.reverse()
    return path


def bfs_shortest_path(graph: nx.DiGraph, source_address: str, target_address: str) -> dict[str, Any]:
    """Plain breadth-first search for the shortest DIRECTED path of actual transactions
    from source_address to target_address.

    First version, intentionally minimal: unweighted, single path, no CEX/cash-out
    detection, no scoring - see find_transaction_paths above for the richer sibling
    (multiple strategies, multiple paths, per-hop amounts) once those are needed.

    Directionality matters here: a graph edge only exists sender -> recipient, so walking
    graph.successors() (rather than treating the graph as undirected) means a returned
    path only ever follows hops where money actually moved that way - never against a
    real transaction's direction.
    """
    if source_address not in graph or target_address not in graph:
        return {'found': False, 'path': [], 'hops': 0}

    if source_address == target_address:
        return {'found': True, 'path': [source_address], 'hops': 0}

    visited = {source_address}
    parent: dict[str, str] = {}
    queue: deque[str] = deque([source_address])

    while queue:
        current = queue.popleft()
        for neighbor in graph.successors(current):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            parent[neighbor] = current
            if neighbor == target_address:
                path = _reconstruct_path(parent, source_address, target_address)
                return {'found': True, 'path': path, 'hops': len(path) - 1}
            queue.append(neighbor)

    return {'found': False, 'path': [], 'hops': 0}