"""Flow of Funds / Layering Analysis: traces how funds move outward (or, in reverse, where
they came from) from a set of seed addresses across multiple hops ("layers"), aggregating
every individual transaction found along the way into Sankey-ready flow records - never raw
per-transaction rows - so a layering scheme (funds bounced through several intermediary
wallets to obscure their origin) reads as a small number of aggregated flows instead of
dozens/hundreds of individual transfers.

Reuses `build_transaction_graph` (app.analytics.graph_building) for graph topology exactly
like path_finding.py/taint_analysis.py already do, and follows the same frontier/visited BFS
shape as path_finding.py's bfs_shortest_path/find_path_to_nearest_of - but this module does
NOT change any of those, it only reads what they already read. It is also NOT one of the
app.analytics.plugins (no shared taint/blacklist pipeline dependency), since a flow trace
starts from analyst-picked seed addresses rather than running unconditionally over a case.

Ethereum (account-based) vs Bitcoin (UTXO) - see BITCOIN-UTXO-PLAN.md:
Both chains are already normalized upstream into one ingested schema (sender_address,
recipient_address, amount, timestamp, metadata=tx hash, optional currency) - see
app/analytics/ingestion.py - which by design erases most of the structural UTXO/account
difference before it ever reaches an analysis. Two differences still show up in that shared
schema and matter for a layering trace, so THIS module (not ingestion) accounts for them:

  1. Unit safety. Amounts are only ever comparable within the same asset. An evidence row
     carries an explicit `currency` when its source CSV declared one; a live on-chain fetch
     (both chains, see onchain_ingestion.py / bitcoin_ingestion/service.py) currently does
     not set one. Where it is missing, the asset is inferred from address *shape* instead of
     assumed - `0x` + 40 hex chars is an Ethereum address, base58/bech32 is a Bitcoin one
     (see `_infer_asset`). A flow between two addresses is only ever aggregated within one
     asset; if a single (source, target, level) pairing genuinely spans more than one asset
     (mixed evidence), it is reported as separate flow records rather than silently summed
     together into a meaningless total.
  2. Same-transaction fan-out. A single Bitcoin transaction id (one `metadata` value) can
     legitimately appear as several evidence rows - one per output - splitting to more than
     one recipient (including its own change address) in a single atomic step. A single
     Ethereum value-transfer transaction id never does (one txlist entry is exactly one
     sender/recipient/value). Each flow record therefore reports `distinct_tx_count` (unique
     `metadata` values) alongside `transaction_count` (row count) and a `multi_output_same_tx`
     flag, which is set exactly when this kind of single-transaction, multi-output split
     occurred - a structural fingerprint worth surfacing on its own regardless of which chain
     produced it (classic layering/structuring signature).

Known-entity/category collapsing (`entity_flows` / `category_flows` below) is naturally
chain-aware too: `get_known_entity` only ever recognizes curated Ethereum addresses (see
app/services/known_entities.json) - a Bitcoin address is never guessed into a fabricated
entity/category, it simply stays at address-level granularity, exactly as it should for
something that is not "reliably known".
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Literal

import pandas as pd

from app.analytics.graph_building import _serialize_value, build_transaction_graph
from app.evidence.tx_identity import transaction_id
from app.services.address_enrichment import get_known_entity


MIN_LEVELS = 1
MAX_LEVELS = 10
DEFAULT_LEVELS = 4
DEFAULT_MAX_FLOWS = 500
MIN_MAX_FLOWS = 1
MAX_MAX_FLOWS = 2000

Direction = Literal['forward', 'backward']

_ETH_ADDRESS_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')
# Legacy P2PKH/P2SH (base58, starts with 1 or 3) and native segwit bech32 (bc1.../tb1...,
# mainnet and testnet) - permissive on purpose (this only ever picks a *label*, never
# changes traversal), not a full checksum validator.
_BTC_ADDRESS_PATTERN = re.compile(r'^(bc1[a-z0-9]{25,59}|tb1[a-z0-9]{25,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$')


def _infer_asset(address: str) -> str | None:
    """Best-effort native-asset guess from address shape alone, used only as a fallback when
    a row has no explicit `currency` (see module docstring, point 1). Never asked to guess
    anything about a specific token/contract - just which base-layer chain the address
    belongs to."""
    if _ETH_ADDRESS_PATTERN.match(address):
        return 'ETH'
    if _BTC_ADDRESS_PATTERN.match(address):
        return 'BTC'
    return None


def _row_asset(currency: Any, sender: str, recipient: str) -> str:
    if currency is not None:
        text = str(currency).strip()
        if text and text.lower() != 'nan':
            return text.upper()
    sender_asset = _infer_asset(sender)
    recipient_asset = _infer_asset(recipient)
    if sender_asset and sender_asset == recipient_asset:
        return sender_asset
    return sender_asset or recipient_asset or 'UNKNOWN'


def _describe_address(address: str) -> dict[str, str | None]:
    entity = get_known_entity(address)
    return {
        'address': address,
        'entity_name': entity['name'] if entity else None,
        'entity_category': entity['category'] if entity else None,
    }


def attach_evidence_provenance(per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]]) -> pd.DataFrame:
    """Concatenates per-evidence-file frames (same input shape as
    app.analytics.case_graph.combine_frames) into one DataFrame, tagged with an extra
    `evidence_stored_name` column per row - the one piece of provenance `combine_frames`
    itself deliberately drops. `trace_flow_of_funds`/`_index_rows_by_edge` read that column,
    when present, to compute each transaction's `tx_id` exactly the way
    app.shared.custody_recording.record_custody_access does, so a flow's transactions can
    link straight to an existing chain-of-custody entry (see app/features/custody/router.py's
    GET .../custody/transactions/{tx_id}). Callers that don't care about that linkage can
    keep using combine_frames as before - this is purely additive."""
    if not per_evidence_frames:
        return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', 'evidence_stored_name'])

    tagged_frames = []
    for evidence_entry, frame in per_evidence_frames:
        tagged = frame.copy()
        tagged['evidence_stored_name'] = str(evidence_entry.get('stored_name') or '')
        tagged_frames.append(tagged)

    return pd.concat(tagged_frames, ignore_index=True)


def _clean_na(value: Any) -> Any:
    """`itertuples()` hands back pandas' raw missing-value sentinel (e.g. `pd.NA` for a
    nullable string column) unconverted - unlike `frame.to_dict('records')`, which quietly
    turns it into plain `None` (see app.shared.custody_recording._clean_scalar, the same
    normalization, needed there for the same reason). `pd.NA or x` raises `TypeError`
    ('boolean value of NA is ambiguous'), so anything handed to `transaction_id()` - which
    does exactly that - has to go through this first to match what to_dict('records') would
    have produced from the same cell."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _index_rows_by_edge(dataframe: pd.DataFrame) -> dict[tuple[str, str], dict[str, list[dict[str, Any]]]]:
    """Groups the raw (already-cleaned) evidence rows by (sender, recipient) and, within
    that, by inferred/declared asset - the per-currency bucketing that keeps amount
    aggregation honest (see module docstring, point 1). Deliberately reads the raw
    DataFrame rather than the pre-built graph's `total_amount`, which already mixes
    whatever currencies were combined into one case."""
    has_currency = 'currency' in dataframe.columns
    # Optional - only present when the caller tagged each row with its source evidence file
    # (see attach_evidence_provenance) - lets every transaction carry the SAME `tx_id` its
    # chain-of-custody entry is keyed by (app.evidence.tx_identity.transaction_id), so a
    # flow's transaction list can link straight to Lanac dokaza. Falls back to '' when
    # absent (e.g. direct/test callers with a plain combined frame) - still a valid,
    # deterministic id, just not guaranteed to match a real custody entry.
    has_evidence_name = 'evidence_stored_name' in dataframe.columns
    index: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for row in dataframe.itertuples(index=False):
        sender = str(getattr(row, 'sender_address'))
        recipient = str(getattr(row, 'recipient_address'))
        amount = float(getattr(row, 'amount'))
        raw_timestamp = getattr(row, 'timestamp', None)
        raw_metadata = getattr(row, 'metadata', None)
        timestamp = _serialize_value(raw_timestamp)
        tx_hash = _serialize_value(raw_metadata)
        currency = getattr(row, 'currency', None) if has_currency else None
        evidence_stored_name = str(getattr(row, 'evidence_stored_name', '') or '') if has_evidence_name else ''

        # Computed from the RAW (pre-serialization) values, matching exactly what
        # app.shared.custody_recording.record_custody_access computes from the same
        # per-evidence DataFrame row - so this id lines up with a real chain-of-custody
        # entry whenever one already exists, not a lookalike. record_custody_access reads
        # its rows via frame.to_dict('records'), which silently turns a missing nullable-
        # string cell (pd.NA) into plain None; itertuples does NOT do that on its own
        # (`pd.NA or x` raises TypeError), so _clean_na does that same normalization here.
        tx_id = transaction_id(
            {
                'sender_address': sender,
                'recipient_address': recipient,
                'amount': _clean_na(getattr(row, 'amount')),
                'timestamp': _clean_na(raw_timestamp),
                'metadata': _clean_na(raw_metadata),
            },
            evidence_stored_name,
        )

        asset = _row_asset(currency, sender, recipient)
        index[(sender, recipient)][asset].append({
            'amount': amount,
            'timestamp': timestamp,
            'tx_hash': tx_hash,
            'tx_id': tx_id,
            'sender_address': sender,
            'recipient_address': recipient,
        })

    return index


def _filter_by_period(dataframe: pd.DataFrame, start_time: str | None, end_time: str | None) -> pd.DataFrame:
    """Restricts evidence rows to a caller-chosen time window BEFORE any graph/aggregation
    step sees them, so a period-scoped trace only ever includes transactions that actually
    happened in that window - filtering the already-aggregated flow records afterwards
    would be wrong, since one flow's amount/tx_hashes can span transactions from outside the
    window. `None` (either bound, or both) means unbounded on that side."""
    if start_time is None and end_time is None:
        return dataframe
    if dataframe.empty or 'timestamp' not in dataframe.columns:
        return dataframe

    timestamps = pd.to_datetime(dataframe['timestamp'], utc=True, errors='coerce')
    mask = pd.Series(True, index=dataframe.index)

    if start_time:
        start = pd.to_datetime(start_time, utc=True, errors='coerce')
        if pd.notna(start):
            mask &= timestamps >= start
    if end_time:
        end = pd.to_datetime(end_time, utc=True, errors='coerce')
        if pd.notna(end):
            mask &= timestamps <= end

    return dataframe[mask].reset_index(drop=True)


def _fan_out_tx_hashes(dataframe: pd.DataFrame) -> set[str]:
    """Transaction ids (metadata) that paid more than one DISTINCT recipient - the general
    UTXO same-transaction, multi-output signature (module docstring, point 2), computed
    once over the whole evidence scope so it's visible regardless of which single edge a
    given row ends up aggregated into. A single Ethereum value-transfer tx id never lands
    in this set, since one txlist entry is exactly one recipient."""
    targets_by_tx: dict[str, set[str]] = defaultdict(set)
    for row in dataframe.itertuples(index=False):
        tx_hash = _serialize_value(getattr(row, 'metadata', None))
        if not tx_hash:
            continue
        targets_by_tx[str(tx_hash)].add(str(getattr(row, 'recipient_address')))
    return {tx_hash for tx_hash, targets in targets_by_tx.items() if len(targets) > 1}


def _build_flow_record(
    level: int,
    source: str,
    target: str,
    asset: str,
    rows: list[dict[str, Any]],
    fan_out_tx_hashes: set[str],
) -> dict[str, Any]:
    tx_hashes = sorted({row['tx_hash'] for row in rows if row['tx_hash']})
    timestamps = sorted(row['timestamp'] for row in rows if row['timestamp'])
    total_amount = sum(row['amount'] for row in rows)
    # The individual evidence rows behind this one aggregated flow, so a caller (e.g. a
    # "show me the transactions behind this Sankey link" UI) never has to re-fetch or
    # re-derive them - every aggregate stays traceable back to concrete rows without a
    # second round trip. sender/recipient_address are carried per-transaction (not just
    # implied by this record's own source/target) because _collapse_flows below reuses the
    # same list at entity/category granularity, where several different address pairs merge
    # into one flow record.
    transactions = sorted(
        (
            {
                'tx_hash': row['tx_hash'],
                'tx_id': row['tx_id'],
                'amount': row['amount'],
                'timestamp': row['timestamp'],
                'sender_address': row['sender_address'],
                'recipient_address': row['recipient_address'],
            }
            for row in rows
        ),
        key=lambda transaction: (transaction['timestamp'] or '', transaction['tx_hash'] or ''),
    )
    # Row count vs. unique tx hash count on THIS edge - collapses repeat payments between
    # the same pair, distinct from (but related to) the fan-out flag below. Falls back to
    # the row count when no row carries a tx hash at all (nothing to tell rows apart by).
    distinct_tx_count = len(tx_hashes) if tx_hashes else len(rows)
    # True when at least one of this edge's transactions also paid a DIFFERENT recipient in
    # the very same tx - the same-transaction, multi-output UTXO signature (see
    # _fan_out_tx_hashes), not merely "this edge saw the sender twice".
    multi_output_same_tx = any(tx_hash in fan_out_tx_hashes for tx_hash in tx_hashes)

    source_info = _describe_address(source)
    target_info = _describe_address(target)

    return {
        'level': level,
        'source': source,
        'source_label': source_info['entity_name'] or source,
        'source_entity_name': source_info['entity_name'],
        'source_entity_category': source_info['entity_category'],
        'target': target,
        'target_label': target_info['entity_name'] or target,
        'target_entity_name': target_info['entity_name'],
        'target_entity_category': target_info['entity_category'],
        'asset': asset,
        'amount': total_amount,
        'value': total_amount,
        'transaction_count': len(rows),
        'distinct_tx_count': distinct_tx_count,
        'multi_output_same_tx': multi_output_same_tx,
        'tx_hashes': tx_hashes,
        'transactions': transactions,
        'first_seen': timestamps[0] if timestamps else None,
        'last_seen': timestamps[-1] if timestamps else None,
    }


def _node_records(flows: list[dict[str, Any]], seeds: set[str]) -> list[dict[str, Any]]:
    """One summary record per unique address that appears in `flows`, with the smallest
    level at which it was reached (0 for a seed) - lets a Sankey renderer place nodes into
    columns without re-deriving reachability itself."""
    best_level: dict[str, int] = {seed: 0 for seed in seeds}
    info: dict[str, dict[str, str | None]] = {}

    for flow in flows:
        for address in (flow['source'], flow['target']):
            if address not in info:
                info[address] = _describe_address(address)

        source_level = best_level.get(flow['source'], flow['level'] - 1)
        target_level = flow['level']
        best_level[flow['source']] = min(best_level.get(flow['source'], source_level), source_level)
        best_level[flow['target']] = min(best_level.get(flow['target'], target_level), target_level)

    nodes = []
    for address, level in sorted(best_level.items(), key=lambda item: (item[1], item[0])):
        address_info = info.get(address, _describe_address(address))
        nodes.append({
            'id': address,
            'label': address_info['entity_name'] or address,
            'level': level,
            'type': 'seed' if address in seeds else 'address',
            'entity_name': address_info['entity_name'],
            'entity_category': address_info['entity_category'],
        })
    return nodes


def _collapse_flows(flows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    """Re-aggregates address-level flow records up to a coarser granularity (entity or
    category), merging every underlying address pair that resolves to the same
    (level, group_key, group_key, asset) group. `key_fn` returns (group_key, label,
    category) for one address - falls back to the raw address itself (as both group_key and
    label) whenever nothing reliably known applies (see module docstring). `group_key` is
    only ever used to decide what collapses together - `label` (never prefixed) is what's
    exposed as this flow's `source`/`target`, so an unknown address still comes back as
    itself, not as an internal "address:0x..." grouping token."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}

    for flow in flows:
        source_key, source_label, source_category = key_fn(flow['source'])
        target_key, target_label, target_category = key_fn(flow['target'])
        group_key = (flow['level'], source_key, target_key, flow['asset'])

        entry = grouped.get(group_key)
        if entry is None:
            entry = {
                'level': flow['level'],
                'source': source_label,
                'source_label': source_label,
                'source_category': source_category,
                'target': target_label,
                'target_label': target_label,
                'target_category': target_category,
                'asset': flow['asset'],
                'amount': 0.0,
                'value': 0.0,
                'transaction_count': 0,
                'tx_hashes': set(),
                'transactions': [],
                'contributing_addresses': {'source': set(), 'target': set()},
                'multi_output_same_tx': False,
                'first_seen': None,
                'last_seen': None,
            }
            grouped[group_key] = entry

        entry['amount'] += flow['amount']
        entry['value'] += flow['amount']
        entry['transaction_count'] += flow['transaction_count']
        entry['tx_hashes'].update(flow['tx_hashes'])
        entry['transactions'].extend(flow['transactions'])
        entry['multi_output_same_tx'] = entry['multi_output_same_tx'] or flow['multi_output_same_tx']
        entry['contributing_addresses']['source'].add(flow['source'])
        entry['contributing_addresses']['target'].add(flow['target'])
        if flow['first_seen'] and (entry['first_seen'] is None or flow['first_seen'] < entry['first_seen']):
            entry['first_seen'] = flow['first_seen']
        if flow['last_seen'] and (entry['last_seen'] is None or flow['last_seen'] > entry['last_seen']):
            entry['last_seen'] = flow['last_seen']

    result = []
    for entry in grouped.values():
        tx_hashes = sorted(entry['tx_hashes'])
        transactions = sorted(entry['transactions'], key=lambda transaction: (transaction['timestamp'] or '', transaction['tx_hash'] or ''))
        result.append({
            **entry,
            'tx_hashes': tx_hashes,
            'transactions': transactions,
            'distinct_tx_count': len(tx_hashes) if tx_hashes else entry['transaction_count'],
            'contributing_addresses': {
                'source': sorted(entry['contributing_addresses']['source']),
                'target': sorted(entry['contributing_addresses']['target']),
            },
        })

    result.sort(key=lambda item: (item['level'], item['source'], item['target'], item['asset']))
    return result


def _entity_key(address: str) -> tuple[str, str, str | None]:
    info = _describe_address(address)
    if info['entity_name']:
        return f'entity:{info["entity_name"]}', str(info['entity_name']), info['entity_category']
    return f'address:{address}', address, None


def _category_key(address: str) -> tuple[str, str, str | None]:
    info = _describe_address(address)
    if info['entity_category']:
        return f'category:{info["entity_category"]}', str(info['entity_category']), info['entity_category']
    return f'address:{address}', address, None


def trace_flow_of_funds(
    dataframe: pd.DataFrame,
    source_addresses: list[str],
    direction: Direction = 'forward',
    max_levels: int = DEFAULT_LEVELS,
    min_amount: float = 0.0,
    max_flows: int = DEFAULT_MAX_FLOWS,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """Traces aggregated flows of funds outward from `source_addresses` (direction
    'forward', following the direction money actually moved - sender -> recipient) or
    backward toward their origin (direction 'backward', walking incoming edges), one BFS
    layer/level at a time, up to `max_levels` hops.

    Every transaction between the same (source, target) pair, at the same BFS level and in
    the same asset, is aggregated into ONE flow record - never returned as individual
    transactions - carrying the underlying transactions (and their tx hashes) so the
    aggregate can always be traced back to concrete evidence rows.

    `start_time`/`end_time` (ISO date or datetime strings, either or both optional) scope
    the trace to a time window - applied to the evidence BEFORE aggregation (see
    `_filter_by_period`), not as a post-hoc filter on the resulting flow records, so a
    period-scoped result only ever reflects transactions that actually happened in that
    window. A seed address that exists in the case but has no activity within the window is
    not an error - it simply ends up with no outgoing flows (still listed in `nodes`).

    Deliberately mirrors the frontier/visited-set shape of
    app.analytics.path_finding.bfs_shortest_path/find_path_to_nearest_of (own copy, that
    module is not imported or modified): a node is only ever *expanded* (used as a new
    frontier node) once, which keeps this bounded on a cyclical graph, but an edge INTO an
    already-visited node is still recorded as its own flow - money converging back onto an
    address already seen is itself a real, forensically relevant layering pattern, not
    something to silently drop.
    """
    max_levels = max(MIN_LEVELS, min(MAX_LEVELS, int(max_levels)))
    max_flows = max(MIN_MAX_FLOWS, min(MAX_MAX_FLOWS, int(max_flows)))

    seeds = [address for address in dict.fromkeys(source_addresses) if address]
    if not seeds:
        raise ValueError('At least one source address is required.')

    # Existence is checked against the FULL (unfiltered) evidence - an address the case has
    # simply never seen is a real error, distinct from "this address exists, but not within
    # the chosen period" (which is a legitimate, empty-ish result, not an error).
    full_graph = build_transaction_graph(dataframe)
    missing = [address for address in seeds if address not in full_graph]
    if missing:
        raise ValueError(f'Source address(es) not found in graph: {", ".join(missing)}')

    period_frame = _filter_by_period(dataframe, start_time, end_time)
    graph = build_transaction_graph(period_frame)
    edge_rows = _index_rows_by_edge(period_frame)
    fan_out_tx_hashes = _fan_out_tx_hashes(period_frame)

    seed_set = set(seeds)
    visited = set(seeds)
    frontier = [address for address in seeds if address in graph]
    flows: list[dict[str, Any]] = []
    truncated = False
    levels_reached = 0

    for level in range(1, max_levels + 1):
        next_frontier: list[str] = []
        level_flows: list[dict[str, Any]] = []

        for node in sorted(frontier):
            neighbors = graph.successors(node) if direction == 'forward' else graph.predecessors(node)
            for neighbor in sorted(set(neighbors)):
                edge_key = (node, neighbor) if direction == 'forward' else (neighbor, node)
                flow_source, flow_target = edge_key
                rows_by_asset = edge_rows.get(edge_key, {})

                for asset in sorted(rows_by_asset):
                    rows = rows_by_asset[asset]
                    record = _build_flow_record(level, flow_source, flow_target, asset, rows, fan_out_tx_hashes)
                    if record['amount'] < min_amount:
                        continue
                    level_flows.append(record)

                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)

        if len(flows) + len(level_flows) > max_flows:
            level_flows = level_flows[: max(0, max_flows - len(flows))]
            truncated = True

        flows.extend(level_flows)
        if level_flows:
            levels_reached = level

        if truncated or not next_frontier:
            break
        frontier = next_frontier

    address_flows = sorted(flows, key=lambda item: (item['level'], item['source'], item['target'], item['asset']))
    nodes = _node_records(address_flows, seed_set)
    entity_flows = _collapse_flows(address_flows, _entity_key)
    category_flows = _collapse_flows(address_flows, _category_key)

    return {
        'source_addresses': seeds,
        'direction': direction,
        'max_levels': max_levels,
        'levels_reached': levels_reached,
        'start_time': start_time,
        'end_time': end_time,
        'flow_count': len(address_flows),
        'truncated': truncated,
        'nodes': nodes,
        'address_flows': address_flows,
        'entity_flows': entity_flows,
        'category_flows': category_flows,
    }
