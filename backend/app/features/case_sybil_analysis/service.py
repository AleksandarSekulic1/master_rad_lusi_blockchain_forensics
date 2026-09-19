"""Helpers specific to writing a Sybil & Bot Network Analysis run into the chain of
custody.

Like Token Approval Analysis (see case_token_approval_analysis/service.py, the pattern
this mirrors), a Sybil run additionally attaches a structured `sybil_evidence` item to
each individual transaction's own custody entry that is part of a FLAGGED cluster - on top
of the generic per-transaction/per-evidence-file record every other analysis already gets
from `app.shared.custody_recording.record_custody_access`. This module builds that extra
piece; the router wires it into `record_custody_access` via its `extra_transaction_fields`
parameter.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.sybil_analysis import EVIDENCE_STORED_NAME_COLUMN


def combine_frames_with_evidence_tag(per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]]) -> pd.DataFrame:
    """Same concatenation as app.analytics.case_graph.combine_frames, plus one extra
    internal column (sybil_analysis.EVIDENCE_STORED_NAME_COLUMN) so detect_sybil_clusters
    can compute a stable `tx_id` per transaction even after its own chronological re-sort
    (pandas keeps every column aligned with its row through a sort/groupby).

    Deliberately duplicated rather than calling combine_frames() and adding the column
    after the fact: combine_frames() is shared by every other analysis, and its output
    shape must stay exactly what it is today for all of them - this tagged variant is used
    ONLY when writing to the chain of evidence (see run_case_sybil_analysis in router.py),
    never by the passive GET route or by sybil_analysis.py's own tests.
    """
    if not per_evidence_frames:
        return pd.DataFrame(columns=['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', EVIDENCE_STORED_NAME_COLUMN])

    tagged_frames = []
    for entry, frame in per_evidence_frames:
        tagged = frame.copy()
        tagged[EVIDENCE_STORED_NAME_COLUMN] = str(entry.get('stored_name') or '')
        tagged_frames.append(tagged)
    return pd.concat(tagged_frames, ignore_index=True)


def sybil_custody_enrichment(full_result: dict[str, object]) -> dict[str, dict[str, object]]:
    """Builds the `extra_transaction_fields` map `record_custody_access` merges into one
    specific transaction's custody row - keyed by `tx_id` (see
    combine_frames_with_evidence_tag above), one entry per transaction that
    detect_sybil_clusters (called with a TAGGED, unfiltered frame) placed inside a FLAGGED
    cluster. A transaction without a `tx_id` (should not happen once the frame is tagged)
    is skipped rather than guessed at - same "don't invent an identity" discipline as
    app.evidence.tx_identity itself.

    Each entry is `SYBIL_CLUSTER` evidence, explicitly split into exactly two groups per
    the requested design - never blurred into a third "in-between" category:
      - `blockchain_facts` - read directly from the transaction row: who sent what, to
        which contract, when, the transaction hash/block (when the evidence declares one),
        and the called function (when declared) - no interpretation.
      - `heuristic_conclusions` - everything Sybil & Bot Network Analysis concluded about
        the CLUSTER this transaction was placed into (how many addresses, how tight the
        timing, the risk score/level and the reasons behind it) - all of it stems from the
        heuristic's own grouping decision, so none of it is presented as fact.
    Every entry also carries the SAME disclaimer text the API response itself returns, so a
    reader of the raw custody log sees the caveat without having to cross-reference a
    separate API call.
    """
    enrichment: dict[str, dict[str, object]] = {}
    disclaimer = full_result.get('disclaimer')

    for cluster in full_result.get('clusters', []):  # type: ignore[union-attr]
        for tx in cluster.get('transactions', []):  # type: ignore[union-attr]
            tx_id = tx.get('tx_id')
            if not tx_id:
                continue

            enrichment[str(tx_id)] = {
                'sybil_evidence': {
                    'type': 'SYBIL_CLUSTER',
                    'blockchain_facts': {
                        'sender_address': tx.get('sender_address'),
                        'contract_address': tx.get('recipient_address'),
                        'amount': tx.get('amount'),
                        'timestamp': tx.get('timestamp'),
                        'transaction_hash': tx.get('tx_hash'),
                        'block_number': tx.get('block_number'),
                        'function_name': tx.get('function_name'),
                    },
                    'heuristic_conclusions': {
                        'cluster_id': cluster.get('cluster_id'),
                        'address_count': cluster.get('address_count'),
                        'activity_count': cluster.get('activity_count'),
                        'window_start': cluster.get('window_start'),
                        'window_end': cluster.get('window_end'),
                        'window_duration_seconds': cluster.get('window_duration_seconds'),
                        'identical_amount_ratio': cluster.get('identical_amount_ratio'),
                        'repeated_address_count': cluster.get('repeated_address_count'),
                        'risk_score': cluster.get('risk_score'),
                        'risk_level': cluster.get('risk_level'),
                        'reasons': cluster.get('reasons'),
                    },
                    'disclaimer': disclaimer,
                },
            }

    return enrichment
