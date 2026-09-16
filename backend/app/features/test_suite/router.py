from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.evidence.audit_log import write_audit_log
from app.services.test_scenarios import (
    create_scenario,
    delete_scenario,
    get_scenario,
    load_scenarios,
    run_all_scenarios,
    update_scenario,
)
from app.services.test_suite_runner import list_suite_tests, run_suite


router = APIRouter(prefix='/tests', tags=['tests'])


class ScenarioTransaction(BaseModel):
    sender: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    amount: float = Field(gt=0)
    timestamp: str = Field(min_length=1)


class ScenarioExpectation(BaseModel):
    address: str = Field(min_length=1)
    expected_percentage: float = Field(ge=0, le=100)


class ScenarioRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ''
    transactions: list[ScenarioTransaction] = Field(min_length=1)
    seed_addresses: list[str] = Field(min_length=1)
    expectations: list[ScenarioExpectation] = Field(min_length=1)


@router.get('/suite')
def get_suite(current_user: dict[str, object] = Depends(require_admin)) -> dict[str, object]:
    """The fixed pytest suite, collected but not run. Read-only by design - these live in
    version-controlled files, so that a failing correctness test cannot be edited away
    through the UI."""
    return list_suite_tests()


@router.post('/suite/run')
def post_suite_run(current_user: dict[str, object] = Depends(require_admin)) -> dict[str, object]:
    result = run_suite()
    write_audit_log(
        action='test_suite_run',
        user=str(current_user['username']),
        details={
            'total': result.get('total'),
            'passed': result.get('passed'),
            'failed': result.get('failed'),
            'duration_ms': result.get('duration_ms'),
        },
    )
    return result


@router.get('/scenarios')
def get_scenarios(current_user: dict[str, object] = Depends(require_admin)) -> dict[str, object]:
    return {'scenarios': load_scenarios()}


@router.post('/scenarios')
def post_scenario(request: ScenarioRequest, current_user: dict[str, object] = Depends(require_admin)) -> dict[str, object]:
    scenario = create_scenario(
        name=request.name,
        description=request.description,
        transactions=[tx.model_dump() for tx in request.transactions],
        seed_addresses=request.seed_addresses,
        expectations=[item.model_dump() for item in request.expectations],
        created_by=str(current_user['username']),
    )
    write_audit_log(
        action='test_scenario_created',
        user=str(current_user['username']),
        details={'scenario_id': scenario['id'], 'name': scenario['name']},
    )
    return scenario


@router.put('/scenarios/{scenario_id}')
def put_scenario(
    scenario_id: str,
    request: ScenarioRequest,
    current_user: dict[str, object] = Depends(require_admin),
) -> dict[str, object]:
    scenario = update_scenario(
        scenario_id,
        name=request.name,
        description=request.description,
        transactions=[tx.model_dump() for tx in request.transactions],
        seed_addresses=request.seed_addresses,
        expectations=[item.model_dump() for item in request.expectations],
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail='Scenario nije pronađen.')
    write_audit_log(
        action='test_scenario_updated',
        user=str(current_user['username']),
        details={'scenario_id': scenario_id, 'name': scenario['name']},
    )
    return scenario


@router.delete('/scenarios/{scenario_id}', status_code=204)
def delete_scenario_route(scenario_id: str, current_user: dict[str, object] = Depends(require_admin)) -> None:
    # Read the name BEFORE deleting - afterwards there is nothing left to resolve the id
    # against, and "which scenario was removed" is the only useful part of this record.
    existing = get_scenario(scenario_id)
    if not delete_scenario(scenario_id):
        raise HTTPException(status_code=404, detail='Scenario nije pronađen.')
    write_audit_log(
        action='test_scenario_deleted',
        user=str(current_user['username']),
        details={'scenario_id': scenario_id, 'name': (existing or {}).get('name', '')},
    )


@router.post('/scenarios/run')
def post_scenarios_run(
    scenario_id: str | None = None,
    current_user: dict[str, object] = Depends(require_admin),
) -> dict[str, object]:
    result = run_all_scenarios(scenario_id)
    write_audit_log(
        action='test_scenarios_run',
        user=str(current_user['username']),
        details={
            'scenario_id': scenario_id,
            'total': result.get('total'),
            'passed': result.get('passed'),
            'failed': result.get('failed'),
            'errors': result.get('errors'),
        },
    )
    return result
