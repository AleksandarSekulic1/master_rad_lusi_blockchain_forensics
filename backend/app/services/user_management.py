from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.paths import DATA_DIR
from app.security import hash_password, verify_password

RESET_TOKEN_TTL_MINUTES = 60


def _users_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / 'users.json'


def _load_users() -> list[dict[str, object]]:
    path = _users_path()
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding='utf-8'))
    return payload if isinstance(payload, list) else []


def _save_users(users: list[dict[str, object]]) -> None:
    _users_path().write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding='utf-8')


def _sanitize(user: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in user.items() if key not in ('password_hash', 'reset_token', 'reset_token_expires_at')}


def list_users() -> list[dict[str, object]]:
    return [_sanitize(user) for user in _load_users()]


def get_user_by_username(username: str) -> dict[str, object] | None:
    normalized = username.strip().lower()
    for user in _load_users():
        if str(user.get('username', '')).lower() == normalized:
            return user
    return None


def get_user_by_id(user_id: str) -> dict[str, object] | None:
    for user in _load_users():
        if user.get('id') == user_id:
            return user
    return None


def username_exists(username: str) -> bool:
    return get_user_by_username(username) is not None


def create_user(*, username: str, password: str, role: str = 'analyst', status: str = 'active') -> dict[str, object]:
    if username_exists(username):
        raise ValueError(f'Korisničko ime "{username}" je već zauzeto.')

    users = _load_users()
    now = datetime.now(timezone.utc).isoformat()
    user = {
        'id': uuid4().hex[:12],
        'username': username.strip(),
        'password_hash': hash_password(password),
        'role': role,
        'status': status,
        'created_at': now,
        'updated_at': now,
    }
    users.append(user)
    _save_users(users)
    return _sanitize(user)


def authenticate(username: str, password: str) -> dict[str, object] | None:
    user = get_user_by_username(username)
    if user is None:
        return None
    if not verify_password(password, str(user.get('password_hash', ''))):
        return None
    if user.get('status') != 'active':
        return None
    return _sanitize(user)


def set_user_status(user_id: str, status: str) -> dict[str, object]:
    if status not in ('active', 'blocked'):
        raise ValueError('Status mora biti "active" ili "blocked".')

    users = _load_users()
    for user in users:
        if user.get('id') == user_id:
            user['status'] = status
            user['updated_at'] = datetime.now(timezone.utc).isoformat()
            _save_users(users)
            return _sanitize(user)

    raise FileNotFoundError(f'Korisnik nije pronađen: {user_id}')


def create_reset_token(user_id: str) -> str:
    users = _load_users()
    for user in users:
        if user.get('id') == user_id:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
            user['reset_token'] = token
            user['reset_token_expires_at'] = expires_at.isoformat()
            user['updated_at'] = datetime.now(timezone.utc).isoformat()
            _save_users(users)
            return token

    raise FileNotFoundError(f'Korisnik nije pronađen: {user_id}')


def reset_password_with_token(token: str, new_password: str) -> bool:
    users = _load_users()
    now = datetime.now(timezone.utc)

    for user in users:
        if user.get('reset_token') != token:
            continue

        expires_raw = user.get('reset_token_expires_at')
        expires_at = datetime.fromisoformat(str(expires_raw)) if expires_raw else None
        if expires_at is None or now > expires_at:
            return False

        user['password_hash'] = hash_password(new_password)
        user['reset_token'] = None
        user['reset_token_expires_at'] = None
        user['updated_at'] = now.isoformat()
        _save_users(users)
        return True

    return False


def bootstrap_admin(*, username: str, password: str) -> None:
    if _load_users():
        return

    create_user(username=username, password=password, role='admin', status='active')
