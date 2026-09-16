"""Helpers specific to writing a Token Approval Analysis run into the chain of custody.

Unlike Taint/Graph/Pathfinding/Behavioral/DEX Swap, a Token Approval run additionally
attaches a structured `token_approval_evidence` item to each individual approve()/
permit() row's own custody entry, on top of the generic per-transaction/per-evidence-file
record every other analysis already gets from
`app.shared.custody_recording.record_custody_access`. This module builds that extra piece;
the router wires it into `record_custody_access` via its `extra_transaction_fields`
parameter.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.token_approval_analysis import EVIDENCE_STORED_NAME_COLUMN


def combine_frames_with_evidence_tag(per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]]) -> pd.DataFrame:
    """Same concatenation as app.analytics.case_graph.combine_frames, plus one extra
    internal column (token_approval_analysis.EVIDENCE_STORED_NAME_COLUMN) so
    correlate_approval_usage can compute a stable `tx_id` per approval row even after its
    own chronological re-sort (pandas keeps every column aligned with its row through a
    sort - see TOKEN-APPROVAL-IMPLEMENTATION.md #18.2).

    Deliberately duplicated rather than calling combine_frames() and adding the column
    after the fact: combine_frames() is shared by Graph/Taint/Pathfinding/Behavioral/DEX
    Swap, and its output shape must stay exactly what it is today for all of them - this
    tagged variant is used ONLY when writing to the chain of evidence (see
    run_case_token_approval_analysis in router.py), never by any other analysis or by
    Token Approval's own read-only GET routes.
    """
    if not per_evidence_frames:
        return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', EVIDENCE_STORED_NAME_COLUMN])

    tagged_frames = []
    for entry, frame in per_evidence_frames:
        tagged = frame.copy()
        tagged[EVIDENCE_STORED_NAME_COLUMN] = str(entry.get('stored_name') or '')
        tagged_frames.append(tagged)
    return pd.concat(tagged_frames, ignore_index=True)


def _token_approval_group_key(owner: str, spender: str, token: str | None) -> tuple[str, str, str]:
    return (owner.lower(), spender.lower(), (token or '').lower())


def token_approval_summary_counts(result: dict[str, object]) -> dict[str, int]:
    """The six SUMMARY counters (§16.5/§19) computed once, from the SAME
    correlate_approval_usage() result already returned to the caller - shared by the
    audit log entry (router.py) and available to the frontend directly on `result` itself,
    so the log and the on-screen summary can never disagree about what a run found.
    """
    correlations = list(result.get('correlations', []))  # type: ignore[arg-type]
    groups_by_key = {
        _token_approval_group_key(str(group['owner']), str(group['spender']), group.get('token_address')): group
        for group in result.get('groups', [])  # type: ignore[union-attr]
    }

    def risk_level_for(entry: dict[str, object]) -> str:
        group = groups_by_key.get(_token_approval_group_key(str(entry['owner']), str(entry['spender']), entry.get('token_address')))
        return str(group['risk_level']) if group else 'LOW'

    return {
        'total_approvals': len(correlations),
        'unlimited_approvals': sum(1 for entry in correlations if entry.get('unlimited_basis')),
        'active_approvals': sum(1 for entry in correlations if not entry.get('revoked')),
        'revoked_approvals': sum(1 for entry in correlations if entry.get('revoked')),
        'used_approvals': sum(1 for entry in correlations if entry.get('used') is True),
        'unused_approvals': sum(1 for entry in correlations if entry.get('used') is False),
        'potentially_risky_approvals': sum(1 for entry in correlations if risk_level_for(entry) != 'LOW'),
    }


def token_approval_custody_enrichment(full_result: dict[str, object]) -> dict[str, dict[str, object]]:
    """Builds the `extra_transaction_fields` map `record_custody_access` merges into one
    specific transaction's custody row - keyed by `tx_id` (see
    combine_frames_with_evidence_tag above), one entry per approve()/permit() grant that
    `correlate_approval_usage` (called with a TAGGED, unfiltered frame) could actually
    identify a transaction for. A grant without a `tx_id` (should not happen once the frame
    is tagged, since every approval row has sender/recipient/amount/timestamp) is skipped
    rather than guessed at - same "don't invent an identity" discipline as
    app.evidence.tx_identity itself.

    Each entry is `TOKEN_APPROVAL` evidence, explicitly split into three groups so a reader
    of the chain of evidence can never mistake one kind of claim for another (see
    TOKEN-APPROVAL-IMPLEMENTATION.md #18.1):
      - `blockchain_facts` - read directly from the evidence, no interpretation.
      - `computed_indicators` - deterministically derived by matching/counting/summing
        (status, usage, revocation, transferFrom totals) - not guessed, but not present in
        the raw data either.
      - `heuristic_conclusions` - the risk assessment (TOKEN-APPROVAL-IMPLEMENTATION.md
        #15) - always the most speculative layer, always carries its own disclaimer.
    """
    groups_by_key = {
        _token_approval_group_key(str(group['owner']), str(group['spender']), group.get('token_address')): group
        for group in full_result.get('groups', [])  # type: ignore[union-attr]
    }

    enrichment: dict[str, dict[str, object]] = {}
    for entry in full_result.get('correlations', []):  # type: ignore[union-attr]
        tx_id = entry.get('tx_id')
        if not tx_id:
            continue

        group = groups_by_key.get(_token_approval_group_key(str(entry['owner']), str(entry['spender']), entry.get('token_address')))
        risk_level = group['risk_level'] if group else 'LOW'
        risk_score = group['risk_score'] if group else 0
        risk_indicators = group['risk_indicators'] if group else []

        enrichment[str(tx_id)] = {
            'token_approval_evidence': {
                'type': 'TOKEN_APPROVAL',
                'blockchain_facts': {
                    'owner': entry.get('owner'),
                    'spender': entry.get('spender'),
                    'token_contract': entry.get('token_address'),
                    'event_type': entry.get('approval_event_type'),
                    'allowance_amount': entry.get('approval_amount'),
                    'approval_timestamp': entry.get('approval_timestamp'),
                    'approval_transaction_hash': entry.get('approval_transaction_hash'),
                    'approval_block_number': entry.get('approval_block_number'),
                },
                'computed_indicators': {
                    'status': entry.get('status'),
                    'used': entry.get('used'),
                    'revoked': entry.get('revoked'),
                    'revocation_timestamp': entry.get('revocation_timestamp'),
                    'revocation_transaction_hash': entry.get('revocation_transaction_hash'),
                    'seconds_to_revocation': entry.get('seconds_to_revocation'),
                    'transfer_from_count': entry.get('transfer_from_count'),
                    'total_amount_transferred': entry.get('total_amount_transferred'),
                    'first_transfer_from': entry.get('first_transfer_from'),
                    'last_transfer_from': entry.get('last_transfer_from'),
                    'receiving_destinations': entry.get('receiving_destinations'),
                },
                'heuristic_conclusions': {
                    'allowance_label': 'UNLIMITED' if entry.get('unlimited_basis') else 'FINITE',
                    'unlimited_basis': entry.get('unlimited_basis'),
                    'risk_level': risk_level,
                    'risk_score': risk_score,
                    'risk_indicators': risk_indicators,
                },
                'disclaimer': full_result.get('disclaimer'),
            },
        }

    return enrichment
