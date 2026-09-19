from __future__ import annotations

from pydantic import BaseModel, Field

from app.analytics.sybil_analysis import (
    DEFAULT_MIN_ADDRESSES,
    DEFAULT_TIME_WINDOW_SECONDS,
    MAX_MIN_ADDRESSES,
    MAX_TIME_WINDOW_SECONDS,
    MIN_MIN_ADDRESSES,
    MIN_TIME_WINDOW_SECONDS,
)
from app.shared.custody_recording import TransactionCustodyEntry


class SybilAnalysisRunRequest(BaseModel):
    address: str | None = None
    contract: str | None = None
    time_window_seconds: int = Field(default=DEFAULT_TIME_WINDOW_SECONDS, ge=MIN_TIME_WINDOW_SECONDS, le=MAX_TIME_WINDOW_SECONDS)
    min_addresses: int = Field(default=DEFAULT_MIN_ADDRESSES, ge=MIN_MIN_ADDRESSES, le=MAX_MIN_ADDRESSES)
    # Same "all fields or none" custody gate every other case analysis run-request uses -
    # optional at the API level, but the Sybil Analysis page's own ANALYZE button always
    # supplies one, since scanning the case's evidence for synchronized-address clusters is
    # the same kind of deliberate access to it as "Pokreni taint analizu"/"FIND PATH"/
    # "Analiziraj graf"/"ANALYZE" on DEX Swaps and Token Approval (see LANAC-DOKAZA.md).
    custody: TransactionCustodyEntry | None = None
