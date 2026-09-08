"""Persistence for investigator links.

One JSON file per investigation - ``data/investigations/<investigation_id>/links.json`` -
holding every link for that investigation:

    { "links": [ { <InvestigatorLink fields> }, ... ] }

Pure dict I/O, no validation (that is ``links_service.py``'s job). The file lives in the
per-investigation directory, so deleting an investigation removes its links with it.

Investigator links are a separate forensic layer: nothing in this module touches the
transaction graph, the evidence case, or any analysis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.investigations.repository import investigation_dir

_LINKS_FILE = 'links.json'


def _links_path(investigation_id: str) -> Path:
    return investigation_dir(investigation_id) / _LINKS_FILE


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_links(investigation_id: str) -> list[dict[str, Any]]:
    payload = _read_json(_links_path(investigation_id), {'links': []})
    if not isinstance(payload, dict):
        return []
    links = payload.get('links')
    return links if isinstance(links, list) else []


def save_links(investigation_id: str, links: list[dict[str, Any]]) -> None:
    _write_json(_links_path(investigation_id), {'links': links})
