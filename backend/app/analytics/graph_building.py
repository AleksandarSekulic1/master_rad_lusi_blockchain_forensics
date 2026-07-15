from __future__ import annotations

from datetime import datetime
from typing import Any

import networkx as nx
import pandas as pd


REQUIRED_COLUMNS = ('sender_address', 'recipient_address', 'amount', 'timestamp')


def _ensure_required_columns(dataframe: pd.DataFrame) -> None:
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in dataframe.columns]
    if missing_columns:
        raise ValueError(f'Missing required columns: {", ".join(missing_columns)}')


def _serialize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (pd.Series, pd.Index)):
        return [_serialize_value(item) for item in value.tolist()]
    if pd.isna(value):
        return None
    return value


def build_transaction_graph(cleaned_frame: pd.DataFrame) -> nx.DiGraph:
    """Build a directed transaction graph from a cleaned transaction table."""

    _ensure_required_columns(cleaned_frame)

    graph = nx.DiGraph()
    graph.graph['generated_at'] = datetime.utcnow().isoformat() + 'Z'
    graph.graph['node_type'] = 'wallet_address'
    graph.graph['edge_type'] = 'transaction'

    for row in cleaned_frame.itertuples(index=False):
        sender = getattr(row, 'sender_address')
        recipient = getattr(row, 'recipient_address')
        amount = float(getattr(row, 'amount'))
        timestamp = _serialize_value(getattr(row, 'timestamp'))
        metadata = _serialize_value(getattr(row, 'metadata', None))

        graph.add_node(sender, address=sender, label=sender)
        graph.add_node(recipient, address=recipient, label=recipient)

        edge_data = graph.get_edge_data(sender, recipient, default={})
        transaction = {
            'amount': amount,
            'timestamp': timestamp,
            'metadata': metadata,
        }

        if edge_data:
            transactions = list(edge_data.get('transactions', []))
            transactions.append(transaction)
            total_amount = float(edge_data.get('total_amount', 0.0)) + amount
            transaction_count = int(edge_data.get('transaction_count', 0)) + 1
            graph.add_edge(
                sender,
                recipient,
                weight=total_amount,
                total_amount=total_amount,
                transaction_count=transaction_count,
                transactions=transactions,
                first_seen=edge_data.get('first_seen', timestamp),
                last_seen=timestamp,
            )
            continue

        graph.add_edge(
            sender,
            recipient,
            weight=amount,
            total_amount=amount,
            transaction_count=1,
            transactions=[transaction],
            first_seen=timestamp,
            last_seen=timestamp,
        )

    return graph


def transaction_graph_to_node_link_json(graph: nx.DiGraph) -> dict[str, Any]:
    """Convert a transaction graph to a frontend-friendly node-link structure."""

    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        serialized_attrs = {key: _serialize_value(value) for key, value in attrs.items()}
        serialized_attrs['id'] = node_id
        nodes.append(serialized_attrs)

    links = []
    for source, target, attrs in graph.edges(data=True):
        serialized_attrs = {key: _serialize_value(value) for key, value in attrs.items()}
        serialized_attrs['source'] = source
        serialized_attrs['target'] = target
        links.append(serialized_attrs)

    return {
        'directed': True,
        'multigraph': False,
        'graph': {key: _serialize_value(value) for key, value in graph.graph.items()},
        'nodes': nodes,
        'links': links,
    }