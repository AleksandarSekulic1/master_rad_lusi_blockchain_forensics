from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

BLOCKSTREAM_BASE_URL = 'https://blockstream.info/api'
SATOSHIS_PER_BTC = 100_000_000

# Esplora returns pages of at most 25 confirmed transactions - a page shorter than that
# means there is nothing left to page through.
_PAGE_SIZE = 25


def _sats_to_btc(value: object) -> float:
    try:
        return int(value) / SATOSHIS_PER_BTC
    except (TypeError, ValueError):
        return 0.0


def _to_iso_timestamp(unix_seconds: object) -> str:
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).isoformat()


def _blockstream_get(path: str) -> Any:
    try:
        response = requests.get(f'{BLOCKSTREAM_BASE_URL}{path}', timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f'Blockstream API nije dostupan: {exc}') from exc
    return response.json()


def _resolve_sender(transaction: dict[str, Any]) -> str | None:
    """Common input ownership heuristic (Meiklejohn et al., 2013): the first input's spent
    address stands in as the transaction's sender, since every input of a Bitcoin
    transaction must be signed by the same wallet. Returns None for a coinbase transaction,
    whose single input has no `prevout` (it mints new coins, it does not spend a UTXO)."""
    for vin in transaction.get('vin', []):
        prevout = vin.get('prevout') or {}
        address = prevout.get('scriptpubkey_address')
        if address:
            return str(address)
    return None


def transaction_to_rows(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalizes one Esplora transaction into the pipeline's standard row shape.

    Unconfirmed (mempool) transactions are skipped - a forensic tool should work over
    settled facts, not transactions that can still be dropped/replaced. A coinbase
    transaction (no resolvable sender) and OP_RETURN outputs (no recipient address) are
    skipped for the same reason: there is no wallet-to-wallet edge to record. Change
    outputs are NOT filtered - they are real UTXOs back to the sender and stay in the
    graph as ordinary edges.
    """
    status = transaction.get('status') or {}
    if not status.get('confirmed'):
        return []

    sender = _resolve_sender(transaction)
    if sender is None:
        return []

    timestamp = _to_iso_timestamp(status.get('block_time', 0))
    txid = transaction.get('txid')

    rows: list[dict[str, Any]] = []
    for vout in transaction.get('vout', []):
        recipient = vout.get('scriptpubkey_address')
        if not recipient:
            continue
        rows.append(
            {
                'sender_address': sender,
                'recipient_address': str(recipient),
                'amount': _sats_to_btc(vout.get('value', 0)),
                'timestamp': timestamp,
                'metadata': txid,
            }
        )
    return rows


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata'])


def fetch_address_transactions(address: str) -> pd.DataFrame:
    """Fetches an address' confirmed transaction history from the Blockstream Esplora API
    and normalizes it into the same (sender, recipient, amount, timestamp, metadata) shape
    the rest of the pipeline - built for Ethereum - already expects (see
    BITCOIN-UTXO-PLAN.md). Every output becomes its own recipient row.

    Paginates via `/txs/chain/{last_seen_txid}` using the last CONFIRMED txid seen so far -
    the first page mixes in up to 50 mempool transactions ahead of the confirmed ones, so
    the very last row of that page is not necessarily a safe pagination cursor.
    """
    rows: list[dict[str, Any]] = []
    last_confirmed_txid: str | None = None

    while True:
        path = f'/address/{address}/txs' if last_confirmed_txid is None else f'/address/{address}/txs/chain/{last_confirmed_txid}'
        page = _blockstream_get(path)
        if not isinstance(page, list) or not page:
            break

        confirmed_in_page = [tx for tx in page if (tx.get('status') or {}).get('confirmed')]
        for transaction in confirmed_in_page:
            rows.extend(transaction_to_rows(transaction))

        if len(confirmed_in_page) < _PAGE_SIZE:
            break

        last_confirmed_txid = confirmed_in_page[-1].get('txid')

    if not rows:
        return _empty_frame()

    return pd.DataFrame(rows, columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata'])
