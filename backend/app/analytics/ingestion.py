from __future__ import annotations

from pathlib import Path

import pandas as pd


COLUMN_ALIASES = {
    'from': 'sender_address',
    'sender': 'sender_address',
    'from_address': 'sender_address',
    'source': 'sender_address',
    'to': 'recipient_address',
    'recipient': 'recipient_address',
    'to_address': 'recipient_address',
    'destination': 'recipient_address',
    'value': 'amount',
    'amount': 'amount',
    'value_eth': 'amount',
    'timestamp': 'timestamp',
    'block_timestamp': 'timestamp',
    'time': 'timestamp',
    'metadata': 'metadata',
    'tx_hash': 'metadata',
    'hash': 'metadata',
}

REQUIRED_COLUMNS = ['sender_address', 'recipient_address', 'amount', 'timestamp']


def _normalize_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized = dataframe.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    normalized = normalized.rename(columns={column: COLUMN_ALIASES.get(column, column) for column in normalized.columns})

    if 'metadata' not in normalized.columns:
        normalized['metadata'] = None

    for column in REQUIRED_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    return normalized


def clean_transaction_csv(file_path: str | Path) -> pd.DataFrame:
    dataframe = pd.read_csv(file_path)
    dataframe = _normalize_columns(dataframe)
    dataframe = dataframe.dropna(how='all')

    dataframe['sender_address'] = dataframe['sender_address'].astype('string').str.strip()
    dataframe['recipient_address'] = dataframe['recipient_address'].astype('string').str.strip()
    dataframe['metadata'] = dataframe['metadata'].astype('string').str.strip()

    dataframe['amount'] = pd.to_numeric(dataframe['amount'], errors='coerce')
    dataframe['timestamp'] = pd.to_datetime(dataframe['timestamp'], errors='coerce', utc=True)

    dataframe = dataframe.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    dataframe = dataframe[dataframe['sender_address'] != '']
    dataframe = dataframe[dataframe['recipient_address'] != '']
    dataframe = dataframe[dataframe['amount'] >= 0]
    dataframe = dataframe[dataframe['timestamp'].notna()]

    dataframe = dataframe.reset_index(drop=True)
    return dataframe
