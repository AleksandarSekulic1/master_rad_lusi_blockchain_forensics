from __future__ import annotations

from pydantic import BaseModel

from app.shared.custody_recording import TransactionCustodyEntry


class RunAnalyticsRequest(BaseModel):
    # Extra taint-analysis seed addresses (e.g. a known theft address that isn't on any
    # blacklist) on top of whatever taint_analysis already auto-seeds from blacklist_flag.
    seed_addresses: list[str] | None = None
    # Optional at the API level: this same endpoint is also called passively (Dashboard,
    # Graf) just to color/annotate a graph the analyst never deliberately "ran" - only the
    # Taint Analysis page's explicit "Pokreni taint analizu" button represents a genuine,
    # purposeful access, and it is the one caller that always supplies this. When present,
    # every one of ITS fields is required (see TransactionCustodyEntry) - the gate is
    # "all or nothing" per call, never a half-filled entry.
    custody: TransactionCustodyEntry | None = None
