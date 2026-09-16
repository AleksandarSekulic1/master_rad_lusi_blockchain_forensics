from __future__ import annotations

from pydantic import BaseModel, Field


class FetchBitcoinTransactionsRequest(BaseModel):
    address: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
