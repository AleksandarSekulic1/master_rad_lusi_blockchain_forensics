"""Korak 10: provera da su SVE Case Management informacije trajno vezane za slučaj (istragu).

Scenario iz zahteva, kroz pravi HTTP API:
  kreiraj slučaj -> dodaj belešku za čvor -> dodaj belešku za transakciju -> zakači čvor
  -> kreiraj istražiteljsku vezu -> "osveži aplikaciju" (nova instanca aplikacije nad
  istim fajlovima na disku) -> sve je i dalje tu -> prebaci na drugi slučaj i nazad.

Takođe: podaci iz jednog slučaja se NE pojavljuju u drugom, i ništa od blockchain
analitičkih podataka (data/cases, data/raw, graf) se ne dira.

"Osvežavanje aplikacije" se simulira novim `TestClient(app)` kontekstom: aplikacija se
podiže ponovo i čita iste JSON fajlove ispod data/investigations/<id>/.
"""

from __future__ import annotations

import json

import pytest

from app.evidence import audit_log
from app.investigations import repository


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Sav investigator-layer upis ide u tmp; pravi data/ se ne dira."""
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')
    monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
    return tmp_path


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _token(client) -> dict[str, str]:
    resp = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
    return {'Authorization': f'Bearer {resp.json()["access_token"]}'}


def _seed_case(client, headers, *, name: str, description: str | None = None) -> dict:
    """Runs the full 'create everything' flow for one case and returns the ids."""
    iid = client.post('/api/v1/investigations', headers=headers, json={'name': name, 'description': description}).json()['id']

    node_note = client.post(
        f'/api/v1/investigations/{iid}/notes', headers=headers, json={'address': '0xNODE', 'text': f'node note ({name})'}
    ).json()
    tx_note = client.post(
        f'/api/v1/investigations/{iid}/notes', headers=headers, json={'tx_id': '0xTX', 'text': f'tx note ({name})'}
    ).json()
    pin = client.put(
        f'/api/v1/investigations/{iid}/pins', headers=headers, json={'address': '0xNODE', 'x': 12.5, 'y': -3.0}
    ).json()
    link = client.post(
        f'/api/v1/investigations/{iid}/links',
        headers=headers,
        json={
            'source_address': '0xNODE',
            'target_address': '0xOTHER',
            'reason': f'reason ({name})',
            'evidence': f'evidence ({name})',
            'confidence': 'High',
        },
    ).json()
    return {'iid': iid, 'node_note': node_note, 'tx_note': tx_note, 'pin': pin, 'link': link}


def _snapshot(client, headers, iid: str) -> dict:
    """Reads back every Case Management category for one case."""
    case = client.get(f'/api/v1/investigations/{iid}', headers=headers).json()
    notes = client.get(f'/api/v1/investigations/{iid}/notes', headers=headers).json()['notes']
    pins = client.get(f'/api/v1/investigations/{iid}/pins', headers=headers).json()['pins']
    links = client.get(f'/api/v1/investigations/{iid}/links', headers=headers).json()['links']
    return {
        'case': case,
        'node_notes': [n for n in notes if n['target_type'] == 'address'],
        'tx_notes': [n for n in notes if n['target_type'] == 'transaction'],
        'pins': pins,
        'links': links,
    }


class TestFullFlowSurvivesReload:
    """Ceo tok preživi osvežavanje aplikacije"""

    def test_case_and_all_investigator_items_persist_after_reload(self, isolated_store):
        """Slučaj + beleške čvora/transakcije + zakačen čvor + veza su i dalje tu posle „reload"-a"""
        with _client() as client:
            h = _token(client)
            seeded = _seed_case(client, h, name='CASE #2026-001', description='Sumnjiva laundering sema')
            iid = seeded['iid']
            before = _snapshot(client, h, iid)

        # brand-new application instance, same files on disk = "reload aplikacije"
        with _client() as client:
            h = _token(client)
            after = _snapshot(client, h, iid)

        assert after['case']['name'] == 'CASE #2026-001'
        assert after['case']['description'] == 'Sumnjiva laundering sema'
        assert len(after['node_notes']) == 1 and after['node_notes'][0]['text'] == 'node note (CASE #2026-001)'
        assert len(after['tx_notes']) == 1 and after['tx_notes'][0]['tx_id'] == '0xTX'
        assert len(after['pins']) == 1
        assert after['pins'][0]['address'] == '0xNODE' and after['pins'][0]['x'] == 12.5 and after['pins'][0]['y'] == -3.0
        assert len(after['links']) == 1 and after['links'][0]['source_address'] == '0xNODE'
        # ids unchanged across the reload
        assert after['node_notes'][0]['id'] == before['node_notes'][0]['id']
        assert after['links'][0]['id'] == before['links'][0]['id']

    def test_every_item_carries_its_case_id(self, isolated_store):
        """Svaki item nosi ID svog slučaja (investigation_id)"""
        with _client() as client:
            h = _token(client)
            seeded = _seed_case(client, h, name='C1')
            iid = seeded['iid']
            snap = _snapshot(client, h, iid)

        for note in snap['node_notes'] + snap['tx_notes']:
            assert note['investigation_id'] == iid
        assert snap['pins'][0]['investigation_id'] == iid
        assert snap['links'][0]['investigation_id'] == iid

    def test_items_are_written_under_the_case_directory(self, isolated_store):
        """Sve se fizički upisuje ispod data/investigations/<case_id>/"""
        with _client() as client:
            h = _token(client)
            iid = _seed_case(client, h, name='C1')['iid']

        case_dir = isolated_store / 'investigations' / iid
        assert (case_dir / 'investigation.json').exists()
        assert (case_dir / 'notes.json').exists()
        assert (case_dir / 'pinned_nodes.json').exists()
        assert (case_dir / 'links.json').exists()


class TestCaseIsolation:
    """Podaci iz jednog slučaja se ne vide u drugom"""

    def test_two_cases_keep_separate_notes_pins_links(self, isolated_store):
        """Dva slučaja imaju odvojene beleške, zakačene čvorove i veze"""
        with _client() as client:
            h = _token(client)
            a = _seed_case(client, h, name='CASE A')
            b = _seed_case(client, h, name='CASE B')

            # add an EXTRA item to A only
            client.post(f'/api/v1/investigations/{a["iid"]}/notes', headers=h, json={'address': '0xEXTRA', 'text': 'only in A'})

            snap_a = _snapshot(client, h, a['iid'])
            snap_b = _snapshot(client, h, b['iid'])

        # counts: A has the extra note, B does not
        assert len(snap_a['node_notes']) == 2
        assert len(snap_b['node_notes']) == 1

        # nothing from A leaked into B
        a_texts = {n['text'] for n in snap_a['node_notes'] + snap_a['tx_notes']}
        b_texts = {n['text'] for n in snap_b['node_notes'] + snap_b['tx_notes']}
        assert 'only in A' in a_texts and 'only in A' not in b_texts
        assert a_texts.isdisjoint(b_texts)

        assert snap_a['links'][0]['reason'] == 'reason (CASE A)'
        assert snap_b['links'][0]['reason'] == 'reason (CASE B)'
        assert snap_a['links'][0]['id'] != snap_b['links'][0]['id']
        assert snap_a['pins'][0]['investigation_id'] == a['iid']
        assert snap_b['pins'][0]['investigation_id'] == b['iid']

    def test_switching_between_cases_shows_the_right_data_each_time(self, isolated_store):
        """Prebacivanje između slučajeva pokazuje tačne podatke svaki put (i posle reload-a)"""
        with _client() as client:
            h = _token(client)
            a = _seed_case(client, h, name='CASE A')['iid']
            b = _seed_case(client, h, name='CASE B')['iid']

        with _client() as client:  # reload
            h = _token(client)
            # open A
            assert _snapshot(client, h, a)['case']['name'] == 'CASE A'
            assert _snapshot(client, h, a)['node_notes'][0]['text'] == 'node note (CASE A)'
            # switch to B
            assert _snapshot(client, h, b)['case']['name'] == 'CASE B'
            assert _snapshot(client, h, b)['node_notes'][0]['text'] == 'node note (CASE B)'
            # back to A - still A's data
            assert _snapshot(client, h, a)['node_notes'][0]['text'] == 'node note (CASE A)'

    def test_deleting_one_case_leaves_the_other_untouched(self, isolated_store):
        """Brisanje jednog slučaja ne dira drugi"""
        with _client() as client:
            h = _token(client)
            a = _seed_case(client, h, name='CASE A')['iid']
            b = _seed_case(client, h, name='CASE B')['iid']

            assert client.delete(f'/api/v1/investigations/{a}', headers=h).status_code == 204

            # A gone at every layer
            assert client.get(f'/api/v1/investigations/{a}', headers=h).status_code == 404
            assert client.get(f'/api/v1/investigations/{a}/notes', headers=h).status_code == 404
            assert client.get(f'/api/v1/investigations/{a}/pins', headers=h).status_code == 404
            assert client.get(f'/api/v1/investigations/{a}/links', headers=h).status_code == 404

            # B fully intact
            snap_b = _snapshot(client, h, b)
            assert snap_b['case']['name'] == 'CASE B'
            assert len(snap_b['node_notes']) == 1 and len(snap_b['tx_notes']) == 1
            assert len(snap_b['pins']) == 1 and len(snap_b['links']) == 1

        assert not (isolated_store / 'investigations' / a).exists()
        assert (isolated_store / 'investigations' / b).exists()


class TestNoBlockchainDataTouched:
    """Blockchain analitički podaci se ne diraju"""

    def test_investigator_layer_never_writes_outside_data_investigations(self, isolated_store):
        """Investigator layer piše samo u data/investigations/ (+ log), ne u cases/raw/graf"""
        with _client() as client:
            h = _token(client)
            _seed_case(client, h, name='C1')

        written = {p.name for p in isolated_store.iterdir()}
        # only the investigations tree and the (redirected) audit log
        assert written <= {'investigations', 'audit_log.jsonl'}

        audit_actions = [json.loads(line)['action'] for line in (isolated_store / 'audit_log.jsonl').read_text().splitlines()]
        assert set(audit_actions) == {
            'investigation_case_created',
            'investigator_note_created',
            'investigator_pin_set',
            'investigator_link_created',
        }
