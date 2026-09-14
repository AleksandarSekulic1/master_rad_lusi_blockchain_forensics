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

# One file, four independent (owner, spender[, token]) relationships, none sharing a
# spender/token combination in a way that would make one scenario's numbers depend on
# another - same discipline as seed_demo_dex_swap_evidence.py. See
# TOKEN-APPROVAL-ANALIZA.md's "Rucno testiranje" section for the exact expected result of
# analyzing each address below and why - this file is the data behind that walkthrough.
#
# Group 1 (rows 0-1): 0xVictimWallet approves 0xDrainerContract for 500000 USDC with
#   is_unlimited=true (DECLARED basis - the strongest signal), then 480000 of it is pulled
#   out via transferFrom only 12 minutes later (well inside the default 3600s
#   rapid_use_seconds window), to a THIRD address (0xDrainerWallet), never revoked
#   afterwards. Textbook ice-phishing drain: unlimited_rapid_drain + active_used_never_
#   revoked + unknown_spender + spender_multi_owner (see Group 2) -> HIGH risk.
# Group 2 (row 2): 0xVictimWallet2 approves the SAME 0xDrainerContract for 5e15 USDC with
#   no is_unlimited flag at all - unlimited only by MAGNITUDE heuristic
#   (potential_by_magnitude, the weaker of the two bases) - and never used yet. A second,
#   still-sleeping victim of the same contract: unlimited_never_used + unknown_spender +
#   spender_multi_owner -> HIGH risk despite nothing having moved yet.
# Group 3 (rows 3-5): 0xCarefulTrader approves a RECOGNIZED spender (0xUniswapRouter -
#   matches the DEX brand-keyword fallback classify_dex_node() already uses for demo
#   pseudo-addresses, see dex_swap_analysis.py) for an ordinary, non-unlimited amount,
#   uses the full allowance once, then explicitly revokes with approve(0). Deliberately
#   the "looks fine" control case: only revoked_after_use fires (informational, not an
#   open risk) -> LOW risk, for contrast against Groups 1-2.
# Groups 4a/4b (rows 6-7): 0xVictimWallet ALSO approved a second, unrecognized contract
#   (0xSweepContract) for TWO different tokens back-to-back, never used - the "sign here"
#   multi-token drainer pattern (multiple_tokens_same_spender + unknown_spender on both) ->
#   MEDIUM risk on each. Reusing 0xVictimWallet here (rather than a fifth address) means
#   analyzing 0xVictimWallet alone already surfaces THREE separate risky relationships at
#   once (Group 1 + both halves of Group 4), which is the point of the walkthrough.
FILE_NAME = 'demo_token_approval_evidence.csv'
CSV_CONTENT = (
    'sender_address,recipient_address,amount,timestamp,event_type,token_address,owner_address,spender_address,is_unlimited\n'
    '0xVictimWallet,0xDrainerContract,500000,2026-07-05T09:00:00Z,approve,0xUSDCContract,0xVictimWallet,0xDrainerContract,true\n'
    '0xVictimWallet,0xDrainerWallet,480000,2026-07-05T09:12:00Z,transferFrom,0xUSDCContract,0xVictimWallet,0xDrainerContract,\n'
    '0xVictimWallet2,0xDrainerContract,5000000000000000,2026-07-06T10:00:00Z,approve,0xUSDCContract,0xVictimWallet2,0xDrainerContract,\n'
    '0xCarefulTrader,0xUniswapRouter,2000,2026-07-07T08:00:00Z,approve,0xDAIContract,0xCarefulTrader,0xUniswapRouter,false\n'
    '0xCarefulTrader,0xUniswapRouter,2000,2026-07-07T10:30:00Z,transferFrom,0xDAIContract,0xCarefulTrader,0xUniswapRouter,\n'
    '0xCarefulTrader,0xUniswapRouter,0,2026-07-07T11:00:00Z,approve,0xDAIContract,0xCarefulTrader,0xUniswapRouter,false\n'
    '0xVictimWallet,0xSweepContract,10000,2026-07-08T09:00:00Z,approve,0xUSDCContract,0xVictimWallet,0xSweepContract,false\n'
    '0xVictimWallet,0xSweepContract,8000,2026-07-08T09:05:00Z,approve,0xDAIContract,0xVictimWallet,0xSweepContract,false\n'
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
        # Deliberately not a single declared currency - event_type/token_address carry the
        # per-row token identity instead (see the module docstring above and
        # TOKEN-APPROVAL-IMPLEMENTATION.md #8.2).
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
