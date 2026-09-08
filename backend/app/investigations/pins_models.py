"""Data model for a *pinned node* - an address an investigator has marked as important and
fixed in place on the graph.

Persisted so the pin survives a reload / navigation (step 10). It belongs to an
investigation, exactly like notes and links: stored under
``data/investigations/<investigation_id>/pinned_nodes.json``, one entry per pinned
address, keyed by the address itself (a node is either pinned or not - there is no
separate id).

``x`` / ``y`` are the cytoscape model-coordinate position captured when the investigator
pinned the node, so a reload can restore the exact spot. They are optional: a pin with no
coordinates just means "this address is pinned", and the frontend locks it wherever it
currently sits.

The address is stored VERBATIM (whitespace trimmed, letter case preserved) - same rule as
investigator notes / links, so it lines up with the graph node id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

ADDRESS_MAX_LENGTH = 256


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PinNodeRequest(BaseModel):
    """Body for pinning an address (upsert - re-sending it just updates the position)."""

    address: str = Field(min_length=1, max_length=ADDRESS_MAX_LENGTH)
    x: float | None = None
    y: float | None = None

    @field_validator('address')
    @classmethod
    def _clean_address(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('Adresa (identifikator čvora) ne sme biti prazna.')
        return stripped


class PinnedNode(BaseModel):
    """The persisted pin."""

    # The "case ID": the step-1 InvestigationCase this pin belongs to.
    investigation_id: str
    address: str
    x: float | None = None
    y: float | None = None
    # Username of the investigator who pinned it; immutable.
    pinned_by: str
    pinned_at: str = Field(default_factory=utc_now_iso)
    # Advances when the position is updated (node re-pinned at new coordinates).
    updated_at: str = Field(default_factory=utc_now_iso)
