from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from app.analytics.dex_swap_analysis import _load_known_dex_contracts, classify_dex_node
from app.evidence.tx_identity import transaction_id
from app.services.address_enrichment import get_known_entity

# Sybil & Bot Network Analysis - a new, standalone module (see SYBIL-ANALIZA.md), built the
# same way as DEX Swap / Token Approval Analysis: it reads a case's already CLEANED
# evidence (app.analytics.case_graph.combine_frames) directly, NOT the shared transaction
# graph - build_transaction_graph (used by Graph/Taint/Pathfinding/Behavioral) intentionally
# aggregates transactions per edge and drops per-transaction ordering/columns this module
# needs (an optional per-row function/method name, and every individual timestamp, not just
# a graph edge's first_seen/last_seen).
#
# THIS IS A HEURISTIC, NOT A PROOF OF COMMON OWNERSHIP. It flags "many different addresses
# interacted with the same target in a short, synchronized burst" - a pattern consistent
# with a Sybil attack, an airdrop-farming bot network, or a bot swarm, but ALSO consistent
# with an ordinary, legitimate rush (e.g. a popular public sale, a giveaway, a genuinely
# viral moment). Every response carries an explicit disclaimer - see build_result() - and
# no field in this module's output should ever be read as "these addresses belong to the
# same person/entity". See build_result()'s disclaimer text for the exact wording every
# caller (API route, PDF report, UI) must preserve.
#
# What "smart contract" means here: this project has no ABI decoding anywhere (no
# eth_getLogs, no 4byte/function-selector lookup - see token_approval_analysis.py's own
# module docstring for the same accounting), so a "smart contract call" is approximated as
# "a transaction whose recipient_address is the address in question" - indistinguishable,
# in this data model, from an ordinary transfer to a wallet. The optional `function_name`
# column (see FUNCTION_COLUMN_CANDIDATES) lets evidence that DOES declare a decoded method
# name (e.g. from a block explorer's "Method" column, exported by hand) refine clusters to
# "same contract AND same function", but its absence never blocks contract-level clustering.

REQUIRED_COLUMNS = ('sender_address', 'recipient_address', 'amount', 'timestamp')

# None of these are in app.analytics.ingestion.COLUMN_ALIASES, so a CSV column using one of
# these exact names survives untouched all the way to the combined_frame this module reads
# (same mechanism `currency`/`event_type` already rely on - see ingestion.py's
# _normalize_columns). The first one present in the evidence wins; absent entirely, function
# name is simply None for every row and clustering falls back to "same contract" alone.
FUNCTION_COLUMN_CANDIDATES = ('function_name', 'function', 'method', 'method_name', 'contract_function')

# Purely descriptive, never fetched by this project today (same accounting as
# token_approval_analysis.py's own BLOCK_NUMBER_COLUMN) - passed through when evidence
# happens to declare it, otherwise honestly None rather than fabricated.
BLOCK_NUMBER_COLUMN = 'block_number'

# Internal-only column (leading underscore - never a real evidence column a CSV would use):
# set by case_sybil_analysis.service.combine_frames_with_evidence_tag ONLY when a run is
# about to be written to the chain of custody (see SYBIL-ANALIZA.md #12) - absent for every
# other caller (the passive GET route, this module's own tests), in which case `tx_id` is
# simply None throughout. Same mechanism as token_approval_analysis.EVIDENCE_STORED_NAME_COLUMN.
EVIDENCE_STORED_NAME_COLUMN = '_evidence_stored_name'

DEFAULT_TIME_WINDOW_SECONDS = 300  # 5 minutes - "kratak vremenski period" po zahtevu, isti podrazumevani prozor kao DEX Swap Analysis
MIN_TIME_WINDOW_SECONDS = 10
MAX_TIME_WINDOW_SECONDS = 3600

DEFAULT_MIN_ADDRESSES = 3  # "više različitih adresa" - dve adrese nisu mreža, tri je najmanji smislen klaster
MIN_MIN_ADDRESSES = 2
MAX_MIN_ADDRESSES = 50


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _clean_text(value: object) -> str | None:
    """Same 'nan' guard as dex_swap_analysis._clean_text - pandas turns a missing string
    cell into the text 'nan' more often than a real None."""
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


def _find_function_column(frame: pd.DataFrame) -> str | None:
    return next((column for column in FUNCTION_COLUMN_CANDIDATES if column in frame.columns), None)


def _prepare_frame(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True, errors='coerce')
    frame = frame.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    return frame.sort_values('timestamp', kind='stable').reset_index(drop=True)


def _burst_segments(rows: list[dict[str, Any]], gap_seconds: int) -> list[list[dict[str, Any]]]:
    """Chains consecutive rows (already sorted by timestamp) into segments where every
    adjacent pair is at most `gap_seconds` apart - same greedy, chronological discipline as
    dex_swap_analysis.detect_dex_swaps' pass 2. A segment's total span can exceed
    `gap_seconds` when several short gaps chain together; that is reported honestly via
    each cluster's own window_duration_seconds rather than hidden behind the parameter."""
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if current and (row['timestamp'] - current[-1]['timestamp']).total_seconds() > gap_seconds:
            segments.append(current)
            current = []
        current.append(row)
    if current:
        segments.append(current)
    return segments


def _contract_label(address: str, known_contracts: dict[str, dict[str, str]]) -> tuple[str, str | None]:
    """Best-effort display name for a cluster's target address - a known real-world entity
    (exchange/mixer/sanctioned, from address_enrichment.get_known_entity) takes priority
    since it names an actual operator, then a known/likely DEX contract (reused from
    dex_swap_analysis - useful for this project's own demo CSVs, which use human-readable
    pseudo-addresses like "0xAirdropBotFactory" instead of real hex addresses), else just
    the raw address with no claimed identity."""
    entity = get_known_entity(address)
    if entity:
        return str(entity.get('name') or address), f'known_entity: {entity.get("category")}'

    dex_name, dex_basis = classify_dex_node(address, known_contracts)
    if dex_name is not None:
        return dex_name, dex_basis

    return address, None


def _classify_risk(score: int) -> str:
    if score >= 80:
        return 'critical'
    if score >= 60:
        return 'high'
    if score >= 35:
        return 'medium'
    if score > 0:
        return 'low'
    return 'none'


def _build_cluster(
    contract_address: str,
    contract_label: str,
    contract_match_basis: str | None,
    function_name: str | None,
    segment: list[dict[str, Any]],
) -> dict[str, Any]:
    addresses = sorted({row['sender_address'] for row in segment})
    timestamps = [row['timestamp'] for row in segment]
    window_start, window_end = min(timestamps), max(timestamps)
    duration_seconds = (window_end - window_start).total_seconds()

    ordered = sorted(segment, key=lambda row: row['timestamp'])
    gaps = [
        (ordered[index]['timestamp'] - ordered[index - 1]['timestamp']).total_seconds()
        for index in range(1, len(ordered))
    ]
    avg_gap_seconds = sum(gaps) / len(gaps) if gaps else 0.0
    max_gap_seconds_observed = max(gaps) if gaps else 0.0

    amounts = [float(row['amount']) for row in segment]
    amount_counter = Counter(amounts)
    modal_amount, modal_amount_count = amount_counter.most_common(1)[0]

    transactions = [
        {
            'sender_address': row['sender_address'],
            'recipient_address': row['recipient_address'],
            'amount': float(row['amount']),
            'timestamp': row['timestamp'].isoformat(),
            'tx_hash': row.get('_hash'),
            'block_number': row.get('_block_number'),
            'function_name': row.get('_function'),
            # Internal-only (see EVIDENCE_STORED_NAME_COLUMN) - None unless this run is
            # about to be written to the chain of custody. Not part of the module's public
            # API contract in the sense the other fields are, but harmless to include: it is
            # simply always None for every existing caller (GET route, this module's tests).
            'tx_id': row.get('_tx_id'),
        }
        for row in ordered
    ]

    return {
        'contract_address': contract_address,
        'contract_name': contract_label,
        'contract_match_basis': contract_match_basis,
        'function_name': function_name,
        'address_count': len(addresses),
        'addresses': addresses,
        'activity_count': len(segment),
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'window_duration_seconds': int(duration_seconds),
        'avg_gap_seconds': round(avg_gap_seconds, 2),
        'max_gap_seconds_observed': int(max_gap_seconds_observed),
        'modal_amount': modal_amount,
        'modal_amount_count': modal_amount_count,
        'identical_amount_ratio': round(modal_amount_count / len(amounts), 4),
        'transactions': transactions,
    }


def _score_cluster(cluster: dict[str, Any], time_window_seconds: int, repeated_address_count: int) -> tuple[int, list[str]]:
    reasons: list[str] = []

    address_component = min(40, cluster['address_count'] * 6)
    reasons.append(
        f"{cluster['address_count']} različitih adresa pozvalo je {cluster['contract_name']} "
        f"({cluster['activity_count']} aktivnosti) u periodu od {cluster['window_duration_seconds']}s "
        f"(+{address_component}/40 za broj adresa)."
    )

    density_ratio = max(0.0, 1 - (cluster['avg_gap_seconds'] / time_window_seconds)) if time_window_seconds > 0 else 1.0
    density_component = round(30 * density_ratio)
    reasons.append(
        f"Prosečan razmak između uzastopnih aktivnosti: {cluster['avg_gap_seconds']}s od dozvoljenih {time_window_seconds}s "
        f"(+{density_component}/30 za vremensku zbijenost)."
    )

    if cluster['function_name']:
        reasons.append(f"Sve aktivnosti pozivaju istu deklarisanu funkciju: '{cluster['function_name']}'.")

    amount_component = 0
    if cluster['modal_amount_count'] >= 2:
        if cluster['identical_amount_ratio'] >= 0.5:
            amount_component = 15
        else:
            amount_component = 7
        reasons.append(
            f"{cluster['modal_amount_count']}/{cluster['activity_count']} transakcija koristi identičan iznos "
            f"({cluster['modal_amount']}) - obrazac tipičan za skriptovano/automatizovano ponašanje "
            f"(+{amount_component}/15)."
        )

    recurrence_component = 0
    if repeated_address_count >= 2:
        recurrence_component = 15
    elif repeated_address_count == 1:
        recurrence_component = 5
    if recurrence_component:
        reasons.append(
            f"{repeated_address_count} adresa iz ovog klastera se ponavlja u bar jednom drugom klasteru "
            f"(+{recurrence_component}/15 za ponavljanje kohorte)."
        )

    score = min(100, address_component + density_component + amount_component + recurrence_component)
    return score, reasons


def detect_sybil_clusters(
    transactions: pd.DataFrame,
    target_address: str | None = None,
    contract: str | None = None,
    time_window_seconds: int = DEFAULT_TIME_WINDOW_SECONDS,
    min_addresses: int = DEFAULT_MIN_ADDRESSES,
    known_contracts: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Sybil & Bot Network Analysis: flags groups of DIFFERENT addresses that call the same
    smart contract and/or the same function within a short, chained time window - a
    heuristic signal for a potential Sybil cluster (coordinated wallets, an airdrop-farming
    bot network, ...), never a proof that the addresses share a real-world owner.

    `target_address`/`contract` use the same exact, case-sensitive match convention as
    path_finding.find_transaction_paths / dex_swap_analysis.detect_dex_swaps - a value that
    never appears in the evidence raises ValueError (the route layer turns this into a 404).

    See SYBIL-ANALIZA.md for the full heuristic and its limitations.
    """
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in transactions.columns]
    if missing_columns:
        raise ValueError(f'Missing required columns: {", ".join(missing_columns)}')

    time_window_seconds = _clamp(time_window_seconds, MIN_TIME_WINDOW_SECONDS, MAX_TIME_WINDOW_SECONDS)
    min_addresses = _clamp(min_addresses, MIN_MIN_ADDRESSES, MAX_MIN_ADDRESSES)

    frame = _prepare_frame(transactions)

    if target_address is not None:
        present = bool(((frame['sender_address'] == target_address) | (frame['recipient_address'] == target_address)).any())
        if not present:
            raise ValueError(f'Adresa nije pronađena u evidenciji: {target_address}')

    if contract is not None:
        present = bool((frame['recipient_address'] == contract).any())
        if not present:
            raise ValueError(f'Kontrakt/adresa nije pronađena kao primalac ni jedne transakcije: {contract}')
        frame = frame[frame['recipient_address'] == contract]

    contracts = known_contracts if known_contracts is not None else _load_known_dex_contracts()
    function_column = _find_function_column(frame)
    has_function_column = function_column is not None

    rows: list[dict[str, Any]] = []
    for row in frame.to_dict('records'):
        row['_function'] = _clean_text(row.get(function_column)) if function_column else None
        row['_hash'] = _clean_text(row.get('metadata'))
        row['_block_number'] = _clean_text(row.get(BLOCK_NUMBER_COLUMN))
        evidence_stored_name = _clean_text(row.get(EVIDENCE_STORED_NAME_COLUMN))
        row['_tx_id'] = transaction_id(row, evidence_stored_name) if evidence_stored_name else None
        rows.append(row)

    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row['recipient_address'], row['_function'])
        groups.setdefault(key, []).append(row)

    raw_clusters: list[dict[str, Any]] = []
    for (contract_address, function_name), group_rows in groups.items():
        group_rows_sorted = sorted(group_rows, key=lambda row: row['timestamp'])
        contract_display_name, contract_basis = _contract_label(contract_address, contracts)
        for segment in _burst_segments(group_rows_sorted, time_window_seconds):
            distinct_senders = {row['sender_address'] for row in segment}
            if len(distinct_senders) < min_addresses:
                continue
            raw_clusters.append(_build_cluster(contract_address, contract_display_name, contract_basis, function_name, segment))

    # Recurrence pass: an address that shows up in more than one flagged cluster strengthens
    # the Sybil hypothesis (the same cohort acting together more than once), so this has to
    # be computed AFTER every cluster is known, not per-cluster in isolation.
    address_to_cluster_indices: dict[str, set[int]] = {}
    for index, cluster in enumerate(raw_clusters):
        for address in cluster['addresses']:
            address_to_cluster_indices.setdefault(address, set()).add(index)

    repeated_counts: list[int] = []
    for index, cluster in enumerate(raw_clusters):
        repeated = sum(1 for address in cluster['addresses'] if len(address_to_cluster_indices[address]) > 1)
        repeated_counts.append(repeated)

    for index, cluster in enumerate(raw_clusters):
        score, reasons = _score_cluster(cluster, time_window_seconds, repeated_counts[index])
        cluster['risk_score'] = score
        cluster['risk_level'] = _classify_risk(score)
        cluster['repeated_address_count'] = repeated_counts[index]
        cluster['reasons'] = reasons

    raw_clusters.sort(key=lambda cluster: cluster['risk_score'], reverse=True)

    if target_address is not None:
        raw_clusters = [cluster for cluster in raw_clusters if target_address in cluster['addresses']]

    clusters_result: list[dict[str, Any]] = []
    for rank, cluster in enumerate(raw_clusters):
        cluster['cluster_id'] = f'SYBIL-{rank + 1}'
        clusters_result.append(cluster)

    return {
        'target_address': target_address,
        'contract': contract,
        'time_window_seconds': time_window_seconds,
        'min_addresses': min_addresses,
        'total_clusters': len(clusters_result),
        'addresses_flagged': len({address for cluster in clusters_result for address in cluster['addresses']}),
        'function_data_available': has_function_column,
        'clusters': clusters_result,
        'disclaimer': (
            'Sybil & Bot Network Analysis je heuristika zasnovana isključivo na vremenskoj sinhronizaciji i '
            'zajedničkom kontraktu/funkciji poziva - NIKADA ne predstavlja dokaz da navedene adrese pripadaju '
            'istoj osobi ili entitetu. Legitimni, nekoordinisani skupovi korisnika (npr. javna prodaja, popularan '
            'airdrop, viralna kampanja) mogu proizvesti isti obrazac. Svaki nalaz zahteva dodatnu, nezavisnu '
            'istražnu proveru pre bilo kakvog zaključka o vlasništvu.'
        ),
    }
