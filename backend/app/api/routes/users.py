from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.security import FRONTEND_URL
from app.services.user_management import create_reset_token, create_user, delete_user, list_users, rename_user, set_user_status


router = APIRouter(prefix='/users', tags=['users'])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)
    role: str = Field(default='analyst')


class SetStatusRequest(BaseModel):
    status: str = Field(pattern='^(active|blocked)$')


class RenameUserRequest(BaseModel):
    username: str = Field(min_length=1)


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


@router.patch('/{user_id}')
def patch_user(user_id: str, request: RenameUserRequest) -> dict[str, object]:
    try:
        return rename_user(user_id, request.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete('/{user_id}', status_code=204)
def delete_user_route(user_id: str, current_user: dict[str, object] = Depends(require_admin)) -> None:
    # Self-deletion is blocked here (an application-level UX rule tied to who's asking,
    # not a data-integrity rule) - deleting the account behind the very session making the
    # request would invalidate that session mid-flow. The last-admin guard lives in
    # delete_user() itself, since that's a rule about the data regardless of caller.
    if user_id == current_user['id']:
        raise HTTPException(status_code=400, detail='Ne možete obrisati sopstveni nalog.')

    try:
        delete_user(user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
