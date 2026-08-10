from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.paths import LOGS_DIR


def _audit_log_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / 'audit_log.jsonl'


def write_audit_log(
    *,
    action: str,
    file_name: str | None = None,
    sha256_hash: str | None = None,
    user: str = 'system',
    case_id: str | None = None,
    case_name: str | None = None,
    details: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Append-only record of one analyst action.

    Originally this only covered evidence intake (hence the required file_name/sha256),
    but chain of custody covers what was DONE with the evidence too, not just how it
    arrived - so those two are optional now and `details` carries whatever parameters are
    specific to the action (seed addresses for an analysis run, endpoints for path
    finding, ...).

    `case_name` is stored alongside `case_id` deliberately: a case can be renamed or
    deleted later, and the log has to keep saying what the case was called at the moment
    the action happened, not what it is called today.
    """
    entry: dict[str, Any] = {
        'timestamp': (timestamp or datetime.now(timezone.utc)).isoformat(),
        'file_name': file_name,
        'sha256': sha256_hash,
        'action': action,
        'user': user,
        'case_id': case_id,
        'case_name': case_name,
        'details': details,
    }

    with _audit_log_path().open('a', encoding='utf-8') as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return entry


def load_audit_log_entries(case_id: str | None = None) -> list[dict[str, Any]]:
    log_path = _audit_log_path()
    if not log_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with log_path.open('r', encoding='utf-8') as log_file:
        for line in log_file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # A single corrupted/half-written line must not make the whole log
                # unreadable - the remaining records are still valid evidence.
                continue
            if case_id is not None and entry.get('case_id') != case_id:
                continue
            entries.append(entry)

    return entries


def local_day_bounds_utc(
    date_from: str | None,
    date_to: str | None,
    tz_offset_minutes: int,
) -> tuple[datetime | None, datetime | None]:
    """Converts a LOCAL calendar date range into the UTC instants that bound it.

    The log stores UTC, but the user picks dates as they see them on screen (local time).
    Filtering on the raw UTC date would silently move actions between days - an action at
    01:00 local in UTC+2 is still 23:00 the previous day in UTC - so a report for "27.07"
    would not match what the page shows for 27.07. That mismatch is exactly the kind of
    thing that discredits a forensic document, hence the explicit conversion here.

    `tz_offset_minutes` follows the JavaScript getTimezoneOffset() convention: the number
    of minutes to ADD to local time to get UTC (UTC+2 reports -120).
    """
    offset = timedelta(minutes=tz_offset_minutes)
    start = None
    end = None
    if date_from:
        start = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc) + offset
    if date_to:
        # Exclusive upper bound at local midnight of the following day, so the whole of
        # date_to is included regardless of the time of day.
        end = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1) + offset
    return start, end


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_activity_log_entries(
    *,
    user: str | None = None,
    users: list[str] | None = None,
    case_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tz_offset_minutes: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest-first view of the log for the activity page and the exported report.

    Entries written before `case_name`/`details` existed simply come back with those keys
    absent; the reader fills them in as None so old and new records render the same way.
    """
    entries = load_audit_log_entries(case_id=case_id)
    if user is not None:
        entries = [entry for entry in entries if entry.get('user') == user]
    if users:
        allowed = set(users)
        entries = [entry for entry in entries if entry.get('user') in allowed]

    start, end = local_day_bounds_utc(date_from, date_to, tz_offset_minutes)
    if start or end:
        filtered = []
        for entry in entries:
            stamp = _parse_timestamp(entry.get('timestamp'))
            if stamp is None:
                continue
            if start and stamp < start:
                continue
            if end and stamp >= end:
                continue
            filtered.append(entry)
        entries = filtered

    normalized = [
        {
            'timestamp': entry.get('timestamp'),
            'user': entry.get('user'),
            'action': entry.get('action'),
            'case_id': entry.get('case_id'),
            'case_name': entry.get('case_name'),
            'file_name': entry.get('file_name'),
            'sha256': entry.get('sha256'),
            'details': entry.get('details'),
        }
        for entry in entries
    ]

    # Sorted by the recorded timestamp rather than by file order - the file is naturally
    # append-ordered anyway, but sorting explicitly keeps the newest-first contract true
    # even if entries ever get written out of order.
    normalized.sort(key=lambda entry: str(entry.get('timestamp') or ''), reverse=True)
    return normalized[:limit] if limit > 0 else normalized


def known_log_users() -> list[str]:
    """Every username that appears anywhere in the log, including accounts that have since
    been removed from the system - otherwise their recorded actions could never be selected
    for a report, which would quietly make parts of the history unreportable."""
    return sorted({str(entry.get('user')) for entry in load_audit_log_entries() if entry.get('user')})
