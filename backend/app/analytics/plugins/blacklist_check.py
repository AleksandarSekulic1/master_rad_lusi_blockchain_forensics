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
    # Real (not simulated) entry - a Bitcoin address is a valid graph node exactly like an
    # Ethereum one, so it belongs in the same index rather than a chain-specific list. OFAC
    # designated Garantex Europe OU on 2022-04-05 (see BITCOIN-UTXO-PLAN.md); this address
    # is listed as one of its "Digital Currency Address - XBT" identifiers in that SDN
    # entry (ofac.treasury.gov/recent-actions/20220405).
    BlacklistEntry(
        address='3lpoy53k625zvee47zasig5jgkaxj27kh1',
        sources=('OFAC',),
        label='Garantex Europe OU (OFAC SDN, designated 2022-04-05)',
    ),
    # Real Ethereum entry - the Ronin Bridge hack (2022-03-23, ~$625M stolen), attributed
    # to the Lazarus Group. OFAC designated this exact address on 2022-04-14 as one of its
    # "Digital Currency Address - ETH" identifiers (ofac.treasury.gov/recent-actions/20220414).
    # Already present in known_entities.json (address enrichment) under the same label -
    # added here too since blacklist_check reads its own list, not that file.
    BlacklistEntry(
        address='0x098b716b8aaf21512996dc57eb0615e2383e2f96',
        sources=('OFAC',),
        label='Ronin Bridge Exploiter / Lazarus Group (OFAC SDN, designated 2022-04-14)',
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