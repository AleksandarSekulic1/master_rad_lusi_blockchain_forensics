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

# Two files, same address (0xNightOwlWallet), so the Behavioral Analysis walkthrough
# (BEHAVIORAL-ANALIZA.md §7.2) can show BOTH a clean result and the "one outlier widens
# Active period" limitation, using the app's own evidence picker rather than two uploads:
#
# 1. demo_behavioral_analysis.csv (10 rows) - a tight nocturnal pattern, entirely inside
#    02:00-04:00 UTC, heaviest on Wednesday at 03:00 - selecting JUST this file shows the
#    "normal" case: a narrow, meaningful Active period.
# 2. demo_behavioral_analysis_outlier.csv (1 row) - a single unrelated daytime (14:00 UTC)
#    transaction for the same address. Selecting "Sve transakcije (kombinovano)" (which
#    always includes every evidence file in the case) pulls this row in too, and Active
#    period widens from 02:00-04:00 to 02:00-14:00 - demonstrating that it is an envelope
#    over ALL activity, not a "typical window" estimate (see BEHAVIORAL-ANALIZA.md §9).
#
# Every number in BEHAVIORAL-ANALIZA.md §7.2 was computed by actually running
# analyze_time_of_day() over this exact content, not by hand - see the docstring there.
CORE_FILE_NAME = 'demo_behavioral_analysis.csv'
CORE_CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp\n'
    '0xNightOwlWallet,0xExchangeCounterparty,50,2026-08-24T02:15:00Z\n'
    '0xPeerWalletA,0xNightOwlWallet,30,2026-08-24T03:05:00Z\n'
    '0xNightOwlWallet,0xExchangeCounterparty,45,2026-08-25T03:10:00Z\n'
    '0xNightOwlWallet,0xPeerWalletB,20,2026-08-26T02:40:00Z\n'
    '0xNightOwlWallet,0xExchangeCounterparty,60,2026-08-26T03:00:00Z\n'
    '0xPeerWalletA,0xNightOwlWallet,25,2026-08-26T03:20:00Z\n'
    '0xNightOwlWallet,0xPeerWalletB,15,2026-08-26T04:00:00Z\n'
    '0xNightOwlWallet,0xExchangeCounterparty,55,2026-08-27T03:30:00Z\n'
    '0xPeerWalletA,0xNightOwlWallet,35,2026-08-28T02:50:00Z\n'
    '0xNightOwlWallet,0xExchangeCounterparty,40,2026-08-29T03:15:00Z\n'
)

OUTLIER_FILE_NAME = 'demo_behavioral_analysis_outlier.csv'
OUTLIER_CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp\n'
    '0xNightOwlWallet,0xExchangeCounterparty,10,2026-08-25T14:00:00Z\n'
)


def _remove_existing_evidence(case: dict[str, object], file_name: str) -> None:
    """Evidence is supposed to be immutable once hashed - "extending" a demo file means
    retiring the old entry (and its stored bytes) and re-submitting the new content under
    a fresh stored_name/hash/audit-log entry, not silently overwriting bytes behind an
    unchanged hash. Mirrors seed_demo_taint_evidence.py's own helper."""
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
    _seed_file(get_case(DEMO_CASE_ID), CORE_FILE_NAME, CORE_CSV_CONTENT)
    # Re-fetch: _seed_file may have just appended the core file above, and the evidence
    # list on the in-memory `case` dict would otherwise be stale for this second call.
    _seed_file(get_case(DEMO_CASE_ID), OUTLIER_FILE_NAME, OUTLIER_CSV_CONTENT)


if __name__ == '__main__':
    main()
