from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.case_management import create_case, get_case, list_cases


def _get_case_or_404(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


router = APIRouter(prefix='/cases', tags=['cases'])


class CreateCaseRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


@router.get('')
def get_cases() -> dict[str, object]:
    return {'cases': list_cases()}


@router.post('')
def post_case(request: CreateCaseRequest, current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    return create_case(name=request.name, analyst=str(current_user['username']), description=request.description)


@router.get('/{case_id}')
def get_case_detail(case_id: str) -> dict[str, object]:
    return _get_case_or_404(case_id)


@router.get('/{case_id}/evidence')
def get_case_evidence(case_id: str) -> dict[str, object]:
    case = _get_case_or_404(case_id)
    return {'case_id': case_id, 'evidence': case.get('evidence', [])}
