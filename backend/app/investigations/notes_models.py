"""Data model for an *investigator note* attached to a blockchain address / graph node.

A note is an investigator's own observation about an address ("suspected cold wallet,
pending confirmation"). It is NOT a blockchain fact: it never comes from the imported
evidence, it is never fed into the graph builder or any analysis, and it is stored only
inside the investigator layer's tree (`data/investigations/<investigation_id>/notes.json`).

`address` is stored and later matched VERBATIM (surrounding whitespace trimmed, letter
case preserved). Graph node ids in this project are the raw address strings from the
evidence and are compared case-sensitively (see `app/analytics/path_finding.py`,
`app/analytics/behavioral_analysis.py`), so a note only lines up with the node it is about
if it keeps the exact same spelling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

ADDRESS_MAX_LENGTH = 256
NOTE_TEXT_MAX_LENGTH = 10_000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_note_id() -> str:
    """12-char hex id, same convention as the investigation case and user accounts."""
    return uuid4().hex[:12]


class InvestigatorNoteCreate(BaseModel):
    """Body accepted when attaching a new note to an address."""

    address: str = Field(min_length=1, max_length=ADDRESS_MAX_LENGTH)
    text: str = Field(min_length=1, max_length=NOTE_TEXT_MAX_LENGTH)

    @field_validator('address')
    @classmethod
    def _clean_address(cls, value: str) -> str:
        # Whitespace only - never lower-cased. The stored value has to equal the graph
        # node id character-for-character for the note to attach to the right node.
        stripped = value.strip()
        if not stripped:
            raise ValueError('Adresa (identifikator čvora) ne sme biti prazna.')
        return stripped

    @field_validator('text')
    @classmethod
    def _clean_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('Tekst beleške ne sme biti prazan.')
        return stripped


class InvestigatorNoteUpdate(BaseModel):
    """Body accepted when editing a note. Only the text can change - `address`, `id`,
    `author` and `created_at` are immutable, so a note stays an accountable record of one
    observation about one address (re-point it and it is a different observation: delete
    and create a new one instead)."""

    text: str = Field(min_length=1, max_length=NOTE_TEXT_MAX_LENGTH)

    @field_validator('text')
    @classmethod
    def _clean_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('Tekst beleške ne sme biti prazan.')
        return stripped


class InvestigatorNote(BaseModel):
    """The persisted note."""

    id: str = Field(default_factory=new_note_id)
    # The "case ID" this note belongs to - the investigator layer container from step 1
    # (app/investigations/models.py::InvestigationCase), NOT the evidence Case.
    investigation_id: str
    address: str
    text: str
    # Username of the investigator who created the note. Immutable; later edits are
    # attributed through the activity log, the same way the rest of this project records
    # "who changed what" (see app/evidence/audit_log.py).
    author: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
