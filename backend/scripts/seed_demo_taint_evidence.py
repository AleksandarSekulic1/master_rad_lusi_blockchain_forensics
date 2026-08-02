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
FILE_NAME = 'demo_taint_dilution.csv'

# Original dilution scenario (0xThief seeds 1000, 0xCleanUser adds 500 unrelated/clean,
# 0xMixer forwards 750 onward - 66.67% at the mixer/exit wallet with 0xThief alone as
# seed) PLUS a second, independent scenario appended below it: two DISTINCT dirty
# sources (not one dirty + one clean) converging into the same hub. Seed 0xHacker1 +
# 0xHacker2 together to see the proportional split show up on the graph - verified by
# hand and by run_taint_analysis directly: 0xLaunderingHub ends up 100% tainted, split
# exactly 60/40, and the 0xLaunderingHub -> 0xFinalDestination hop carries that same
# 60/40 split, so the arrow between them reads "60%+40%" instead of one plain number.
# The two scenarios don't share any address, so testing one never affects the other -
# the original single-seed (0xThief) numbers documented in BLOCKCHAIN-UVOZ.md still hold
# exactly as before.
CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp\n'
    '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n'
    '0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z\n'
    '0xMixer,0xExitWallet,750,2026-03-01T00:10:00Z\n'
    '0xHacker1,0xLaunderingHub,600,2026-04-01T00:00:00Z\n'
    '0xHacker2,0xLaunderingHub,400,2026-04-01T00:05:00Z\n'
    '0xLaunderingHub,0xFinalDestination,800,2026-04-01T00:10:00Z\n'
)


def _remove_existing_evidence(case: dict[str, object]) -> None:
    """Evidence is supposed to be immutable once hashed - "extending" the demo file means
    retiring the old entry (and its stored bytes) and re-submitting the new content under
    a fresh stored_name/hash/audit-log entry, not silently overwriting bytes behind an
    unchanged hash."""
    existing = next((entry for entry in case.get('evidence', []) if entry.get('file_name') == FILE_NAME), None)
    if existing is None:
        return

    old_stored_name = str(existing['stored_name'])
    for path in (RAW_DIR / old_stored_name, CASES_DIR / DEMO_CASE_ID / 'evidence' / old_stored_name):
        if path.exists():
            path.unlink()

    case['evidence'] = [entry for entry in case['evidence'] if entry.get('file_name') != FILE_NAME]
    update_case(case)
    print(f'Removed previous "{FILE_NAME}" ({old_stored_name}) to replace it with the extended version.')


def main() -> None:
    require_open_case(DEMO_CASE_ID)
    case = get_case(DEMO_CASE_ID)

    existing = next((entry for entry in case.get('evidence', []) if entry.get('file_name') == FILE_NAME), None)
    if existing is not None:
        old_path = RAW_DIR / str(existing['stored_name'])
        if old_path.exists() and old_path.read_text(encoding='utf-8') == CSV_CONTENT:
            print(f'"{FILE_NAME}" already has the extended content - nothing to do.')
            return

    _remove_existing_evidence(case)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    stored_name = f'{timestamp}_{FILE_NAME}'
    stored_path = RAW_DIR / stored_name
    stored_path.write_text(CSV_CONTENT, encoding='utf-8')

    sha256_hash = calculate_sha256(stored_path)
    size_bytes = stored_path.stat().st_size

    store_case_evidence(DEMO_CASE_ID, stored_path, stored_name)
    evidence_entry = append_evidence(
        DEMO_CASE_ID,
        original_name=FILE_NAME,
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

    print(f'Added extended "{FILE_NAME}" as evidence {stored_name} to case {DEMO_CASE_ID}.')
    print('case_evidence_count=', evidence_entry['case']['evidence_count'])


if __name__ == '__main__':
    main()
