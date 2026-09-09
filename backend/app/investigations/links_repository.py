"""Persistence for investigator links.

One JSON file per investigation - ``data/investigations/<investigation_id>/links.json`` -
shaped ``{ "links": [ { <InvestigatorLink fields> }, ... ] }``.

Pure dict I/O via the shared collection helpers in ``app/investigations/repository.py`` -
no validation (that is ``links_service.py``'s job). The file lives in the per-investigation
directory, so deleting an investigation removes its links with it. Investigator links are
a separate forensic layer: nothing here touches the transaction graph, the evidence case,
or any analysis.
"""

from __future__ import annotations

from typing import Any

from app.investigations.repository import read_collection, write_collection

_FILE = 'links.json'
_KEY = 'links'


def load_links(investigation_id: str) -> list[dict[str, Any]]:
    return read_collection(investigation_id, _FILE, _KEY)


def save_links(investigation_id: str, links: list[dict[str, Any]]) -> None:
    write_collection(investigation_id, _FILE, _KEY, links)
