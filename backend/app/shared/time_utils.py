"""Shared time helper used across feature slices.

Was previously copy-pasted, word for word, into every investigations model module
(``models.py``, ``notes_models.py``, ``links_models.py``, ``pins_models.py``). Kept here
instead as the one place that defines "how a timestamp is stamped" for the investigator
layer, so the four modules import it rather than redefine it.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """UTC timestamp in ISO-8601, the same way every other record in this project stamps
    time (see `app/services/case_management.py`, `app/evidence/audit_log.py`)."""
    return datetime.now(timezone.utc).isoformat()
