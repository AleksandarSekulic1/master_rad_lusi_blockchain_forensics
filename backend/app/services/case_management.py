from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _cases_root() -> Path:
    root = _repo_root() / 'data' / 'cases'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path() -> Path:
    return _cases_root() / 'index.json'


def _case_dir(case_id: str) -> Path:
    return _cases_root() / case_id


def _case_file(case_id: str) -> Path:
    return _case_dir(case_id) / 'case.json'


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default

    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def _normalize_case(case: dict[str, object]) -> dict[str, object]:
    evidence = [entry for entry in case.get('evidence', []) if isinstance(entry, dict)]
    case['evidence'] = evidence
    case['evidence_count'] = len(evidence)
    case['total_size_bytes'] = sum(int(entry.get('size_bytes', 0) or 0) for entry in evidence)
    case['last_imported_at'] = evidence[-1].get('imported_at') if evidence else None
    return case


def _case_summary(case: dict[str, object]) -> dict[str, object]:
    return {
        'id': case['id'],
        'name': case.get('name'),
        'description': case.get('description'),
        'analyst': case.get('analyst'),
        'status': case.get('status', 'open'),
        'created_at': case.get('created_at'),
        'updated_at': case.get('updated_at'),
        'evidence_count': case.get('evidence_count', 0),
        'total_size_bytes': case.get('total_size_bytes', 0),
        'last_imported_at': case.get('last_imported_at'),
    }


def _load_case_payload(case_id: str) -> dict[str, object]:
    payload = _read_json(_case_file(case_id), None)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f'Case not found: {case_id}')

    return _normalize_case(payload)


def _load_index() -> dict[str, object]:
    payload = _read_json(_index_path(), {'cases': []})
    if not isinstance(payload, dict):
        return {'cases': []}
    if 'cases' not in payload or not isinstance(payload['cases'], list):
        payload['cases'] = []
    return payload


def _save_index(cases: list[dict[str, object]]) -> None:
    sorted_cases = sorted(cases, key=lambda item: str(item.get('updated_at', '')), reverse=True)
    _write_json(_index_path(), {'cases': sorted_cases})


def list_cases() -> list[dict[str, object]]:
    index = _load_index()
    return sorted(index['cases'], key=lambda item: str(item.get('updated_at', '')), reverse=True)


def get_case(case_id: str) -> dict[str, object]:
    return _load_case_payload(case_id)


def create_case(*, name: str, analyst: str = 'system', description: str | None = None) -> dict[str, object]:
    case_id = uuid4().hex[:12]
    now = _timestamp()
    case = _normalize_case({
        'id': case_id,
        'name': name.strip(),
        'description': description.strip() if description else None,
        'analyst': analyst.strip() if analyst else 'system',
        'status': 'open',
        'created_at': now,
        'updated_at': now,
        'evidence': [],
    })

    _case_dir(case_id).mkdir(parents=True, exist_ok=True)
    _write_json(_case_file(case_id), case)

    cases = [entry for entry in list_cases() if entry.get('id') != case_id]
    cases.append(_case_summary(case))
    _save_index(cases)
    return case


def update_case(case: dict[str, object]) -> dict[str, object]:
    normalized = _normalize_case(case)
    _case_dir(str(normalized['id'])).mkdir(parents=True, exist_ok=True)
    _write_json(_case_file(str(normalized['id'])), normalized)

    cases = [entry for entry in list_cases() if entry.get('id') != normalized['id']]
    cases.append(_case_summary(normalized))
    _save_index(cases)
    return normalized


def resolve_case(case_id: str | None, *, analyst: str = 'system') -> dict[str, object]:
    if case_id:
        return get_case(case_id)

    existing_cases = list_cases()
    if existing_cases:
        return get_case(str(existing_cases[0]['id']))

    return create_case(name='Default investigation case', analyst=analyst)


def append_evidence(
    case_id: str,
    *,
    original_name: str,
    stored_name: str,
    size_bytes: int,
    sha256_hash: str,
    analyst: str,
    imported_at: datetime | None = None,
) -> dict[str, object]:
    case = get_case(case_id)
    evidence_entry = {
        'file_name': original_name,
        'stored_name': stored_name,
        'imported_at': (imported_at or datetime.now(timezone.utc)).isoformat(),
        'size_bytes': int(size_bytes),
        'sha256': sha256_hash,
        'analyst': analyst,
    }

    case.setdefault('evidence', [])
    case['evidence'].append(evidence_entry)
    case['updated_at'] = evidence_entry['imported_at']
    updated_case = update_case(case)
    return evidence_entry | {'case': _case_summary(updated_case)}


def store_case_evidence(case_id: str, source_path: Path, stored_name: str) -> Path:
    case_storage = _case_dir(case_id) / 'evidence'
    case_storage.mkdir(parents=True, exist_ok=True)
    destination = case_storage / stored_name
    shutil.copy2(source_path, destination)
    return destination


def get_case_evidence_path(case: dict[str, object], file_name: str | None = None) -> Path:
    evidence = [entry for entry in case.get('evidence', []) if isinstance(entry, dict)]
    if not evidence:
        raise FileNotFoundError(f'No evidence recorded for case {case["id"]}')

    selected_name = file_name or str(evidence[-1]['stored_name'])
    evidence_paths = [
        _repo_root() / 'data' / 'raw' / selected_name,
        _case_dir(str(case['id'])) / 'evidence' / selected_name,
    ]

    for path in evidence_paths:
        if path.exists():
            return path

    raise FileNotFoundError(f'Evidence file not found: {selected_name}')
