from __future__ import annotations

from pathlib import Path
import sys

import networkx as nx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def graph_from_rows():
    """Builds the same edge/transactions shape the CSV ingestion produces, so the tests
    exercise the algorithm through its real input format rather than a hand-made one.

    Rows are (sender, recipient, amount, timestamp). Several transactions between the
    same pair collapse onto one edge with a transaction list, exactly like the ingestion
    does - the algorithm has to flatten them back out in chronological order.
    """

    def build(rows: list[tuple[str, str, float, str]]) -> nx.DiGraph:
        graph = nx.DiGraph()
        for sender, recipient, amount, timestamp in rows:
            transaction = {'amount': amount, 'timestamp': timestamp, 'metadata': None}
            if graph.has_edge(sender, recipient):
                graph[sender][recipient]['transactions'].append(transaction)
            else:
                graph.add_edge(sender, recipient, transactions=[transaction])
        return graph

    return build
