"""Data model for an *investigator note*.

A note is an investigator's own observation. It is attached to exactly ONE of:

- an **address / graph node** - identified by the raw address string (``target_type ==
  "address"``), OR
- a **transaction / edge** - identified by the SAME transaction id the chain of custody
  uses (``target_type == "transaction"``). That id is the transaction hash when the
  evidence carries one, otherwise the deterministic ``row-<sha256(...)[:16]>`` fallback
  from ``app/evidence/tx_identity.py::transaction_id``. This module does NOT recompute or
  re-invent that id - it stores whatever id string the caller passes, verbatim, exactly
  the way it treats an address string.

Which of the two a note targets is recorded explicitly in ``target_type`` so node notes
and transaction/edge notes stay cleanly distinguishable even when read straight from the
JSON file.

A note is NOT a blockchain fact: it never comes from the imported evidence, is never fed
into the graph builder or any analysis, and is stored only inside the investigator layer's
tree (``data/investigations/<investigation_id>/notes.json``).

Both identifiers (``address`` and ``tx_id``) are stored and later matched VERBATIM
(surrounding whitespace trimmed, letter case preserved). Graph node ids and custody
``tx_id`` values are compared case-sensitively elsewhere in this project
(``app/analytics/path_finding.py``, ``app/evidence/custody_log.py``), so a note only lines
up with its target if it keeps the exact same spelling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ADDRESS_MAX_LENGTH = 256
TX_ID_MAX_LENGTH = 256
NOTE_TEXT_MAX_LENGTH = 10_000

NoteTargetType = Literal['address', 'transaction']


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_note_id() -> str:
    """12-char hex id, same convention as the investigation case and user accounts."""
    return uuid4().hex[:12]


def _trim_or_none(value: str | None) -> str | None:
    # Whitespace only - identifiers are never lower-cased; the stored value has to equal
    # the graph node id / custody tx_id character-for-character.
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class InvestigatorNoteCreate(BaseModel):
    """Body accepted when attaching a new note. Provide EXACTLY ONE of ``address`` or
    ``tx_id`` - that choice is what makes the note a node note or a transaction/edge
    note."""

    address: str | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    tx_id: str | None = Field(default=None, max_length=TX_ID_MAX_LENGTH)
    text: str = Field(min_length=1, max_length=NOTE_TEXT_MAX_LENGTH)

    @field_validator('address', 'tx_id')
    @classmethod
    def _trim_identifier(cls, value: str | None) -> str | None:
        return _trim_or_none(value)

    @field_validator('text')
    @classmethod
    def _clean_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('Tekst beleške ne sme biti prazan.')
        return stripped

    @model_validator(mode='after')
    def _exactly_one_target(self) -> InvestigatorNoteCreate:
        targets = [name for name, value in (('address', self.address), ('tx_id', self.tx_id)) if value is not None]
        if len(targets) != 1:
            raise ValueError(
                'Beleška mora biti vezana za tačno jedno: adresu/čvor (address) ili '
                'transakciju/granu (tx_id).'
            )
        return self

    @property
    def target_type(self) -> NoteTargetType:
        return 'address' if self.address is not None else 'transaction'


class InvestigatorNoteUpdate(BaseModel):
    """Body accepted when editing a note. Only the text can change - ``id``, ``author``,
    ``created_at`` and the target (``target_type`` / ``address`` / ``tx_id``) are all
    immutable, so a note stays an accountable record of one observation about one thing.
    Re-target it and it is a different observation: delete and create a new one."""

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
    # 'address' -> the note is about a graph node; 'transaction' -> about a tx/edge.
    # Exactly one of address / tx_id below is set, matching this.
    target_type: NoteTargetType
    address: str | None = None
    tx_id: str | None = None
    text: str
    # Username of the investigator who created the note. Immutable; later edits are
    # attributed through the activity log (see app/evidence/audit_log.py).
    author: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode='after')
    def _target_matches_type(self) -> InvestigatorNote:
        if self.target_type == 'address' and (not self.address or self.tx_id is not None):
            raise ValueError('target_type "address" zahteva postavljen address i prazan tx_id.')
        if self.target_type == 'transaction' and (not self.tx_id or self.address is not None):
            raise ValueError('target_type "transaction" zahteva postavljen tx_id i prazan address.')
        return self

    @property
    def target_id(self) -> str:
        """The identifier of whatever this note is about (the address or the tx_id) -
        for logging / exports without branching on target_type."""
        return (self.address if self.target_type == 'address' else self.tx_id) or ''
