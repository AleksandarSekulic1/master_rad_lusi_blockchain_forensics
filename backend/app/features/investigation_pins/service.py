"""Business logic for pinned nodes.

Sits between the API router and ``repository.py``. Always verifies the parent
investigation exists first (reusing the investigation aggregate's own service). Pins are
keyed by address within an investigation - pinning an already-pinned address updates its
stored position rather than creating a duplicate.

Never imports or touches any analytics / graph code.
"""

from __future__ import annotations

from app.features.investigation_pins import repository
from app.features.investigation_pins.models import PinNodeRequest, PinnedNode, utc_now_iso
from app.investigations.service import get_investigation


class PinnedNodeNotFoundError(FileNotFoundError):
    """An address that is not pinned in the given investigation. Subclasses
    FileNotFoundError, same convention as InvestigationCaseNotFoundError."""


def _load_models(investigation_id: str) -> list[PinnedNode]:
    return [PinnedNode(**row) for row in repository.load_pins(investigation_id)]


def _persist(investigation_id: str, pins: list[PinnedNode]) -> None:
    repository.save_pins(investigation_id, [pin.model_dump() for pin in pins])


def list_pins(investigation_id: str) -> list[PinnedNode]:
    """Every pinned node in the investigation, oldest pin first (stable order for the
    frontend to replay)."""
    get_investigation(investigation_id)  # -> InvestigationCaseNotFoundError -> HTTP 404
    return sorted(_load_models(investigation_id), key=lambda pin: (pin.pinned_at, pin.address))


def set_pin(investigation_id: str, data: PinNodeRequest, *, pinned_by: str) -> PinnedNode:
    """Pin an address, or update the position of an already-pinned one (upsert by address)."""
    get_investigation(investigation_id)

    now = utc_now_iso()
    pins = _load_models(investigation_id)
    for index, pin in enumerate(pins):
        if pin.address == data.address:
            pin.x = data.x
            pin.y = data.y
            pin.updated_at = now
            pins[index] = pin
            _persist(investigation_id, pins)
            return pin

    pin = PinnedNode(
        investigation_id=investigation_id,
        address=data.address,
        x=data.x,
        y=data.y,
        pinned_by=pinned_by,
        pinned_at=now,
        updated_at=now,
    )
    pins.append(pin)
    _persist(investigation_id, pins)
    return pin


def clear_pin(investigation_id: str, address: str) -> None:
    get_investigation(investigation_id)

    target = address.strip()
    pins = _load_models(investigation_id)
    remaining = [pin for pin in pins if pin.address != target]
    if len(remaining) == len(pins):
        raise PinnedNodeNotFoundError(f'Adresa nije zakačena: {address}')
    _persist(investigation_id, remaining)
