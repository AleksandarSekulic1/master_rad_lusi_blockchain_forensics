from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.plugins.blacklist_check import _normalize_address

# Token Approval / Ice Phishing Analysis - a new, standalone module (see
# TOKEN-APPROVAL-IMPLEMENTATION.md), built the same way as DEX Swap Analysis
# (app.analytics.dex_swap_analysis): it reads a case's already CLEANED evidence
# (app.analytics.case_graph.combine_frames), NOT the shared transaction graph -
# build_transaction_graph (used by Graph/Taint/Pathfinding/Behavioral) intentionally keeps
# only {amount, timestamp, metadata} per transaction, so it cannot carry the extra fields
# an ERC-20 approve/permit/transferFrom event needs (event type, token contract, spender).
# Reading the cleaned DataFrame directly is the only way to see those fields without
# touching that shared code path.
#
# THIS MODULE DOES NOT DECODE ANYTHING FROM THE CHAIN. The project's only real on-chain
# source (app.services.onchain_ingestion) calls Etherscan's `txlist` only (native ETH
# transfers) - it never calls `tokentx` or `eth_getLogs`, so it never receives an ERC-20
# `Approval` event or a `permit()` call in the first place. There is also no ABI-decoding
# or keccak256-capable dependency anywhere in this project (checked: not in
# backend/requirements.txt, not installed in the venv) - decoding raw event logs is out of
# scope for this module. See TOKEN-APPROVAL-IMPLEMENTATION.md #7 for the full accounting.
#
# What this module DOES do: extract and classify approval/permit/transferFrom rows from
# evidence that already declares them - either a CSV an analyst prepared by hand (e.g. from
# a block explorer's "Token Approvals" page or a manual research spreadsheet), or evidence
# from some future on-chain pull that populates the same optional columns. The required
# base columns (sender_address, recipient_address, amount, timestamp, metadata) are reused
# exactly as the rest of the app already defines them - see _row_owner/_row_spender below
# for how - and a handful of new, OPTIONAL columns carry what a plain transfer cannot:
#
#   event_type       'approve' | 'permit' | 'transferFrom'  (case-insensitive)
#   token_address     the ERC-20 contract the approval/transfer concerns
#   owner_address     explicit token owner (falls back to sender_address when absent -
#                     correct for approve(); permit() may be relayed, so an explicit
#                     column is the only reliable source when the signer != tx sender)
#   spender_address   the approved spender (approve/permit rows) or the address that
#                     actually invoked transferFrom (transferFrom rows) - NEVER defaulted
#                     to recipient_address for transferFrom, because the funds' final
#                     recipient can be a different address than the spender itself
#   is_unlimited      explicit tri-state flag, when the evidence declares it directly
#   block_number      purely descriptive - never fetched by this project today (see
#                     TOKEN-APPROVAL-IMPLEMENTATION.md #7.4)
#   permit_deadline   opaque passthrough, only read for event_type='permit' rows
#   permit_nonce      opaque passthrough, only read for event_type='permit' rows
#
# None of these need a new ingestion.COLUMN_ALIASES entry: ingestion._normalize_columns
# only renames KNOWN aliases and otherwise leaves every other CSV column untouched, all the
# way through to the combined_frame this module reads (see
# TOKEN-APPROVAL-IMPLEMENTATION.md #7.3) - so a CSV column named exactly `event_type` (etc.)
# already survives with zero changes to ingestion.py, exactly the mechanism `currency`
# already uses for DEX Swap Analysis. A row missing every one of these optional columns is
# simply not considered an approval-related row at all - it stays a plain transfer for
# every OTHER analysis, unaffected by this module's existence.
#
# Every classification below is either read directly from a column the evidence declared,
# or a clearly-labelled heuristic (see UnlimitedBasis / risk indicator codes) - never a
# silent guess. See build_result() and the module docstring sections above for the
# disclaimer text.

REQUIRED_COLUMNS = ('sender_address', 'recipient_address', 'amount', 'timestamp')

EVENT_TYPE_COLUMN = 'event_type'
TOKEN_ADDRESS_COLUMN = 'token_address'
OWNER_ADDRESS_COLUMN = 'owner_address'
SPENDER_ADDRESS_COLUMN = 'spender_address'
IS_UNLIMITED_COLUMN = 'is_unlimited'
BLOCK_NUMBER_COLUMN = 'block_number'
PERMIT_DEADLINE_COLUMN = 'permit_deadline'
PERMIT_NONCE_COLUMN = 'permit_nonce'

OPTIONAL_COLUMNS = (
    EVENT_TYPE_COLUMN,
    TOKEN_ADDRESS_COLUMN,
    OWNER_ADDRESS_COLUMN,
    SPENDER_ADDRESS_COLUMN,
    IS_UNLIMITED_COLUMN,
    BLOCK_NUMBER_COLUMN,
    PERMIT_DEADLINE_COLUMN,
    PERMIT_NONCE_COLUMN,
)

APPROVE_EVENT = 'approve'
PERMIT_EVENT = 'permit'
TRANSFER_FROM_EVENT = 'transferFrom'
APPROVAL_EVENT_TYPES = (APPROVE_EVENT, PERMIT_EVENT)

# Deliberately narrow - only the event's own canonical name and its most obvious spelling
# variants (case/underscore), NOT generic words like 'type'/'action' that unrelated CSVs
# already in this project might use for something else (that column would need to be
# named literally 'event_type' to be picked up at all - see module docstring above).
_EVENT_TYPE_ALIASES: dict[str, str] = {
    'approve': APPROVE_EVENT,
    'erc20_approve': APPROVE_EVENT,
    'permit': PERMIT_EVENT,
    'eip2612_permit': PERMIT_EVENT,
    'eip-2612_permit': PERMIT_EVENT,
    'transferfrom': TRANSFER_FROM_EVENT,
    'transfer_from': TRANSFER_FROM_EVENT,
}

# "Unlimited allowance" magnitude heuristic. amount arrives as a float64 (parsed by
# ingestion.clean_transaction_csv's pd.to_numeric), so it cannot reliably prove exact
# equality with a 78-digit sentinel like 2**256-1 (float64 only carries ~15-17 significant
# digits) - and this project has no token-decimals registry, so it cannot even tell whether
# a given amount is already in raw base units or human-scaled. A fixed "looks like
# 2**256-1" comparison would therefore be false precision. Instead: any declared
# `is_unlimited` value is trusted as-is (strongest signal - the evidence said so directly);
# absent that, only a configurable ORDER-OF-MAGNITUDE threshold is used, always labelled as
# a heuristic (`potential_by_magnitude`), never as confirmed. See
# TOKEN-APPROVAL-IMPLEMENTATION.md #7.4/#8.3 for the full reasoning.
DEFAULT_UNLIMITED_THRESHOLD = 1e15
MIN_UNLIMITED_THRESHOLD = 1.0
MAX_UNLIMITED_THRESHOLD = 1e40

# "Rapid drain" window - how soon after an approval a transferFrom must land to be flagged
# as a fast-drain pattern. Same spirit and same tunability as
# dex_swap_analysis.DEFAULT_MAX_GAP_SECONDS, just a wider default range since draining an
# allowance is not expected to happen within seconds the way an atomic DEX swap does.
DEFAULT_RAPID_USE_SECONDS = 3600
MIN_RAPID_USE_SECONDS = 0
MAX_RAPID_USE_SECONDS = 30 * 24 * 3600


def _clean_text(value: object) -> str | None:
    """None/NaN/blank/the literal string 'nan' all collapse to None; everything else is
    stripped. Same nan-guard idiom as dex_swap_analysis._clean_text / tx_identity."""
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


def _normalize_event_type(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    return _EVENT_TYPE_ALIASES.get(text.strip().lower())


def _parse_tri_state_bool(value: object) -> bool | None:
    text = _clean_text(value)
    if text is None:
        return None
    normalized = text.strip().lower()
    if normalized in ('true', '1', 'yes', 'y', 'unlimited', 'infinite'):
        return True
    if normalized in ('false', '0', 'no', 'n'):
        return False
    return None


def _parse_int(value: object) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _address_key(value: object) -> str | None:
    """Normalized (lowercased) form used ONLY as an internal grouping/matching key - the
    same idea as dex_swap_analysis.classify_dex_node's lookup against known_dex_contracts
    .json. Display fields always keep the address text as it first appeared in the
    evidence (see _first_seen_display)."""
    normalized = _normalize_address(value)
    return normalized or None


def _prepare_frame(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True, errors='coerce')
    frame = frame.dropna(subset=['sender_address', 'recipient_address', 'amount', 'timestamp'])
    return frame.sort_values('timestamp', kind='stable').reset_index(drop=True)


def _address_present(frame: pd.DataFrame, address: str) -> bool:
    """Whether `address` appears anywhere relevant to this analysis - not just
    sender/recipient (dex_swap_analysis's check), but also owner_address/spender_address
    when those columns exist, since an explicit owner (permit relayer case) might never
    appear as a plain sender/recipient at all."""
    columns = [column for column in ('sender_address', 'recipient_address', OWNER_ADDRESS_COLUMN, SPENDER_ADDRESS_COLUMN) if column in frame.columns]
    return any(bool((frame[column] == address).any()) for column in columns)


def _row_owner(row: dict[str, Any]) -> str | None:
    return _clean_text(row.get(OWNER_ADDRESS_COLUMN)) or _clean_text(row.get('sender_address'))


def _row_spender_for_approval(row: dict[str, Any]) -> str | None:
    """approve()/permit() rows: no funds move, so recipient_address is reused to carry the
    approved spender when spender_address isn't explicitly given - the same "reinterpret
    sender/recipient by context" trick dex_swap_analysis already uses for wallet/DEX."""
    return _clean_text(row.get(SPENDER_ADDRESS_COLUMN)) or _clean_text(row.get('recipient_address'))


def _row_spender_for_transfer(row: dict[str, Any]) -> str | None:
    """transferFrom() rows: recipient_address is the funds' actual destination, which can
    legitimately differ from the spender that invoked the call - so the spender is NEVER
    inferred here, only read from an explicit column. None means "cannot attribute this
    transfer to a specific approval" (see _match_transfer_to_group), not "unknown but
    assumed to be the recipient"."""
    return _clean_text(row.get(SPENDER_ADDRESS_COLUMN))


def _extract_approval_rows(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Splits the frame into (approval_events, transfer_events, skipped_rows,
    unrecognized_event_type_values). Rows with no event_type at all are ordinary transfers
    for every other analysis and are silently not part of this one - only rows with an
    event_type value that fails to match a known alias are reported back
    (unrecognized_event_type_values), so a typo'd column value is visible rather than
    quietly dropped."""
    approval_events: list[dict[str, Any]] = []
    transfer_events: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    unrecognized_values: set[str] = set()

    has_event_type = EVENT_TYPE_COLUMN in frame.columns
    if not has_event_type:
        return approval_events, transfer_events, skipped_rows, []

    for row in frame.to_dict('records'):
        raw_event_type = row.get(EVENT_TYPE_COLUMN)
        raw_text = _clean_text(raw_event_type)
        if raw_text is None:
            continue  # plain transfer row, not part of this analysis at all

        event_type = _normalize_event_type(raw_event_type)
        if event_type is None:
            unrecognized_values.add(raw_text)
            skipped_rows.append(row)
            continue

        timestamp = row['timestamp']
        tx_hash = _clean_text(row.get('metadata'))
        token_address = _clean_text(row.get(TOKEN_ADDRESS_COLUMN))
        block_number = _parse_int(row.get(BLOCK_NUMBER_COLUMN))
        amount = float(row['amount'])

        if event_type in APPROVAL_EVENT_TYPES:
            owner = _row_owner(row)
            spender = _row_spender_for_approval(row)
            if owner is None or spender is None:
                # Cannot form a meaningful (owner, spender) pair even with the
                # sender/recipient fallback (e.g. a blank recipient_address, which
                # ingestion would already have dropped for a REQUIRED column - this guard
                # is defensive, not expected to trigger in practice).
                skipped_rows.append(row)
                continue

            approval_events.append({
                'event_type': event_type,
                'owner': owner,
                'owner_key': _address_key(owner),
                'spender': spender,
                'spender_key': _address_key(spender),
                'token_address': token_address,
                'token_key': _address_key(token_address),
                'amount': amount,
                'is_zero': amount == 0.0,
                'is_unlimited_declared': _parse_tri_state_bool(row.get(IS_UNLIMITED_COLUMN)),
                'timestamp': timestamp,
                'transaction_hash': tx_hash,
                'block_number': block_number,
                'permit_deadline': _clean_text(row.get(PERMIT_DEADLINE_COLUMN)) if event_type == PERMIT_EVENT else None,
                'permit_nonce': _parse_int(row.get(PERMIT_NONCE_COLUMN)) if event_type == PERMIT_EVENT else None,
            })
        else:  # TRANSFER_FROM_EVENT
            owner = _row_owner(row)
            spender = _row_spender_for_transfer(row)
            recipient = _clean_text(row.get('recipient_address'))
            if owner is None:
                skipped_rows.append(row)
                continue

            transfer_events.append({
                'event_type': event_type,
                'owner': owner,
                'owner_key': _address_key(owner),
                'spender': spender,
                'spender_key': _address_key(spender),
                'recipient': recipient,
                'recipient_key': _address_key(recipient),
                'token_address': token_address,
                'token_key': _address_key(token_address),
                'amount': amount,
                'timestamp': timestamp,
                'transaction_hash': tx_hash,
                'block_number': block_number,
            })

    return approval_events, transfer_events, skipped_rows, sorted(unrecognized_values)


def _unlimited_basis(event: dict[str, Any], unlimited_threshold: float) -> str | None:
    if event['is_zero']:
        return None
    if event['is_unlimited_declared'] is True:
        return 'declared'
    if event['amount'] >= unlimited_threshold:
        return 'potential_by_magnitude'
    return None


def _group_key(event: dict[str, Any]) -> tuple[str, str, str | None]:
    return (event['owner_key'], event['spender_key'], event['token_key'])


def _match_transfer_to_group(
    transfer: dict[str, Any],
    approval_groups_by_full_key: dict[tuple[str, str, str | None], list[dict[str, Any]]],
    approval_groups_by_owner_spender: dict[tuple[str, str], list[tuple[str, str, str | None]]],
) -> tuple[tuple[str, str, str | None] | None, str, str | None]:
    """Returns (matched_group_key, match_basis, unattributed_reason).

    Two tiers, same discipline as dex_swap_analysis's two-pass matching:
      1. Exact (owner, spender, token) match - the strongest signal.
      2. If the transfer has no usable token_key (or no exact-key group exists) but there
         is EXACTLY ONE approval group for that (owner, spender) pair regardless of token,
         it is unambiguous even without a token match - attribute it, but label the match
         as inferred so a reader can tell the token identity was not directly confirmed.

    A transfer with no spender_key at all (transferFrom row that never named its spender)
    is never matched - see _row_spender_for_transfer's docstring: guessing which approval
    it drained would not be a reliable finding.
    """
    if transfer['spender_key'] is None:
        return None, 'unattributed', 'no_spender_column'

    owner_spender = (transfer['owner_key'], transfer['spender_key'])
    full_key = (transfer['owner_key'], transfer['spender_key'], transfer['token_key'])

    if full_key in approval_groups_by_full_key:
        return full_key, 'exact', None

    candidates = approval_groups_by_owner_spender.get(owner_spender, [])
    if len(candidates) == 1:
        return candidates[0], 'inferred_unambiguous', None
    if len(candidates) > 1:
        return None, 'unattributed', 'ambiguous_token_multiple_approvals'
    return None, 'unattributed', 'no_matching_approval'


def _seconds_between(later: pd.Timestamp, earlier: pd.Timestamp) -> float:
    return float((later - earlier).total_seconds())


# Per-approval history status - the APPROVE -> allowance change -> eventual REVOCATION ->
# current status chain from TOKEN-APPROVAL-IMPLEMENTATION.md #13, one label per approval
# ROW (not just one per group): each approve()/permit() call gets its own verdict about
# what became of THAT SPECIFIC grant, not only the group's latest state.
STATUS_ACTIVE = 'ACTIVE'
STATUS_REVOKED = 'REVOKED'
STATUS_USED = 'USED'
STATUS_UNKNOWN = 'UNKNOWN'


def _build_group_result(
    key: tuple[str, str, str | None],
    approvals: list[dict[str, Any]],
    linked_transfers: list[dict[str, Any]],
    plausible_unconfirmed_transfers: list[dict[str, Any]],
    unlimited_threshold: float,
    rapid_use_seconds: int,
    spender_owner_counts: dict[str, int],
    now: pd.Timestamp,
) -> dict[str, Any]:
    approvals_sorted = sorted(approvals, key=lambda event: event['timestamp'])

    # For each linked transfer, find the approval that was actually "in force" at that
    # moment (the latest approval at/before the transfer's own timestamp) - not just the
    # group's first approval - so time_to_first_use reflects the grant that was really
    # spent, even when several approve() calls happened in between. Computed BEFORE the
    # per-approval loop below so each approval record can look up whether IT SPECIFICALLY
    # was the one used (see usage_by_approval_index).
    linked_records: list[dict[str, Any]] = []
    usage_by_approval_index: dict[int, list[dict[str, Any]]] = {}
    for transfer in sorted(linked_transfers, key=lambda event: event['timestamp']):
        preceding_index = next(
            (index for index in range(len(approvals_sorted) - 1, -1, -1) if approvals_sorted[index]['timestamp'] <= transfer['timestamp']),
            None,
        )
        preceding_approval = approvals_sorted[preceding_index] if preceding_index is not None else None
        seconds_since_approval = (
            _seconds_between(transfer['timestamp'], preceding_approval['timestamp']) if preceding_approval else None
        )
        # Which grant was actually "in force" for THIS transfer, not just whether the
        # group ever had an unlimited approval anywhere in its history - a group can mix
        # a small early approval with a later unlimited one, and only a transfer that
        # followed the UNLIMITED grant should count toward the rapid-drain indicator.
        preceding_unlimited_basis = _unlimited_basis(preceding_approval, unlimited_threshold) if preceding_approval else None
        record = {
            'amount': transfer['amount'],
            'timestamp': transfer['timestamp'].isoformat(),
            'transaction_hash': transfer['transaction_hash'],
            'block_number': transfer['block_number'],
            'recipient': transfer['recipient'],
            'preceding_approval_timestamp': preceding_approval['timestamp'].isoformat() if preceding_approval else None,
            'preceding_approval_unlimited_basis': preceding_unlimited_basis,
            'seconds_since_approval': seconds_since_approval,
            'before_any_approval': preceding_approval is None,
        }
        linked_records.append(record)
        if preceding_index is not None:
            usage_by_approval_index.setdefault(preceding_index, []).append(record)

    approval_records: list[dict[str, Any]] = []
    for index, event in enumerate(approvals_sorted):
        is_last = index == len(approvals_sorted) - 1
        next_event = approvals_sorted[index + 1] if not is_last else None

        if not is_last:
            sequence_status = 'superseded'
        else:
            sequence_status = 'revoked' if event['is_zero'] else 'active'

        # This event's own "in force" window: from its own timestamp up to whatever
        # superseded it (next_event), or up to `now` if it is still the group's current
        # grant - used both for the ACTIVE/USED/UNKNOWN decision below and for the
        # duration fields (see TOKEN-APPROVAL-IMPLEMENTATION.md #13.2).
        window_end = next_event['timestamp'] if next_event is not None else now
        confirmed_usage = usage_by_approval_index.get(index, [])
        plausible_unconfirmed_in_window = [
            entry for entry in plausible_unconfirmed_transfers
            if event['timestamp'] <= entry['timestamp'] < window_end
            and (entry['spender_key'] is None or entry['spender_key'] == key[1])
        ]

        if event['is_zero']:
            status = STATUS_REVOKED
        elif confirmed_usage:
            status = STATUS_USED
        elif not is_last:
            # Superseded by a later approve()/permit() (zero OR a changed nonzero amount) -
            # this specific grant is no longer the one in force either way. Whether the
            # LATER event was an explicit revocation is a separate, more precise question
            # answered by `seconds_to_explicit_revocation` below, not by this status label.
            status = STATUS_REVOKED
        elif plausible_unconfirmed_in_window:
            # Cannot confirm actual use, but there IS at least one transferFrom in this
            # window whose spender was never declared (or was ambiguous between several of
            # this owner's approvals) - honestly reported as unknown rather than assumed
            # unused. See TOKEN-APPROVAL-IMPLEMENTATION.md #13.3.
            status = STATUS_UNKNOWN
        else:
            status = STATUS_ACTIVE

        if next_event is not None:
            active_duration_seconds = _seconds_between(next_event['timestamp'], event['timestamp'])
            active_duration_ongoing = False
        elif event['is_zero']:
            # A revocation row itself has no "grant lifetime" of its own to report - the
            # duration of the grant IT ended is already on the PRECEDING approval record.
            active_duration_seconds = None
            active_duration_ongoing = False
        else:
            active_duration_seconds = max(0.0, _seconds_between(now, event['timestamp']))
            active_duration_ongoing = True

        # Only set when the IMMEDIATE next event is an explicit approve(spender, 0) - a
        # precise metric, deliberately narrower than the broader REVOKED status label above
        # (which also covers "superseded by a changed nonzero amount").
        is_explicitly_revoked_next = next_event is not None and next_event['is_zero'] and not event['is_zero']
        seconds_to_explicit_revocation = (
            _seconds_between(next_event['timestamp'], event['timestamp']) if is_explicitly_revoked_next else None
        )
        revocation_transaction_hash = next_event['transaction_hash'] if is_explicitly_revoked_next else None
        revocation_timestamp = next_event['timestamp'].isoformat() if is_explicitly_revoked_next else None

        if confirmed_usage:
            used_before_end = True
        elif plausible_unconfirmed_in_window:
            used_before_end = None
        else:
            used_before_end = False if not event['is_zero'] else None

        approval_records.append({
            'event_type': event['event_type'],
            'amount': event['amount'],
            'is_zero': event['is_zero'],
            'unlimited_basis': _unlimited_basis(event, unlimited_threshold),
            'is_unlimited_declared': event['is_unlimited_declared'],
            'sequence_status': sequence_status,
            'status': status,
            'active_duration_seconds': active_duration_seconds,
            'active_duration_ongoing': active_duration_ongoing,
            'seconds_to_explicit_revocation': seconds_to_explicit_revocation,
            'revocation_transaction_hash': revocation_transaction_hash,
            'revocation_timestamp': revocation_timestamp,
            'used_before_end': used_before_end,
            'timestamp': event['timestamp'].isoformat(),
            'transaction_hash': event['transaction_hash'],
            'block_number': event['block_number'],
            'permit_deadline': event['permit_deadline'],
            'permit_nonce': event['permit_nonce'],
            # Only the transferFrom rows matched to THIS SPECIFIC approval's window (see
            # usage_by_approval_index above) - used by correlate_approval_usage to build
            # first/last/count/total/destinations/hashes without re-deriving the matching.
            'linked_transfers': confirmed_usage,
        })

    latest_event = approvals_sorted[-1]
    current_status = 'revoked' if latest_event['is_zero'] else 'active'
    current_unlimited_basis = _unlimited_basis(latest_event, unlimited_threshold)
    ever_unlimited = any(record['unlimited_basis'] is not None for record in approval_records)

    transfer_from_count = len(linked_records)
    total_transferred_amount = sum(record['amount'] for record in linked_records)
    first_use_record = linked_records[0] if linked_records else None
    time_to_first_use_seconds = first_use_record['seconds_since_approval'] if first_use_record else None
    time_to_first_use_anomaly = bool(first_use_record and first_use_record['before_any_approval'])

    related_addresses = sorted({
        record['recipient'] for record in linked_records
        if record['recipient'] and _address_key(record['recipient']) != key[1]
    })

    owner_display = approvals_sorted[0]['owner']
    spender_display = approvals_sorted[0]['spender']
    token_display = next((event['token_address'] for event in approvals_sorted if event['token_address']), None)

    distinct_owner_count = spender_owner_counts.get(key[1], 1)
    spender_multi_owner = {'distinct_owner_count': distinct_owner_count} if distinct_owner_count > 1 else None

    risk_indicators: list[dict[str, Any]] = []
    if ever_unlimited and current_status == 'active' and transfer_from_count == 0:
        risk_indicators.append({
            'code': 'unlimited_never_used',
            'label': 'Neograničen allowance, nikad iskorišćen',
            'reasons': [
                'Bar jedan approve/permit u ovoj grupi ima neograničenu (ili vrlo veliku) dozvoljenu vrednost.',
                'U dostupnoj evidenciji nema nijedne transferFrom transakcije koja koristi ovu dozvolu.',
                'Dozvola je i dalje aktivna (nije opozvana approve(0) pozivom) - otvoren, neiskorišćen rizik.',
            ],
        })
    if any(
        record['seconds_since_approval'] is not None
        and record['seconds_since_approval'] <= rapid_use_seconds
        and record['preceding_approval_unlimited_basis'] is not None
        for record in linked_records
    ):
        risk_indicators.append({
            'code': 'unlimited_rapid_drain',
            'label': 'Neograničen allowance povučen ubrzo posle odobrenja',
            'reasons': [
                f'Bar jedna transferFrom transakcija je usledila unutar {rapid_use_seconds}s od odobrenja koje je bilo na snazi.',
                'Klasičan obrazac ice-phishing napada: žrtva potpiše/odobri, sredstva se povuku ubrzo zatim.',
                'Prag je podesiv parametar (rapid_use_seconds), ne fiksan forenzički standard.',
            ],
        })
    if current_status == 'revoked' and transfer_from_count > 0:
        risk_indicators.append({
            'code': 'revoked_after_use',
            'label': 'Opozvano tek posle korišćenja',
            'reasons': [
                'Poslednji approve/permit u ovoj grupi ima iznos 0 (opoziv).',
                'Bar jedna transferFrom transakcija je zabeležena pre opoziva - informativno, ne nužno otvoren rizik.',
            ],
        })
    if current_status == 'active' and transfer_from_count > 0:
        risk_indicators.append({
            'code': 'active_used_never_revoked',
            'label': 'Aktivna dozvola, već korišćena, nikad opozvana',
            'reasons': [
                'Dozvola je i dalje aktivna (nema approve(0) posle poslednjeg odobrenja).',
                'Bar jedna transferFrom transakcija je već iskoristila ovu dozvolu.',
            ],
        })
    if spender_multi_owner is not None:
        risk_indicators.append({
            'code': 'spender_multi_owner',
            'label': 'Spender adresu je odobrilo više različitih vlasnika',
            'reasons': [
                f'Ova spender adresa se pojavljuje kao odobreni trošilac za {distinct_owner_count} različitih owner adresa u ovoj evidenciji.',
                'Strukturni signal (moguć drainer/phishing ugovor sa više žrtava) - ne potvrda, treba dodatnu proveru.',
            ],
        })

    return {
        'owner': owner_display,
        'spender': spender_display,
        'token_address': token_display,
        'token_identified': token_display is not None,
        'current_status': current_status,
        'current_allowance_amount': latest_event['amount'],
        'current_unlimited_basis': current_unlimited_basis,
        'ever_unlimited': ever_unlimited,
        'approval_event_count': len(approval_records),
        'first_approval_timestamp': approvals_sorted[0]['timestamp'].isoformat(),
        'latest_approval_timestamp': latest_event['timestamp'].isoformat(),
        'approvals': approval_records,
        'transfer_from_count': transfer_from_count,
        'total_transferred_amount': total_transferred_amount,
        'linked_transfers': linked_records,
        'first_use_timestamp': first_use_record['timestamp'] if first_use_record else None,
        'time_to_first_use_seconds': time_to_first_use_seconds,
        'time_to_first_use_anomaly': time_to_first_use_anomaly,
        'related_addresses': related_addresses,
        'spender_multi_owner': spender_multi_owner,
        'risk_indicators': risk_indicators,
    }


def analyze_token_approvals(
    transactions: pd.DataFrame,
    target_address: str | None = None,
    unlimited_threshold: float = DEFAULT_UNLIMITED_THRESHOLD,
    rapid_use_seconds: int = DEFAULT_RAPID_USE_SECONDS,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Token Approval / Ice Phishing Analysis: extracts and classifies ERC-20
    approve()/EIP-2612 permit() grants and their transferFrom() usage from `transactions`
    (the case's cleaned, combined evidence - see module docstring for the exact optional
    columns this reads and why none of it is invented).

    Requires an EXACT (case-sensitive) address match when `target_address` is given -
    checked across sender/recipient/owner_address/spender_address, same convention as
    dex_swap_analysis.detect_dex_swaps/path_finding/behavioral_analysis - raises
    ValueError (the route layer turns this into a 404) rather than silently returning an
    empty result for a typo'd address.

    `now` (default: the real current UTC time) is ONLY used to measure how long a still-open
    approval has been active (see approvals[].active_duration_seconds /
    build_token_approval_history) - accepting it as a parameter (instead of always calling
    pd.Timestamp.now internally) makes that duration deterministic and testable, the same
    reason node_taint_series-style "as of now" figures elsewhere are usually avoided in this
    project's core algorithms; here it is unavoidable since the question itself ("how long
    has this been active") is inherently relative to the present moment.

    See TOKEN-APPROVAL-IMPLEMENTATION.md for the full field-by-field justification of what
    is read directly vs. derived vs. explicitly reported as unavailable.
    """
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in transactions.columns]
    if missing_columns:
        raise ValueError(f'Missing required columns: {", ".join(missing_columns)}')

    unlimited_threshold = max(MIN_UNLIMITED_THRESHOLD, min(MAX_UNLIMITED_THRESHOLD, float(unlimited_threshold)))
    rapid_use_seconds = max(MIN_RAPID_USE_SECONDS, min(MAX_RAPID_USE_SECONDS, int(rapid_use_seconds)))
    now = now if now is not None else pd.Timestamp.now(tz='UTC')

    frame = _prepare_frame(transactions)

    if target_address is not None and not _address_present(frame, target_address):
        raise ValueError(f'Adresa nije pronađena u evidenciji: {target_address}')

    approval_events, transfer_events, skipped_rows, unrecognized_event_type_values = _extract_approval_rows(frame)

    has_event_type_column = EVENT_TYPE_COLUMN in frame.columns
    token_address_declared = bool(frame[TOKEN_ADDRESS_COLUMN].map(_clean_text).notna().any()) if TOKEN_ADDRESS_COLUMN in frame.columns else False
    owner_address_declared = bool(frame[OWNER_ADDRESS_COLUMN].map(_clean_text).notna().any()) if OWNER_ADDRESS_COLUMN in frame.columns else False
    spender_address_declared = bool(frame[SPENDER_ADDRESS_COLUMN].map(_clean_text).notna().any()) if SPENDER_ADDRESS_COLUMN in frame.columns else False
    is_unlimited_declared_anywhere = bool(frame[IS_UNLIMITED_COLUMN].map(_parse_tri_state_bool).notna().any()) if IS_UNLIMITED_COLUMN in frame.columns else False
    block_number_declared = bool(frame[BLOCK_NUMBER_COLUMN].map(_parse_int).notna().any()) if BLOCK_NUMBER_COLUMN in frame.columns else False
    permit_fields_declared = (
        bool(frame[PERMIT_DEADLINE_COLUMN].map(_clean_text).notna().any()) if PERMIT_DEADLINE_COLUMN in frame.columns else False
    ) or (
        bool(frame[PERMIT_NONCE_COLUMN].map(_parse_int).notna().any()) if PERMIT_NONCE_COLUMN in frame.columns else False
    )

    notes: list[str] = []
    if not has_event_type_column:
        notes.append(
            'Evidencija nema kolonu "event_type" - nijedan red se ne može klasifikovati kao approve/permit/'
            'transferFrom. Ovo NIJE greška: projekat danas ne povlači approve/permit evente automatski sa lanca '
            '(vidi TOKEN-APPROVAL-IMPLEMENTATION.md #7) - ovi podaci moraju biti u uvezenoj evidenciji.'
        )
    if not token_address_declared:
        notes.append(
            'Kolona "token_address" nije popunjena ni u jednom redu - allowance-i se ne mogu pouzdano razdvojiti '
            'po tokenu ako isti (owner, spender) par ima dozvole za više tokena; tretiraju se kao jedna grupa '
            '"nepoznat token" po (owner, spender) paru.'
        )
    if not spender_address_declared:
        notes.append(
            'Kolona "spender_address" nije popunjena - transferFrom transakcije se ne mogu pouzdano povezati sa '
            'konkretnim odobrenjem (izlazna adresa transfera nije nužno isti entitet kao spender), pa ostaju '
            'neatribuirane (vidi unattributed_transfers).'
        )
    if not owner_address_declared:
        notes.append(
            'Kolona "owner_address" nije popunjena - vlasnik tokena je pretpostavljen kao sender_address reda. '
            'Ovo je tačno za approve(), ali NE mora biti tačno za permit() kad transakciju šalje neko drugi '
            '(relayer) u ime potpisnika.'
        )
    if not is_unlimited_declared_anywhere:
        notes.append(
            '"is_unlimited" nije deklarisano ni za jedan red - neograničena dozvola se prepoznaje isključivo '
            f'heuristikom po veličini iznosa (prag: {unlimited_threshold:g}), nikad potvrđeno.'
        )
    if not block_number_declared:
        notes.append(
            'Broj bloka nije dostupan ni za jedan red - projekat danas ne čuva ovo polje ni za jedan on-chain '
            'izvor (vidi TOKEN-APPROVAL-IMPLEMENTATION.md #7.4); prikazuje se samo kad ga evidencija sama nosi.'
        )
    if has_event_type_column and not permit_fields_declared and any(event['event_type'] == PERMIT_EVENT for event in approval_events):
        notes.append('permit_deadline/permit_nonce nisu popunjeni ni za jedan "permit" red u ovoj evidenciji.')

    # Case-wide signal (computed BEFORE any target_address filtering, so it reflects the
    # whole evidence scope even when the final response is narrowed to one address) - how
    # many DISTINCT owners approved each spender.
    spender_owner_keys: dict[str, set[str]] = {}
    for event in approval_events:
        spender_owner_keys.setdefault(event['spender_key'], set()).add(event['owner_key'])
    spender_owner_counts = {spender: len(owners) for spender, owners in spender_owner_keys.items()}

    approval_groups: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for event in approval_events:
        approval_groups.setdefault(_group_key(event), []).append(event)

    approval_groups_by_owner_spender: dict[tuple[str, str], list[tuple[str, str, str | None]]] = {}
    for key in approval_groups:
        approval_groups_by_owner_spender.setdefault((key[0], key[1]), []).append(key)

    linked_transfers_by_group: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    # Kept in RAW form (owner_key/spender_key/pd.Timestamp intact, not yet serialized) -
    # fed into _build_group_result so a group can tell "there IS a transferFrom that might
    # have used my allowance, but we structurally cannot confirm it" (STATUS_UNKNOWN) apart
    # from "there is genuinely no evidence of use at all" (STATUS_ACTIVE). See
    # TOKEN-APPROVAL-IMPLEMENTATION.md #13.3.
    unattributed_transfers_raw: list[dict[str, Any]] = []
    for transfer in transfer_events:
        matched_key, match_basis, reason = _match_transfer_to_group(transfer, approval_groups, approval_groups_by_owner_spender)
        if matched_key is None:
            unattributed_transfers_raw.append({**transfer, '_reason': reason})
            continue
        linked_transfers_by_group.setdefault(matched_key, []).append({**transfer, '_match_basis': match_basis})

    unattributed_by_owner_key: dict[str, list[dict[str, Any]]] = {}
    for entry in unattributed_transfers_raw:
        unattributed_by_owner_key.setdefault(entry['owner_key'], []).append(entry)

    unattributed_transfers = [
        {
            'owner': entry['owner'],
            'spender': entry['spender'],
            'recipient': entry['recipient'],
            'token_address': entry['token_address'],
            'amount': entry['amount'],
            'timestamp': entry['timestamp'].isoformat(),
            'transaction_hash': entry['transaction_hash'],
            'block_number': entry['block_number'],
            'reason': entry['_reason'],
        }
        for entry in unattributed_transfers_raw
    ]

    groups = [
        _build_group_result(
            key,
            events,
            linked_transfers_by_group.get(key, []),
            unattributed_by_owner_key.get(key[0], []),
            unlimited_threshold,
            rapid_use_seconds,
            spender_owner_counts,
            now,
        )
        for key, events in approval_groups.items()
    ]
    groups.sort(key=lambda group: group['latest_approval_timestamp'], reverse=True)

    if target_address is not None:
        groups = [group for group in groups if target_address in (group['owner'], group['spender'])]
        unattributed_transfers = [
            entry for entry in unattributed_transfers
            if target_address in (entry['owner'], entry['spender'], entry['recipient'])
        ]

    total_approve_events = sum(1 for event in approval_events if event['event_type'] == APPROVE_EVENT)
    total_permit_events = sum(1 for event in approval_events if event['event_type'] == PERMIT_EVENT)
    total_transferfrom_events = len(transfer_events)

    return {
        'address': target_address,
        'total_approval_events': len(approval_events),
        'total_approve_events': total_approve_events,
        'total_permit_events': total_permit_events,
        'total_transferfrom_events': total_transferfrom_events,
        'unattributed_transferfrom_count': len(unattributed_transfers),
        'unique_owners': len({event['owner_key'] for event in approval_events}),
        'unique_spenders': len({event['spender_key'] for event in approval_events}),
        'unique_tokens': len({event['token_key'] for event in approval_events if event['token_key']}),
        'groups': groups,
        'unattributed_transfers': unattributed_transfers,
        'skipped_row_count': len(skipped_rows),
        'unrecognized_event_type_values': unrecognized_event_type_values,
        'data_completeness': {
            'event_type_declared': has_event_type_column,
            'token_address_declared': token_address_declared,
            'owner_address_declared': owner_address_declared,
            'spender_address_declared': spender_address_declared,
            'is_unlimited_declared': is_unlimited_declared_anywhere,
            'block_number_declared': block_number_declared,
            'permit_fields_declared': permit_fields_declared,
            'notes': notes,
        },
        'unlimited_threshold': unlimited_threshold,
        'rapid_use_seconds': rapid_use_seconds,
        'disclaimer': (
            'Token Approval Analysis čita isključivo polja koja evidencija stvarno deklariše - ništa nije '
            'dekodirano sa lanca (projekat danas ne povlači Approval/permit evente automatski - vidi '
            'TOKEN-APPROVAL-IMPLEMENTATION.md #7). Neograničena dozvola, brzo povlačenje i "spender sa više '
            'vlasnika" su OZNAČENE HEURISTIKE, ne dokaz zloupotrebe - svaki nalaz nosi razlog na osnovu kog je '
            'izveden i treba dodatnu proveru pre bilo kakvog forenzičkog zaključka.'
        ),
    }


def build_token_approval_history(
    transactions: pd.DataFrame,
    address: str,
    unlimited_threshold: float = DEFAULT_UNLIMITED_THRESHOLD,
    rapid_use_seconds: int = DEFAULT_RAPID_USE_SECONDS,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Reconstructs the APPROVE -> allowance change -> eventual REVOCATION -> current
    status timeline for ONE address (as owner and/or spender), per
    TOKEN-APPROVAL-IMPLEMENTATION.md #13.

    Deliberately built ON TOP of analyze_token_approvals rather than re-implementing
    extraction/grouping/matching - same groups, same matching rules, so the history view
    and the general (optional-address) analysis can never silently disagree with each
    other. `address` is REQUIRED here (unlike analyze_token_approvals's optional one) -
    "history" only means something for a specific address; propagates the same ValueError
    (-> 404 at the route) for an address that never appears in the evidence at all.

    Returns the same `groups` analyze_token_approvals would (already scoped to `address`),
    PLUS a flat `history` list: every individual approve()/permit() event across ALL of
    that address's groups, sorted chronologically ascending (oldest first, reads top-to-
    bottom as a timeline) - each entry already carries token/spender/allowance/timestamp/
    tx hash/block/status/duration fields (see _build_group_result), so a caller does not
    need to re-flatten `groups` itself.
    """
    if not address or not address.strip():
        raise ValueError('Adresa je obavezna za istoriju Token Approval-a.')

    base = analyze_token_approvals(
        transactions,
        target_address=address,
        unlimited_threshold=unlimited_threshold,
        rapid_use_seconds=rapid_use_seconds,
        now=now,
    )

    history: list[dict[str, Any]] = []
    for group in base['groups']:
        if group['owner'] == address:
            role = 'owner'
        elif group['spender'] == address:
            role = 'spender'
        else:
            role = 'unknown'  # defensive - analyze_token_approvals already filters to owner/spender matches only

        for approval in group['approvals']:
            history.append({
                'owner': group['owner'],
                'spender': group['spender'],
                'role': role,
                'token_address': group['token_address'],
                'token_identified': group['token_identified'],
                'event_type': approval['event_type'],
                'allowance_amount': approval['amount'],
                'is_zero': approval['is_zero'],
                'unlimited_basis': approval['unlimited_basis'],
                'approval_timestamp': approval['timestamp'],
                'transaction_hash': approval['transaction_hash'],
                'block_number': approval['block_number'],
                'permit_deadline': approval['permit_deadline'],
                'permit_nonce': approval['permit_nonce'],
                'status': approval['status'],
                'active_duration_seconds': approval['active_duration_seconds'],
                'active_duration_ongoing': approval['active_duration_ongoing'],
                'seconds_to_explicit_revocation': approval['seconds_to_explicit_revocation'],
                'used_before_end': approval['used_before_end'],
            })

    history.sort(key=lambda entry: entry['approval_timestamp'])

    return {
        'address': address,
        'entry_count': len(history),
        'history': history,
        'groups': base['groups'],
        'unattributed_transfers': base['unattributed_transfers'],
        'data_completeness': base['data_completeness'],
        'unlimited_threshold': base['unlimited_threshold'],
        'rapid_use_seconds': base['rapid_use_seconds'],
        'disclaimer': base['disclaimer'],
    }


# Correlation status: OWNER -> APPROVAL -> SPENDER -> (eventual) transferFrom -> token
# transfer, per TOKEN-APPROVAL-IMPLEMENTATION.md #14. A COMBINABLE label, deliberately
# different in shape from build_token_approval_history's single-word per-approval `status`
# (ACTIVE/REVOKED/USED/UNKNOWN, §13): that one picks ONE word by precedence (USED wins over
# REVOKED so "used then revoked" still just reads USED); this one reports BOTH facts at
# once, because a correlation record's whole point is showing whether usage and revocation
# each happened, independently - "APPROVED + USED + REVOKED" is a real, meaningful state
# distinct from "APPROVED + REVOKED" (approved, revoked, NEVER used) that the single-word
# status cannot express. Built from the exact same underlying fields (used_before_end,
# seconds_to_explicit_revocation) as §13 - no new heuristic, only a different combination.
CORRELATION_APPROVED = 'APPROVED'
CORRELATION_UNKNOWN = 'UNKNOWN'


def _correlation_status(approval: dict[str, Any]) -> str:
    if approval['used_before_end'] is None:
        # Cannot rule out use (§13.3's UNKNOWN condition) - reporting "APPROVED" alone, or
        # "APPROVED + REVOKED", would silently assert "never used", which is exactly the
        # guess the request says not to make.
        return CORRELATION_UNKNOWN

    parts = [CORRELATION_APPROVED]
    if approval['used_before_end']:
        parts.append('USED')
    if approval['seconds_to_explicit_revocation'] is not None:
        parts.append('REVOKED')
    return ' + '.join(parts)


def _transfer_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        'amount': record['amount'],
        'timestamp': record['timestamp'],
        'transaction_hash': record['transaction_hash'],
        'block_number': record['block_number'],
        'recipient': record['recipient'],
    }


def correlate_approval_usage(
    transactions: pd.DataFrame,
    target_address: str | None = None,
    unlimited_threshold: float = DEFAULT_UNLIMITED_THRESHOLD,
    rapid_use_seconds: int = DEFAULT_RAPID_USE_SECONDS,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Correlates each individual approve()/permit() GRANT with its later transferFrom()
    usage - the OWNER -> APPROVAL -> SPENDER -> transferFrom -> token transfer chain from
    TOKEN-APPROVAL-IMPLEMENTATION.md #14. Built on top of analyze_token_approvals (§12) -
    same grouping/matching, no separate extraction logic to keep in sync (same discipline
    as build_token_approval_history, §13).

    One correlation record per NONZERO approve()/permit() row (an approve(spender, 0) row
    is a revocation ACT, not a grant to correlate usage against - it already appears as the
    `revocation_*` fields on the grant it ended, via seconds_to_explicit_revocation/
    revocation_transaction_hash/revocation_timestamp computed in _build_group_result).

    `target_address` is OPTIONAL (unlike build_token_approval_history's required address) -
    omitted, every grant in the evidence is correlated; given, scoped to grants where that
    address is the owner or spender (same convention as analyze_token_approvals).

    Every numeric/timestamp/hash field is read directly from already-extracted approval/
    transferFrom rows or a straightforward count/sum/min/max over them - nothing is
    inferred beyond what §12's matching already established, and status is UNKNOWN rather
    than guessed when usage cannot be confirmed (see _correlation_status).
    """
    base = analyze_token_approvals(
        transactions,
        target_address=target_address,
        unlimited_threshold=unlimited_threshold,
        rapid_use_seconds=rapid_use_seconds,
        now=now,
    )

    correlations: list[dict[str, Any]] = []
    for group in base['groups']:
        for approval in group['approvals']:
            if approval['is_zero']:
                continue  # a revocation call, not a grant - nothing to correlate usage against

            linked = approval['linked_transfers']
            first_transfer = linked[0] if linked else None
            last_transfer = linked[-1] if linked else None
            receiving_destinations = sorted({record['recipient'] for record in linked if record['recipient']})

            correlations.append({
                'owner': group['owner'],
                'spender': group['spender'],
                'token_address': group['token_address'],
                'token_identified': group['token_identified'],
                'approval_event_type': approval['event_type'],
                'approval_amount': approval['amount'],
                'unlimited_basis': approval['unlimited_basis'],
                'approval_timestamp': approval['timestamp'],
                'approval_transaction_hash': approval['transaction_hash'],
                'approval_block_number': approval['block_number'],
                'status': _correlation_status(approval),
                'used': approval['used_before_end'],
                'revoked': approval['seconds_to_explicit_revocation'] is not None,
                'revocation_timestamp': approval['revocation_timestamp'],
                'revocation_transaction_hash': approval['revocation_transaction_hash'],
                'seconds_to_revocation': approval['seconds_to_explicit_revocation'],
                'transfer_from_count': len(linked),
                'total_amount_transferred': sum(record['amount'] for record in linked),
                'first_use_timestamp': first_transfer['timestamp'] if first_transfer else None,
                'time_to_first_use_seconds': first_transfer['seconds_since_approval'] if first_transfer else None,
                'first_transfer_from': _transfer_summary(first_transfer),
                'last_transfer_from': _transfer_summary(last_transfer),
                'receiving_destinations': receiving_destinations,
                'transaction_hashes': {
                    'approval': approval['transaction_hash'],
                    'revocation': approval['revocation_transaction_hash'],
                    'transfer_from': [record['transaction_hash'] for record in linked if record['transaction_hash']],
                },
            })

    correlations.sort(key=lambda entry: entry['approval_timestamp'])

    return {
        'address': target_address,
        'correlation_count': len(correlations),
        'correlations': correlations,
        'unattributed_transfers': base['unattributed_transfers'],
        'data_completeness': base['data_completeness'],
        'unlimited_threshold': base['unlimited_threshold'],
        'rapid_use_seconds': base['rapid_use_seconds'],
        'disclaimer': base['disclaimer'],
    }
