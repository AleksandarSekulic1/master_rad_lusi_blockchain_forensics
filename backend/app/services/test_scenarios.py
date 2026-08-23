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
    ] + [
        {**scenario, 'id': uuid4().hex[:12], 'created_at': now, 'updated_at': now, 'created_by': 'system'}
        for scenario in EXTRA_DEFAULT_SCENARIOS
    ]


def _tx(sender: str, recipient: str, amount: float, timestamp: str) -> dict[str, Any]:
    return {'sender': sender, 'recipient': recipient, 'amount': amount, 'timestamp': timestamp}


def _expect(address: str, percentage: float) -> dict[str, Any]:
    return {'address': address, 'expected_percentage': percentage}


# Each scenario pins down a DIFFERENT property of the model, not a variation of the same
# one - a library of near-identical cases would inflate the count without proving more.
EXTRA_DEFAULT_SCENARIOS: list[dict[str, Any]] = [
    {
        'name': 'Odliv ne menja procenat posiljaoca',
        'description': 'Mikser posalje deo dalje - njegov sopstveni procenat ostaje 66.67%, jer odliv odnosi '
                       'prljavo i cisto u istoj srazmeri (definisuca osobina haircut modela).',
        'transactions': [
            _tx('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            _tx('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
            _tx('0xMixer', '0xExit', 750, '2026-03-01T00:10:00Z'),
        ],
        'seed_addresses': ['0xThief'],
        'expectations': [_expect('0xMixer', 66.67), _expect('0xExit', 66.67)],
    },
    {
        'name': 'Visestruko razblazivanje kroz lanac',
        'description': 'Svaki sledeci cvor prima jednaku kolicinu cistog novca, pa procenat pada 100% -> 50% -> 25%. '
                       'Provera da se razblazivanje slaze kroz vise koraka, a ne samo jednom.',
        'transactions': [
            _tx('0xOrigin', '0xHop1', 100, '2026-03-01T00:00:00Z'),
            _tx('0xClean1', '0xHop1', 100, '2026-03-01T00:05:00Z'),
            _tx('0xHop1', '0xHop2', 100, '2026-03-01T00:10:00Z'),
            _tx('0xClean2', '0xHop2', 100, '2026-03-01T00:15:00Z'),
        ],
        'seed_addresses': ['0xOrigin'],
        'expectations': [_expect('0xHop1', 50.0), _expect('0xHop2', 25.0)],
    },
    {
        'name': 'Hronologija: cist priliv PRE prljavog',
        'description': 'Cist novac stigne prvi, pa tek onda prljav. Rezultat mora biti isti 50% kao i u obrnutom '
                       'redosledu - inace bi algoritam obradjivao granu-po-granu umesto po vremenu.',
        'transactions': [
            _tx('0xCleanFirst', '0xTarget', 100, '2026-03-01T00:00:00Z'),
            _tx('0xDirty', '0xTarget', 100, '2026-03-01T00:05:00Z'),
        ],
        'seed_addresses': ['0xDirty'],
        'expectations': [_expect('0xTarget', 50.0)],
    },
    {
        'name': 'Tri izvora se spajaju (50/30/20)',
        'description': 'Provera da raspodela po izvorima radi i sa vise od dva izvora - ukupno 100%, ali podeljeno '
                       'na tri nejednaka udela.',
        'transactions': [
            _tx('0xSrcA', '0xHub', 500, '2026-03-01T00:00:00Z'),
            _tx('0xSrcB', '0xHub', 300, '2026-03-01T00:05:00Z'),
            _tx('0xSrcC', '0xHub', 200, '2026-03-01T00:10:00Z'),
        ],
        'seed_addresses': ['0xSrcA', '0xSrcB', '0xSrcC'],
        'expectations': [_expect('0xHub', 100.0)],
    },
    {
        'name': 'Samo jedan od tri izvora je oznacen',
        'description': 'Isti podaci kao prethodni scenario, ali samo 0xSrcB je izvor. Procenat mora pasti na tacno '
                       'njegov udeo (300/1000 = 30%) - dokaz da rezultat zavisi od izbora izvora.',
        'transactions': [
            _tx('0xSrcA', '0xHub', 500, '2026-03-01T00:00:00Z'),
            _tx('0xSrcB', '0xHub', 300, '2026-03-01T00:05:00Z'),
            _tx('0xSrcC', '0xHub', 200, '2026-03-01T00:10:00Z'),
        ],
        'seed_addresses': ['0xSrcB'],
        'expectations': [_expect('0xHub', 30.0)],
    },
    {
        'name': 'Adresa koja salje pre nego sto primi (zastita od >100%)',
        'description': 'Adresa prvo potrosi sredstva koja je imala pre evidencije, pa tek onda primi zaprljana. '
                       'Ranije je davala nemogucih 111.11% - sada mora biti tacno 100%.',
        'transactions': [
            _tx('0xSpender', '0xSomeone', 5, '2026-03-01T00:00:00Z'),
            _tx('0xSpender', '0xSomeone', 5, '2026-03-01T00:01:00Z'),
            _tx('0xThief2', '0xSpender', 50, '2026-03-01T00:02:00Z'),
            _tx('0xThief2', '0xSpender', 50, '2026-03-01T00:03:00Z'),
        ],
        'seed_addresses': ['0xThief2'],
        'expectations': [_expect('0xSpender', 100.0)],
    },
    {
        'name': 'Peel chain - glavni tok ostaje skoro nerazblazen',
        'description': 'Pri svakom koraku se odvaja mali deo, a ostatak ide dalje. Za razliku od miksera, procenat '
                       'ostaje 100% jer se nista cisto ne mesa - samo se iznos smanjuje.',
        'transactions': [
            _tx('0xPeelOrigin', '0xPeel1', 1000, '2026-03-01T00:00:00Z'),
            _tx('0xPeel1', '0xSmall1', 50, '2026-03-01T00:05:00Z'),
            _tx('0xPeel1', '0xPeel2', 950, '2026-03-01T00:06:00Z'),
            _tx('0xPeel2', '0xSmall2', 50, '2026-03-01T00:10:00Z'),
            _tx('0xPeel2', '0xPeel3', 900, '2026-03-01T00:11:00Z'),
        ],
        'seed_addresses': ['0xPeelOrigin'],
        'expectations': [
            _expect('0xPeel1', 100.0), _expect('0xPeel2', 100.0), _expect('0xPeel3', 100.0),
            _expect('0xSmall1', 100.0),
        ],
    },
    {
        'name': 'Nedodirnuta adresa ostaje na nuli',
        'description': 'Transakcije koje nemaju veze sa izvorom ne smeju da podignu procenat - zastita od modela '
                       'koji bi "prljao" ceo graf.',
        'transactions': [
            _tx('0xThief3', '0xMule', 100, '2026-03-01T00:00:00Z'),
            _tx('0xStranger', '0xOtherParty', 400, '2026-03-01T00:05:00Z'),
        ],
        'seed_addresses': ['0xThief3'],
        'expectations': [_expect('0xMule', 100.0), _expect('0xOtherParty', 0.0), _expect('0xStranger', 0.0)],
    },
    {
        'name': 'Kruzni tok - novac se vraca izvoru',
        'description': 'Sredstva prodju kroz lanac i vrate se na polaznu adresu. Provera da algoritam ne upadne u '
                       'petlju i da izvor ostane 100%.',
        'transactions': [
            _tx('0xLoopSeed', '0xA', 100, '2026-03-01T00:00:00Z'),
            _tx('0xA', '0xB', 100, '2026-03-01T00:05:00Z'),
            _tx('0xB', '0xLoopSeed', 100, '2026-03-01T00:10:00Z'),
        ],
        'seed_addresses': ['0xLoopSeed'],
        'expectations': [_expect('0xLoopSeed', 100.0), _expect('0xA', 100.0), _expect('0xB', 100.0)],
    },
    {
        'name': 'Unovcavanje na poznatoj berzi (Binance)',
        'description': 'Lanac zavrsava na stvarnoj Binance adresi iz baze poznatih entiteta. Provera da propagacija '
                       'kroz vise skokova radi i da adresa dobije 100%.',
        'transactions': [
            _tx('0xExchHacker', '0xExchMule', 200, '2026-06-01T00:00:00Z'),
            _tx('0xExchMule', '0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be', 200, '2026-06-01T00:05:00Z'),
        ],
        'seed_addresses': ['0xExchHacker'],
        'expectations': [
            _expect('0xExchMule', 100.0),
            _expect('0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be', 100.0),
        ],
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
