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
    # Optional time window (ISO date/datetime), applied to evidence before aggregation - see
    # app.analytics.flow_of_funds.trace_flow_of_funds. Either/both may be omitted.
    start_time: str | None = None
    end_time: str | None = None
    # Cross-references this trace's nodes against the rest of the app's existing analyses -
    # see app.analytics.flow_of_funds_enrichment. The cheap, deterministic ones (known-entity/
    # blacklist registry, risk scoring, peel chains, chain hopping, wallet clustering, DEX
    # swaps, token approvals) always run; these two default OFF since both are heavier,
    # deliberate analyses elsewhere in the app with their own "run" action.
    include_taint: bool = False
    include_sybil: bool = False
    custody: TransactionCustodyEntry | None = None
