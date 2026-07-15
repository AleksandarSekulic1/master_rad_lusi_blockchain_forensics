from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.security import FRONTEND_URL
from app.services.user_management import create_reset_token, create_user, list_users, set_user_status


router = APIRouter(prefix='/users', tags=['users'])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)
    role: str = Field(default='analyst')


class SetStatusRequest(BaseModel):
    status: str = Field(pattern='^(active|blocked)$')


@router.get('')
def get_users() -> dict[str, object]:
    return {'users': list_users()}


@router.post('')
def post_user(request: CreateUserRequest) -> dict[str, object]:
    if request.role not in ('admin', 'analyst'):
        raise HTTPException(status_code=400, detail='Uloga mora biti "admin" ili "analyst".')

    try:
        return create_user(username=request.username, password=request.password, role=request.role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch('/{user_id}/status')
def patch_user_status(user_id: str, request: SetStatusRequest) -> dict[str, object]:
    try:
        return set_user_status(user_id, request.status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{user_id}/reset-link')
def post_reset_link(user_id: str) -> dict[str, object]:
    try:
        token = create_reset_token(user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {'reset_link': f'{FRONTEND_URL}/reset-password?token={token}', 'token': token}
