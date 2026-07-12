from __future__ import annotations

import pandas as pd
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.graph_building.service import (
    build_transaction_graph,
    transaction_graph_to_node_link_json,
)
from app.analytics.path_finding.service import find_transaction_paths


def main() -> None:
    dataframe = pd.DataFrame(
        [
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 10, 'timestamp': '2024-01-01T00:00:00Z', 'metadata': 'tx1'},
            {'sender_address': '0xB', 'recipient_address': '0xC', 'amount': 5, 'timestamp': '2024-01-01T00:05:00Z', 'metadata': 'tx2'},
            {'sender_address': '0xA', 'recipient_address': '0xC', 'amount': 1, 'timestamp': '2024-01-01T00:10:00Z', 'metadata': 'tx3'},
        ]
    )

    dataframe['timestamp'] = pd.to_datetime(dataframe['timestamp'], utc=True)

    graph = build_transaction_graph(dataframe)
    node_link = transaction_graph_to_node_link_json(graph)
    path_result = find_transaction_paths(dataframe, '0xA', '0xC', strategy='shortest')

    assert len(node_link['nodes']) == 3, 'Expected 3 graph nodes.'
    assert len(node_link['links']) == 3, 'Expected 3 graph edges.'
    assert path_result['path_count'] == 1, 'Expected one shortest path.'
    assert path_result['paths'][0]['nodes'] in (['0xA', '0xC'], ['0xA', '0xB', '0xC'])

    print('graph_nodes=', len(node_link['nodes']))
    print('graph_edges=', len(node_link['links']))
    print('shortest_path=', path_result['paths'][0]['nodes'])


if __name__ == '__main__':
    main()