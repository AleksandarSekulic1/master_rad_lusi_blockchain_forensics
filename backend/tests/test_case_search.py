"""Filter po nazivu slučaja u list_cases() — koristi ga GET /api/v1/cases?search=..."""

from __future__ import annotations

import pytest

from app.services import case_management


@pytest.fixture(autouse=True)
def isolated_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(case_management, '_cases_root', lambda: tmp_path)
    for name in ('Hakovanje berze Q3', 'Sumnjiva LAUNDERING šema', 'Interni audit 2026'):
        case_management.create_case(name=name, analyst='tester')
    return tmp_path


def test_no_search_returns_everything():
    assert len(case_management.list_cases()) == 3


def test_search_is_case_insensitive_substring():
    hits = [case['name'] for case in case_management.list_cases(search='laundering')]
    assert hits == ['Sumnjiva LAUNDERING šema']


def test_search_matches_multiple():
    assert {case['name'] for case in case_management.list_cases(search='a')} == {
        'Hakovanje berze Q3',
        'Sumnjiva LAUNDERING šema',
        'Interni audit 2026',
    }


def test_blank_search_is_ignored():
    assert len(case_management.list_cases(search='   ')) == 3


def test_search_with_no_match_returns_empty():
    assert case_management.list_cases(search='nepostojeci-slucaj') == []
