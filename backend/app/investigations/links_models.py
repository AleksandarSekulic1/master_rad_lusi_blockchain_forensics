"""Data model for an *investigator link* between two blockchain addresses.

An investigator link records that, in the investigator's judgement, two addresses MAY BE
RELATED - based on OFF-CHAIN evidence (server logs, subpoena returns, OSINT, ...), never
on anything read from the blockchain. It is a **suspected relation / investigator
association**, explicitly NOT a proven fact and NOT a transaction-graph edge:

- it is stored only inside the investigator layer (``data/investigations/<id>/links.json``);
- it is never written into ``build_transaction_graph``, the node-link JSON, the case
  exports, or any analysis - the existing blockchain graph edges are untouched;
- the record carries a free-text ``reason`` and an ``evidence`` reference plus a
  ``confidence`` of Low / Medium / High. It never carries a loaded label such as
  "same owner" / "same person" / "proven connection". If the investigator wants to make
  that specific claim, they write it themselves into ``reason`` / ``evidence``.

Directionality: the step-1 investigation model does not require direction (notes attach to
a single address or a single transaction; nothing consumes a from/to ordering). So a link
is an **undirected association** - ``directed`` is always False, ``source_address`` /
``target_address`` are simply "the two addresses" and their order carries no meaning.
Retrieval by address matches either endpoint.

Both addresses are stored and matched VERBATIM (whitespace trimmed, letter case
preserved), the same rule investigator notes use - so a link lines up with the graph node
it refers to only if it keeps the exact spelling.
"""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.shared.time_utils import utc_now_iso

ADDRESS_MAX_LENGTH = 256
REASON_MAX_LENGTH = 5_000
EVIDENCE_MAX_LENGTH = 2_000

LinkConfidence = Literal['Low', 'Medium', 'High']
LINK_CONFIDENCE_VALUES: tuple[str, ...] = ('Low', 'Medium', 'High')


def new_link_id() -> str:
    """12-char hex id, same convention as the investigation case, notes and user accounts."""
    return uuid4().hex[:12]


def _clean_required(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f'{field_label} ne sme biti prazno.')
    return stripped


class InvestigatorLinkCreate(BaseModel):
    """Body accepted when recording a new investigator link. Every field is required - an
    off-chain association with no stated reason or evidence reference is not worth
    recording."""

    source_address: str = Field(min_length=1, max_length=ADDRESS_MAX_LENGTH)
    target_address: str = Field(min_length=1, max_length=ADDRESS_MAX_LENGTH)
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)
    evidence: str = Field(min_length=1, max_length=EVIDENCE_MAX_LENGTH)
    confidence: LinkConfidence

    @field_validator('source_address', 'target_address')
    @classmethod
    def _clean_address(cls, value: str) -> str:
        return _clean_required(value, 'Adresa')

    @field_validator('reason')
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        return _clean_required(value, 'Razlog (reason)')

    @field_validator('evidence')
    @classmethod
    def _clean_evidence(cls, value: str) -> str:
        return _clean_required(value, 'Dokaz/referenca (evidence)')

    @model_validator(mode='after')
    def _distinct_endpoints(self) -> InvestigatorLinkCreate:
        if self.source_address == self.target_address:
            raise ValueError('Investigator link mora povezivati dve različite adrese.')
        return self


class InvestigatorLinkUpdate(BaseModel):
    """Partial edit. Only ``reason`` / ``evidence`` / ``confidence`` can change (an
    investigator refining the wording, or raising/lowering confidence as more off-chain
    evidence comes in). The two addresses are immutable - re-pointing a link is a
    different association, so delete and create a new one."""

    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)
    evidence: str | None = Field(default=None, min_length=1, max_length=EVIDENCE_MAX_LENGTH)
    confidence: LinkConfidence | None = None

    @field_validator('reason')
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return None if value is None else _clean_required(value, 'Razlog (reason)')

    @field_validator('evidence')
    @classmethod
    def _clean_evidence(cls, value: str | None) -> str | None:
        return None if value is None else _clean_required(value, 'Dokaz/referenca (evidence)')


class InvestigatorLink(BaseModel):
    """The persisted investigator link - a **suspected relation**, not a proven fact."""

    id: str = Field(default_factory=new_link_id)
    # The "case ID": the step-1 InvestigationCase this link belongs to, NOT the evidence Case.
    investigation_id: str
    # Undirected: order carries no meaning, retrieval matches either endpoint.
    source_address: str
    target_address: str
    directed: bool = False
    reason: str
    evidence: str
    confidence: LinkConfidence
    # Username of the investigator who recorded the link; immutable (edits are attributed
    # through the activity log, like everywhere else in this project).
    author: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode='after')
    def _distinct_endpoints(self) -> InvestigatorLink:
        if self.source_address == self.target_address:
            raise ValueError('Investigator link mora povezivati dve različite adrese.')
        return self

    def involves(self, address: str) -> bool:
        """True when ``address`` (exact, whitespace-trimmed) is either endpoint - the
        undirected "links for this address" match."""
        target = address.strip()
        return self.source_address == target or self.target_address == target
