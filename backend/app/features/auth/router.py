from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.security import create_access_token
from app.services.user_management import authenticate, reset_password_with_token


router = APIRouter(prefix='/auth', tags=['auth'])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


@router.post('/login')
def login(request: LoginRequest) -> dict[str, object]:
    user = authenticate(request.username, request.password)
    if user is None:
        raise HTTPException(status_code=401, detail='Pogrešno korisničko ime ili lozinka, ili je nalog blokiran.')

    token = create_access_token(user_id=str(user['id']), username=str(user['username']), role=str(user['role']))
    return {'access_token': token, 'token_type': 'bearer', 'user': user}


@router.get('/me')
def me(current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    return current_user


@router.post('/reset-password')
def reset_password(request: ResetPasswordRequest) -> dict[str, object]:
    success = reset_password_with_token(request.token, request.new_password)
    if not success:
        raise HTTPException(status_code=400, detail='Link za resetovanje lozinke je nevažeći ili je istekao.')

    return {'status': 'ok'}
