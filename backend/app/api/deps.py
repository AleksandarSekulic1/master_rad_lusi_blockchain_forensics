from __future__ import annotations

from fastapi import Depends, Header, HTTPException
import jwt

from app.security import decode_access_token
from app.services.user_management import get_user_by_id


def get_current_user(authorization: str = Header(default='')) -> dict[str, object]:
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Nedostaje prijavni token.')

    token = authorization.removeprefix('Bearer ').strip()

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Sesija je istekla, prijavite se ponovo.')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Nevažeći prijavni token.')

    user = get_user_by_id(str(payload.get('sub', '')))
    if user is None:
        raise HTTPException(status_code=401, detail='Korisnik ne postoji.')
    if user.get('status') != 'active':
        raise HTTPException(status_code=403, detail='Nalog je blokiran.')

    return {'id': user['id'], 'username': user['username'], 'role': user['role']}


def require_admin(current_user: dict[str, object] = Depends(get_current_user)) -> dict[str, object]:
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Potrebna su administratorska ovlašćenja.')
    return current_user