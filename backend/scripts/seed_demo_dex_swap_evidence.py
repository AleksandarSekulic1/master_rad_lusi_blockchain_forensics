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

# One file, six deliberately isolated pairs, each demonstrating exactly ONE thing about
# the DEX Swap Analysis heuristic (see DEX-SWAP-ANALIZA.md #6 for the numbers this
# produces). Seeded directly (bypassing the /upload/csv single-currency-per-file guard,
# same as every other demo seed script here) because this file needs more than one
# currency to demonstrate the heuristic at all - see DEX-SWAP-ANALIZA.md #2 for why the
# normal upload path cannot accept a file like this yet.
#
# Pair 1 (rows 1-2): Detected Swap - same tx_hash on both legs, different tokens.
# Pair 2 (rows 3-4): Potential Swap - no shared hash, different tokens, 70s apart.
# Pair 3 (rows 5-6): NOT flagged - same token (ETH) on both legs.
# Pair 4 (rows 7-8): NOT flagged at the default 5-minute window - 2 hours apart
#   (still recoverable by widening max_gap_seconds, see DEX-SWAP-ANALIZA.md #6).
# Pair 5 (rows 9-10): NOT flagged - the outgoing leg's counterpart returns to a
#   DIFFERENT address (0xOtherWallet), not back to the original sender.
# Pair 6 (rows 11-12): NOT flagged - ordinary bounce between two plain wallets, neither
#   of which is a recognized (known-address or keyword) DEX node at all.
FILE_NAME = 'demo_dex_swap_analysis.csv'
CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp,currency,tx_hash\n'
    '0xInvestorWallet,0xUniswapRouter,10,2026-08-24T09:00:00Z,ETH,0xswap0001\n'
    '0xUniswapRouter,0xInvestorWallet,25000,2026-08-24T09:00:00Z,USDC,0xswap0001\n'
    '0xInvestorWallet,0xUniswapRouter,2,2026-08-25T11:15:00Z,ETH,\n'
    '0xUniswapRouter,0xInvestorWallet,3200,2026-08-25T11:16:10Z,DAI,\n'
    '0xInvestorWallet,0xUniswapRouter,1,2026-08-27T14:00:00Z,ETH,\n'
    '0xUniswapRouter,0xInvestorWallet,0.98,2026-08-27T14:00:30Z,ETH,\n'
    '0xInvestorWallet,0xSushiRouter,4,2026-08-28T16:00:00Z,ETH,\n'
    '0xSushiRouter,0xInvestorWallet,6000,2026-08-28T18:00:00Z,USDT,\n'
    '0xInvestorWallet,0xSushiRouter,3,2026-08-29T09:00:00Z,ETH,\n'
    '0xSushiRouter,0xOtherWallet,4500,2026-08-29T09:01:00Z,USDC,\n'
    '0xInvestorWallet,0xPeerWalletB,100,2026-08-30T08:00:00Z,USDC,\n'
    '0xPeerWalletB,0xInvestorWallet,100,2026-08-30T08:01:00Z,USDC,\n'
)


def _remove_existing_evidence(case: dict[str, object], file_name: str) -> None:
    """Evidence is supposed to be immutable once hashed - "extending" a demo file means
    retiring the old entry (and its stored bytes) and re-submitting the new content under
    a fresh stored_name/hash/audit-log entry. Mirrors
    seed_demo_behavioral_evidence.py's own helper."""
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
        # Deliberately NOT a single currency - see the module docstring above. Left None
        # rather than guessing one label for a genuinely mixed-currency file.
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
