"""Business logic for investigation cases.

Sits between the API routes and the storage layer (`app/investigations/repository.py`):
generates ids/timestamps, applies partial edits, and keeps the index in sync with the
per-investigation records. Works in terms of the Pydantic models
(`app/investigations/models.py`); the repository below it only ever sees plain dicts.
"""

from __future__ import annotations

from app.investigations import repository
from app.investigations.models import (
    InvestigationCase,
    InvestigationCaseCreate,
    InvestigationCaseUpdate,
    utc_now_iso,
)


class InvestigationCaseNotFoundError(FileNotFoundError):
    """An investigation id that does not resolve to a stored record.

    Subclasses `FileNotFoundError` so it lines up with how the evidence `Case` service
    signals the same thing (`app/services/case_management.py`), while still being specific
    enough for a route to catch on its own.
    """


def _summary(case: InvestigationCase) -> dict[str, object]:
    """The projection stored in `index.json`. Identical to the full record today; kept as
    its own function so the index can stay lean once note/pin/link counts are added to the
    detail view later."""
    return {
        'id': case.id,
        'name': case.name,
        'description': case.description,
        'created_at': case.created_at,
        'updated_at': case.updated_at,
    }


def _reindex(case: InvestigationCase) -> None:
    entries = [entry for entry in repository.load_index() if entry.get('id') != case.id]
    entries.append(_summary(case))
    repository.save_index(entries)


def list_investigations() -> list[InvestigationCase]:
    """Every investigation case, newest-updated first (the ordering `save_index` keeps)."""
    return [InvestigationCase(**entry) for entry in repository.load_index()]


def get_investigation(investigation_id: str) -> InvestigationCase:
    record = repository.read_record(investigation_id)
    if record is None:
        raise InvestigationCaseNotFoundError(f'Istraga nije pronađena: {investigation_id}')
    return InvestigationCase(**record)


def create_investigation(data: InvestigationCaseCreate) -> InvestigationCase:
    now = utc_now_iso()
    case = InvestigationCase(
        name=data.name,
        description=data.description,
        created_at=now,
        updated_at=now,
    )
    repository.write_record(case.model_dump())
    _reindex(case)
    return case


def update_investigation(investigation_id: str, changes: InvestigationCaseUpdate) -> InvestigationCase:
    case = get_investigation(investigation_id)
    patch = changes.model_dump(exclude_unset=True)
    if not patch:
        return case

    if patch.get('name') is not None:
        case.name = patch['name']
    if 'description' in patch:
        case.description = patch['description']

    case.updated_at = utc_now_iso()
    repository.write_record(case.model_dump())
    _reindex(case)
    return case


def delete_investigation(investigation_id: str) -> None:
    if not repository.delete_record(investigation_id):
        raise InvestigationCaseNotFoundError(f'Istraga nije pronađena: {investigation_id}')
    entries = [entry for entry in repository.load_index() if entry.get('id') != investigation_id]
    repository.save_index(entries)
