from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evidence.audit_log import write_audit_log
from app.evidence.hashing import calculate_sha256
from app.paths import RAW_DIR
from app.services.case_management import append_evidence, get_case, require_open_case, store_case_evidence


DEMO_CASE_ID = '46ae7f91db9b'
FILE_NAME = 'demo_taint_dilution.csv'

# Same numbers verified by hand and by the taint_analysis smoke test: 0xThief seeds 1000
# (fully tainted), 0xMixer also receives 500 unrelated/clean funds, then forwards 750
# onward - expected taint at the mixer and at 0xExitWallet is exactly 1000/1500 = 66.67%.
CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp\n'
    '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n'
    '0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z\n'
    '0xMixer,0xExitWallet,750,2026-03-01T00:10:00Z\n'
)


def main() -> None:
    case = get_case(DEMO_CASE_ID)
    already_present = any(entry.get('file_name') == FILE_NAME for entry in case.get('evidence', []))
    if already_present:
        print(f'"{FILE_NAME}" is already attached to case {DEMO_CASE_ID} - nothing to do.')
        return

    require_open_case(DEMO_CASE_ID)

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

    print(f'Added "{FILE_NAME}" as evidence {stored_name} to case {DEMO_CASE_ID}.')
    print('case_evidence_count=', evidence_entry['case']['evidence_count'])


if __name__ == '__main__':
    main()
