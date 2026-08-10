"""Runs the fixed pytest suite (backend/tests/) and reports structured results.

pytest runs in a SUBPROCESS rather than in-process: a test import that fails, a fixture
that leaks state, or a plugin that calls sys.exit would otherwise be able to take the API
server down with it. The results come back via `--junit-xml`, which is a stable, parseable
format - far safer than scraping pytest's human-readable console output, which changes
between versions.

Nothing here executes user-supplied input. The suite is whatever is committed under
backend/tests/, and there is deliberately no endpoint to modify it - see the module
docstring in app/services/test_scenarios.py for why that separation matters.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = BACKEND_ROOT / 'tests'

# A hung test must not hold an HTTP request (and a worker) open indefinitely.
RUN_TIMEOUT_SECONDS = 120


def _readable_name(classname: str, name: str) -> str:
    """"tests.test_taint_analysis.TestHaircutDilution" + "test_clean_inflow_dilutes_percentage"
    -> "TestHaircutDilution · clean inflow dilutes percentage"."""
    group = classname.split('.')[-1] if classname else ''
    readable = name.removeprefix('test_').replace('_', ' ')
    return f'{group} · {readable}' if group and group.startswith('Test') else readable


def _module_of(classname: str) -> str:
    parts = [part for part in classname.split('.') if part]
    module_parts = [part for part in parts if not part.startswith('Test')]
    return module_parts[-1] if module_parts else 'tests'


def list_suite_tests() -> dict[str, Any]:
    """Collects (without running) every test in the suite, so the page can show what
    exists before anything has been run."""
    if not TESTS_DIR.exists():
        return {'tests': [], 'total': 0, 'error': 'Direktorijum sa testovima ne postoji.'}

    try:
        completed = subprocess.run(
            [sys.executable, '-m', 'pytest', str(TESTS_DIR), '--collect-only', '-q', '--no-header'],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {'tests': [], 'total': 0, 'error': 'Isteklo vreme pri prikupljanju testova.'}

    tests: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        # "tests/test_taint_analysis.py::TestHaircutDilution::test_clean_inflow..."
        if '::' not in line or not line.startswith('tests'):
            continue
        parts = line.split('::')
        file_part = parts[0]
        name = parts[-1]
        group = parts[1] if len(parts) > 2 else ''
        tests.append({
            'id': line,
            'name': _readable_name(group, name),
            'raw_name': name,
            'group': group,
            'module': Path(file_part).stem,
        })

    return {'tests': tests, 'total': len(tests), 'error': None}


def run_suite() -> dict[str, Any]:
    if not TESTS_DIR.exists():
        return {
            'results': [], 'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0,
            'duration_ms': 0.0, 'ran_at': datetime.now(timezone.utc).isoformat(),
            'error': 'Direktorijum sa testovima ne postoji.',
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        report_path = Path(temp_dir) / 'report.xml'
        try:
            subprocess.run(
                [
                    sys.executable, '-m', 'pytest', str(TESTS_DIR),
                    f'--junit-xml={report_path}', '-q', '--no-header', '--tb=line',
                ],
                cwd=str(BACKEND_ROOT),
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {
                'results': [], 'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0,
                'duration_ms': 0.0, 'ran_at': datetime.now(timezone.utc).isoformat(),
                'error': f'Testovi nisu zavrseni u roku od {RUN_TIMEOUT_SECONDS} sekundi.',
            }

        if not report_path.exists():
            return {
                'results': [], 'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0,
                'duration_ms': 0.0, 'ran_at': datetime.now(timezone.utc).isoformat(),
                'error': 'pytest nije generisao izvestaj (moguce greska pri ucitavanju testova).',
            }

        return _parse_junit_report(report_path)


def _parse_junit_report(report_path: Path) -> dict[str, Any]:
    tree = ElementTree.parse(report_path)
    root = tree.getroot()
    suite = root.find('testsuite') if root.tag == 'testsuites' else root

    results: list[dict[str, Any]] = []
    for case in suite.findall('testcase') if suite is not None else []:
        classname = case.get('classname', '')
        name = case.get('name', '')

        failure = case.find('failure')
        error = case.find('error')
        skipped = case.find('skipped')

        if failure is not None or error is not None:
            node = failure if failure is not None else error
            status = 'failed'
            message = (node.get('message') or '').strip() or (node.text or '').strip()
        elif skipped is not None:
            status = 'skipped'
            message = (skipped.get('message') or '').strip()
        else:
            status = 'passed'
            message = None

        results.append({
            'id': f'{classname}::{name}',
            'name': _readable_name(classname, name),
            'raw_name': name,
            'group': classname.split('.')[-1] if classname.split('.')[-1].startswith('Test') else '',
            'module': _module_of(classname),
            'status': status,
            'message': message,
            'duration_ms': round(float(case.get('time', 0) or 0) * 1000, 2),
        })

    return {
        'results': results,
        'total': len(results),
        'passed': sum(1 for item in results if item['status'] == 'passed'),
        'failed': sum(1 for item in results if item['status'] == 'failed'),
        'skipped': sum(1 for item in results if item['status'] == 'skipped'),
        'duration_ms': round(float(suite.get('time', 0) or 0) * 1000, 2) if suite is not None else 0.0,
        'ran_at': datetime.now(timezone.utc).isoformat(),
        'error': None,
    }
