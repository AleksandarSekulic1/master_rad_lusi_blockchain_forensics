from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.plugins.blacklist_check import _normalize_address
from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.base import BasePlugin


def _parse_timestamp(value: object) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(timestamp):
        return None
    return timestamp


def _collect_transactions(dataframe: pd.DataFrame | None, graph: nx.DiGraph | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    if dataframe is not None:
        working_frame = dataframe.copy()
        if 'timestamp' in working_frame.columns:
            working_frame['timestamp'] = pd.to_datetime(working_frame['timestamp'], utc=True, errors='coerce')
        for row in working_frame.itertuples(index=False):
            sender = _normalize_address(getattr(row, 'sender_address', None))
            recipient = _normalize_address(getattr(row, 'recipient_address', None))
            amount = getattr(row, 'amount', None)
            timestamp = getattr(row, 'timestamp', None)
            if not sender or not recipient or amount is None or pd.isna(amount) or timestamp is None or pd.isna(timestamp):
                continue
            rows.append(
                {
                    'sender_address': sender,
                    'recipient_address': recipient,
                    'amount': float(amount),
                    'timestamp': timestamp,
                }
            )
        if rows:
            return pd.DataFrame(rows)

    if graph is None:
        return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp'])

    for source, target, attrs in graph.edges(data=True):
        transactions = list(attrs.get('transactions', []))
        if not transactions:
            rows.append(
                {
                    'sender_address': _normalize_address(source),
                    'recipient_address': _normalize_address(target),
                    'amount': float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0),
                    'timestamp': _parse_timestamp(attrs.get('first_seen')),
                }
            )
            continue

        for transaction in transactions:
            timestamp = _parse_timestamp(transaction.get('timestamp'))
            if timestamp is None:
                timestamp = _parse_timestamp(attrs.get('first_seen'))
            rows.append(
                {
                    'sender_address': _normalize_address(source),
                    'recipient_address': _normalize_address(target),
                    'amount': float(transaction.get('amount', 0.0) or 0.0),
                    'timestamp': timestamp,
                }
            )

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    return frame


@dataclass(frozen=True)
class PeelStep:
    node: str
    received_amount: float
    received_at: pd.Timestamp
    forwarded_to: str | None
    forwarded_amount: float
    peeled_amount: float
    transaction_count: int


def _build_flow_index(transactions: pd.DataFrame) -> dict[str, dict[str, list[dict[str, Any]]]]:
    flow_index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {'incoming': [], 'outgoing': []})

    for row in transactions.itertuples(index=False):
        sender = _normalize_address(getattr(row, 'sender_address'))
        recipient = _normalize_address(getattr(row, 'recipient_address'))
        timestamp = _parse_timestamp(getattr(row, 'timestamp'))
        if not sender or not recipient or timestamp is None:
            continue

        transaction = {
            'sender_address': sender,
            'recipient_address': recipient,
            'amount': float(getattr(row, 'amount') or 0.0),
            'timestamp': timestamp,
        }
        flow_index[sender]['outgoing'].append(transaction)
        flow_index[recipient]['incoming'].append(transaction)

    for node_flows in flow_index.values():
        node_flows['incoming'].sort(key=lambda item: item['timestamp'])
        node_flows['outgoing'].sort(key=lambda item: item['timestamp'])

    return flow_index


def _follow_chain(
    seed_node: str,
    incoming_transaction: dict[str, Any],
    flow_index: dict[str, dict[str, list[dict[str, Any]]]],
    max_depth: int,
    max_gap_minutes: int,
    min_forward_ratio: float,
    max_peel_ratio: float,
) -> list[PeelStep]:
    chain: list[PeelStep] = []
    current_node = seed_node
    received_amount = float(incoming_transaction['amount'])
    received_at = incoming_transaction['timestamp']
    visited = {seed_node}

    for _ in range(max_depth):
        outgoing_transactions = [
            transaction
            for transaction in flow_index.get(current_node, {}).get('outgoing', [])
            if transaction['timestamp'] >= received_at
            and transaction['timestamp'] <= received_at + timedelta(minutes=max_gap_minutes)
        ]
        if len(outgoing_transactions) < 2:
            break

        indexed_outgoing = list(enumerate(outgoing_transactions))
        continuation_index, continuation_transaction = max(indexed_outgoing, key=lambda item: item[1]['amount'])
        peeled_transactions = [transaction for index, transaction in indexed_outgoing if index != continuation_index]
        forwarded_amount = float(continuation_transaction['amount'])
        peeled_amount = sum(float(transaction['amount']) for transaction in peeled_transactions)

        if received_amount <= 0:
            break

        forwarded_ratio = forwarded_amount / received_amount
        peeled_ratio = peeled_amount / received_amount if received_amount else 0.0

        if forwarded_ratio < min_forward_ratio or peeled_ratio <= 0 or peeled_ratio > max_peel_ratio:
            break

        chain.append(
            PeelStep(
                node=current_node,
                received_amount=received_amount,
                received_at=received_at,
                forwarded_to=str(continuation_transaction['recipient_address']),
                forwarded_amount=forwarded_amount,
                peeled_amount=peeled_amount,
                transaction_count=len(outgoing_transactions),
            )
        )

        current_node = str(continuation_transaction['recipient_address'])
        if current_node in visited:
            break
        visited.add(current_node)
        received_amount = forwarded_amount
        received_at = continuation_transaction['timestamp']

    if chain:
        chain.append(
            PeelStep(
                node=current_node,
                received_amount=received_amount,
                received_at=received_at,
                forwarded_to=None,
                forwarded_amount=0.0,
                peeled_amount=0.0,
                transaction_count=len(flow_index.get(current_node, {}).get('outgoing', [])),
            )
        )

    return chain


def _chain_signature(chain: list[PeelStep]) -> tuple[str, ...]:
    return tuple(step.node for step in chain)


def _is_contained(candidate: tuple[str, ...], container: tuple[str, ...]) -> bool:
    if len(candidate) > len(container):
        return False
    for start_index in range(len(container) - len(candidate) + 1):
        if container[start_index : start_index + len(candidate)] == candidate:
            return True
    return False


class PeelChainsPlugin(BasePlugin):
    name = 'peel_chains'
    description = 'Detects automated peel-chain dispersion patterns in transaction flows.'

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        working_graph = graph
        if working_graph is None:
            if dataframe is None:
                raise ValueError('Peel chain detection requires a graph or a transaction DataFrame.')
            working_graph = build_transaction_graph(dataframe)

        transactions = _collect_transactions(dataframe, working_graph)
        if transactions.empty:
            return {
                'plugin': self.name,
                'description': self.description,
                'chain_count': 0,
                'chains': [],
            }

        transactions['timestamp'] = pd.to_datetime(transactions['timestamp'], utc=True, errors='coerce')
        transactions = transactions.dropna(subset=['timestamp'])
        flow_index = _build_flow_index(transactions)

        min_seed_amount = float(context.get('min_seed_amount', 100.0))
        max_gap_minutes = int(context.get('max_gap_minutes', 180))
        min_forward_ratio = float(context.get('min_forward_ratio', 0.65))
        max_peel_ratio = float(context.get('max_peel_ratio', 0.35))
        min_chain_length = int(context.get('min_chain_length', 3))
        max_depth = int(context.get('max_depth', 8))

        candidate_chains: list[list[PeelStep]] = []
        for node, node_flows in flow_index.items():
            incoming_transactions = [
                transaction
                for transaction in node_flows['incoming']
                if float(transaction['amount']) >= min_seed_amount
            ]
            if not incoming_transactions:
                continue

            for incoming_transaction in incoming_transactions:
                chain = _follow_chain(
                    seed_node=node,
                    incoming_transaction=incoming_transaction,
                    flow_index=flow_index,
                    max_depth=max_depth,
                    max_gap_minutes=max_gap_minutes,
                    min_forward_ratio=min_forward_ratio,
                    max_peel_ratio=max_peel_ratio,
                )
                if len(chain) >= min_chain_length:
                    candidate_chains.append(chain)

        unique_chains: list[list[PeelStep]] = []
        for chain in sorted(candidate_chains, key=len, reverse=True):
            signature = _chain_signature(chain)
            if any(_is_contained(signature, _chain_signature(existing)) for existing in unique_chains):
                continue
            unique_chains.append(chain)

        chain_records: list[dict[str, Any]] = []
        for chain_index, chain in enumerate(sorted(unique_chains, key=len, reverse=True), start=1):
            chain_id = f'peel_chain_{chain_index}'
            nodes = [step.node for step in chain]
            received_amounts = [step.received_amount for step in chain]
            peeled_amounts = [step.peeled_amount for step in chain[:-1]]
            time_span_seconds = 0
            if chain and chain[0].received_at is not None and chain[-1].received_at is not None:
                time_span_seconds = int((chain[-1].received_at - chain[0].received_at).total_seconds())

            confidence = min(100, int(round(40 + len(chain) * 10 + sum(1 for amount in peeled_amounts if amount > 0) * 5)))

            for step_index, step in enumerate(chain):
                if working_graph.has_node(step.node):
                    working_graph.nodes[step.node]['peel_chain_flag'] = True
                    working_graph.nodes[step.node]['peel_chain_id'] = chain_id
                    working_graph.nodes[step.node]['peel_chain_step'] = step_index + 1
                    working_graph.nodes[step.node]['peel_chain_role'] = 'seed' if step_index == 0 else 'relay' if step.forwarded_to else 'terminal'
                    working_graph.nodes[step.node]['peel_chain_score'] = confidence

            chain_records.append(
                {
                    'chain_id': chain_id,
                    'depth': len(chain),
                    'nodes': nodes,
                    'seed_address': nodes[0],
                    'terminal_address': nodes[-1],
                    'received_amounts': received_amounts,
                    'peeled_amounts': peeled_amounts,
                    'time_span_seconds': time_span_seconds,
                    'confidence': confidence,
                }
            )

        if working_graph is not None:
            working_graph.graph['peel_chain_count'] = len(chain_records)

        return {
            'plugin': self.name,
            'description': self.description,
            'chain_count': len(chain_records),
            'chains': chain_records,
        }


def run_peel_chains(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
    **context: Any,
) -> dict[str, Any]:
    return PeelChainsPlugin().run(dataframe=dataframe, graph=graph, **context)