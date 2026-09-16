from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.services.report_registry import compute_content_hash, register_report, verify_report


router = APIRouter(prefix='/reports', tags=['reports'])


class RegisterReportRequest(BaseModel):
    case_id: str = Field(min_length=1)
    case_name: str = ''
    declaration: str = ''
    # The analysis data the report is built from. Hashed as-is, so whatever the frontend
    # puts here is exactly what a later verification re-checks.
    content: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    # Which page's PDF this is ('taint' | 'pathfinding' | 'dex_swap', so far) - purely
    # descriptive, never part of the content hash. Without it, every signed report from
    # every page looked identical in Log aktivnosti ("report_signed", no way to tell which
    # kind), since only verification_code/content_hash were recorded before this field
    # existed. Optional and tolerant of unknown values, since this endpoint has no other
    # way to enforce a closed set of callers.
    report_type: str = ''


@router.post('/register')
def post_register(
    request: RegisterReportRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Registruje izveštaj pre generisanja PDF-a i vraća kontrolni broj koji se u njega štampa."""
    content_hash = compute_content_hash(request.content)
    analyst = str(current_user['username'])

    entry = register_report(
        case_id=request.case_id,
        case_name=request.case_name,
        content_hash=content_hash,
        analyst=analyst,
        declaration=request.declaration,
        summary=request.summary,
    )

    write_audit_log(
        action='report_signed',
        user=analyst,
        case_id=request.case_id,
        case_name=request.case_name,
        details={
            # request.summary first, so the fixed fields below always win on a key
            # collision (a caller's summary dict is free-form, not a contract).
            **request.summary,
            'report_type': request.report_type or 'unknown',
            'verification_code': entry['verification_code'],
            'content_hash': content_hash,
        },
    )

    return {
        'verification_code': entry['verification_code'],
        'content_hash': content_hash,
        'registered_at': entry['registered_at'],
        'analyst': analyst,
    }


@router.get('/verify')
def get_verify(
    code: str = Query(min_length=1),
    content_hash: str | None = Query(default=None),
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    """Provera da li izveštaj sa datim kontrolnim brojem postoji i da li mu se sadržaj poklapa."""
    return verify_report(verification_code=code, content_hash=content_hash)
