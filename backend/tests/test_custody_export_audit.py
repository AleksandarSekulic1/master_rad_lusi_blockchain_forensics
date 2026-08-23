"""Provera da izvoz PDF-a iz lanca dokaza ostavlja trag u logu aktivnosti.

Skidanje PDF kopije lanca dokaza je samo po sebi radnja vredna beleženja - bez ovoga bi
opšti log aktivnosti pokazivao svako pokretanje analize koje je dotaklo neki dokaz, ali
ništa o tome ko je kasnije odštampao/izvezao zapis tih pristupa, niti kada.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.api.routes import custody as custody_routes
from app.evidence import audit_log, custody_evidence_log, custody_log


CURRENT_USER = {'id': '1', 'username': 'aco', 'role': 'analyst'}
FAKE_CASE = {'id': 'c1', 'name': 'Slučaj 1'}


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u prave log fajlove, niti da čitaju prave slučajeve sa diska."""
    monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')
    monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
    monkeypatch.setattr(custody_routes, 'get_case', lambda case_id: dict(FAKE_CASE))


class TestCustodyPdfExportIsAudited:
    """Izvoz PDF-a lanca dokaza se beleži u log aktivnosti"""

    def test_transaction_export_writes_audit_entry(self):
        """Izvoz po transakciji upisuje 'custody_pdf_exported' u log aktivnosti"""
        custody_log.append_custody_batch([{
            'timestamp': '2026-06-08T09:00:00+00:00', 'case_id': 'c1', 'case_name': 'Slučaj 1',
            'tx_id': 'tx-1', 'ime_prezime': 'aco', 'opis_radnje': 'Provera', 'signature_image': 'data:image/png;base64,AAA',
        }])

        custody_routes.export_transaction_custody_pdf(case_id='c1', tx_id='tx-1', current_user=CURRENT_USER)

        entries = audit_log.load_audit_log_entries(case_id='c1')
        assert len(entries) == 1
        assert entries[0]['action'] == 'custody_pdf_exported'
        assert entries[0]['user'] == 'aco'
        assert entries[0]['details']['scope'] == 'transaction'
        assert entries[0]['details']['tx_id'] == 'tx-1'

    def test_evidence_export_writes_audit_entry(self):
        """Izvoz po dokaznom fajlu upisuje 'custody_pdf_exported' u log aktivnosti"""
        custody_evidence_log.append_evidence_custody_batch([{
            'timestamp': '2026-06-08T09:00:00+00:00', 'case_id': 'c1', 'case_name': 'Slučaj 1',
            'evidence_stored_name': 'evidence.csv', 'evidence_file_name': 'original.csv',
            'ime_prezime': 'aco', 'opis_radnje': 'Provera', 'signature_image': 'data:image/png;base64,AAA',
        }])

        custody_routes.export_evidence_custody_pdf(case_id='c1', evidence_stored_name='evidence.csv', current_user=CURRENT_USER)

        entries = audit_log.load_audit_log_entries(case_id='c1')
        assert len(entries) == 1
        assert entries[0]['action'] == 'custody_pdf_exported'
        assert entries[0]['details']['scope'] == 'evidence_file'
        assert entries[0]['details']['evidence_stored_name'] == 'evidence.csv'

    def test_missing_chain_does_not_write_audit_entry(self):
        """Neuspešan izvoz (nepostojeća transakcija) ne ostavlja trag u logu aktivnosti"""
        with pytest.raises(Exception):  # noqa: B017, PT011 - FastAPI's HTTPException, not worth importing just for this
            custody_routes.export_transaction_custody_pdf(case_id='c1', tx_id='ne-postoji', current_user=CURRENT_USER)

        assert audit_log.load_audit_log_entries(case_id='c1') == []
