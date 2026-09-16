from __future__ import annotations

from pydantic import BaseModel, Field

from app.analytics.token_approval_analysis import (
    DEFAULT_LARGE_AMOUNT_THRESHOLD,
    DEFAULT_LONG_ACTIVE_PERIOD_SECONDS,
    DEFAULT_MULTIPLE_TRANSFER_THRESHOLD,
    DEFAULT_RAPID_USE_SECONDS,
    DEFAULT_UNLIMITED_THRESHOLD,
    MAX_LARGE_AMOUNT_THRESHOLD,
    MAX_LONG_ACTIVE_PERIOD_SECONDS,
    MAX_MULTIPLE_TRANSFER_THRESHOLD,
    MAX_RAPID_USE_SECONDS,
    MAX_UNLIMITED_THRESHOLD,
    MIN_LARGE_AMOUNT_THRESHOLD,
    MIN_LONG_ACTIVE_PERIOD_SECONDS,
    MIN_MULTIPLE_TRANSFER_THRESHOLD,
    MIN_RAPID_USE_SECONDS,
    MIN_UNLIMITED_THRESHOLD,
)
from app.shared.custody_recording import TransactionCustodyEntry


class TokenApprovalAnalysisRunRequest(BaseModel):
    address: str | None = None
    unlimited_threshold: float = Field(default=DEFAULT_UNLIMITED_THRESHOLD, ge=MIN_UNLIMITED_THRESHOLD, le=MAX_UNLIMITED_THRESHOLD)
    rapid_use_seconds: int = Field(default=DEFAULT_RAPID_USE_SECONDS, ge=MIN_RAPID_USE_SECONDS, le=MAX_RAPID_USE_SECONDS)
    large_amount_threshold: float = Field(default=DEFAULT_LARGE_AMOUNT_THRESHOLD, ge=MIN_LARGE_AMOUNT_THRESHOLD, le=MAX_LARGE_AMOUNT_THRESHOLD)
    multiple_transfer_threshold: int = Field(
        default=DEFAULT_MULTIPLE_TRANSFER_THRESHOLD, ge=MIN_MULTIPLE_TRANSFER_THRESHOLD, le=MAX_MULTIPLE_TRANSFER_THRESHOLD
    )
    long_active_period_seconds: int = Field(
        default=DEFAULT_LONG_ACTIVE_PERIOD_SECONDS, ge=MIN_LONG_ACTIVE_PERIOD_SECONDS, le=MAX_LONG_ACTIVE_PERIOD_SECONDS
    )
    # Same "all fields or none" custody gate every other case analysis run-request uses -
    # optional at the API level, but the Token Approval Analysis page's own ANALYZE button
    # always supplies one now, since correlating the case's evidence for approval/
    # transferFrom pairs is the same kind of deliberate access to it as "Pokreni taint
    # analizu"/"FIND PATH"/"ANALYZE" on DEX Swaps (see LANAC-DOKAZA.md, and
    # TOKEN-APPROVAL-IMPLEMENTATION.md #18 for what this run additionally writes into the
    # chain of evidence).
    custody: TransactionCustodyEntry | None = None
