"""Validation scenarios: declarative reference cases the taint algorithm is checked against.

A scenario is DATA, never code - a list of transactions, the seed addresses, and the
percentages each address is expected to end up with. The runner feeds them through the
real taint plugin and compares. That distinction is deliberate: an admin can add, edit and
delete scenarios from the UI without the server ever executing anything they wrote, and
without turning the correctness evidence into something that can be edited until it
passes (the fixed pytest suite in backend/tests/ covers that side, and is not editable).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import networkx as nx

from app.analytics.plugins.taint_analysis import run_taint_analysis
from app.paths import DATA_DIR

# Percentages are rounded to 2 decimals by the algorithm, so anything closer than this is
# the same number as far as a comparison is concerned.
COMPARISON_TOLERANCE = 0.01


def _scenarios_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / 'test_scenarios.json'


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_scenarios() -> list[dict[str, Any]]:
    """Seeded once, only when no scenario file exists at all - so a fresh install has
    something meaningful on the page, but deleting them afterwards makes them stay
    deleted rather than reappearing on the next read.

    These mirror the documented scenarios in BLOCKCHAIN-UVOZ.md 6.1, which is why their
    expected numbers can be checked by hand against the thesis text.
    """
    now = _timestamp()
    return [
        {
            'id': uuid4().hex[:12],
            'name': 'Mikser razblazuje cistim prilivom',
            'description': 'Ukradenih 1000 se mesa sa 500 cistih - ocekuje se pad na 66.67% (scenario 6.1).',
            'transactions': [
                {'sender': '0xThief', 'recipient': '0xMixer', 'amount': 1000, 'timestamp': '2026-03-01T00:00:00Z'},
                {'sender': '0xCleanUser', 'recipient': '0xMixer', 'amount': 500, 'timestamp': '2026-03-01T00:05:00Z'},
                {'sender': '0xMixer', 'recipient': '0xExitWallet', 'amount': 750, 'timestamp': '2026-03-01T00:10:00Z'},
            ],
            'seed_addresses': ['0xThief'],
            'expectations': [
                {'address': '0xThief', 'expected_percentage': 100.0},
                {'address': '0xMixer', 'expected_percentage': 66.67},
                {'address': '0xExitWallet', 'expected_percentage': 66.67},
                {'address': '0xCleanUser', 'expected_percentage': 0.0},
            ],
            'created_at': now,
            'updated_at': now,
            'created_by': 'system',
        },
        {
            'id': uuid4().hex[:12],
            'name': 'Dva nezavisna izvora se spajaju (60/40)',
            'description': 'Dva hakera uplacuju 600 i 400 na isti hub - ukupno 100%, ali podeljeno 60/40.',
            'transactions': [
                {'sender': '0xHacker1', 'recipient': '0xLaunderingHub', 'amount': 600, 'timestamp': '2026-04-01T00:00:00Z'},
                {'sender': '0xHacker2', 'recipient': '0xLaunderingHub', 'amount': 400, 'timestamp': '2026-04-01T00:05:00Z'},
                {'sender': '0xLaunderingHub', 'recipient': '0xFinalDestination', 'amount': 800, 'timestamp': '2026-04-01T00:10:00Z'},
            ],
            'seed_addresses': ['0xHacker1', '0xHacker2'],
            'expectations': [
                {'address': '0xLaunderingHub', 'expected_percentage': 100.0},
                {'address': '0xFinalDestination', 'expected_percentage': 100.0},
            ],
            'created_at': now,
            'updated_at': now,
            'created_by': 'system',
        },
    ]


def load_scenarios() -> list[dict[str, Any]]:
    path = _scenarios_path()
    if not path.exists():
        defaults = _default_scenarios()
        _save_scenarios(defaults)
        return defaults

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return []

    return payload if isinstance(payload, list) else []


def _save_scenarios(scenarios: list[dict[str, Any]]) -> None:
    _scenarios_path().write_text(json.dumps(scenarios, ensure_ascii=False, indent=2), encoding='utf-8')


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    return next((item for item in load_scenarios() if item.get('id') == scenario_id), None)


def create_scenario(*, name: str, description: str, transactions: list[dict], seed_addresses: list[str],
                    expectations: list[dict], created_by: str) -> dict[str, Any]:
    now = _timestamp()
    scenario = {
        'id': uuid4().hex[:12],
        'name': name,
        'description': description,
        'transactions': transactions,
        'seed_addresses': seed_addresses,
        'expectations': expectations,
        'created_at': now,
        'updated_at': now,
        'created_by': created_by,
    }
    scenarios = load_scenarios()
    scenarios.append(scenario)
    _save_scenarios(scenarios)
    return scenario


def update_scenario(scenario_id: str, *, name: str, description: str, transactions: list[dict],
                    seed_addresses: list[str], expectations: list[dict]) -> dict[str, Any] | None:
    scenarios = load_scenarios()
    for scenario in scenarios:
        if scenario.get('id') != scenario_id:
            continue
        scenario.update({
            'name': name,
            'description': description,
            'transactions': transactions,
            'seed_addresses': seed_addresses,
            'expectations': expectations,
            'updated_at': _timestamp(),
        })
        _save_scenarios(scenarios)
        return scenario
    return None


def delete_scenario(scenario_id: str) -> bool:
    scenarios = load_scenarios()
    remaining = [item for item in scenarios if item.get('id') != scenario_id]
    if len(remaining) == len(scenarios):
        return False
    _save_scenarios(remaining)
    return True


def _graph_from_transactions(transactions: list[dict[str, Any]]) -> nx.DiGraph:
    """Same edge shape the CSV ingestion produces, so a scenario exercises the algorithm
    through its real input format rather than a special test-only path."""
    graph = nx.DiGraph()
    for tx in transactions:
        sender = str(tx.get('sender', '')).strip()
        recipient = str(tx.get('recipient', '')).strip()
        if not sender or not recipient:
            continue
        entry = {
            'amount': float(tx.get('amount', 0) or 0),
            'timestamp': tx.get('timestamp'),
            'metadata': None,
        }
        if graph.has_edge(sender, recipient):
            graph[sender][recipient]['transactions'].append(entry)
        else:
            graph.add_edge(sender, recipient, transactions=[entry])
    return graph


def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Runs one scenario through the real taint algorithm and compares every expectation.

    A scenario that blows up (bad timestamps, empty transaction list, ...) is reported as
    an error rather than raising - one broken scenario must not stop the rest of the suite
    from running.
    """
    started = datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    error: str | None = None

    try:
        graph = _graph_from_transactions(scenario.get('transactions', []))
        if graph.number_of_edges() == 0:
            raise ValueError('Scenario nema nijednu validnu transakciju.')

        result = run_taint_analysis(
            graph=graph,
            seed_addresses=list(scenario.get('seed_addresses', [])),
            seed_from_blacklist=False,
        )
        actual_by_address = {item['address']: item['taint_percentage'] for item in result['results']}

        for expectation in scenario.get('expectations', []):
            address = str(expectation.get('address', '')).strip()
            expected = float(expectation.get('expected_percentage', 0) or 0)
            # An address the scenario never mentions is a failure with a clear message,
            # not a silent 0% pass - a typo in the address would otherwise look correct
            # whenever the expected value happened to be 0.
            present = address in actual_by_address
            actual = actual_by_address.get(address)
            passed = present and abs(float(actual) - expected) <= COMPARISON_TOLERANCE
            checks.append({
                'address': address,
                'expected_percentage': expected,
                'actual_percentage': actual,
                'passed': passed,
                'message': None if present else 'Adresa se ne pojavljuje u rezultatu analize.',
            })
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never swallowed
        error = str(exc)

    duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    passed_count = sum(1 for check in checks if check['passed'])

    return {
        'scenario_id': scenario.get('id'),
        'name': scenario.get('name'),
        'description': scenario.get('description'),
        'status': 'error' if error else ('passed' if checks and passed_count == len(checks) else 'failed'),
        'error': error,
        'checks': checks,
        'passed_checks': passed_count,
        'total_checks': len(checks),
        'duration_ms': round(duration_ms, 2),
    }


def run_all_scenarios(scenario_id: str | None = None) -> dict[str, Any]:
    scenarios = load_scenarios()
    if scenario_id:
        scenarios = [item for item in scenarios if item.get('id') == scenario_id]

    results = [run_scenario(scenario) for scenario in scenarios]
    return {
        'results': results,
        'total': len(results),
        'passed': sum(1 for item in results if item['status'] == 'passed'),
        'failed': sum(1 for item in results if item['status'] == 'failed'),
        'errors': sum(1 for item in results if item['status'] == 'error'),
        'duration_ms': round(sum(item['duration_ms'] for item in results), 2),
        'ran_at': _timestamp(),
    }
