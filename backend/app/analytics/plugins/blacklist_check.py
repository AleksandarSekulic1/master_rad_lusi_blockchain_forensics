from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.plugins.base import BasePlugin


def _normalize_address(value: object) -> str:
    if value is None:
        return ''
    if pd.isna(value):
        return ''
    return str(value).strip().lower()


@dataclass(frozen=True)
class BlacklistEntry:
    address: str
    sources: tuple[str, ...]
    label: str


DEFAULT_BLACKLIST_ENTRIES: tuple[BlacklistEntry, ...] = (
    BlacklistEntry(
        address='0xbad0000000000000000000000000000000000001',
        sources=('OFAC',),
        label='Simulated OFAC sanctioned address',
    ),
    BlacklistEntry(
        address='0xdead00000000000000000000000000000000cafe',
        sources=('Chainabuse',),
        label='Simulated Chainabuse high-risk address',
    ),
    BlacklistEntry(
        address='0xfeed00000000000000000000000000000000beef',
        sources=('OFAC', 'Chainabuse'),
        label='Simulated multi-source malicious address',
    ),
)


def _build_blacklist_index(entries: tuple[BlacklistEntry, ...]) -> dict[str, BlacklistEntry]:
    return {entry.address: entry for entry in entries}


def _collect_addresses(dataframe: pd.DataFrame | None, graph: nx.DiGraph | None) -> set[str]:
    addresses: set[str] = set()

    if graph is not None:
        addresses.update(_normalize_address(node) for node in graph.nodes)

    if dataframe is not None:
        for column in ('sender_address', 'recipient_address', 'address'):
            if column in dataframe.columns:
                addresses.update(_normalize_address(value) for value in dataframe[column].dropna().tolist())

    return {address for address in addresses if address}


def _build_node_lookup(graph: nx.DiGraph | None) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    if graph is None:
        return lookup

    for node in graph.nodes:
        normalized = _normalize_address(node)
        if not normalized:
            continue
        lookup.setdefault(normalized, []).append(node)

    return lookup


class BlacklistCheckPlugin(BasePlugin):
    name = 'blacklist_check'
    description = 'Flags addresses that match a local or simulated blacklist.'

    def __init__(self, blacklist_entries: tuple[BlacklistEntry, ...] | None = None) -> None:
        self._blacklist_index = _build_blacklist_index(blacklist_entries or DEFAULT_BLACKLIST_ENTRIES)

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        addresses = _collect_addresses(dataframe, graph)
        node_lookup = _build_node_lookup(graph)
        matches: list[dict[str, Any]] = []

        for address in sorted(addresses):
            entry = self._blacklist_index.get(address)
            if entry is None:
                continue

            matching_nodes = node_lookup.get(address, [])
            for node_id in matching_nodes:
                graph.nodes[node_id]['blacklist_flag'] = True
                graph.nodes[node_id]['blacklist_sources'] = list(entry.sources)
                graph.nodes[node_id]['blacklist_label'] = entry.label

            matches.append(
                {
                    'address': address,
                    'flagged': True,
                    'sources': list(entry.sources),
                    'label': entry.label,
                    'nodes': matching_nodes,
                }
            )

        if graph is not None:
            graph.graph['blacklist_match_count'] = len(matches)

        return {
            'plugin': self.name,
            'description': self.description,
            'blacklist_size': len(self._blacklist_index),
            'matched_count': len(matches),
            'matches': matches,
        }


def run_blacklist_check(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
    blacklist_entries: tuple[BlacklistEntry, ...] | None = None,
) -> dict[str, Any]:
    return BlacklistCheckPlugin(blacklist_entries=blacklist_entries).run(dataframe=dataframe, graph=graph)