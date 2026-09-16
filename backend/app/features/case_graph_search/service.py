"""Writes a case's transaction graph into Neo4j and answers "neighborhood of an address"
queries against it - the one capability this pilot exists to demonstrate: a bounded,
variable-length graph traversal (`(a)-[:TRANSACTED*1..N]-(b)`), which is a single Cypher
query here versus a hand-rolled bounded BFS in `app.analytics.path_finding`.

Everything else in the app keeps using its NetworkX graph exactly as before - see
PREDLOG-GRAF-SUBP.md for why this stays a small, additive slice rather than a replacement.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.analytics.case_graph import clean_evidence_frames
from app.evidence.tx_identity import transaction_id
from app.shared.graph_db import get_driver

_ENSURE_CONSTRAINT_CYPHER = """
CREATE CONSTRAINT case_address_unique IF NOT EXISTS
FOR (a:Address) REQUIRE (a.case_id, a.address) IS UNIQUE
"""

_WIPE_CASE_CYPHER = 'MATCH (a:Address {case_id: $case_id}) DETACH DELETE a'

_WRITE_ROWS_CYPHER = """
UNWIND $rows AS row
MERGE (a:Address {case_id: row.case_id, address: row.sender})
MERGE (b:Address {case_id: row.case_id, address: row.recipient})
MERGE (a)-[t:TRANSACTED {tx_id: row.tx_id}]->(b)
SET t.amount = row.amount, t.currency = row.currency, t.timestamp = row.timestamp
"""


def _rows_from_evidence(case_id: str, evidence_paths: list[tuple[dict[str, object], Path]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry, frame in clean_evidence_frames(evidence_paths):
        stored_name = str(entry.get('stored_name') or '')
        for row in frame.to_dict('records'):
            amount = row.get('amount')
            timestamp = row.get('timestamp')
            rows.append({
                'case_id': case_id,
                'sender': row.get('sender_address'),
                'recipient': row.get('recipient_address'),
                'amount': float(amount) if pd.notna(amount) else None,
                'currency': row.get('currency'),
                'timestamp': timestamp.isoformat() if pd.notna(timestamp) else None,
                # Same identifier every other chain-of-custody record in this project uses
                # (app.evidence.tx_identity.transaction_id) - one tx_id scheme everywhere,
                # including here.
                'tx_id': transaction_id(row, stored_name),
            })
    return rows


def sync_case_graph(case_id: str, evidence_paths: list[tuple[dict[str, object], Path]]) -> int:
    """(Re)writes one case's graph into Neo4j from its cleaned evidence - the SAME cleaned
    frames every other analysis reads, so the graph in Neo4j always matches what the rest
    of the app already shows. Existing nodes/edges for this case are wiped first: this is
    a mirror rebuilt on demand, not an accumulating history - exactly like the in-memory
    NetworkX graph is rebuilt fresh on every request today. Returns rows written.
    """
    rows = _rows_from_evidence(case_id, evidence_paths)

    driver = get_driver()
    with driver.session() as session:
        session.run(_ENSURE_CONSTRAINT_CYPHER)
        session.run(_WIPE_CASE_CYPHER, case_id=case_id)
        if rows:
            session.run(_WRITE_ROWS_CYPHER, rows=rows)
    return len(rows)


def neighborhood(case_id: str, address: str, max_hops: int) -> list[dict[str, object]] | None:
    """Every address reachable from `address` within `max_hops` TRANSACTED steps
    (direction-agnostic - "connected to", not "received from"), closest first.

    Returns None when `address` itself isn't in this case's graph (the router turns that
    into a 404, same convention as every other case analysis). `max_hops` is bounded by
    the router (Query(..., ge=1, le=5)) before it ever reaches here, so inlining it into
    the Cypher pattern below is safe - Neo4j does not accept a bound parameter inside a
    relationship's `*min..max` quantifier, only a literal integer.
    """
    query = f"""
    MATCH (start:Address {{case_id: $case_id, address: $address}})
    OPTIONAL MATCH path = (start)-[:TRANSACTED*1..{max_hops}]-(other:Address {{case_id: $case_id}})
    WHERE other IS NOT NULL AND other <> start
    WITH start, other, min(length(path)) AS hops
    RETURN other.address AS address, hops
    ORDER BY hops, address
    """
    driver = get_driver()
    with driver.session() as session:
        exists = session.run(
            'MATCH (a:Address {case_id: $case_id, address: $address}) RETURN a LIMIT 1',
            case_id=case_id, address=address,
        ).single()
        if exists is None:
            return None

        records = session.run(query, case_id=case_id, address=address)
        return [{'address': record['address'], 'hops': record['hops']} for record in records if record['address'] is not None]
