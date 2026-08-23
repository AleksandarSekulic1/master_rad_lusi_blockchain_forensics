from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.evidence.audit_log import known_log_users, load_activity_log_entries, write_audit_log
from app.exports.activity_report import build_activity_csv, build_activity_pdf, format_period
from app.services.user_management import list_users


router = APIRouter(prefix='/activity-log', tags=['activity-log'])

# Reports are bounded by the chosen date range rather than by a row cap: cutting a report
# off mid-period would make it silently incomplete, which is worse than a long document.
REPORT_LIMIT = 0  # 0 = no limit (see load_activity_log_entries)


def _resolve_users(
    current_user: dict[str, object],
    requested_users: list[str] | None,
) -> tuple[list[str] | None, bool]:
    """Decides whose entries the caller may actually see.

    Scope is derived from the caller's role on the server, never from what the request
    asks for - a non-admin always gets exactly their own entries, so hand-editing the
    query string cannot turn into a way to read a colleague's activity.
    """
    is_admin = current_user.get('role') == 'admin'
    own_username = str(current_user['username'])
    if not is_admin:
        return [own_username], False
    return (requested_users or None), True


def _parse_users_param(users: str | None) -> list[str] | None:
    if not users:
        return None
    parsed = [value.strip() for value in users.split(',') if value.strip()]
    return parsed or None


def _validate_date(value: str | None, field: str) -> None:
    if value is None:
        return
    try:
        datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail=f'Neispravan datum u polju {field}: očekivan format GGGG-MM-DD.')


@router.get('')
def get_activity_log(
    user: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Analyst actions, newest first (the on-screen list)."""
    is_admin = current_user.get('role') == 'admin'
    own_username = str(current_user['username'])
    requested_user = user if (is_admin and user) else (None if is_admin else own_username)

    entries = load_activity_log_entries(user=requested_user, case_id=case_id, limit=limit)

    return {
        'entries': entries,
        'scope': 'all' if is_admin else 'self',
        'filtered_user': requested_user,
        # Roster for the admin filter: accounts in the system PLUS any username that only
        # exists in the log (a removed account), so past actions stay selectable.
        'available_users': _report_user_roster() if is_admin else [],
    }


def _report_user_roster() -> list[str]:
    active = {str(item['username']) for item in list_users()}
    return sorted(active | set(known_log_users()))


@router.get('/report/preview')
def get_report_preview(
    users: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0),
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """How many records the chosen filter would produce.

    Drives the disabled/enabled state of the generate button, so an empty report is
    prevented before the click rather than reported as an error afterwards.
    """
    _validate_date(date_from, 'date_from')
    _validate_date(date_to, 'date_to')
    selected, is_admin = _resolve_users(current_user, _parse_users_param(users))

    entries = load_activity_log_entries(
        users=selected,
        date_from=date_from,
        date_to=date_to,
        tz_offset_minutes=tz_offset_minutes,
        limit=REPORT_LIMIT,
    )
    return {
        'count': len(entries),
        'period': format_period(date_from, date_to),
        'scope': 'all' if is_admin else 'self',
        'available_users': _report_user_roster() if is_admin else [],
        'active_users': sorted({str(item['username']) for item in list_users()}) if is_admin else [],
    }


def _build_report(
    current_user: dict[str, object],
    users: str | None,
    date_from: str | None,
    date_to: str | None,
    tz_offset_minutes: int,
    output_format: str,
) -> tuple[bytes, str, str]:
    _validate_date(date_from, 'date_from')
    _validate_date(date_to, 'date_to')
    selected, is_admin = _resolve_users(current_user, _parse_users_param(users))

    entries = load_activity_log_entries(
        users=selected,
        date_from=date_from,
        date_to=date_to,
        tz_offset_minutes=tz_offset_minutes,
        limit=REPORT_LIMIT,
    )
    if not entries:
        raise HTTPException(status_code=404, detail='Nema zabeleženih akcija za izabrani period, izveštaj nije generisan.')

    generated_by = str(current_user['username'])
    if output_format == 'csv':
        payload = build_activity_csv(entries, tz_offset_minutes=tz_offset_minutes).encode('utf-8-sig')
        media_type = 'text/csv; charset=utf-8'
    else:
        payload = build_activity_pdf(
            entries,
            generated_by=generated_by,
            date_from=date_from,
            date_to=date_to,
            tz_offset_minutes=tz_offset_minutes,
            selected_users=selected or [],
            scope='all' if is_admin else 'self',
        )
        media_type = 'application/pdf'

    write_audit_log(
        action='activity_report_exported',
        user=generated_by,
        details={
            'format': output_format,
            'entry_count': len(entries),
            'date_from': date_from,
            'date_to': date_to,
            'users': selected or 'svi',
        },
    )

    suffix = date_from or 'sve'
    file_name = f'activity_report_{suffix}.{output_format}'
    return payload, media_type, file_name


@router.get('/report.pdf')
def get_report_pdf(
    users: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0),
    current_user: dict[str, object] = Depends(get_current_user),
) -> Response:
    payload, media_type, file_name = _build_report(current_user, users, date_from, date_to, tz_offset_minutes, 'pdf')
    return Response(
        content=payload,
        media_type=media_type,
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )


@router.get('/report.csv')
def get_report_csv(
    users: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0),
    current_user: dict[str, object] = Depends(get_current_user),
) -> Response:
    payload, media_type, file_name = _build_report(current_user, users, date_from, date_to, tz_offset_minutes, 'csv')
    return Response(
        content=payload,
        media_type=media_type,
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )
