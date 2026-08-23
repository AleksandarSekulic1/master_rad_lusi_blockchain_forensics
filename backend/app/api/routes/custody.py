from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.evidence.custody_evidence_log import custody_chain_for_evidence, list_case_evidence
from app.evidence.custody_log import custody_chain_for_transaction, field_suggestions, list_case_transactions
from app.exports.custody_evidence_report import build_custody_evidence_pdf
from app.exports.custody_report import build_custody_pdf
from app.services.case_management import get_case


router = APIRouter(prefix='/cases/{case_id}/custody', tags=['custody'])


def _case_or_404(case_id: str) -> dict[str, object]:
    try:
        return get_case(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/suggestions')
def get_case_custody_suggestions(case_id: str) -> dict[str, object]:
    """Distinct prior values for the editable identification fields, so re-accessing the
    same evidence offers what was typed before instead of asking for an identical retype.
    Shared by both granularities below - it's the same dialog, same fields, either way."""
    _case_or_404(case_id)
    return field_suggestions(case_id)


# --- Per transaction (Лanac dokaza za pojedinačnu transakciju) ------------------------

@router.get('/transactions')
def get_case_custody_transactions(case_id: str) -> dict[str, object]:
    """Every transaction that has been accessed at least once in this case, most recently
    accessed first - the browsing list behind the "Lanac dokaza" page. Open to any
    authenticated user (analyst or admin), same as the rest of the case's data."""
    _case_or_404(case_id)
    return {'case_id': case_id, 'transactions': list_case_transactions(case_id)}


@router.get('/transactions/{tx_id}')
def get_transaction_custody_chain(case_id: str, tx_id: str) -> dict[str, object]:
    _case_or_404(case_id)
    chain = custody_chain_for_transaction(case_id, tx_id)
    if chain is None:
        raise HTTPException(status_code=404, detail=f'Nema zabelezenih pristupa transakciji {tx_id} u ovom slucaju.')
    return chain


@router.get('/transactions/{tx_id}/export.pdf')
def export_transaction_custody_pdf(
    case_id: str,
    tx_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> Response:
    case = _case_or_404(case_id)
    chain = custody_chain_for_transaction(case_id, tx_id)
    if chain is None:
        raise HTTPException(status_code=404, detail=f'Nema zabelezenih pristupa transakciji {tx_id} u ovom slucaju.')

    payload = build_custody_pdf(chain, chain['entries'])

    # Taking a copy of the chain of custody OUT of the application is itself an action
    # worth recording IN the chain of custody's own audit trail - otherwise the general
    # activity log would show every analysis run that touched this evidence, but nothing
    # about who later printed/exported the record of those accesses, or when.
    write_audit_log(
        action='custody_pdf_exported',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={'scope': 'transaction', 'tx_id': tx_id, 'entry_count': len(chain['entries'])},
    )

    file_name = f'{case_id}_{tx_id}_lanac_dokaza.pdf'
    return Response(
        content=payload,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )


# --- Per evidence file (Лanac dokaza za ceo dokazni fajl) ------------------------------

@router.get('/evidence')
def get_case_custody_evidence(case_id: str) -> dict[str, object]:
    """Every evidence file that has been accessed at least once in this case, most
    recently accessed first - the coarser sibling of /transactions above (see
    LANAC-DOKAZA.md for why both exist)."""
    _case_or_404(case_id)
    return {'case_id': case_id, 'evidence': list_case_evidence(case_id)}


@router.get('/evidence/{evidence_stored_name}')
def get_evidence_custody_chain(case_id: str, evidence_stored_name: str) -> dict[str, object]:
    _case_or_404(case_id)
    chain = custody_chain_for_evidence(case_id, evidence_stored_name)
    if chain is None:
        raise HTTPException(
            status_code=404,
            detail=f'Nema zabelezenih pristupa dokaznom fajlu {evidence_stored_name} u ovom slucaju.',
        )
    return chain


@router.get('/evidence/{evidence_stored_name}/export.pdf')
def export_evidence_custody_pdf(
    case_id: str,
    evidence_stored_name: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> Response:
    case = _case_or_404(case_id)
    chain = custody_chain_for_evidence(case_id, evidence_stored_name)
    if chain is None:
        raise HTTPException(
            status_code=404,
            detail=f'Nema zabelezenih pristupa dokaznom fajlu {evidence_stored_name} u ovom slucaju.',
        )

    payload = build_custody_evidence_pdf(chain, chain['entries'])

    # See the transaction-level export above for why this is logged here too.
    write_audit_log(
        action='custody_pdf_exported',
        user=str(current_user['username']),
        case_id=case_id,
        case_name=str(case.get('name') or ''),
        details={'scope': 'evidence_file', 'evidence_stored_name': evidence_stored_name, 'entry_count': len(chain['entries'])},
    )

    file_name = f'{case_id}_{evidence_stored_name}_lanac_dokaza.pdf'
    return Response(
        content=payload,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )
