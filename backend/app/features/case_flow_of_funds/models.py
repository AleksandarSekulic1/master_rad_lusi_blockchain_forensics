from __future__ import annotations

from pydantic import BaseModel, Field

from app.analytics.flow_of_funds import DEFAULT_LEVELS, DEFAULT_MAX_FLOWS, MAX_LEVELS, MAX_MAX_FLOWS, MIN_LEVELS, MIN_MAX_FLOWS
from app.shared.custody_recording import TransactionCustodyEntry


class FlowOfFundsRunRequest(BaseModel):
    """`source_addresses` accepts one or more seeds (a layering trace often needs to start
    from several known entry points in the same case, not just one).

    Same "all fields or none" custody gate every other case analysis run-request uses -
    optional at the API level, but the Flow of Funds page's own "Run" button (once built)
    always supplies one, since tracing a case's evidence outward across several hops is the
    same kind of deliberate access to it as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj
    graf" (see LANAC-DOKAZA.md).
    """

    source_addresses: list[str] = Field(min_length=1)
    direction: str = Field(default='forward')
    max_levels: int = Field(default=DEFAULT_LEVELS, ge=MIN_LEVELS, le=MAX_LEVELS)
    min_amount: float = Field(default=0.0, ge=0.0)
    max_flows: int = Field(default=DEFAULT_MAX_FLOWS, ge=MIN_MAX_FLOWS, le=MAX_MAX_FLOWS)
    custody: TransactionCustodyEntry | None = None
