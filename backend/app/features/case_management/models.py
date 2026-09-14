from __future__ import annotations

from pydantic import BaseModel, Field


class CreateCaseRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class SetCaseStatusRequest(BaseModel):
    status: str = Field(pattern='^(open|closed)$')
