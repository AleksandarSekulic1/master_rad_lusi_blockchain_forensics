from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.shared.custody_recording import TransactionCustodyEntry


class CasePathfindingRequest(BaseModel):
    """`from` is always required. `to` is required only for destination_mode
    'specific_address' (the original, first-version behaviour) - for 'nearest_cex' the
    destination is resolved server-side from the case's own graph, so it stays optional
    and is ignored if sent.

    Field names use Python-safe identifiers with aliases for 'from'/'to' (reserved word),
    so the JSON body still looks exactly like {"from": "0x...", "to": "0x..."} -
    populate_by_name also allows calling code (tests, other Python) to pass
    from_address/to_address directly.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_address: str = Field(min_length=1, alias='from')
    to_address: str | None = Field(default=None, alias='to')
    # 'cash_out_point' is deliberately not accepted yet - only "Known CEX" is implemented;
    # requesting it fails clearly (see router.run_case_pathfinding) instead of being
    # silently accepted and doing nothing useful.
    destination_mode: str = Field(default='specific_address')
    # Same "all fields or none" custody gate every other case analysis run-request uses -
    # optional at the API level (so direct/test callers without a UI in front of them
    # still work), but the ONLY real caller (the Pathfinding page's "FIND PATH" button)
    # always supplies one, since running a BFS search over the case's evidence is itself a
    # deliberate access to every transaction it traverses, same as "Pokreni taint
    # analizu"/"Analiziraj graf".
    custody: TransactionCustodyEntry | None = None
