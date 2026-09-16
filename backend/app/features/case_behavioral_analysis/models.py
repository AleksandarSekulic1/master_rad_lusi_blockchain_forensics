from __future__ import annotations

from pydantic import BaseModel, Field

from app.shared.custody_recording import TransactionCustodyEntry


class BehavioralAnalysisRunRequest(BaseModel):
    """`address` is always required. Same "all fields or none" custody gate every other
    case analysis run-request uses - optional at the API level so direct/test callers
    still work, but the only real caller (the Behavioral Analysis page's "Analiziraj"
    button) always supplies one: aggregating an address's hour-of-day pattern still reads
    every transaction of the selected evidence to build the graph, the same deliberate
    access as "FIND PATH" / "Pokreni taint analizu"."""

    address: str = Field(min_length=1)
    custody: TransactionCustodyEntry | None = None
