from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.plugins.blacklist_check import run_blacklist_check
from app.analytics.plugins.anomaly_detection import run_anomaly_detection
from app.analytics.plugins.chain_hopping import run_chain_hopping
from app.analytics.plugins.wallet_clustering import run_wallet_clustering
from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.peel_chains import run_peel_chains
from app.analytics.plugins.manager import run_plugin_pipeline
from app.analytics.plugins.risk_scoring import run_risk_scoring
from app.analytics.plugins.taint_analysis import run_taint_analysis


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
    # Must run after blacklist_check - it auto-seeds from blacklist_flag by default.
    taint_result = run_taint_analysis(dataframe=dataframe, graph=graph)
    clustering_result = run_wallet_clustering(dataframe=dataframe, graph=graph)
    risk_result = run_risk_scoring(dataframe=dataframe, graph=graph)

    assert blacklist_result['matched_count'] == 1, 'Expected one blacklisted address in the sample.'
    assert clustering_result['cluster_count'] >= 1, 'Expected at least one logical wallet cluster.'
    assert any(item['risk_score'] >= 70 for item in risk_result['results']), 'Expected at least one high risk address.'

    bridge_node = graph.nodes['0xBridge']
    blacklisted_node = graph.nodes['0xbad0000000000000000000000000000000000001']
    cashout_node = graph.nodes['0xCashout']

    assert blacklisted_node.get('taint_percentage') == 100.0, 'Blacklist-seeded address should show as 100% tainted.'
    assert cashout_node.get('taint_percentage') == 100.0, 'Funds received straight from the tainted seed should be fully tainted.'
    assert bridge_node.get('taint_percentage', 0.0) == 0.0, (
        'Bridge only ever SENT to the blacklisted address, never received from it - taint must not flow backwards.'
    )

    print('blacklist_matches=', blacklist_result['matched_count'])
    print('taint_seeds=', taint_result['seed_addresses'])
    print('taint_seed_pct=', blacklisted_node.get('taint_percentage'))
    print('taint_cashout_pct=', cashout_node.get('taint_percentage'))
    print('taint_bridge_pct=', bridge_node.get('taint_percentage'))
    print('clusters=', clustering_result['cluster_count'])
    print('bridge_risk=', bridge_node.get('risk_score'))
    print('blacklisted_score=', blacklisted_node.get('risk_score'))
    print('top_risk=', risk_result['results'][0]['address'], risk_result['results'][0]['risk_score'])

    advanced_frame = pd.DataFrame(
        [
            {'sender_address': '0xFunding', 'recipient_address': '0xPeelSeed', 'amount': 1000, 'timestamp': '2024-02-01T00:00:00Z', 'metadata': 'funding'},
            {'sender_address': '0xPeelSeed', 'recipient_address': '0xRelayA', 'amount': 920, 'timestamp': '2024-02-01T00:04:00Z', 'metadata': 'peel_1'},
            {'sender_address': '0xPeelSeed', 'recipient_address': '0xPeelTip1', 'amount': 80, 'timestamp': '2024-02-01T00:05:00Z', 'metadata': 'peel_1'},
            {'sender_address': '0xRelayA', 'recipient_address': '0xBridgeHub', 'amount': 850, 'timestamp': '2024-02-01T00:08:00Z', 'metadata': 'peel_2'},
            {'sender_address': '0xRelayA', 'recipient_address': '0xPeelTip2', 'amount': 70, 'timestamp': '2024-02-01T00:09:00Z', 'metadata': 'peel_2'},
            {'sender_address': '0xBridgeHub', 'recipient_address': '0xSwapRouter', 'amount': 810, 'timestamp': '2024-02-01T00:12:00Z', 'metadata': 'bridge hop'},
            {'sender_address': '0xBridgeHub', 'recipient_address': '0xBridgeTip', 'amount': 40, 'timestamp': '2024-02-01T00:13:00Z', 'metadata': 'bridge hop'},
            {'sender_address': '0xSwapRouter', 'recipient_address': '0xExitWallet', 'amount': 780, 'timestamp': '2024-02-01T00:16:00Z', 'metadata': 'swap hop'},
            {'sender_address': '0xSwapRouter', 'recipient_address': '0xSwapTip', 'amount': 30, 'timestamp': '2024-02-01T00:17:00Z', 'metadata': 'swap hop'},
            {'sender_address': '0xNormal1', 'recipient_address': '0xNormal2', 'amount': 3, 'timestamp': '2024-02-01T12:00:00Z', 'metadata': 'normal'},
            {'sender_address': '0xNormal2', 'recipient_address': '0xNormal3', 'amount': 4, 'timestamp': '2024-02-01T12:20:00Z', 'metadata': 'normal'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeA', 'amount': 1, 'timestamp': '2024-02-01T13:00:00Z', 'metadata': 'burst'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeB', 'amount': 2, 'timestamp': '2024-02-01T13:01:00Z', 'metadata': 'burst'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeC', 'amount': 3, 'timestamp': '2024-02-01T13:02:00Z', 'metadata': 'burst'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeD', 'amount': 2500, 'timestamp': '2024-02-01T13:03:00Z', 'metadata': 'burst'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeE', 'amount': 4, 'timestamp': '2024-02-01T13:04:00Z', 'metadata': 'burst'},
            {'sender_address': '0xWhale', 'recipient_address': '0xNodeF', 'amount': 5, 'timestamp': '2024-02-01T13:05:00Z', 'metadata': 'burst'},
        ]
    )
    advanced_frame['timestamp'] = pd.to_datetime(advanced_frame['timestamp'], utc=True)

    advanced_graph = build_transaction_graph(advanced_frame)
    advanced_results = run_plugin_pipeline(
        dataframe=advanced_frame,
        graph=advanced_graph,
        plugin_names=['peel_chains', 'chain_hopping', 'anomaly_detection'],
    )

    assert advanced_results['peel_chains']['chain_count'] >= 1, 'Expected a peel-chain detection on the synthetic sample.'
    assert advanced_results['chain_hopping']['hop_count'] >= 1, 'Expected at least one chain-hopping node on the synthetic sample.'
    assert any(item['anomaly_flag'] for item in advanced_results['anomaly_detection']['results']), 'Expected at least one anomaly flag on the synthetic sample.'

    peel_result = run_peel_chains(dataframe=advanced_frame, graph=advanced_graph)
    chain_hop_result = run_chain_hopping(dataframe=advanced_frame, graph=advanced_graph)
    anomaly_result = run_anomaly_detection(dataframe=advanced_frame, graph=advanced_graph)

    print('peel_chains=', peel_result['chain_count'])
    print('chain_hops=', chain_hop_result['hop_count'])
    print('anomalies=', anomaly_result['anomaly_count'])
    print('pipeline_plugins=', ','.join(advanced_results.keys()))

    # Proportional-dilution check: 0xThief seeds 1000 (explicit seed, not blacklisted),
    # 0xMixer also receives 500 clean funds, then forwards 750 onward. Expected ratio at
    # the mixer (and carried forward to whoever it pays) is 1000 / (1000 + 500) = 66.67%.
    taint_frame = pd.DataFrame(
        [
            {'sender_address': '0xThief', 'recipient_address': '0xMixer', 'amount': 1000, 'timestamp': '2024-03-01T00:00:00Z', 'metadata': 'stolen_funds'},
            {'sender_address': '0xCleanUser', 'recipient_address': '0xMixer', 'amount': 500, 'timestamp': '2024-03-01T00:05:00Z', 'metadata': 'unrelated_deposit'},
            {'sender_address': '0xMixer', 'recipient_address': '0xExitWallet', 'amount': 750, 'timestamp': '2024-03-01T00:10:00Z', 'metadata': 'mixer_payout'},
        ]
    )
    taint_frame['timestamp'] = pd.to_datetime(taint_frame['timestamp'], utc=True)

    taint_graph = build_transaction_graph(taint_frame)
    dilution_result = run_taint_analysis(dataframe=taint_frame, graph=taint_graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

    thief_pct = taint_graph.nodes['0xThief']['taint_percentage']
    mixer_pct = taint_graph.nodes['0xMixer']['taint_percentage']
    exit_pct = taint_graph.nodes['0xExitWallet']['taint_percentage']
    clean_user_pct = taint_graph.nodes['0xCleanUser']['taint_percentage']

    assert thief_pct == 100.0, 'Explicit seed address should show as 100% tainted.'
    assert mixer_pct == 66.67, f'Expected mixer to dilute to 1000/1500=66.67%, got {mixer_pct}.'
    assert exit_pct == 66.67, f'Exit wallet should inherit the mixer\'s exact ratio at time of payout, got {exit_pct}.'
    assert clean_user_pct == 0.0, 'A pure funding source unrelated to the seed should never show any taint.'

    print('dilution_seeds=', dilution_result['seed_addresses'])
    print('dilution_thief_pct=', thief_pct)
    print('dilution_mixer_pct=', mixer_pct)
    print('dilution_exit_pct=', exit_pct)
    print('dilution_clean_user_pct=', clean_user_pct)


if __name__ == '__main__':
    main()