from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.base import BasePlugin


def _normalize_address(value: object) -> str:
    if value is None or pd.isna(value):
        return ''
    return str(value).strip().lower()


def _get_group_key(row: pd.Series) -> str:
    for column in ('transaction_id', 'tx_hash', 'hash', 'metadata'):
        if column in row.index:
            value = _normalize_address(row.get(column))
            if value:
                return value
    timestamp = row.get('timestamp')
    recipient = _normalize_address(row.get('recipient_address'))
    amount = row.get('amount')
    if pd.notna(timestamp) and recipient and pd.notna(amount):
        return f'{pd.to_datetime(timestamp, utc=True).isoformat()}::{recipient}::{float(amount):.8f}'
    return ''


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {item: item for item in items}
        self.rank = {item: 0 for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        if self.rank[root_left] == self.rank[root_right]:
            self.rank[root_left] += 1


def _cluster_by_shared_transaction(dataframe: pd.DataFrame, union_find: _UnionFind) -> list[str]:
    grouped_addresses: list[list[str]] = []
    if dataframe.empty:
        return []

    working_frame = dataframe.copy()
    working_frame['__cluster_key__'] = working_frame.apply(_get_group_key, axis=1)

    for _, group in working_frame.groupby('__cluster_key__'):
        addresses = {
            _normalize_address(value)
            for value in group['sender_address'].tolist()
            if _normalize_address(value)
        }
        if len(addresses) < 2:
            continue
        grouped_addresses.append(sorted(addresses))
        for left, right in combinations(sorted(addresses), 2):
            union_find.union(left, right)

    return [address for cluster in grouped_addresses for address in cluster]


def _cluster_by_behavior(graph: nx.DiGraph, union_find: _UnionFind) -> None:
    behavioral_profiles: dict[str, set[str]] = {}
    for node in graph.nodes:
        outbound_neighbors = {target for _, target in graph.out_edges(node)}
        inbound_neighbors = {source for source, _ in graph.in_edges(node)}
        behavioral_profiles[node] = outbound_neighbors | inbound_neighbors

    nodes = sorted(graph.nodes)
    for index, left in enumerate(nodes):
        left_profile = behavioral_profiles.get(left, set())
        if len(left_profile) < 2:
            continue
        for right in nodes[index + 1 :]:
            right_profile = behavioral_profiles.get(right, set())
            if len(right_profile) < 2:
                continue
            union_size = len(left_profile | right_profile)
            if union_size == 0:
                continue
            overlap = len(left_profile & right_profile) / union_size
            if overlap >= 0.75:
                union_find.union(left, right)


class WalletClusteringPlugin(BasePlugin):
    name = 'wallet_clustering'
    description = 'Groups wallets into logical clusters using multi-input and behavioral heuristics.'

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        working_frame = dataframe.copy() if dataframe is not None else pd.DataFrame()
        working_graph = graph
        if working_graph is None and not working_frame.empty:
            working_graph = build_transaction_graph(working_frame)
        elif working_graph is None:
            working_graph = nx.DiGraph()

        known_addresses = sorted({str(node) for node in working_graph.nodes})
        if working_frame is not None and not working_frame.empty:
            known_addresses.extend(
                sorted(
                    {
                        _normalize_address(value)
                        for column in ('sender_address', 'recipient_address')
                        if column in working_frame.columns
                        for value in working_frame[column].dropna().tolist()
                        if _normalize_address(value)
                    }
                )
            )

        address_universe = sorted({address for address in known_addresses if address})
        union_find = _UnionFind(address_universe)

        multi_input_addresses = _cluster_by_shared_transaction(working_frame, union_find) if not working_frame.empty else []
        _cluster_by_behavior(working_graph, union_find)

        clusters_map: dict[str, set[str]] = defaultdict(set)
        for address in address_universe:
            root = union_find.find(address)
            clusters_map[root].add(address)

        cluster_records: list[dict[str, Any]] = []
        sorted_clusters = sorted(clusters_map.values(), key=lambda cluster: (-len(cluster), sorted(cluster)[0]))
        for index, members in enumerate(sorted_clusters, start=1):
            if len(members) < 2:
                continue
            cluster_id = f'cluster_{index}'
            sorted_members = sorted(members)
            for member in sorted_members:
                if working_graph.has_node(member):
                    working_graph.nodes[member]['cluster_id'] = cluster_id
                    working_graph.nodes[member]['cluster_size'] = len(sorted_members)

            cluster_records.append(
                {
                    'cluster_id': cluster_id,
                    'size': len(sorted_members),
                    'members': sorted_members,
                    'heuristics': [
                        'multi_input' if any(member in multi_input_addresses for member in sorted_members) else 'behavioral_similarity'
                    ],
                }
            )

        cluster_records.sort(key=lambda item: (-item['size'], item['cluster_id']))

        if working_graph is not None:
            working_graph.graph['cluster_count'] = len(cluster_records)

        return {
            'plugin': self.name,
            'description': self.description,
            'cluster_count': len(cluster_records),
            'clusters': cluster_records,
        }


def run_wallet_clustering(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
) -> dict[str, Any]:
    return WalletClusteringPlugin().run(dataframe=dataframe, graph=graph)