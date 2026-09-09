"""Persistence for pinned nodes.

One JSON file per investigation -
``data/investigations/<investigation_id>/pinned_nodes.json`` - shaped
``{ "pinned_nodes": [ { <PinnedNode fields> }, ... ] }``.

Pure dict I/O via the shared collection helpers in ``app/investigations/repository.py`` -
no validation (that is ``pins_service.py``'s job). The file lives in the per-investigation
directory, so deleting an investigation removes its pins with it. Nothing here touches the
transaction graph or the evidence case.
"""

from __future__ import annotations

from typing import Any

from app.investigations.repository import read_collection, write_collection

_FILE = 'pinned_nodes.json'
_KEY = 'pinned_nodes'


def load_pins(investigation_id: str) -> list[dict[str, Any]]:
    return read_collection(investigation_id, _FILE, _KEY)


def save_pins(investigation_id: str, pins: list[dict[str, Any]]) -> None:
    write_collection(investigation_id, _FILE, _KEY, pins)
