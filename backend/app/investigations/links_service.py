"""Business logic for investigator links.

Sits between the API routes and ``links_repository.py``. Generates ids/timestamps, keeps
``updated_at`` moving without ever touching ``created_at``, and always verifies the parent
investigation exists first (reusing step 1's service), so a link can never be created
against, or read from, an investigation that is not there.

An investigator link is an additional forensic layer on top of the blockchain graph - a
**suspected relation** between two addresses based on off-chain evidence. This module
never imports or touches any analytics / graph code.
"""

from __future__ import annotations

from app.investigations import links_repository
from app.investigations.links_models import (
    InvestigatorLink,
    InvestigatorLinkCreate,
    InvestigatorLinkUpdate,
    utc_now_iso,
)
from app.investigations.service import get_investigation


class InvestigatorLinkNotFoundError(FileNotFoundError):
    """A link id that does not exist within the given investigation. Subclasses
    FileNotFoundError, same convention as InvestigationCaseNotFoundError."""


def _load_models(investigation_id: str) -> list[InvestigatorLink]:
    return [InvestigatorLink(**row) for row in links_repository.load_links(investigation_id)]


def _persist(investigation_id: str, links: list[InvestigatorLink]) -> None:
    links_repository.save_links(investigation_id, [link.model_dump() for link in links])


def list_links(investigation_id: str, *, address: str | None = None) -> list[InvestigatorLink]:
    """Links for one investigation, newest created first. With ``address``, only links
    where that address is EITHER endpoint (the association is undirected) - exact match,
    whitespace-trimmed, case-sensitive."""
    get_investigation(investigation_id)  # -> InvestigationCaseNotFoundError -> HTTP 404

    links = _load_models(investigation_id)
    if address is not None:
        links = [link for link in links if link.involves(address)]
    return sorted(links, key=lambda link: (link.created_at, link.id), reverse=True)


def get_link(investigation_id: str, link_id: str) -> InvestigatorLink:
    get_investigation(investigation_id)
    for link in _load_models(investigation_id):
        if link.id == link_id:
            return link
    raise InvestigatorLinkNotFoundError(f'Investigator link nije pronađen: {link_id}')


def create_link(investigation_id: str, data: InvestigatorLinkCreate, *, author: str) -> InvestigatorLink:
    get_investigation(investigation_id)

    now = utc_now_iso()
    link = InvestigatorLink(
        investigation_id=investigation_id,
        source_address=data.source_address,
        target_address=data.target_address,
        reason=data.reason,
        evidence=data.evidence,
        confidence=data.confidence,
        author=author,
        created_at=now,
        updated_at=now,
    )
    links = _load_models(investigation_id)
    links.append(link)
    _persist(investigation_id, links)
    return link


def update_link(investigation_id: str, link_id: str, changes: InvestigatorLinkUpdate) -> InvestigatorLink:
    get_investigation(investigation_id)

    patch = changes.model_dump(exclude_unset=True)
    links = _load_models(investigation_id)
    for index, link in enumerate(links):
        if link.id != link_id:
            continue
        if not patch:
            return link
        if patch.get('reason') is not None:
            link.reason = patch['reason']
        if patch.get('evidence') is not None:
            link.evidence = patch['evidence']
        if patch.get('confidence') is not None:
            link.confidence = patch['confidence']
        link.updated_at = utc_now_iso()
        links[index] = link
        _persist(investigation_id, links)
        return link
    raise InvestigatorLinkNotFoundError(f'Investigator link nije pronađen: {link_id}')


def delete_link(investigation_id: str, link_id: str) -> None:
    get_investigation(investigation_id)

    links = _load_models(investigation_id)
    remaining = [link for link in links if link.id != link_id]
    if len(remaining) == len(links):
        raise InvestigatorLinkNotFoundError(f'Investigator link nije pronađen: {link_id}')
    _persist(investigation_id, remaining)
