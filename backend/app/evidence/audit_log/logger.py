from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _audit_log_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    log_dir = repo_root / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / 'audit_log.jsonl'


def write_audit_log(
    *,
    file_name: str,
    sha256_hash: str,
    action: str,
    user: str = 'system',
    timestamp: datetime | None = None,
) -> dict[str, str]:
    entry = {
        'timestamp': (timestamp or datetime.now(timezone.utc)).isoformat(),
        'file_name': file_name,
        'sha256': sha256_hash,
        'action': action,
        'user': user,
    }

    with _audit_log_path().open('a', encoding='utf-8') as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return entry
