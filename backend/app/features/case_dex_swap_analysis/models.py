from __future__ import annotations

from pydantic import BaseModel, Field

from app.analytics.dex_swap_analysis import DEFAULT_MAX_GAP_SECONDS, MAX_MAX_GAP_SECONDS, MIN_MAX_GAP_SECONDS
from app.shared.custody_recording import TransactionCustodyEntry


class DexSwapAnalysisRunRequest(BaseModel):
    address: str | None = None
    max_gap_seconds: int = Field(default=DEFAULT_MAX_GAP_SECONDS, ge=MIN_MAX_GAP_SECONDS, le=MAX_MAX_GAP_SECONDS)
    # Same "all fields or none" custody gate every other case analysis run-request uses -
    # optional at the API level, but the DEX Swap Analysis page's own ANALYZE button always
    # supplies one, since scanning the case's evidence for swap pairs is the same kind of
    # deliberate access to it as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf" (see
    # LANAC-DOKAZA.md).
    custody: TransactionCustodyEntry | None = None
