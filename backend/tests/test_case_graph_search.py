"""Napredna pretraga grafa (susedstvo adrese do N koraka) preko Neo4j-a - graph-db pilot.

Ovi testovi PRESKAČU CEO MODUL (ne padaju) ako Neo4j nije dostupan (npr. lokalno bez
`docker compose up neo4j`) - ovo je namerno opciona, dodatna mogućnost (vidi
PREDLOG-GRAF-SUBP.md), pa ne sme da uslovljava ostatak test suite-a niti "Testovi"
stranicu u aplikaciji kad Neo4j nije pokrenut.

Pokrivaju: susedstvo do zadatog broja koraka, isključivanje adresa van tog dometa,
nepostojeću adresu (404), izolaciju po slučaju (dve istrage sa istom adresom se ne mešaju),
i da ruta vraća jasan 503 kad Neo4j nije dostupan.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.shared import case_access, graph_db

pytestmark = pytest.mark.skipif(
    not graph_db.is_available(),
    reason='Neo4j nije dostupan (opciono - pokrenite "docker compose up neo4j").',
)

HEADER = 'sender_address,recipient_address,amount,timestamp'


def write_csv(tmp_path: Path, rows: str, name: str = 'evidence.csv') -> Path:
    path = tmp_path / name
    path.write_text(f'{HEADER}\n{rows}', encoding='utf-8')
    return path


@pytest.fixture
def case_id():
    """Sveža, nasumična case_id po testu - Neo4j je stvarna deljena baza (za razliku od
    tmp_path izolacije koju ostali testovi koriste), pa se testovi izoluju po case_id-u
    umesto po fajl sistemu."""
    return f'test-{uuid4().hex[:12]}'


@pytest.fixture(autouse=True)
def _cleanup(case_id):
    yield
    with graph_db.get_driver().session() as session:
        session.run('MATCH (a:Address {case_id: $case_id}) DETACH DELETE a', case_id=case_id)


def _route(tmp_path, monkeypatch, case_id: str, rows: str):
    from app.features.case_graph_search import router as graph_search_router

    case = {'id': case_id, 'name': 'Slučaj', 'evidence': []}
    monkeypatch.setattr(case_access, 'get_case', lambda cid: case)
    csv_path = write_csv(tmp_path, rows)
    evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
    monkeypatch.setattr(case_access, 'get_case_evidence_paths', lambda c: [(evidence_entry, csv_path)])
    return graph_search_router


class TestNeighborhood:
    """Susedstvo adrese do N koraka"""

    def test_direct_neighbor_found_at_one_hop(self, tmp_path, monkeypatch, case_id):
        """Direktna transakcija je susedstvo na 1 korak"""
        router = _route(tmp_path, monkeypatch, case_id, '0xA,0xB,100,2026-01-01T00:00:00Z\n')

        result = router.get_case_graph_neighborhood(case_id, address='0xA', max_hops=1)

        assert result['neighbors'] == [{'address': '0xB', 'hops': 1}]
        assert result['transactions_indexed'] == 1

    def test_excludes_addresses_beyond_hop_limit(self, tmp_path, monkeypatch, case_id):
        """Adrese van zadatog broja koraka se ne vraćaju"""
        rows = '0xA,0xB,100,2026-01-01T00:00:00Z\n0xB,0xC,90,2026-01-01T01:00:00Z\n0xC,0xD,80,2026-01-01T02:00:00Z\n'
        router = _route(tmp_path, monkeypatch, case_id, rows)

        result = router.get_case_graph_neighborhood(case_id, address='0xA', max_hops=1)

        addresses = {n['address'] for n in result['neighbors']}
        assert addresses == {'0xB'}
        assert '0xC' not in addresses and '0xD' not in addresses

    def test_wider_radius_reaches_further_nodes(self, tmp_path, monkeypatch, case_id):
        """Veći broj dozvoljenih koraka dopire do udaljenijih adresa"""
        rows = '0xA,0xB,100,2026-01-01T00:00:00Z\n0xB,0xC,90,2026-01-01T01:00:00Z\n0xC,0xD,80,2026-01-01T02:00:00Z\n'
        router = _route(tmp_path, monkeypatch, case_id, rows)

        result = router.get_case_graph_neighborhood(case_id, address='0xA', max_hops=3)

        by_address = {n['address']: n['hops'] for n in result['neighbors']}
        assert by_address == {'0xB': 1, '0xC': 2, '0xD': 3}

    def test_traversal_is_direction_agnostic(self, tmp_path, monkeypatch, case_id):
        """Susedstvo prati vezu u oba smera, ne samo pravac novca"""
        router = _route(tmp_path, monkeypatch, case_id, '0xB,0xA,100,2026-01-01T00:00:00Z\n')

        result = router.get_case_graph_neighborhood(case_id, address='0xA', max_hops=1)

        assert result['neighbors'] == [{'address': '0xB', 'hops': 1}]

    def test_unknown_address_raises_404(self, tmp_path, monkeypatch, case_id):
        """Adresa koja se ne pojavljuje u evidenciji prijavljuje „nije pronađeno"""
        router = _route(tmp_path, monkeypatch, case_id, '0xA,0xB,100,2026-01-01T00:00:00Z\n')

        with pytest.raises(HTTPException) as excinfo:
            router.get_case_graph_neighborhood(case_id, address='0xNepostojeca', max_hops=1)
        assert excinfo.value.status_code == 404

    def test_isolated_per_case(self, tmp_path, monkeypatch):
        """Ista adresa u dva različita slučaja ne meša njihova susedstva"""
        from app.features.case_graph_search import router as graph_search_router

        case_a, case_b = f'test-{uuid4().hex[:12]}', f'test-{uuid4().hex[:12]}'
        try:
            _route(tmp_path, monkeypatch, case_a, '0xA,0xB,100,2026-01-01T00:00:00Z\n')
            result_a = graph_search_router.get_case_graph_neighborhood(case_a, address='0xA', max_hops=1)

            _route(tmp_path, monkeypatch, case_b, '0xA,0xC,50,2026-01-01T00:00:00Z\n')
            result_b = graph_search_router.get_case_graph_neighborhood(case_b, address='0xA', max_hops=1)

            assert result_a['neighbors'] == [{'address': '0xB', 'hops': 1}]
            assert result_b['neighbors'] == [{'address': '0xC', 'hops': 1}]
        finally:
            with graph_db.get_driver().session() as session:
                session.run('MATCH (a:Address) WHERE a.case_id IN $ids DETACH DELETE a', ids=[case_a, case_b])


class TestUnavailable:
    """Ponašanje kad Neo4j nije dostupan"""

    def test_returns_503_when_neo4j_unreachable(self, monkeypatch):
        """Ruta vraća jasan 503 umesto da padne kad Neo4j nije dostupan"""
        from app.features.case_graph_search import router as graph_search_router

        monkeypatch.setattr(graph_search_router, 'is_available', lambda: False)

        with pytest.raises(HTTPException) as excinfo:
            graph_search_router.get_case_graph_neighborhood('bilo-koji', address='0xA', max_hops=1)
        assert excinfo.value.status_code == 503
