"""Data model for the investigator layer's top-level container: the *investigation case*.

An investigation case groups everything an investigator concludes about a set of addresses
and transactions. It is deliberately kept SEPARATE from the evidence `Case`
(`app/services/case_management.py`): that entity owns imported on-chain facts and their
chain of custody, this one owns investigator-generated interpretation. Keeping the two
apart is the whole point of the layer - see `CASE-MANAGEMENT-IMPLEMENTATION.md`.

This module defines only the container. Investigator notes, pinned graph nodes and manual
off-chain address links attach to it in later steps and are NOT part of this model yet.
"""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.shared.time_utils import utc_now_iso


def new_investigation_id() -> str:
    """12-char hex id, the same convention the evidence `Case`
    (`case_management.create_case`) and user accounts (`user_management.create_user`)
    already use."""
    return uuid4().hex[:12]


class InvestigationCaseCreate(BaseModel):
    """Fields accepted when opening a new investigation case."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator('name')
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('Naziv istrage ne sme biti prazan.')
        return stripped

    @field_validator('description')
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class InvestigationCaseUpdate(BaseModel):
    """Partial edit - only the fields actually present in the request body are changed
    (the service reads that via `model_dump(exclude_unset=True)`). Sending
    `description: ""` clears it; omitting `description` leaves the stored value alone."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator('name')
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError('Naziv istrage ne sme biti prazan.')
        return stripped

    @field_validator('description')
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class InvestigationCase(BaseModel):
    """The persisted investigation case.

    Minimal on purpose (this is step one). The per-case directory the repository creates
    (`data/investigations/<id>/`) is where the child collections - notes, pinned nodes,
    off-chain links - will live once they are implemented; nothing about this model has to
    change when they land, only new fields get added alongside.
    """

    id: str = Field(default_factory=new_investigation_id)
    name: str
    description: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
