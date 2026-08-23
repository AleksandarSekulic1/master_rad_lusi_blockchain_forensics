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


def find_path_to_nearest_of(graph: nx.DiGraph, source_address: str, target_addresses: set[str]) -> dict[str, Any]:
    """Same directed BFS as bfs_shortest_path, but the destination isn't known in advance -
    only a CANDIDATE SET is (e.g. "every known-CEX address actually present in this
    graph"). Stops at the first of those candidates reached, which is guaranteed to be the
    nearest one by hop count: BFS visits nodes in strictly non-decreasing distance order,
    so the first candidate encountered can never be beaten by one found later.

    Deliberately generic - this function has no idea what a "CEX" is, it just walks the
    graph toward the nearest node in a set the caller supplies. That set is the caller's
    job to build from real data (see run_case_pathfinding in api/routes/cases.py, which
    checks every graph node against the local known_entities registry) - this function
    never guesses at what a target address "looks like".

    Ties (more than one candidate at the same, nearest distance) are broken by address,
    ascending - otherwise "nearest" would silently depend on graph iteration order, which
    is not something a forensic result should be allowed to hinge on.
    """
    if source_address not in graph:
        return {'found': False, 'path': [], 'hops': 0, 'destination': None}

    if source_address in target_addresses:
        return {'found': True, 'path': [source_address], 'hops': 0, 'destination': source_address}

    visited = {source_address}
    parent: dict[str, str] = {}
    frontier = [source_address]

    while frontier:
        next_frontier: list[str] = []
        for current in frontier:
            for neighbor in graph.successors(current):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                parent[neighbor] = current
                next_frontier.append(neighbor)

        matches = sorted(node for node in next_frontier if node in target_addresses)
        if matches:
            destination = matches[0]
            path = _reconstruct_path(parent, source_address, destination)
            return {'found': True, 'path': path, 'hops': len(path) - 1, 'destination': destination}

        frontier = next_frontier

    return {'found': False, 'path': [], 'hops': 0, 'destination': None}