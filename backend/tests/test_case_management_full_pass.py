"""Korak 11: fokusirani test-prolaz kroz CELU Case Management funkcionalnost.

Jedan fajl koji 1:1 prati čeklistu iz zahteva - CASE / NOTES / PINNING / INVESTIGATOR
LINKS / PERSISTENCE / ISOLATION - kroz pravi HTTP API, sa izolovanim skladištem (tmp).
"Reload/restart" se simulira novom `TestClient(app)` instancom nad istim fajlovima.

Ispravnost samih analitičkih algoritama (Graph / Taint / Pathfinding / Behavioral / DEX)
pokrivaju postojeći testovi (test_taint_analysis.py, test_path_finding_bfs.py,
test_behavioral_analysis.py, test_dex_swap_analysis.py, ...); ovaj fajl proverava samo da
Case Management radi i da ništa od toga nije pokvareno na nivou API rutiranja.
"""

from __future__ import annotations

import json

import pytest

from app.evidence import audit_log
from app.investigations import repository
from app.investigations.links_models import EVIDENCE_MAX_LENGTH, REASON_MAX_LENGTH
from app.investigations.notes_models import NOTE_TEXT_MAX_LENGTH


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')
    monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
    return tmp_path


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
    return client.post('/api/v1/investigations', headers=auth, json={'name': 'CASE #2026-011'}).json()['id']


# ---------------------------------------------------------------------------- CASE

class TestCase:
    """CASE - create / load / update"""

    def test_create_load_update(self, client, auth):
        created = client.post(
            '/api/v1/investigations', headers=auth, json={'name': 'Op. Focus', 'description': 'prvi opis'}
        )
        assert created.status_code == 200
        cid = created.json()['id']
        assert created.json()['created_at'] == created.json()['updated_at']

        loaded = client.get(f'/api/v1/investigations/{cid}', headers=auth)
        assert loaded.status_code == 200 and loaded.json()['name'] == 'Op. Focus'

        updated = client.patch(f'/api/v1/investigations/{cid}', headers=auth, json={'name': 'Op. Focus 2', 'description': ''})
        assert updated.status_code == 200
        assert updated.json()['name'] == 'Op. Focus 2'
        assert updated.json()['description'] is None
        assert updated.json()['created_at'] == created.json()['created_at']
        assert updated.json()['updated_at'] >= created.json()['created_at']

        assert client.get('/api/v1/investigations/nema123', headers=auth).status_code == 404


# --------------------------------------------------------------------------- NOTES

class TestNodeNotes:
    """NOTES - node note create / edit / delete / empty validation / very long"""

    def test_create_edit_delete(self, client, auth, case_id):
        r = client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xNODE', 'text': 'v1'})
        assert r.status_code == 200 and r.json()['target_type'] == 'address'
        nid = r.json()['id']

        e = client.patch(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth, json={'text': 'v2'})
        assert e.status_code == 200 and e.json()['text'] == 'v2' and e.json()['updated_at'] != e.json()['created_at']

        assert client.delete(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth).status_code == 204
        assert client.get(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth).status_code == 404

    def test_empty_note_validation(self, client, auth, case_id):
        # missing text field
        assert client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xA'}).status_code == 422
        # blank / whitespace text
        assert (
            client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xA', 'text': '   '}).status_code
            == 422
        )
        # blank on edit
        nid = client.post(
            f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xA', 'text': 'ok'}
        ).json()['id']
        assert client.patch(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth, json={'text': ' '}).status_code == 422

    def test_very_long_note_handling(self, client, auth, case_id):
        at_limit = 'x' * NOTE_TEXT_MAX_LENGTH
        over_limit = 'x' * (NOTE_TEXT_MAX_LENGTH + 1)

        ok = client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xLONG', 'text': at_limit})
        assert ok.status_code == 200 and len(ok.json()['text']) == NOTE_TEXT_MAX_LENGTH

        too_long = client.post(
            f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xLONG', 'text': over_limit}
        )
        assert too_long.status_code == 422

        # the at-limit note round-trips intact
        got = client.get(f'/api/v1/investigations/{case_id}/notes', headers=auth, params={'address': '0xLONG'}).json()['notes']
        assert len(got) == 1 and len(got[0]['text']) == NOTE_TEXT_MAX_LENGTH


class TestTransactionNotes:
    """NOTES - transaction note create / edit / delete"""

    def test_create_edit_delete(self, client, auth, case_id):
        r = client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'tx_id': '0xTX', 'text': 'tx v1'})
        assert r.status_code == 200 and r.json()['target_type'] == 'transaction' and r.json()['address'] is None
        nid = r.json()['id']

        e = client.patch(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth, json={'text': 'tx v2'})
        assert e.status_code == 200 and e.json()['text'] == 'tx v2' and e.json()['tx_id'] == '0xTX'

        assert client.delete(f'/api/v1/investigations/{case_id}/notes/{nid}', headers=auth).status_code == 204

    def test_note_targets_exactly_one_of_address_or_tx(self, client, auth, case_id):
        assert client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'text': 'neither'}).status_code == 422
        assert (
            client.post(
                f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xA', 'tx_id': '0x1', 'text': 'both'}
            ).status_code
            == 422
        )


# ------------------------------------------------------------------------- PINNING

class TestPinning:
    """PINNING - pin / unpin / multiple pinned / persistence"""

    def test_pin_and_unpin(self, client, auth, case_id):
        p = client.put(f'/api/v1/investigations/{case_id}/pins', headers=auth, json={'address': '0xP1', 'x': 5.0, 'y': 6.0})
        assert p.status_code == 200 and p.json()['address'] == '0xP1' and p.json()['x'] == 5.0

        assert [x['address'] for x in client.get(f'/api/v1/investigations/{case_id}/pins', headers=auth).json()['pins']] == ['0xP1']

        assert client.delete(f'/api/v1/investigations/{case_id}/pins', headers=auth, params={'address': '0xP1'}).status_code == 204
        assert client.get(f'/api/v1/investigations/{case_id}/pins', headers=auth).json()['pins'] == []
        # unpinning something not pinned -> 404
        assert client.delete(f'/api/v1/investigations/{case_id}/pins', headers=auth, params={'address': '0xNEMA'}).status_code == 404

    def test_multiple_pinned_nodes_and_reload(self, client, auth, case_id):
        for i, addr in enumerate(['0xA', '0xB', '0xC', '0xD']):
            client.put(f'/api/v1/investigations/{case_id}/pins', headers=auth, json={'address': addr, 'x': float(i), 'y': float(i)})
        # re-pin one at a new position (upsert, not a duplicate)
        client.put(f'/api/v1/investigations/{case_id}/pins', headers=auth, json={'address': '0xB', 'x': 99.0, 'y': 88.0})

        pins = client.get(f'/api/v1/investigations/{case_id}/pins', headers=auth).json()['pins']
        assert {p['address'] for p in pins} == {'0xA', '0xB', '0xC', '0xD'}
        assert next(p for p in pins if p['address'] == '0xB')['x'] == 99.0

        # "restart backend": brand-new app instance over the same files
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as fresh:
            h = {'Authorization': f'Bearer {fresh.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]}'}
            after = fresh.get(f'/api/v1/investigations/{case_id}/pins', headers=h).json()['pins']
        assert {p['address'] for p in after} == {'0xA', '0xB', '0xC', '0xD'}
        assert next(p for p in after if p['address'] == '0xB')['x'] == 99.0


# ---------------------------------------------------------------- INVESTIGATOR LINKS

class TestInvestigatorLinks:
    """INVESTIGATOR LINKS - create / delete / same src==tgt / duplicates / missing reason /
    missing evidence / confidence values"""

    def _body(self, **over):
        body = {
            'source_address': '0xSRC',
            'target_address': '0xTGT',
            'reason': 'IP correlation',
            'evidence': 'Server log #42',
            'confidence': 'High',
        }
        body.update(over)
        return body

    def test_create_and_delete(self, client, auth, case_id):
        r = client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body())
        assert r.status_code == 200 and r.json()['directed'] is False
        lid = r.json()['id']
        assert client.delete(f'/api/v1/investigations/{case_id}/links/{lid}', headers=auth).status_code == 204
        assert client.get(f'/api/v1/investigations/{case_id}/links/{lid}', headers=auth).status_code == 404

    def test_same_address_source_and_target_rejected(self, client, auth, case_id):
        assert (
            client.post(
                f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(source_address='0xX', target_address=' 0xX ')
            ).status_code
            == 422
        )

    def test_duplicate_links_are_allowed(self, client, auth, case_id):
        # two links between the same pair, different evidence -> both kept, independent ids
        a = client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(evidence='log #1')).json()
        b = client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(evidence='log #2')).json()
        assert a['id'] != b['id']
        links = client.get(f'/api/v1/investigations/{case_id}/links', headers=auth).json()['links']
        assert len([l for l in links if {l['source_address'], l['target_address']} == {'0xSRC', '0xTGT'}]) == 2

    def test_missing_reason_rejected(self, client, auth, case_id):
        b = self._body()
        del b['reason']
        assert client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=b).status_code == 422
        assert client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(reason='   ')).status_code == 422

    def test_missing_evidence_rejected(self, client, auth, case_id):
        b = self._body()
        del b['evidence']
        assert client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=b).status_code == 422
        assert client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(evidence='')).status_code == 422

    def test_confidence_values(self, client, auth, case_id):
        for level in ('Low', 'Medium', 'High'):
            r = client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(confidence=level))
            assert r.status_code == 200 and r.json()['confidence'] == level
        for bad in ('low', 'Certain', 'Proven', ''):
            assert (
                client.post(f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(confidence=bad)).status_code
                == 422
            )

    def test_long_reason_and_evidence_boundaries(self, client, auth, case_id):
        assert (
            client.post(
                f'/api/v1/investigations/{case_id}/links',
                headers=auth,
                json=self._body(reason='r' * REASON_MAX_LENGTH, evidence='e' * EVIDENCE_MAX_LENGTH),
            ).status_code
            == 200
        )
        assert (
            client.post(
                f'/api/v1/investigations/{case_id}/links', headers=auth, json=self._body(reason='r' * (REASON_MAX_LENGTH + 1))
            ).status_code
            == 422
        )


# --------------------------------------------------------------------- PERSISTENCE

class TestPersistence:
    """PERSISTENCE - reload / restart, information remains, and it is real on-disk data"""

    def test_everything_survives_a_restart_and_is_on_disk(self, client, auth, case_id, isolated_store):
        client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'address': '0xN', 'text': 'node note'})
        client.post(f'/api/v1/investigations/{case_id}/notes', headers=auth, json={'tx_id': '0xT', 'text': 'tx note'})
        client.put(f'/api/v1/investigations/{case_id}/pins', headers=auth, json={'address': '0xN', 'x': 1.0, 'y': 2.0})
        client.post(
            f'/api/v1/investigations/{case_id}/links',
            headers=auth,
            json={'source_address': '0xN', 'target_address': '0xM', 'reason': 'r', 'evidence': 'e', 'confidence': 'Medium'},
        )

        # read the raw files off disk, independent of the app
        case_dir = isolated_store / 'investigations' / case_id
        notes = json.loads((case_dir / 'notes.json').read_text(encoding='utf-8'))['notes']
        pins = json.loads((case_dir / 'pinned_nodes.json').read_text(encoding='utf-8'))['pinned_nodes']
        links = json.loads((case_dir / 'links.json').read_text(encoding='utf-8'))['links']
        assert len(notes) == 2 and len(pins) == 1 and len(links) == 1
        assert all(n['investigation_id'] == case_id for n in notes)
        assert pins[0]['investigation_id'] == case_id and links[0]['investigation_id'] == case_id

        # brand-new app instance -> same data back through the API
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as fresh:
            h = {'Authorization': f'Bearer {fresh.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]}'}
            assert fresh.get(f'/api/v1/investigations/{case_id}', headers=h).json()['name'] == 'CASE #2026-011'
            assert len(fresh.get(f'/api/v1/investigations/{case_id}/notes', headers=h).json()['notes']) == 2
            assert len(fresh.get(f'/api/v1/investigations/{case_id}/pins', headers=h).json()['pins']) == 1
            assert len(fresh.get(f'/api/v1/investigations/{case_id}/links', headers=h).json()['links']) == 1


# ----------------------------------------------------------------------- ISOLATION

class TestIsolation:
    """ISOLATION - Case A data does not appear in Case B"""

    def test_case_a_data_not_visible_in_case_b(self, client, auth):
        a = client.post('/api/v1/investigations', headers=auth, json={'name': 'CASE A'}).json()['id']
        b = client.post('/api/v1/investigations', headers=auth, json={'name': 'CASE B'}).json()['id']

        client.post(f'/api/v1/investigations/{a}/notes', headers=auth, json={'address': '0xONLYA', 'text': 'only in A'})
        client.post(f'/api/v1/investigations/{a}/notes', headers=auth, json={'tx_id': '0xTXA', 'text': 'tx only in A'})
        client.put(f'/api/v1/investigations/{a}/pins', headers=auth, json={'address': '0xONLYA'})
        client.post(
            f'/api/v1/investigations/{a}/links',
            headers=auth,
            json={'source_address': '0xONLYA', 'target_address': '0xZ', 'reason': 'r', 'evidence': 'e', 'confidence': 'Low'},
        )

        assert client.get(f'/api/v1/investigations/{b}/notes', headers=auth).json()['notes'] == []
        assert client.get(f'/api/v1/investigations/{b}/pins', headers=auth).json()['pins'] == []
        assert client.get(f'/api/v1/investigations/{b}/links', headers=auth).json()['links'] == []
        # even filtering B by an address that only exists in A returns nothing
        assert client.get(f'/api/v1/investigations/{b}/notes', headers=auth, params={'address': '0xONLYA'}).json()['notes'] == []
        assert client.get(f'/api/v1/investigations/{b}/links', headers=auth, params={'address': '0xONLYA'}).json()['links'] == []

        # A still has all four
        assert len(client.get(f'/api/v1/investigations/{a}/notes', headers=auth).json()['notes']) == 2
        assert len(client.get(f'/api/v1/investigations/{a}/pins', headers=auth).json()['pins']) == 1
        assert len(client.get(f'/api/v1/investigations/{a}/links', headers=auth).json()['links']) == 1


# ------------------------------------------- existing analysis endpoints unaffected

class TestExistingAnalysisEndpointsIntact:
    """Postojeće analize (Graph / Taint / Pathfinding / Behavioral / DEX) - rute i dalje
    postoje i odgovaraju (algoritamsku ispravnost pokrivaju njihovi vlastiti testovi)."""

    def test_analysis_routes_still_registered(self, client):
        paths = set(client.get('/openapi.json').json()['paths'])
        for expected in (
            '/api/v1/graph',
            '/api/v1/analytics/run',
            '/api/v1/cases/{case_id}/graph',
            '/api/v1/cases/{case_id}/analytics/run',
            '/api/v1/cases/{case_id}/pathfinding',
            '/api/v1/cases/{case_id}/behavioral-analysis',
            '/api/v1/cases/{case_id}/dex-swap-analysis',
        ):
            assert expected in paths, expected

    def test_analysis_endpoints_respond_for_a_missing_case(self, client, auth):
        # 404 (case not found), NOT 500 - proves the route + its dependencies still wire up.
        assert client.get('/api/v1/cases/nema/graph', headers=auth).status_code == 404
        assert client.post('/api/v1/cases/nema/pathfinding', headers=auth, json={'from': '0xA', 'to': '0xB'}).status_code == 404
        assert client.get('/api/v1/cases/nema/behavioral-analysis', headers=auth, params={'address': '0xA'}).status_code == 404
        assert client.get('/api/v1/cases/nema/dex-swap-analysis', headers=auth).status_code == 404
