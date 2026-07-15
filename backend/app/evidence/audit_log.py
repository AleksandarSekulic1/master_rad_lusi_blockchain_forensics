from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.paths import LOGS_DIR


def _audit_log_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / 'audit_log.jsonl'


def write_audit_log(
    *,
    file_name: str,
    sha256_hash: str,
    action: str,
    user: str = 'system',
    case_id: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, str]:
    entry = {
        'timestamp': (timestamp or datetime.now(timezone.utc)).isoformat(),
        'file_name': file_name,
        'sha256': sha256_hash,
        'action': action,
        'user': user,
        'case_id': case_id,
    }

    with _audit_log_path().open('a', encoding='utf-8') as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return entry


def load_audit_log_entries(case_id: str | None = None) -> list[dict[str, str]]:
    log_path = _audit_log_path()
    if not log_path.exists():
        return []

    entries: list[dict[str, str]] = []
    with log_path.open('r', encoding='utf-8') as log_file:
        for line in log_file:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if case_id is not None and entry.get('case_id') != case_id:
                continue
            entries.append(entry)

    return entries
