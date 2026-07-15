from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.analytics.plugins.blacklist_check import _normalize_address
from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.base import BasePlugin


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(timestamp):
        return None
    return timestamp


def _transaction_rows_from_graph(graph: nx.DiGraph) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, target, attrs in graph.edges(data=True):
        transactions = list(attrs.get('transactions', []))
        if not transactions:
            rows.append(
                {
                    'sender_address': source,
                    'recipient_address': target,
                    'amount': float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0),
                    'timestamp': _parse_timestamp(attrs.get('first_seen')),
                }
            )
            continue

        for transaction in transactions:
            timestamp = _parse_timestamp(transaction.get('timestamp')) or _parse_timestamp(attrs.get('first_seen'))
            rows.append(
                {
                    'sender_address': source,
                    'recipient_address': target,
                    'amount': float(transaction.get('amount', 0.0) or 0.0),
                    'timestamp': timestamp,
                }
            )

    return rows


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

    if not rows and graph is not None:
        rows = _transaction_rows_from_graph(graph)

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True, errors='coerce')
        frame = frame.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    return frame


def _safe_division(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _node_features(graph: nx.DiGraph, node: str) -> dict[str, Any]:
    incoming_edges = list(graph.in_edges(node, data=True))
    outgoing_edges = list(graph.out_edges(node, data=True))

    incoming_amounts: list[float] = []
    outgoing_amounts: list[float] = []
    incoming_timestamps: list[pd.Timestamp] = []
    outgoing_timestamps: list[pd.Timestamp] = []
    counterparties: set[str] = set()

    for source, _, attrs in incoming_edges:
        counterparties.add(source)
        transaction_amounts = [float(transaction.get('amount', 0.0) or 0.0) for transaction in attrs.get('transactions', [])]
        if transaction_amounts:
            incoming_amounts.extend(transaction_amounts)
        else:
            incoming_amounts.append(float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0))
        for transaction in attrs.get('transactions', []):
            timestamp = _parse_timestamp(transaction.get('timestamp'))
            if timestamp is not None:
                incoming_timestamps.append(timestamp)
        first_seen = _parse_timestamp(attrs.get('first_seen'))
        last_seen = _parse_timestamp(attrs.get('last_seen'))
        if first_seen is not None:
            incoming_timestamps.append(first_seen)
        if last_seen is not None:
            incoming_timestamps.append(last_seen)

    for _, target, attrs in outgoing_edges:
        counterparties.add(target)
        transaction_amounts = [float(transaction.get('amount', 0.0) or 0.0) for transaction in attrs.get('transactions', [])]
        if transaction_amounts:
            outgoing_amounts.extend(transaction_amounts)
        else:
            outgoing_amounts.append(float(attrs.get('total_amount', attrs.get('weight', 0.0)) or 0.0))
        for transaction in attrs.get('transactions', []):
            timestamp = _parse_timestamp(transaction.get('timestamp'))
            if timestamp is not None:
                outgoing_timestamps.append(timestamp)
        first_seen = _parse_timestamp(attrs.get('first_seen'))
        last_seen = _parse_timestamp(attrs.get('last_seen'))
        if first_seen is not None:
            outgoing_timestamps.append(first_seen)
        if last_seen is not None:
            outgoing_timestamps.append(last_seen)

    total_in_amount = sum(incoming_amounts)
    total_out_amount = sum(outgoing_amounts)
    total_transactions = len(incoming_amounts) + len(outgoing_amounts)
    all_amounts = [amount for amount in incoming_amounts + outgoing_amounts if amount > 0]
    max_amount = max(all_amounts, default=0.0)
    average_amount = sum(all_amounts) / len(all_amounts) if all_amounts else 0.0
    amount_std = pd.Series(all_amounts).std(ddof=0) if len(all_amounts) > 1 else 0.0

    all_timestamps = [timestamp for timestamp in incoming_timestamps + outgoing_timestamps if timestamp is not None]
    active_span_seconds = int((max(all_timestamps) - min(all_timestamps)).total_seconds()) if len(all_timestamps) >= 2 else 0
    activity_rate = _safe_division(total_transactions, max(active_span_seconds / 3600.0, 1.0))

    node_balance = total_in_amount - total_out_amount
    amount_spike_ratio = _safe_division(max_amount, average_amount)

    return {
        'in_degree': graph.in_degree(node),
        'out_degree': graph.out_degree(node),
        'transaction_count': total_transactions,
        'total_in_amount': total_in_amount,
        'total_out_amount': total_out_amount,
        'total_volume': total_in_amount + total_out_amount,
        'counterparty_count': len(counterparties),
        'active_span_seconds': active_span_seconds,
        'in_out_ratio': _safe_division(total_in_amount, total_out_amount),
        'out_in_ratio': _safe_division(total_out_amount, total_in_amount),
        'balance_delta': abs(node_balance),
        'average_amount': average_amount,
        'max_amount': max_amount,
        'amount_std': float(amount_std or 0.0),
        'amount_spike_ratio': amount_spike_ratio,
        'activity_rate': activity_rate,
    }


class AnomalyDetectionPlugin(BasePlugin):
    name = 'anomaly_detection'
    description = 'Uses Isolation Forest to flag atypical flow behavior as investigation hints.'

    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        working_graph = graph
        if working_graph is None:
            if dataframe is None:
                raise ValueError('Anomaly detection requires a graph or a transaction DataFrame.')
            working_graph = build_transaction_graph(dataframe)

        node_records = [
            {'address': node, **_node_features(working_graph, node)}
            for node in working_graph.nodes
        ]
        if not node_records:
            return {
                'plugin': self.name,
                'description': self.description,
                'anomaly_count': 0,
                'results': [],
            }

        feature_frame = pd.DataFrame(node_records).set_index('address')
        feature_columns = [
            'in_degree',
            'out_degree',
            'transaction_count',
            'total_in_amount',
            'total_out_amount',
            'total_volume',
            'counterparty_count',
            'active_span_seconds',
            'in_out_ratio',
            'out_in_ratio',
            'balance_delta',
            'average_amount',
            'max_amount',
            'amount_std',
            'amount_spike_ratio',
            'activity_rate',
        ]
        training_frame = feature_frame[feature_columns].fillna(0.0)

        if len(training_frame) >= 2:
            contamination = float(
                context.get(
                    'contamination',
                    min(0.2, max(0.05, 1.0 / max(len(training_frame), 20))),
                )
            )
            contamination = min(max(contamination, 0.01), 0.5)
            random_state = int(context.get('random_state', 42))
            model = IsolationForest(
                n_estimators=int(context.get('n_estimators', 100)),
                contamination=contamination,
                random_state=random_state,
            )
            model.fit(training_frame)
            predictions = model.predict(training_frame)
            scores = model.decision_function(training_frame)
        else:
            predictions = [1] * len(training_frame)
            scores = [0.0] * len(training_frame)

        results: list[dict[str, Any]] = []
        for position, (address, row) in enumerate(feature_frame.iterrows()):
            anomaly_score = float(-scores[position])
            anomaly_flag = bool(predictions[position] == -1)
            node_attrs = working_graph.nodes[address]
            node_attrs['anomaly_flag'] = anomaly_flag
            node_attrs['anomaly_score'] = anomaly_score
            node_attrs['anomaly_features'] = {column: row[column] for column in feature_columns}

            results.append(
                {
                    'address': address,
                    'anomaly_flag': anomaly_flag,
                    'anomaly_score': anomaly_score,
                    'features': {column: row[column] for column in feature_columns},
                }
            )

        results.sort(key=lambda item: (-item['anomaly_score'], item['address']))

        if working_graph is not None:
            working_graph.graph['anomaly_flagged_count'] = sum(1 for item in results if item['anomaly_flag'])

        return {
            'plugin': self.name,
            'description': self.description,
            'anomaly_count': sum(1 for item in results if item['anomaly_flag']),
            'feature_columns': feature_columns,
            'results': results,
        }


def run_anomaly_detection(
    dataframe: pd.DataFrame | None = None,
    graph: nx.DiGraph | None = None,
    **context: Any,
) -> dict[str, Any]:
    return AnomalyDetectionPlugin().run(dataframe=dataframe, graph=graph, **context)