"""Uklanjanje pojedinačnog dokaza iz slučaja — inverz CSV otpremanja
(koristi ga DELETE /api/v1/cases/{case_id}/evidence/{stored_name})."""

from __future__ import annotations

import pytest

from app.services import case_management


@pytest.fixture(autouse=True)
def isolated_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(case_management, '_cases_root', lambda: tmp_path)


def _make_case_with_two_files() -> str:
    case = case_management.create_case(name='Test predmet', analyst='tester')
    case_id = str(case['id'])
    case_management.append_evidence(
        case_id,
        original_name='a.csv',
        stored_name='stored_a.csv',
        size_bytes=100,
        sha256_hash='aaa',
        analyst='tester',
    )
    case_management.append_evidence(
        case_id,
        original_name='b.csv',
        stored_name='stored_b.csv',
        size_bytes=250,
        sha256_hash='bbb',
        analyst='tester',
    )
    return case_id


def test_remove_evidence_drops_the_entry_and_renormalizes():
    case_id = _make_case_with_two_files()

    updated = case_management.remove_evidence(case_id, 'stored_a.csv')

    stored_names = [entry['stored_name'] for entry in updated['evidence']]
    assert stored_names == ['stored_b.csv']
    assert updated['evidence_count'] == 1
    assert updated['total_size_bytes'] == 250


def test_remove_evidence_persists_to_disk():
    case_id = _make_case_with_two_files()
    case_management.remove_evidence(case_id, 'stored_b.csv')

    reloaded = case_management.get_case(case_id)
    assert [entry['stored_name'] for entry in reloaded['evidence']] == ['stored_a.csv']


def test_remove_unknown_evidence_raises():
    case_id = _make_case_with_two_files()
    with pytest.raises(FileNotFoundError):
        case_management.remove_evidence(case_id, 'stored_nepostojeci.csv')


def test_remove_last_evidence_clears_derived_fields():
    case = case_management.create_case(name='Solo', analyst='tester')
    case_id = str(case['id'])
    case_management.append_evidence(
        case_id,
        original_name='only.csv',
        stored_name='only_stored.csv',
        size_bytes=42,
        sha256_hash='ccc',
        analyst='tester',
    )

    updated = case_management.remove_evidence(case_id, 'only_stored.csv')

    assert updated['evidence'] == []
    assert updated['evidence_count'] == 0
    assert updated['total_size_bytes'] == 0
    assert updated['last_imported_at'] is None
