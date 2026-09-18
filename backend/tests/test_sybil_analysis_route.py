"""GET/POST /cases/{id}/sybil-analysis kroz prave HTTP rute.

Cilj: potvrditi da read-only GET ruta radi bez lanca dokaza (nema custody, nema upisa u
Log aktivnosti), dok POST .../run - isti obrazac kao DEX Swap/Token Approval/Behavioral -
upisuje u Log aktivnosti i, kad je custody prisutan, u lanac dokaza (custody_log +
custody_evidence_log), koristeći POSTOJEĆI `write_audit_log`/`record_custody_access`
mehanizam, ne nov/paralelan sistem.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.evidence import audit_log, custody_evidence_log, custody_log


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
    monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')
    from app.features.upload import router as upload_routes
    from app.services import case_management, user_management

    raw_dir = tmp_path / 'raw'
    monkeypatch.setattr(case_management, 'CASES_DIR', tmp_path / 'cases')
    monkeypatch.setattr(case_management, 'RAW_DIR', raw_dir)
    monkeypatch.setattr(upload_routes, 'RAW_DIR', raw_dir)
    users_dir = tmp_path_factory.mktemp('account-store')
    monkeypatch.setattr(user_management, '_users_path', lambda: users_dir / 'users.json')
    user_management.create_user(username='admin', password='admin123', role='admin')


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as instance:
        yield instance


@pytest.fixture
def auth(client):
    resp = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
    return {'Authorization': f'Bearer {resp.json()["access_token"]}'}


@pytest.fixture
def case_id(client, auth):
    return client.post('/api/v1/cases', headers=auth, json={'name': 'Sybil Analysis Route Test'}).json()['id']


CSV_HEADER = 'sender_address,recipient_address,amount,timestamp,metadata,function_name'


def upload_csv(client, auth, case_id, rows: str, filename: str = 'sybil.csv'):
    content = f'{CSV_HEADER}\n{rows}'
    return client.post(
        '/api/v1/upload/csv',
        headers=auth,
        data={'case_id': case_id},
        files={'file': (filename, content, 'text/csv')},
    )


CUSTODY = {
    'ime_prezime': 'Aleksandar Sekulić',
    'opis_radnje': 'Provera Sybil klastera',
    'signature_image': 'data:image/png;base64,AAA',
}

SYNCHRONIZED_ROWS = (
    '0xAddrA,0xClaimContract,1,2026-01-01T00:00:00Z,0xa1,claim\n'
    '0xAddrB,0xClaimContract,1,2026-01-01T00:00:20Z,0xa2,claim\n'
    '0xAddrC,0xClaimContract,1,2026-01-01T00:00:40Z,0xa3,claim\n'
)


class TestPassiveRoute:
    """GET /sybil-analysis - pasivna varijanta"""

    def test_get_returns_clusters_without_writing_any_log(self, client, auth, case_id):
        """GET vraća klastere ali ne piše ni u log aktivnosti ni u lanac dokaza"""
        upload_csv(client, auth, case_id, SYNCHRONIZED_ROWS)

        resp = client.get(f'/api/v1/cases/{case_id}/sybil-analysis', headers=auth)

        assert resp.status_code == 200
        body = resp.json()
        assert body['total_clusters'] == 1
        assert body['clusters'][0]['address_count'] == 3
        assert body['disclaimer']

        entries = [entry for entry in audit_log.load_audit_log_entries(case_id=case_id) if entry['action'] == 'sybil_analysis_run']
        assert entries == []
        assert custody_log.load_custody_entries(case_id=case_id) == []

    def test_get_with_unknown_address_returns_404(self, client, auth, case_id):
        """GET sa adresom koja ne postoji u evidenciji vraća 404"""
        upload_csv(client, auth, case_id, SYNCHRONIZED_ROWS)

        resp = client.get(f'/api/v1/cases/{case_id}/sybil-analysis', headers=auth, params={'address': '0xGhost'})

        assert resp.status_code == 404


class TestDeliberateRunRoute:
    """POST /sybil-analysis/run - deliberatna varijanta, lanac dokaza"""

    def test_run_with_custody_writes_activity_log_and_chain_of_custody(self, client, auth, case_id):
        """Pokretanje sa custody-jem upisuje log aktivnosti i lanac dokaza"""
        upload_csv(client, auth, case_id, SYNCHRONIZED_ROWS)

        resp = client.post(
            f'/api/v1/cases/{case_id}/sybil-analysis/run',
            headers=auth,
            json={'custody': CUSTODY},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body['total_clusters'] == 1
        assert body['addresses_flagged'] == 3
        assert body['custody_findings_recorded'] == 3

        entries = audit_log.load_audit_log_entries(case_id=case_id)
        run_entries = [entry for entry in entries if entry['action'] == 'sybil_analysis_run']
        assert len(run_entries) == 1
        details = run_entries[0]['details']
        assert details['total_clusters'] == 1
        assert details['addresses_flagged'] == 3
        assert details['custody_recorded'] is True
        assert details['custody_transaction_rows'] == 3
        assert details['sybil_findings_recorded'] == 3

        custody_entries = custody_log.load_custody_entries(case_id=case_id)
        assert len(custody_entries) == 3
        assert all('sybil_evidence' in entry for entry in custody_entries)
        assert {entry['sybil_evidence']['type'] for entry in custody_entries} == {'SYBIL_CLUSTER'}
        assert len(custody_evidence_log.load_evidence_custody_entries(case_id=case_id)) == 1

    def test_run_without_custody_logs_but_skips_chain_of_custody(self, client, auth, case_id):
        """Pokretanje bez custody-ja se loguje, ali ništa se ne upisuje u lanac dokaza"""
        upload_csv(client, auth, case_id, SYNCHRONIZED_ROWS)

        resp = client.post(f'/api/v1/cases/{case_id}/sybil-analysis/run', headers=auth, json={})

        assert resp.status_code == 200
        details = audit_log.load_audit_log_entries(case_id=case_id)[-1]['details']
        assert details['custody_recorded'] is False
        assert custody_log.load_custody_entries(case_id=case_id) == []

    def test_run_with_unknown_address_returns_404_and_writes_nothing(self, client, auth, case_id):
        """Adresa koja ne postoji u evidenciji vraća 404 i ne piše ništa"""
        upload_csv(client, auth, case_id, SYNCHRONIZED_ROWS)

        resp = client.post(
            f'/api/v1/cases/{case_id}/sybil-analysis/run',
            headers=auth,
            json={'address': '0xGhost', 'custody': CUSTODY},
        )

        assert resp.status_code == 404
        entries = [entry for entry in audit_log.load_audit_log_entries(case_id=case_id) if entry['action'] == 'sybil_analysis_run']
        assert entries == []
        assert custody_log.load_custody_entries(case_id=case_id) == []
