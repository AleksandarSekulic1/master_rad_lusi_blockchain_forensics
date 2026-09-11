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
    # Optional. The taint model adds and divides `amount` values, which only means
    # anything if they are all the same unit - mixing ETH and USDT rows in one file would
    # produce percentages that look precise and are arithmetically meaningless.
    'currency': 'currency',
    'valuta': 'currency',
    'token': 'currency',
    'symbol': 'currency',
    'asset': 'currency',
}

REQUIRED_COLUMNS = ['sender_address', 'recipient_address', 'amount', 'timestamp']


def _normalize_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized = dataframe.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    normalized = normalized.rename(columns={column: COLUMN_ALIASES.get(column, column) for column in normalized.columns})

    if 'metadata' not in normalized.columns:
        normalized['metadata'] = None

    if 'currency' not in normalized.columns:
        normalized['currency'] = None

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


def detect_currencies(file_path: str | Path) -> list[str]:
    """Distinct currency labels declared in the file, uppercased.

    Empty when the file has no currency column at all - which is not the same as "one
    currency": it means the file never said, and the caller has to report that honestly
    rather than assume ETH.
    """
    try:
        dataframe = _normalize_columns(pd.read_csv(file_path))
    except Exception:  # noqa: BLE001 - upload validation must not fail on a broken file
        return []

    if 'currency' not in dataframe.columns:
        return []

    values = (
        dataframe['currency']
        .dropna()
        .astype('string')
        .str.strip()
        .str.upper()
    )
    return sorted({value for value in values if value})


def split_by_currency(file_path: str | Path) -> dict[str | None, pd.DataFrame]:
    """Splits a raw (not yet cleaned) evidence CSV into one DataFrame per declared
    currency label, preserving the file's own original columns/headers exactly as
    uploaded - used when an upload mixes currencies (see app.api.routes.upload) so each
    resulting evidence file is internally consistent for the taint model, instead of
    asking the analyst to split it by hand outside the app.

    Rows with no declared currency at all go under the `None` key: they cannot be safely
    assigned to any one of the declared currencies, so they are kept apart rather than
    guessed into one of the groups.
    """
    raw = pd.read_csv(file_path)
    normalized = _normalize_columns(raw)
    if 'currency' not in normalized.columns:
        return {None: raw}

    labels = normalized['currency'].astype('string').str.strip().str.upper()
    labels = labels.mask(labels == '')

    groups: dict[str | None, pd.DataFrame] = {}
    null_mask = labels.isna()
    if null_mask.any():
        groups[None] = raw[null_mask]
    for label in sorted(labels.dropna().unique()):
        subset = raw[labels == label]
        if not subset.empty:
            groups[str(label)] = subset
    return groups
