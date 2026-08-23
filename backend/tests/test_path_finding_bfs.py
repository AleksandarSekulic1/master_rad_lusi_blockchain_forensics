"""Provera BFS pathfinding-a (prva verzija Pathfinding Analysis modula).

Cilj je jednostavan: nezatežena (BFS) najkraća putanja stvarnih transakcija između dve
adrese, kroz postojeći graf slučaja - bez CEX/cash-out/weighted heuristika (dolazi kasnije).
Ovi testovi proveravaju samu BFS funkciju (bfs_shortest_path) i, odvojeno, da nova ruta
POST /cases/{id}/pathfinding ispravno gradi graf od evidencije slučaja i vraća tačno
{found, path, hops}.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics.path_finding import bfs_shortest_path


class TestBfsShortestPath:
    """BFS najkraća putanja

    Graf je usmeren (pošiljalac -> primalac), pa put sme da ide samo u smeru u kome se
    novac stvarno kretao - obrnuti smer ne postoji kao grana.
    """

    def test_finds_direct_path(self, graph_from_rows):
        """Direktna transakcija je put dužine 1"""
        graph = graph_from_rows([('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        result = bfs_shortest_path(graph, '0xA', '0xB')

        assert result == {'found': True, 'path': ['0xA', '0xB'], 'hops': 1}

    def test_finds_multi_hop_path(self, graph_from_rows):
        """Put kroz više posrednika se pravilno rekonstruiše, redom"""
        graph = graph_from_rows([
            ('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z'),
            ('0xB', '0xC', 90.0, '2026-01-01T01:00:00Z'),
            ('0xC', '0xD', 80.0, '2026-01-01T02:00:00Z'),
        ])

        result = bfs_shortest_path(graph, '0xA', '0xD')

        assert result == {'found': True, 'path': ['0xA', '0xB', '0xC', '0xD'], 'hops': 3}

    def test_prefers_shortest_when_multiple_paths_exist(self, graph_from_rows):
        """BFS bira najkraću putanju kad postoji i duža alternativa"""
        graph = graph_from_rows([
            ('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z'),
            ('0xB', '0xD', 90.0, '2026-01-01T01:00:00Z'),
            ('0xA', '0xC', 100.0, '2026-01-01T00:00:00Z'),
            ('0xC', '0xE', 90.0, '2026-01-01T01:00:00Z'),
            ('0xE', '0xD', 90.0, '2026-01-01T02:00:00Z'),
        ])

        result = bfs_shortest_path(graph, '0xA', '0xD')

        assert result['found'] is True
        assert result['hops'] == 2
        assert result['path'] == ['0xA', '0xB', '0xD']

    def test_ignores_wrong_direction_edge(self, graph_from_rows):
        """Grana postoji samo u smeru pošiljalac -> primalac, ne i obrnuto"""
        graph = graph_from_rows([('0xB', '0xA', 100.0, '2026-01-01T00:00:00Z')])

        result = bfs_shortest_path(graph, '0xA', '0xB')

        assert result == {'found': False, 'path': [], 'hops': 0}

    def test_no_path_between_disconnected_addresses(self, graph_from_rows):
        """Nepovezane adrese vraćaju found: false, ne grešku"""
        graph = graph_from_rows([
            ('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z'),
            ('0xX', '0xY', 50.0, '2026-01-01T00:00:00Z'),
        ])

        result = bfs_shortest_path(graph, '0xA', '0xY')

        assert result == {'found': False, 'path': [], 'hops': 0}

    def test_address_not_in_graph_returns_not_found(self, graph_from_rows):
        """Adresa koja uopšte nije u grafu (npr. tipfeler) ne baca grešku"""
        graph = graph_from_rows([('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        assert bfs_shortest_path(graph, '0xA', '0xNePostoji') == {'found': False, 'path': [], 'hops': 0}
        assert bfs_shortest_path(graph, '0xNePostoji', '0xB') == {'found': False, 'path': [], 'hops': 0}

    def test_same_source_and_target(self, graph_from_rows):
        """Ista adresa za oba kraja je put dužine 0, ne 'nije pronađen'"""
        graph = graph_from_rows([('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        result = bfs_shortest_path(graph, '0xA', '0xA')

        assert result == {'found': True, 'path': ['0xA'], 'hops': 0}


HEADER = 'sender_address,recipient_address,amount,timestamp'


def write_csv(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / 'evidence.csv'
    path.write_text(f'{HEADER}\n{rows}', encoding='utf-8')
    return path


class TestPathfindingRoute:
    """Ruta POST /cases/{id}/pathfinding

    Provera da ruta ispravno gradi graf od evidencije slučaja (isti put kao Taint analiza/
    Graf), prosleđuje adrese BFS funkciji, i vraća tačno oblik iz specifikacije -
    {found, path, hops}, ništa više ništa manje.
    """

    def test_returns_minimal_shape_and_writes_audit_log(self, tmp_path, monkeypatch):
        """Ruta vraća {found, path, hops} i beleži path_finding u log aktivnosti"""
        from app.api.routes import cases as cases_routes
        from app.evidence import audit_log

        monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')

        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(cases_routes, 'get_case', lambda case_id: case)

        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z\n0xB,0xC,90,2026-01-01T01:00:00Z\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(cases_routes, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA', 'to': '0xC'})
        result = cases_routes.run_case_pathfinding(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result == {'found': True, 'path': ['0xA', '0xB', '0xC'], 'hops': 2}

        entries = audit_log.load_audit_log_entries(case_id='c1')
        assert len(entries) == 1
        assert entries[0]['action'] == 'path_finding'
        assert entries[0]['details']['source_address'] == '0xA'
        assert entries[0]['details']['target_address'] == '0xC'
        assert entries[0]['details']['found'] is True

    def test_request_accepts_from_to_json_keys(self):
        """Telo zahteva koristi 'from'/'to' kao u specifikaciji, ne 'from_address'/'to_address'"""
        from app.api.routes.cases import CasePathfindingRequest

        request = CasePathfindingRequest.model_validate({'from': '0xA', 'to': '0xB'})

        assert request.from_address == '0xA'
        assert request.to_address == '0xB'
