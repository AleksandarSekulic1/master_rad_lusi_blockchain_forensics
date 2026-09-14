"""POST /cases/{id}/token-approval-analysis/run kroz pravu HTTP rutu - SUCCESS/FAILED log.

Cilj: potvrditi da pokretanje Token Approval analize preko prave rute (ne direktnim pozivom
interne funkcije) upisuje u Log aktivnosti tačno ono što je traženo - status (SUCCESS/
FAILED), analiziranu adresu, vreme, i (za SUCCESS) broj odobrenja po kategoriji
(unlimited/active/revoked/used/unused/rizičnih) - koristeći POSTOJEĆI `write_audit_log`
mehanizam, ne nov/paralelan logging sistem.

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
    # upload.py imports its own RAW_DIR reference directly (see app.paths) - must be
    # isolated separately, or an uploaded CSV would land in the real data/raw/ directory
    # while case_management looks for it under the isolated one above.
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
    return client.post('/api/v1/cases', headers=auth, json={'name': 'Token Approval Run Route Test'}).json()['id']


CSV_HEADER = 'sender_address,recipient_address,amount,timestamp,metadata,event_type,token_address,spender_address,is_unlimited'


def upload_csv(client, auth, case_id, rows: str, filename: str = 'ta.csv'):
    content = f'{CSV_HEADER}\n{rows}'
    return client.post(
        '/api/v1/upload/csv',
        headers=auth,
        data={'case_id': case_id},
        files={'file': (filename, content, 'text/csv')},
    )


CUSTODY = {
    'ime_prezime': 'Aleksandar Sekulić',
    'opis_radnje': 'Provera Token Approval nalaza',
    'signature_image': 'data:image/png;base64,AAA',
}


class TestSuccessLog:
    """SUCCESS log - status i brojevi po kategoriji"""

    def test_success_log_has_status_and_full_counts(self, client, auth, case_id):
        """Uspešno pokretanje upisuje status SUCCESS sa svim traženim brojevima"""
        upload_csv(
            client, auth, case_id,
            # 1: unlimited, used, never revoked (active)
            '0xOwnerA,0xSpenderA,1000000000000000000,2026-01-01T00:00:00Z,0xa1,approve,0xTokenA,0xSpenderA,true\n'
            '0xOwnerA,0xDest,500000000000000000,2026-01-01T01:00:00Z,0xt1,transferFrom,0xTokenA,0xSpenderA,\n'
            # 2: normal amount, never used, revoked
            '0xOwnerB,0xSpenderB,100,2026-01-02T00:00:00Z,0xa2,approve,0xTokenB,0xSpenderB,\n'
            '0xOwnerB,0xSpenderB,0,2026-01-03T00:00:00Z,0xa2rev,approve,0xTokenB,0xSpenderB,\n',
        )

        resp = client.post(
            f'/api/v1/cases/{case_id}/token-approval-analysis/run',
            headers=auth,
            json={'custody': CUSTODY},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['correlation_count'] == 2

        entries = audit_log.load_audit_log_entries(case_id=case_id)
        run_entries = [entry for entry in entries if entry['action'] == 'token_approval_analysis_run']
        assert len(run_entries) == 1
        details = run_entries[0]['details']

        assert details['status'] == 'SUCCESS'
        assert details['total_approvals'] == 2
        assert details['unlimited_approvals'] == 1
        assert details['active_approvals'] == 1
        assert details['revoked_approvals'] == 1
        assert details['used_approvals'] == 1
        assert details['unused_approvals'] == 1
        assert details['custody_recorded'] is True
        assert details['token_approval_findings_recorded'] == 2
        # 'timestamp' at the entry level (not inside details) already exists on every
        # audit log row - confirms the SUCCESS entry carries it like every other action.
        assert run_entries[0]['timestamp']

    def test_success_without_custody_does_not_write_chain_of_evidence(self, client, auth, case_id):
        """Bez custody-ja, SUCCESS se loguje ali se ništa ne upisuje u lanac dokaza"""
        upload_csv(client, auth, case_id, '0xOwner,0xSpender,100,2026-01-01T00:00:00Z,0xa1,approve,0xTokenA,0xSpender,\n')

        resp = client.post(f'/api/v1/cases/{case_id}/token-approval-analysis/run', headers=auth, json={})
        assert resp.status_code == 200

        details = audit_log.load_audit_log_entries(case_id=case_id)[-1]['details']
        assert details['status'] == 'SUCCESS'
        assert details['custody_recorded'] is False
        assert custody_log.load_custody_entries(case_id=case_id) == []


class TestFailedLog:
    """FAILED log - adresa koja ne postoji u evidenciji"""

    def test_failed_run_logs_status_failed_with_error(self, client, auth, case_id):
        """Neuspešno pokretanje (adresa nije u evidenciji) upisuje status FAILED sa greškom"""
        upload_csv(client, auth, case_id, '0xOwner,0xSpender,100,2026-01-01T00:00:00Z,0xa1,approve,0xTokenA,0xSpender,\n')

        resp = client.post(
            f'/api/v1/cases/{case_id}/token-approval-analysis/run',
            headers=auth,
            json={'address': '0xNoSuchAddress', 'custody': CUSTODY},
        )
        assert resp.status_code == 404

        entries = audit_log.load_audit_log_entries(case_id=case_id)
        run_entries = [entry for entry in entries if entry['action'] == 'token_approval_analysis_run']
        assert len(run_entries) == 1
        details = run_entries[0]['details']
        assert details['status'] == 'FAILED'
        assert details['address'] == '0xNoSuchAddress'
        assert 'error' in details and details['error']

    def test_failed_run_does_not_write_to_chain_of_evidence(self, client, auth, case_id):
        """Neuspešno pokretanje ne piše NIŠTA u lanac dokaza, iako je custody poslat"""
        upload_csv(client, auth, case_id, '0xOwner,0xSpender,100,2026-01-01T00:00:00Z,0xa1,approve,0xTokenA,0xSpender,\n')

        client.post(
            f'/api/v1/cases/{case_id}/token-approval-analysis/run',
            headers=auth,
            json={'address': '0xGhost', 'custody': CUSTODY},
        )

        assert custody_log.load_custody_entries(case_id=case_id) == []
