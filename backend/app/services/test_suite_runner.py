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

import ast
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


def _split_docstring(docstring: str | None) -> tuple[str, str]:
    """First line is the human-readable title, the rest is the explanation.

    This is why the display name lives in the test's own docstring rather than in a
    translation table somewhere else: renaming a test or changing what it checks updates
    the UI automatically, so the two cannot drift apart.

    The explanation is re-flowed into paragraphs: line breaks in the source exist only
    because the file is wrapped at ~110 columns, and carrying them into the browser made
    sentences break at arbitrary points. Blank lines are real paragraph breaks and are
    kept as such.
    """
    if not docstring:
        return '', ''
    lines = [line.strip() for line in docstring.strip().splitlines()]
    title = lines[0] if lines else ''

    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines[1:]:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))

    return title, '\n\n'.join(paragraphs)


def _test_metadata() -> dict[str, dict[str, str]]:
    """Docstring + source of every test function, read straight from the test files.

    Parsed with `ast` rather than imported: reading the files can never execute them,
    which keeps this safe to call from a request handler. Deliberately NOT cached - the
    files are small, and a cache would keep serving the old docstring after a test is
    edited, which is exactly the kind of quiet drift these tests exist to prevent.
    """
    metadata: dict[str, dict[str, str]] = {}
    if not TESTS_DIR.exists():
        return metadata

    for path in sorted(TESTS_DIR.glob('test_*.py')):
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        def register(node: ast.FunctionDef, class_name: str, class_doc: str) -> None:
            title, body = _split_docstring(ast.get_docstring(node))
            metadata[node.name] = {
                'title': title,
                'explanation': body,
                'source': ast.get_source_segment(source, node) or '',
                'class_name': class_name,
                'group_title': class_doc,
                'module': path.stem,
                'line': str(node.lineno),
            }

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                group_title, _ = _split_docstring(ast.get_docstring(node))
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name.startswith('test_'):
                        register(child, node.name, group_title)
            elif isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
                register(node, '', '')

    return metadata


def _enrich(entry: dict[str, Any], raw_name: str) -> dict[str, Any]:
    """Attaches the Serbian title/explanation/source pulled from the test file itself."""
    meta = _test_metadata().get(raw_name)
    if not meta:
        # Fall back to a readable version of the function name so a test added without a
        # docstring still shows up (unnamed, but never hidden).
        entry['name'] = raw_name.removeprefix('test_').replace('_', ' ')
        entry['explanation'] = ''
        entry['source'] = ''
        entry['group_title'] = entry.get('group', '')
        return entry

    entry['name'] = meta['title'] or raw_name.removeprefix('test_').replace('_', ' ')
    entry['explanation'] = meta['explanation']
    entry['source'] = meta['source']
    entry['group_title'] = meta['group_title'] or meta['class_name']
    entry['module'] = meta['module']
    return entry


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

    tests: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        # "tests/test_taint_analysis.py::TestHaircutDilution::test_clean_inflow..."
        if '::' not in line or not line.startswith('tests'):
            continue
        parts = line.split('::')
        file_part = parts[0]
        name = parts[-1]
        group = parts[1] if len(parts) > 2 else ''
        tests.append(_enrich({
            'id': line,
            'raw_name': name,
            'group': group,
            'module': Path(file_part).stem,
        }, name))

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

        results.append(_enrich({
            'id': f'{classname}::{name}',
            'raw_name': name,
            'group': classname.split('.')[-1] if classname.split('.')[-1].startswith('Test') else '',
            'module': _module_of(classname),
            'status': status,
            'message': message,
            'duration_ms': round(float(case.get('time', 0) or 0) * 1000, 2),
        }, name))

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
