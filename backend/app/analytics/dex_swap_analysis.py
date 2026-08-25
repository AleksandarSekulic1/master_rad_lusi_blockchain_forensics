from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.analytics.plugins.blacklist_check import _normalize_address

# DEX Swap Analysis - a new, standalone module (see DEX-SWAP-ANALIZA.md). Detects the
# pattern "wallet sends a token to a known/likely DEX contract, then receives a
# different token back from that same contract shortly after" from a case's already
# CLEANED evidence (app.analytics.case_graph.combine_frames), NOT from the shared
# transaction graph - build_transaction_graph (used by Graph/Taint/Pathfinding/
# Behavioral) intentionally does not carry a per-transaction `currency` field, so it
# cannot distinguish "10 ETH out" from "25,000 USDC in". Reading the cleaned DataFrame
# directly is the only way to see per-row currency without touching that shared code
# path - see DEX-SWAP-ANALIZA.md #2 for the full reasoning.
#
# This is a HEURISTIC, not a proof. Every event carries an explicit confidence level
# (Detected vs Potential) and every response carries a disclaimer - see build_event()
# and detect_dex_swaps() below.

KNOWN_DEX_CONTRACTS_PATH = Path(__file__).parent / 'known_dex_contracts.json'

REQUIRED_COLUMNS = ('sender_address', 'recipient_address', 'amount', 'timestamp')

DEFAULT_MAX_GAP_SECONDS = 300  # 5 minutes - "veoma kratak vremenski period" po zahtevu
MIN_MAX_GAP_SECONDS = 10
MAX_MAX_GAP_SECONDS = 3600

# Two tiers, deliberately NOT reusing chain_hopping.DEFAULT_SWAP_KEYWORDS as-is: that
# list includes 'exchange', which would misclassify '0xExchangeCounterparty' (an address
# already present in this project's own Behavioral demo evidence, same case) as a DEX.
# Brand names are a strong, low-ambiguity signal; the generic tier is intentionally
# weaker and labelled as such in the response (dex_match_basis), since a word like
# "router" alone can belong to something that is not a DEX at all (e.g. a bridge relay -
# see DEX-SWAP-ANALIZA.md's worked false-positive example). Bare "swap" was deliberately
# left OUT of the generic tier too: an ordinary user/wallet label containing that word
# (e.g. "0xSwapTraderWallet" - a trader's own wallet, not a DEX) would otherwise
# self-classify as a DEX node; every actual swap-branded protocol worth naming here is
# already covered by the brand tier above.
DEX_BRAND_KEYWORDS: tuple[str, ...] = (
    'uniswap',
    'sushiswap',
    'sushi',
    'pancakeswap',
    'pancake',
    'curve',
    'balancer',
    '1inch',
    'paraswap',
    'kyberswap',
    'kyber',
    'quickswap',
    'dodoex',
)
DEX_GENERIC_KEYWORDS: tuple[str, ...] = ('dex', 'router', 'aggregator')


def _load_known_dex_contracts() -> dict[str, dict[str, str]]:
    with KNOWN_DEX_CONTRACTS_PATH.open(encoding='utf-8') as handle:
        return json.load(handle)


def _clean_text(value: object) -> str | None:
    """None/NaN/blank/the literal string 'nan' all collapse to None; everything else is
    stripped. Same 'nan' guard as tx_identity.transaction_id, for the same reason:
    pandas turns a missing string cell into the text 'nan' more often than a real None,
    and treating that as a real hash/currency value would be wrong."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() == 'nan':
        return None
    return text


def _currency_of(value: object) -> str | None:
    text = _clean_text(value)
    return text.upper() if text else None


def classify_dex_node(address: str, known_contracts: dict[str, dict[str, str]]) -> tuple[str | None, str | None]:
    """Whether `address` looks like a DEX contract, and on what basis.

    Two independent signals, same dual strategy as
    app.analytics.plugins.chain_hopping._classify_node:
      1. Exact match against a curated list of real, well-known DEX router/pool
         addresses (known_dex_contracts.json) - the reliable path for real on-chain data.
      2. A DEX-ish keyword in the address text itself - the only signal available for
         demo/manual CSVs that use human-readable pseudo-addresses (e.g.
         "0xUniswapRouter") instead of real 0x hex addresses.

    Returns (display_name, match_basis), or (None, None) if neither signal fires - the
    address is then not considered a DEX node at all.
    """
    normalized = _normalize_address(address)
    if not normalized:
        return None, None

    entry = known_contracts.get(normalized)
    if entry:
        return str(entry.get('name') or address), 'known_address'

    text = str(address).lower()

    brand_hits = sorted({keyword for keyword in DEX_BRAND_KEYWORDS if keyword in text})
    if brand_hits:
        return address, f'keyword_match_brand: {", ".join(brand_hits)}'

    generic_hits = sorted({keyword for keyword in DEX_GENERIC_KEYWORDS if keyword in text})
    if generic_hits:
        return address, f'keyword_match_generic: {", ".join(generic_hits)}'

    return None, None


def _prepare_frame(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True, errors='coerce')
    frame = frame.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    return frame.sort_values('timestamp', kind='stable').reset_index(drop=True)


def _same_declared_currency(leg_in: dict[str, Any], leg_out: dict[str, Any]) -> bool:
    return leg_in['_currency'] is not None and leg_in['_currency'] == leg_out['_currency']


def _build_event(
    wallet: str,
    dex_address: str,
    dex_name: str,
    dex_match_basis: str,
    leg_in: dict[str, Any],
    leg_out: dict[str, Any],
    match_basis: str,
    confidence: str,
) -> dict[str, Any]:
    gap_seconds = (leg_out['timestamp'] - leg_in['timestamp']).total_seconds()

    reasons = [
        f'{wallet} -> {dex_address} pa {dex_address} -> {wallet}, {int(gap_seconds)}s kasnije.',
        f'DEX čvor prepoznat preko: {dex_match_basis}.',
    ]
    if match_basis == 'shared_transaction_hash':
        reasons.append('Oba kraka dele isti transaction hash - najjači raspoloživ signal u ovom skupu podataka.')
    else:
        reasons.append('Uparivanje isključivo po adresi i vremenskoj bliskosti (unutar zadatog prozora).')
    if leg_in['_currency'] is None or leg_out['_currency'] is None:
        reasons.append('Valuta/token nije deklarisana u evidenciji za bar jedan krak - par NIJE potvrđen kao dva različita tokena.')

    return {
        'type': 'SWAP',
        'confidence': confidence,
        'label': f'{confidence} Swap',
        'user_address': wallet,
        'dex_address': dex_address,
        'dex_name': dex_name,
        'dex_match_basis': dex_match_basis,
        'input_token': leg_in['_currency'],
        'input_amount': float(leg_in['amount']),
        'input_transaction_hash': leg_in['_hash'],
        'input_timestamp': leg_in['timestamp'].isoformat(),
        'output_token': leg_out['_currency'],
        'output_amount': float(leg_out['amount']),
        'output_transaction_hash': leg_out['_hash'],
        'output_timestamp': leg_out['timestamp'].isoformat(),
        'time_gap_seconds': int(gap_seconds),
        'match_basis': match_basis,
        'reasons': reasons,
    }


def detect_dex_swaps(
    transactions: pd.DataFrame,
    target_address: str | None = None,
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
    known_contracts: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """DEX Swap Analysis: best-effort detection of "wallet -> DEX -> same wallet, two
    different tokens, close in time" patterns in `transactions` (the case's cleaned,
    combined evidence - same shape as app.analytics.ingestion.clean_transaction_csv's
    output: sender_address, recipient_address, amount, timestamp, metadata, optionally
    currency).

    Requires an EXACT address match against the evidence's own sender/recipient values
    when `target_address` is given (same case-sensitive convention as
    path_finding.find_transaction_paths and behavioral_analysis.analyze_time_of_day) -
    raises ValueError (the route layer turns this into a 404) rather than silently
    returning an empty result for a typo'd address.

    See DEX-SWAP-ANALIZA.md for the full heuristic, its false-positive guards, and what
    it deliberately does NOT claim.
    """
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in transactions.columns]
    if missing_columns:
        raise ValueError(f'Missing required columns: {", ".join(missing_columns)}')

    max_gap_seconds = max(MIN_MAX_GAP_SECONDS, min(MAX_MAX_GAP_SECONDS, int(max_gap_seconds)))
    frame = _prepare_frame(transactions)

    if target_address is not None:
        present = bool(((frame['sender_address'] == target_address) | (frame['recipient_address'] == target_address)).any())
        if not present:
            raise ValueError(f'Adresa nije pronađena u evidenciji: {target_address}')

    contracts = known_contracts if known_contracts is not None else _load_known_dex_contracts()

    has_currency_column = 'currency' in frame.columns
    currency_declared = bool(frame['currency'].map(_currency_of).notna().any()) if has_currency_column else False

    all_addresses = pd.unique(pd.concat([frame['sender_address'], frame['recipient_address']]))
    dex_nodes: dict[str, tuple[str, str]] = {}
    for address in all_addresses:
        name, basis = classify_dex_node(str(address), contracts)
        if name is not None and basis is not None:
            dex_nodes[str(address)] = (name, basis)

    rows: list[dict[str, Any]] = []
    for row in frame.to_dict('records'):
        row['_used'] = False
        row['_currency'] = _currency_of(row.get('currency'))
        row['_hash'] = _clean_text(row.get('metadata'))
        rows.append(row)

    events: list[dict[str, Any]] = []

    # Pass 1: both legs share the SAME real transaction hash (not a synthetic fallback
    # id - only a genuinely declared metadata/hash value counts). This is the strongest
    # signal available in this data model, since it means the evidence explicitly
    # recorded both transfers as part of one on-chain transaction.
    hash_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row['_hash']:
            hash_groups.setdefault(row['_hash'], []).append(row)

    for group in hash_groups.values():
        if len(group) < 2:
            continue
        for leg_in in group:
            if leg_in['_used']:
                continue
            dex_address = leg_in['recipient_address']
            if dex_address not in dex_nodes:
                continue
            wallet = leg_in['sender_address']
            if wallet == dex_address:
                continue
            leg_out = next(
                (
                    candidate
                    for candidate in group
                    if not candidate['_used']
                    and candidate is not leg_in
                    and candidate['sender_address'] == dex_address
                    and candidate['recipient_address'] == wallet
                    and candidate['timestamp'] >= leg_in['timestamp']
                ),
                None,
            )
            if leg_out is None or _same_declared_currency(leg_in, leg_out):
                continue
            leg_in['_used'] = leg_out['_used'] = True
            dex_name, dex_basis = dex_nodes[dex_address]
            events.append(_build_event(wallet, dex_address, dex_name, dex_basis, leg_in, leg_out, 'shared_transaction_hash', 'Detected'))

    # Pass 2: whatever Pass 1 didn't resolve, matched purely by address + a short time
    # window. Greedy, earliest-available pairing (same discipline as
    # plugins.peel_chains._follow_chain) - not globally optimal, but deterministic.
    for dex_address, (dex_name, dex_basis) in dex_nodes.items():
        into_dex = sorted(
            (row for row in rows if not row['_used'] and row['recipient_address'] == dex_address and row['sender_address'] != dex_address),
            key=lambda row: row['timestamp'],
        )
        out_of_dex = sorted(
            (row for row in rows if not row['_used'] and row['sender_address'] == dex_address and row['recipient_address'] != dex_address),
            key=lambda row: row['timestamp'],
        )
        for leg_in in into_dex:
            if leg_in['_used']:
                continue
            wallet = leg_in['sender_address']
            window_end = leg_in['timestamp'] + pd.Timedelta(seconds=max_gap_seconds)
            leg_out = next(
                (
                    candidate
                    for candidate in out_of_dex
                    if not candidate['_used']
                    and candidate['recipient_address'] == wallet
                    and leg_in['timestamp'] <= candidate['timestamp'] <= window_end
                ),
                None,
            )
            if leg_out is None or _same_declared_currency(leg_in, leg_out):
                continue
            leg_in['_used'] = leg_out['_used'] = True
            events.append(_build_event(wallet, dex_address, dex_name, dex_basis, leg_in, leg_out, 'time_window', 'Potential'))

    events.sort(key=lambda event: event['input_timestamp'])

    if target_address is not None:
        events = [event for event in events if event['user_address'] == target_address]

    detected_count = sum(1 for event in events if event['confidence'] == 'Detected')
    potential_count = len(events) - detected_count

    return {
        'address': target_address,
        'total_events': len(events),
        'detected_count': detected_count,
        'potential_count': potential_count,
        'events': events,
        'dex_nodes_considered': [
            {'address': address, 'name': name, 'match_basis': basis}
            for address, (name, basis) in sorted(dex_nodes.items())
        ],
        'data_completeness': {
            'currency_declared': currency_declared,
            'note': (
                'Evidencija deklariše valutu/token za bar jednu transakciju - input/output token su čitani direktno iz nje.'
                if currency_declared
                else (
                    'Nijedna transakcija u ovoj evidenciji ne deklariše valutu/token (nema "currency" kolone, ili je '
                    'prazna). Uparivanje se oslanja isključivo na adresu DEX čvora i vremensku bliskost - bez potvrde '
                    'da se zaista radi o dva različita tokena. input_token/output_token su null.'
                )
            ),
        },
        'max_gap_seconds': max_gap_seconds,
        'disclaimer': (
            'DEX swap detekcija je heuristika zasnovana na adresnom obrascu, vremenskoj bliskosti i (kad postoji) '
            'deklarisanoj valuti - ne predstavlja kriptografski dokaz da se radi o swap transakciji.'
        ),
    }
