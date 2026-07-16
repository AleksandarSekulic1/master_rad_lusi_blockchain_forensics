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


def _hex_wei_to_eth(value: object) -> float:
    try:
        return int(str(value), 16) / 1_000_000_000_000_000_000
    except (TypeError, ValueError):
        return 0.0


def _to_iso_timestamp(unix_seconds: object) -> str:
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).isoformat()


def _hex_to_iso_timestamp(hex_seconds: object) -> str:
    return _to_iso_timestamp(int(str(hex_seconds), 16))


def _require_chain_id(network: str) -> int:
    chain_id = NETWORK_CHAIN_IDS.get(network)
    if chain_id is None:
        raise ValueError(f'Nepoznata mreža: {network}')
    return chain_id


def _require_api_key() -> str:
    if not ETHERSCAN_API_KEY:
        raise RuntimeError('ETHERSCAN_API_KEY nije podešen na serveru. Pogledaj BLOCKCHAIN-UVOZ.md.')
    return ETHERSCAN_API_KEY


def _etherscan_get(params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f'Etherscan API nije dostupan: {exc}') from exc
    return response.json()


def fetch_address_transactions(address: str, network: str = 'mainnet') -> pd.DataFrame:
    """Fetches an address' confirmed transaction history from Etherscan (V2 API).

    Only reads publicly available on-chain history - no transaction is ever sent,
    so this never costs gas regardless of network.
    """
    chain_id = _require_chain_id(network)
    api_key = _require_api_key()

    params = {
        'chainid': chain_id,
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'asc',
        'apikey': api_key,
    }

    payload = _etherscan_get(params)
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


def fetch_transaction_by_hash(tx_hash: str, network: str = 'mainnet') -> dict[str, Any]:
    """Fetches a single transaction's details by hash via Etherscan's JSON-RPC proxy.

    Etherscan's eth_getTransactionByHash response includes blockTimestamp directly,
    so a single call is enough (no separate eth_getBlockByNumber lookup needed).
    """
    chain_id = _require_chain_id(network)
    api_key = _require_api_key()

    params = {
        'chainid': chain_id,
        'module': 'proxy',
        'action': 'eth_getTransactionByHash',
        'txhash': tx_hash,
        'apikey': api_key,
    }

    payload = _etherscan_get(params)
    tx = payload.get('result')
    if not tx:
        raise RuntimeError('Transakcija sa datim hešom nije pronađena.')

    return {
        'sender_address': tx.get('from'),
        'recipient_address': tx.get('to'),
        'amount': _hex_wei_to_eth(tx.get('value', '0x0')),
        'timestamp': _hex_to_iso_timestamp(tx.get('blockTimestamp', '0x0')),
        'metadata': tx.get('hash'),
    }


def fetch_single_transaction_frame(tx_hash: str, network: str = 'mainnet') -> pd.DataFrame:
    """Wraps a single transaction (by hash) in a one-row DataFrame matching the CSV schema."""
    row = fetch_transaction_by_hash(tx_hash, network)
    return pd.DataFrame([row], columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata'])


def fetch_expanded_sender_history(tx_hash: str, network: str = 'mainnet') -> tuple[pd.DataFrame, str]:
    """Resolves a transaction hash to its sender, then fetches that sender's full history.

    A single isolated transaction has little forensic value on its own; expanding to the
    sender's full activity is usually what an investigator actually wants.
    """
    seed = fetch_transaction_by_hash(tx_hash, network)
    sender = str(seed.get('sender_address') or '')
    if not sender:
        raise RuntimeError('Transakcija nema validnu adresu pošiljaoca.')

    dataframe = fetch_address_transactions(sender, network)
    return dataframe, sender
