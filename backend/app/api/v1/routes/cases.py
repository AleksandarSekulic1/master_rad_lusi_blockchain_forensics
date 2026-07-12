from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.case_management import create_case, get_case, list_cases


router = APIRouter(prefix='/cases', tags=['cases'])


class CreateCaseRequest(BaseModel):
    name: str = Field(min_length=1)
    analyst: str = Field(default='system')
    description: str | None = None


@router.get('')
def get_cases() -> dict[str, object]:
    return {'cases': list_cases()}


@router.post('')
def post_case(request: CreateCaseRequest) -> dict[str, object]:
    return create_case(name=request.name, analyst=request.analyst, description=request.description)


@router.get('/{case_id}')
def get_case_detail(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
