from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.blacklist_check.service import _normalize_address
from app.analytics.graph_building.service import build_transaction_graph
from app.analytics.plugins.base import BasePlugin


DEFAULT_BRIDGE_KEYWORDS = (
    'bridge',
    'cbridge',
    'hop',
    'wormhole',
    'stargate',
    'celer',
    'layerzero',
    'synapse',
    'multichain',
    'portal',
)

DEFAULT_SWAP_KEYWORDS = (
    'swap',
    'dex',
    'router',
    'exchange',
    'uniswap',
    'sushiswap',
    'curve',
    'pancakeswap',
    '1inch',
    'paraswap',
    'aggregator',
)


def _parse_set(values: object) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        normalized = _normalize_address(values)
        return {normalized} if normalized else set()
    return {_normalize_address(value) for value in values if _normalize_address(value)}


def _node_text(node: str, attrs: dict[str, Any]) -> str:
    parts = [node]
    for key in ('address', 'label', 'service_name', 'name', 'metadata', 'tag'):
        value = attrs.get(key)
        if value is not None:
            parts.append(str(value))
    return ' '.join(parts).lower()


def _classify_node(
    node: str,
    attrs: dict[str, Any],
    bridge_addresses: set[str],
    swap_addresses: set[str],
    bridge_keywords: tuple[str, ...],
    swap_keywords: tuple[str, ...],
) -> tuple[str | None, list[str]]:
    normalized_node = _normalize_address(node)
    text = _node_text(node, attrs)
    reasons: list[str] = []

    if normalized_node in bridge_addresses:
        reasons.append('matched configured bridge address')
        return 'bridge', reasons
    if normalized_node in swap_addresses:
        reasons.append('matched configured swap address')
        return 'swap', reasons

    bridge_hits = [keyword for keyword in bridge_keywords if keyword in text]
    swap_hits = [keyword for keyword in swap_keywords if keyword in text]

    if bridge_hits:
        reasons.append(f'bridge keyword match: {", ".join(sorted(set(bridge_hits)))}')
        return 'bridge', reasons
    if swap_hits:
        reasons.append(f'swap keyword match: {", ".join(sorted(set(swap_hits)))}')
        return 'swap', reasons

    in_degree = int(attrs.get('in_degree', 0) or 0)
    out_degree = int(attrs.get('out_degree', 0) or 0)
    transaction_count = int(attrs.get('transaction_count', 0) or 0)
    total_in = float(attrs.get('total_in_amount', 0.0) or 0.0)
    total_out = float(attrs.get('total_out_amount', 0.0) or 0.0)

    if in_degree >= 2 and out_degree >= 1 and transaction_count >= 2 and total_in > 0 and total_out > 0:
        flow_ratio = total_out / total_in if total_in else 0.0
        if 0.5 <= flow_ratio <= 1.5:
            reasons.append('high-flow relay pattern')
            return 'bridge', reasons

    return None, reasons


class ChainHoppingPlugin(BasePlugin):
    name = 'chain_hopping'
    description = 'Flags likely cross-chain bridges and swap services as chain-hopping points.'

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        working_graph = graph
        if working_graph is None:
            if dataframe is None:
                raise ValueError('Chain hopping detection requires a graph or a transaction DataFrame.')
            working_graph = build_transaction_graph(dataframe)

        bridge_addresses = _parse_set(context.get('bridge_addresses'))
        swap_addresses = _parse_set(context.get('swap_addresses'))
        bridge_keywords = tuple(context.get('bridge_keywords', DEFAULT_BRIDGE_KEYWORDS))
        swap_keywords = tuple(context.get('swap_keywords', DEFAULT_SWAP_KEYWORDS))

        hop_points: list[dict[str, Any]] = []
        for node, attrs in working_graph.nodes(data=True):
            node_attrs = dict(attrs)
            service_type, reasons = _classify_node(
                node=node,
                attrs=node_attrs,
                bridge_addresses=bridge_addresses,
                swap_addresses=swap_addresses,
                bridge_keywords=bridge_keywords,
                swap_keywords=swap_keywords,
            )
            if service_type is None:
                continue

            incoming_sources = sorted({source for source, _ in working_graph.in_edges(node)})
            outgoing_targets = sorted({target for _, target in working_graph.out_edges(node)})
            total_in_amount = sum(float(edge_attrs.get('total_amount', edge_attrs.get('weight', 0.0)) or 0.0) for _, _, edge_attrs in working_graph.in_edges(node, data=True))
            total_out_amount = sum(float(edge_attrs.get('total_amount', edge_attrs.get('weight', 0.0)) or 0.0) for _, _, edge_attrs in working_graph.out_edges(node, data=True))
            transaction_count = int(node_attrs.get('transaction_count', 0) or 0)

            for _, _, edge_attrs in working_graph.in_edges(node, data=True):
                edge_attrs['chain_hop_flag'] = True
                edge_attrs['chain_hop_type'] = service_type
                edge_attrs['chain_hop_target'] = node
                edge_attrs['chain_hop_reason'] = reasons

            working_graph.nodes[node]['chain_hop_flag'] = True
            working_graph.nodes[node]['chain_hop_type'] = service_type
            working_graph.nodes[node]['chain_hop_reasons'] = reasons

            hop_points.append(
                {
                    'node': node,
                    'type': service_type,
                    'reasons': reasons,
                    'incoming_sources': incoming_sources,
                    'outgoing_targets': outgoing_targets,
                    'transaction_count': transaction_count,
                    'total_in_amount': total_in_amount,
                    'total_out_amount': total_out_amount,
                    'flow_ratio': (total_out_amount / total_in_amount) if total_in_amount else None,
                }
            )

        if working_graph is not None:
            working_graph.graph['chain_hop_count'] = len(hop_points)

        return {
            'plugin': self.name,
            'description': self.description,
            'hop_count': len(hop_points),
            'hops': hop_points,
        }


def run_chain_hopping(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
    **context: Any,
) -> dict[str, Any]:
    return ChainHoppingPlugin().run(dataframe=dataframe, graph=graph, **context)