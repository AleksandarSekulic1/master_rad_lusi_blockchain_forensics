"""REST API for investigator links between two blockchain addresses.

An investigator link is a **suspected relation** recorded by an investigator on the basis
of OFF-CHAIN evidence - an "investigator association", explicitly NOT a proven blockchain
fact and NOT a transaction-graph edge. It is an additional forensic layer: the blockchain
graph edges are never modified, and links are stored only under
``data/investigations/<id>/links.json``.

Nested under the investigation container from step 1:
``/investigations/{investigation_id}/links``. Any authenticated user, same access level
as the rest of the investigator layer.

This module, together with `models.py`, `repository.py` and `service.py` in this same
package, is the complete "investigator links" feature slice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.evidence.audit_log import write_audit_log
from app.features.investigation_links import service
from app.features.investigation_links.models import (
    InvestigatorLink,
    InvestigatorLinkCreate,
    InvestigatorLinkUpdate,
)
from app.features.investigation_links.service import InvestigatorLinkNotFoundError
from app.investigations.service import InvestigationCaseNotFoundError

router = APIRouter(prefix='/investigations/{investigation_id}/links', tags=['investigator-links'])

# Missing investigation and missing link both surface to the client as 404.
_NOT_FOUND = (InvestigationCaseNotFoundError, InvestigatorLinkNotFoundError)

# Rendered on every list response so a consumer can never mistake these records for
# blockchain facts. Same "carry the disclaimer, not just the data" pattern the DEX swap
# and behavioral-analysis endpoints already use.
_LINKS_DISCLAIMER = (
    'Investigator link je pretpostavljena veza (suspected relation / investigator association) '
    'koju je istražitelj zabeležio na osnovu vanlančanih (off-chain) dokaza - NIJE dokazana '
    'blockchain činjenica i NIJE grana transakcionog grafa.'
)


def _audit_details(investigation_id: str, link: InvestigatorLink) -> dict[str, object]:
    return {
        'investigation_id': investigation_id,
        'link_id': link.id,
        'source_address': link.source_address,
        'target_address': link.target_address,
        'confidence': link.confidence,
    }


@router.get('')
def get_links(
    investigation_id: str,
    address: str | None = Query(
        default=None,
        description='Exact address to filter by; returns links where it is EITHER endpoint (the association is undirected).',
    ),
) -> dict[str, object]:
    try:
        links = service.list_links(investigation_id, address=address)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        'investigation_id': investigation_id,
        'address': address,
        'disclaimer': _LINKS_DISCLAIMER,
        'links': links,
    }


@router.post('')
def post_link(
    investigation_id: str,
    request: InvestigatorLinkCreate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigatorLink:
    try:
        link = service.create_link(investigation_id, request, author=str(current_user['username']))
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_link_created',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, link),
    )
    return link


@router.get('/{link_id}')
def get_link(investigation_id: str, link_id: str) -> InvestigatorLink:
    try:
        return service.get_link(investigation_id, link_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch('/{link_id}')
def patch_link(
    investigation_id: str,
    link_id: str,
    request: InvestigatorLinkUpdate,
    current_user: dict[str, object] = Depends(get_current_user),
) -> InvestigatorLink:
    try:
        link = service.update_link(investigation_id, link_id, request)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_link_updated',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, link),
    )
    return link


@router.delete('/{link_id}', status_code=204)
def delete_link_route(
    investigation_id: str,
    link_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> None:
    try:
        # Read it first so the deleted link's endpoints can go into the activity log.
        link = service.get_link(investigation_id, link_id)
        service.delete_link(investigation_id, link_id)
    except _NOT_FOUND as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit_log(
        action='investigator_link_deleted',
        user=str(current_user['username']),
        details=_audit_details(investigation_id, link),
    )
