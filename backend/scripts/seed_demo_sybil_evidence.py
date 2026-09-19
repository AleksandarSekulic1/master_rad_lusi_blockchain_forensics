from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.paths import CASES_DIR, RAW_DIR
from app.services.case_management import append_evidence, get_case, require_open_case, store_case_evidence, update_case


DEMO_CASE_ID = '46ae7f91db9b'

# One file, four deliberately isolated groups, each demonstrating exactly ONE thing about
# the Sybil & Bot Network Analysis heuristic (see SYBIL-ANALIZA.md for the numbers this
# produces). Seeded directly (bypassing /upload/csv), same as every other demo seed script
# here, purely so the `function_name` column survives untouched (it does with a normal
# upload too - this just keeps every demo evidence file seeded the same way).
#
# Group 1 (rows 0-4): FLAGGED - 5 different addresses call 0xAirdropClaimContract's
#   'claimAirdrop' function within ~80 seconds, all sending the same 0-value "claim" tx
#   (amount 0.0001, identical) - classic airdrop-farming bot network shape. Should score
#   high/critical.
# Group 2 (rows 5-6): NOT flagged - only 2 addresses (below the default min_addresses=3).
# Group 3 (rows 7-9): NOT flagged - 3 addresses call the same contract, but hours apart
#   (outside the default 300s window) - ordinary, unrelated usage over time.
# Group 4 (rows 10-12): FLAGGED, recurrence demo - 0xBotWallet1/2/3 (three of the SAME
#   addresses from Group 1) also call a SECOND contract (0xMintContract) together in a
#   separate synchronized burst - repeated_address_count > 0 on both clusters, showing the
#   same cohort acting together more than once.
FILE_NAME = 'demo_sybil_analysis.csv'
CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp,tx_hash,function_name\n'
    '0xBotWallet1,0xAirdropClaimContract,0.0001,2026-09-01T10:00:00Z,0xsyb0001,claimAirdrop\n'
    '0xBotWallet2,0xAirdropClaimContract,0.0001,2026-09-01T10:00:15Z,0xsyb0002,claimAirdrop\n'
    '0xBotWallet3,0xAirdropClaimContract,0.0001,2026-09-01T10:00:32Z,0xsyb0003,claimAirdrop\n'
    '0xBotWallet4,0xAirdropClaimContract,0.0001,2026-09-01T10:00:48Z,0xsyb0004,claimAirdrop\n'
    '0xBotWallet5,0xAirdropClaimContract,0.0001,2026-09-01T10:01:05Z,0xsyb0005,claimAirdrop\n'
    '0xRegularUserA,0xAirdropClaimContract,0.0001,2026-09-02T09:00:00Z,0xsyb0006,claimAirdrop\n'
    '0xRegularUserB,0xAirdropClaimContract,0.0001,2026-09-02T09:00:20Z,0xsyb0007,claimAirdrop\n'
    '0xSlowUserA,0xVoteContract,1,2026-09-03T08:00:00Z,0xsyb0008,vote\n'
    '0xSlowUserB,0xVoteContract,1,2026-09-03T11:00:00Z,0xsyb0009,vote\n'
    '0xSlowUserC,0xVoteContract,1,2026-09-03T15:00:00Z,0xsyb0010,vote\n'
    '0xBotWallet1,0xMintContract,0.05,2026-09-04T12:00:00Z,0xsyb0011,mint\n'
    '0xBotWallet2,0xMintContract,0.05,2026-09-04T12:00:18Z,0xsyb0012,mint\n'
    '0xBotWallet3,0xMintContract,0.05,2026-09-04T12:00:36Z,0xsyb0013,mint\n'
)


def _remove_existing_evidence(case: dict[str, object], file_name: str) -> None:
    """Evidence is supposed to be immutable once hashed - "extending" a demo file means
    retiring the old entry (and its stored bytes) and re-submitting the new content under
    a fresh stored_name/hash/audit-log entry. Mirrors seed_demo_dex_swap_evidence.py's own
    helper."""
    existing = next((entry for entry in case.get('evidence', []) if entry.get('file_name') == file_name), None)
    if existing is None:
        return

    old_stored_name = str(existing['stored_name'])
    for path in (RAW_DIR / old_stored_name, CASES_DIR / DEMO_CASE_ID / 'evidence' / old_stored_name):
        if path.exists():
            path.unlink()

    case['evidence'] = [entry for entry in case['evidence'] if entry.get('file_name') != file_name]
    update_case(case)
    print(f'Removed previous "{file_name}" ({old_stored_name}) to replace it.')


def _seed_file(case: dict[str, object], file_name: str, csv_content: str) -> None:
    existing = next((entry for entry in case.get('evidence', []) if entry.get('file_name') == file_name), None)
    if existing is not None:
        old_path = RAW_DIR / str(existing['stored_name'])
        if old_path.exists() and old_path.read_text(encoding='utf-8') == csv_content:
            print(f'"{file_name}" already has the expected content - nothing to do.')
            return

    _remove_existing_evidence(case, file_name)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    stored_name = f'{timestamp}_{file_name}'
    stored_path = RAW_DIR / stored_name
    stored_path.write_text(csv_content, encoding='utf-8')

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size

    store_case_evidence(DEMO_CASE_ID, stored_path, stored_name)
    evidence_entry = append_evidence(
        DEMO_CASE_ID,
        original_name=file_name,
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        analyst='admin',
        currency=None,
    )
    write_audit_log(
        file_name=stored_name,
        sha256_hash=sha256_hash,
        action='csv_upload',
        user='admin',
        case_id=DEMO_CASE_ID,
    )
    print(f'Added "{file_name}" as evidence {stored_name} to case {DEMO_CASE_ID}.')
    print('case_evidence_count=', evidence_entry['case']['evidence_count'])


def main() -> None:
    require_open_case(DEMO_CASE_ID)
    _seed_file(get_case(DEMO_CASE_ID), FILE_NAME, CSV_CONTENT)


if __name__ == '__main__':
    main()
