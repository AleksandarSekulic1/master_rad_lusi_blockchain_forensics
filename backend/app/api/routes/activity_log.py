from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import load_activity_log_entries
from app.services.user_management import list_users


router = APIRouter(prefix='/activity-log', tags=['activity-log'])


@router.get('')
def get_activity_log(
    user: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Analyst actions, newest first.

    Scope is decided here, on the server, from the caller's own role - a non-admin always
    gets exactly their own entries no matter what `user` they pass, so hand-editing the
    query string can't turn into a way to read a colleague's activity.
    """
    is_admin = current_user.get('role') == 'admin'
    own_username = str(current_user['username'])
    requested_user = user if (is_admin and user) else (None if is_admin else own_username)

    entries = load_activity_log_entries(user=requested_user, case_id=case_id, limit=limit)

    return {
        'entries': entries,
        'scope': 'all' if is_admin else 'self',
        'filtered_user': requested_user,
        # Only an admin gets the roster to filter by; for everyone else the frontend has
        # no filter to render in the first place.
        'available_users': [str(item['username']) for item in list_users()] if is_admin else [],
    }
