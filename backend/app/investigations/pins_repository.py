"""Persistence for pinned nodes.

One JSON file per investigation - ``data/investigations/<investigation_id>/pinned_nodes.json`` -
holding every pinned address for that investigation:

    { "pinned_nodes": [ { <PinnedNode fields> }, ... ] }

Pure dict I/O, no validation (that is ``pins_service.py``'s job). The file lives in the
per-investigation directory, so deleting an investigation removes its pins with it.
Nothing here touches the transaction graph or the evidence case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.investigations.repository import investigation_dir

_PINS_FILE = 'pinned_nodes.json'


def _pins_path(investigation_id: str) -> Path:
    return investigation_dir(investigation_id) / _PINS_FILE


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_pins(investigation_id: str) -> list[dict[str, Any]]:
    payload = _read_json(_pins_path(investigation_id), {'pinned_nodes': []})
    if not isinstance(payload, dict):
        return []
    pins = payload.get('pinned_nodes')
    return pins if isinstance(pins, list) else []


def save_pins(investigation_id: str, pins: list[dict[str, Any]]) -> None:
    _write_json(_pins_path(investigation_id), {'pinned_nodes': pins})
