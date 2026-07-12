from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.blacklist_check.service import run_blacklist_check
from app.analytics.clustering.service import run_wallet_clustering
from app.analytics.graph_building.service import build_transaction_graph
from app.analytics.risk_scoring.service import run_risk_scoring


def main() -> None:
    dataframe = pd.DataFrame(
        [
            {
                'sender_address': '0xAlpha1',
                'recipient_address': '0xBridge',
                'amount': 40,
                'timestamp': '2024-01-01T00:00:00Z',
                'metadata': 'tx_group_1',
            },
            {
                'sender_address': '0xAlpha2',
                'recipient_address': '0xBridge',
                'amount': 60,
                'timestamp': '2024-01-01T00:00:00Z',
                'metadata': 'tx_group_1',
            },
            {
                'sender_address': '0xBridge',
                'recipient_address': '0xbad0000000000000000000000000000000000001',
                'amount': 100,
                'timestamp': '2024-01-01T00:02:00Z',
                'metadata': 'tx_transfer_1',
            },
            {
                'sender_address': '0xbad0000000000000000000000000000000000001',
                'recipient_address': '0xCashout',
                'amount': 100,
                'timestamp': '2024-01-01T00:03:00Z',
                'metadata': 'tx_transfer_2',
            },
            {
                'sender_address': '0xAlpha1',
                'recipient_address': '0xBridge',
                'amount': 15,
                'timestamp': '2024-01-01T00:04:00Z',
                'metadata': 'tx_group_2',
            },
        ]
    )
    dataframe['timestamp'] = pd.to_datetime(dataframe['timestamp'], utc=True)

    graph = build_transaction_graph(dataframe)
    blacklist_result = run_blacklist_check(dataframe=dataframe, graph=graph)
    clustering_result = run_wallet_clustering(dataframe=dataframe, graph=graph)
    risk_result = run_risk_scoring(dataframe=dataframe, graph=graph)

    assert blacklist_result['matched_count'] == 1, 'Expected one blacklisted address in the sample.'
    assert clustering_result['cluster_count'] >= 1, 'Expected at least one logical wallet cluster.'
    assert any(item['risk_score'] >= 70 for item in risk_result['results']), 'Expected at least one high risk address.'

    bridge_node = graph.nodes['0xBridge']
    blacklisted_node = graph.nodes['0xbad0000000000000000000000000000000000001']

    print('blacklist_matches=', blacklist_result['matched_count'])
    print('clusters=', clustering_result['cluster_count'])
    print('bridge_risk=', bridge_node.get('risk_score'))
    print('blacklisted_score=', blacklisted_node.get('risk_score'))
    print('top_risk=', risk_result['results'][0]['address'], risk_result['results'][0]['risk_score'])


if __name__ == '__main__':
    main()