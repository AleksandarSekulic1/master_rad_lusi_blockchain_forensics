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
from fastapi import HTTPException

from app.analytics.path_finding import bfs_shortest_path, find_path_to_nearest_of


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


class TestFindPathToNearestOf:
    """BFS ka najbližoj adresi iz zadatog skupa (osnova za "Nearest known CEX")

    Funkcija ne zna šta je "CEX" - samo prima skup kandidata i traži hronološki
    nezavisno, strukturno najbližeg. "Najbliži" mora biti determinističan: kad postoji
    više kandidata na istom, najmanjem broju skokova, bira se alfabetski najmanja adresa,
    ne nešto što zavisi od redosleda grana u grafu.
    """

    def test_picks_nearer_candidate_over_farther_one(self, graph_from_rows):
        """Bira kandidata sa manje skokova, ne prvog na koji naiđe"""
        graph = graph_from_rows([
            ('0xA', '0xFarCex', 100.0, '2026-01-01T00:00:00Z'),
            ('0xFarCex', '0xEvenFarther', 90.0, '2026-01-01T01:00:00Z'),
            ('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z'),
            ('0xB', '0xNearCex', 90.0, '2026-01-01T01:00:00Z'),
        ])

        result = find_path_to_nearest_of(graph, '0xA', {'0xFarCex', '0xNearCex'})

        # 0xFarCex is 1 hop away, 0xNearCex is 2 - the nearer one must win even though
        # it's not the one reachable by the shorter-looking direct edge.
        assert result['found'] is True
        assert result['destination'] == '0xFarCex'
        assert result['hops'] == 1
        assert result['path'] == ['0xA', '0xFarCex']

    def test_breaks_ties_by_address_ascending(self, graph_from_rows):
        """Kad su dva kandidata na istom (najmanjem) broju skokova, bira se manja adresa"""
        graph = graph_from_rows([
            ('0xA', '0xZCex', 100.0, '2026-01-01T00:00:00Z'),
            ('0xA', '0xACex', 100.0, '2026-01-01T00:00:00Z'),
        ])

        result = find_path_to_nearest_of(graph, '0xA', {'0xZCex', '0xACex'})

        assert result['destination'] == '0xACex'
        assert result['hops'] == 1

    def test_source_itself_is_a_candidate(self, graph_from_rows):
        """Polazna adresa koja je i sama u skupu kandidata je put dužine 0"""
        graph = graph_from_rows([('0xCex', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        result = find_path_to_nearest_of(graph, '0xCex', {'0xCex'})

        assert result == {'found': True, 'path': ['0xCex'], 'hops': 0, 'destination': '0xCex'}

    def test_no_candidates_present(self, graph_from_rows):
        """Prazan skup kandidata (npr. nijedna CEX adresa u evidenciji) - nema pogađanja"""
        graph = graph_from_rows([('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        result = find_path_to_nearest_of(graph, '0xA', set())

        assert result == {'found': False, 'path': [], 'hops': 0, 'destination': None}

    def test_candidates_exist_but_unreachable(self, graph_from_rows):
        """Kandidat postoji u grafu, ali nije dostiživ iz polazne adrese"""
        graph = graph_from_rows([
            ('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z'),
            ('0xX', '0xCex', 50.0, '2026-01-01T00:00:00Z'),
        ])

        result = find_path_to_nearest_of(graph, '0xA', {'0xCex'})

        assert result == {'found': False, 'path': [], 'hops': 0, 'destination': None}

    def test_source_not_in_graph(self, graph_from_rows):
        """Nepostojeća polazna adresa ne baca grešku"""
        graph = graph_from_rows([('0xA', '0xB', 100.0, '2026-01-01T00:00:00Z')])

        assert find_path_to_nearest_of(graph, '0xNePostoji', {'0xB'}) == {
            'found': False, 'path': [], 'hops': 0, 'destination': None,
        }


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

    def test_specific_address_mode_requires_to_field(self, tmp_path, monkeypatch):
        """destination_mode 'specific_address' bez 'to' vraća jasnu grešku, ne pucanje"""
        from app.api.routes import cases as cases_routes

        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(cases_routes, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(cases_routes, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA'})
        with pytest.raises(HTTPException) as excinfo:
            cases_routes.run_case_pathfinding(
                case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
            )
        assert excinfo.value.status_code == 400

    def test_unsupported_destination_mode_is_rejected(self, tmp_path, monkeypatch):
        """destination_mode 'cash_out_point' (nije još implementiran) vraća jasnu grešku"""
        from app.api.routes import cases as cases_routes

        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(cases_routes, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(cases_routes, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA', 'destination_mode': 'cash_out_point'})
        with pytest.raises(HTTPException) as excinfo:
            cases_routes.run_case_pathfinding(
                case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
            )
        assert excinfo.value.status_code == 400


class TestNearestCexMode:
    """Ruta POST /cases/{id}/pathfinding sa destination_mode 'nearest_cex'

    Provera da ruta pita LOKALNI registar poznatih entiteta (get_known_entity) za svaku
    adresu u grafu - nikad ne pretpostavlja da je adresa CEX na osnovu izgleda/naziva - i
    da vraća dodatna polja (destination_address, destination_label) bez kršenja starog
    {found, path, hops} oblika za destination_mode 'specific_address'.
    """

    def _setup(self, tmp_path, monkeypatch, rows: str, known_entities: dict[str, dict[str, str]]):
        from app.api.routes import cases as cases_routes
        from app.evidence import audit_log

        monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(cases_routes, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, rows)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(cases_routes, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])
        monkeypatch.setattr(cases_routes, 'get_known_entity', lambda address: known_entities.get(address))
        return cases_routes

    def test_finds_nearest_known_cex_and_reports_its_label(self, tmp_path, monkeypatch):
        """Pronalazi najbližu adresu koju lokalni registar označava kao 'exchange'"""
        cases_routes = self._setup(
            tmp_path, monkeypatch,
            '0xThief,0xMixer,1000,2026-01-01T00:00:00Z\n0xMixer,0xRealCex,750,2026-01-01T01:00:00Z\n',
            {'0xRealCex': {'category': 'exchange', 'name': 'Binance 9'}},
        )

        request = cases_routes.CasePathfindingRequest(**{'from': '0xThief', 'destination_mode': 'nearest_cex'})
        result = cases_routes.run_case_pathfinding(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result['found'] is True
        assert result['path'] == ['0xThief', '0xMixer', '0xRealCex']
        assert result['hops'] == 2
        assert result['destination_address'] == '0xRealCex'
        assert result['destination_label'] == 'Binance 9'

    def test_never_treats_mixer_or_sanctioned_as_cex(self, tmp_path, monkeypatch):
        """Ne meša CEX sa mikserom/sankcionisanom adresom - samo category 'exchange' važi"""
        cases_routes = self._setup(
            tmp_path, monkeypatch,
            '0xA,0xMixerNode,100,2026-01-01T00:00:00Z\n',
            {'0xMixerNode': {'category': 'mixer', 'name': 'Tornado.Cash'}},
        )

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA', 'destination_mode': 'nearest_cex'})
        result = cases_routes.run_case_pathfinding(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result['found'] is False
        assert 'nije prisutna' in result['message']

    def test_reports_distinct_message_when_cex_exists_but_unreachable(self, tmp_path, monkeypatch):
        """Poruka razlikuje 'nema CEX uopšte' od 'CEX postoji, ali nije dostiživ'"""
        cases_routes = self._setup(
            tmp_path, monkeypatch,
            '0xA,0xB,100,2026-01-01T00:00:00Z\n0xX,0xRealCex,50,2026-01-01T00:00:00Z\n',
            {'0xRealCex': {'category': 'exchange', 'name': 'Coinbase'}},
        )

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA', 'destination_mode': 'nearest_cex'})
        result = cases_routes.run_case_pathfinding(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result['found'] is False
        assert 'nije dostupna' in result['message']

    def test_specific_address_mode_response_unaffected(self, tmp_path, monkeypatch):
        """destination_mode 'specific_address' i dalje vraća TAČNO {found, path, hops}"""
        cases_routes = self._setup(tmp_path, monkeypatch, '0xA,0xB,100,2026-01-01T00:00:00Z\n', {})

        request = cases_routes.CasePathfindingRequest(**{'from': '0xA', 'to': '0xB'})
        result = cases_routes.run_case_pathfinding(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result == {'found': True, 'path': ['0xA', '0xB'], 'hops': 1}
