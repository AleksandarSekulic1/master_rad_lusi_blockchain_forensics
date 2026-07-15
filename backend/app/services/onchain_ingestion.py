from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
ETHERSCAN_BASE_URL = 'https://api.etherscan.io/v2/api'

NETWORK_CHAIN_IDS: dict[str, int] = {
    'mainnet': 1,
    'sepolia': 11155111,
}


def _wei_to_eth(value: object) -> float:
    try:
        return int(value) / 1_000_000_000_000_000_000
    except (TypeError, ValueError):
        return 0.0


def _to_iso_timestamp(unix_seconds: object) -> str:
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).isoformat()


def fetch_address_transactions(address: str, network: str = 'mainnet') -> pd.DataFrame:
    """Fetches an address' confirmed transaction history from Etherscan (V2 API).

    Only reads publicly available on-chain history - no transaction is ever sent,
    so this never costs gas regardless of network.
    """
    chain_id = NETWORK_CHAIN_IDS.get(network)
    if chain_id is None:
        raise ValueError(f'Nepoznata mreža: {network}')

    if not ETHERSCAN_API_KEY:
        raise RuntimeError('ETHERSCAN_API_KEY nije podešen na serveru. Pogledaj BLOCKCHAIN-UVOZ.md.')

    params = {
        'chainid': chain_id,
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'asc',
        'apikey': ETHERSCAN_API_KEY,
    }

    try:
        response = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f'Etherscan API nije dostupan: {exc}') from exc

    payload: dict[str, Any] = response.json()
    result = payload.get('result')

    if payload.get('status') != '1':
        message = str(payload.get('message', 'Nepoznata greška'))
        if 'No transactions found' in message:
            return _empty_frame()
        detail = result if isinstance(result, str) else message
        raise RuntimeError(f'Etherscan greška: {detail}')

    if not isinstance(result, list):
        return _empty_frame()

    rows = []
    for tx in result:
        if str(tx.get('isError', '0')) == '1':
            continue  # preskoči neuspele transakcije (revert)

        rows.append(
            {
                'sender_address': tx.get('from'),
                'recipient_address': tx.get('to') or tx.get('contractAddress'),
                'amount': _wei_to_eth(tx.get('value', 0)),
                'timestamp': _to_iso_timestamp(tx.get('timeStamp', 0)),
                'metadata': tx.get('hash'),
            }
        )

    if not rows:
        return _empty_frame()

    return pd.DataFrame(rows, columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata'])


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata'])
