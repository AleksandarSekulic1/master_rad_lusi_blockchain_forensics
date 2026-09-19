"""Provera Flow of Funds / Layering Analysis modula (prva verzija).

Cilj: pratiti tok sredstava od jedne ili više početnih adresa kroz više nivoa transakcija,
i vratiti AGREGIRANE tokove (source, target, level, amount, transaction_count, tx_hashes) -
nikad pojedinačne transakcije - spremne da se prikažu kao Sankey dijagram. Testovi takođe
proveravaju razliku Ethereum (account-based) / Bitcoin (UTXO): očuvanje jedinica po valuti i
prepoznavanje transakcije koja u jednom koraku plaća više primalaca (multi-output).

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici "Testovi"
u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

from app.analytics.flow_of_funds import trace_flow_of_funds


def frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Rows use the same column shape app.analytics.ingestion.clean_transaction_csv
    produces: sender_address, recipient_address, amount, timestamp, and optionally
    metadata (tx hash) / currency. Missing optional keys default to None."""
    columns = ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', 'currency']
    normalized = [{column: row.get(column) for column in columns} for row in rows]
    return pd.DataFrame(normalized, columns=columns)


class TestTraceFlowOfFunds:
    """Praćenje toka sredstava kroz nivoe"""

    def test_aggregates_direct_transfer_as_one_level_one_flow(self):
        """Direktan transfer je jedan agregirani tok na nivou 1"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': '0xtx1'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert result['flow_count'] == 1
        flow = result['address_flows'][0]
        assert flow['level'] == 1
        assert flow['source'] == '0xA'
        assert flow['target'] == '0xB'
        assert flow['amount'] == 100
        assert flow['transaction_count'] == 1
        assert flow['tx_hashes'] == ['0xtx1']

    def test_follows_multiple_levels_outward(self):
        """Prati tok kroz više posrednika, svaki hop je svoj nivo"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': '0xtx1'},
            {'sender_address': '0xB', 'recipient_address': '0xC', 'amount': 90, 'timestamp': '2026-01-01T01:00:00Z', 'metadata': '0xtx2'},
            {'sender_address': '0xC', 'recipient_address': '0xD', 'amount': 80, 'timestamp': '2026-01-01T02:00:00Z', 'metadata': '0xtx3'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], max_levels=3)

        assert result['levels_reached'] == 3
        levels = {flow['level'] for flow in result['address_flows']}
        assert levels == {1, 2, 3}
        assert [flow['target'] for flow in result['address_flows']] == ['0xB', '0xC', '0xD']

    def test_max_levels_stops_traversal_early(self):
        """max_levels ograničava dubinu praćenja, dalji nivoi se ne vraćaju"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xB', 'recipient_address': '0xC', 'amount': 90, 'timestamp': '2026-01-01T01:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], max_levels=1)

        assert result['levels_reached'] == 1
        assert result['flow_count'] == 1
        assert result['address_flows'][0]['target'] == '0xB'

    def test_multiple_transactions_same_pair_collapse_into_one_aggregated_flow(self):
        """Više transakcija između istog para adresa se agregira u JEDAN tok, ne u listu"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 40, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': '0xtx1'},
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 60, 'timestamp': '2026-01-01T00:05:00Z', 'metadata': '0xtx2'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert result['flow_count'] == 1
        flow = result['address_flows'][0]
        assert flow['amount'] == 100
        assert flow['transaction_count'] == 2
        assert flow['tx_hashes'] == ['0xtx1', '0xtx2']

    def test_fan_out_to_multiple_targets_produces_one_flow_per_target(self):
        """Grananje ka više primalaca na istom nivou daje poseban agregirani tok za svakog"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 50, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xA', 'recipient_address': '0xC', 'amount': 30, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        targets = {flow['target'] for flow in result['address_flows']}
        assert targets == {'0xB', '0xC'}
        assert result['flow_count'] == 2

    def test_backward_direction_traces_origin_of_funds(self):
        """direction='backward' prati odakle su sredstva stigla, ne gde su otišla"""
        frame = frame_from_rows([
            {'sender_address': '0xOrigin', 'recipient_address': '0xA', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], direction='backward')

        assert result['flow_count'] == 1
        flow = result['address_flows'][0]
        assert flow['source'] == '0xOrigin'
        assert flow['target'] == '0xA'

    def test_min_amount_filters_out_small_flows(self):
        """min_amount izbacuje agregirane tokove ispod praga (dust filtriranje)"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 5, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xA', 'recipient_address': '0xC', 'amount': 500, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], min_amount=100)

        assert result['flow_count'] == 1
        assert result['address_flows'][0]['target'] == '0xC'

    def test_max_flows_truncates_and_reports_truncated_true(self):
        """max_flows ograničava broj vraćenih tokova i signalizuje truncated"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': f'0xTarget{i}', 'amount': 10, 'timestamp': '2026-01-01T00:00:00Z'}
            for i in range(5)
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], max_flows=2)

        assert result['flow_count'] == 2
        assert result['truncated'] is True

    def test_unknown_source_address_raises_value_error(self):
        """Polazna adresa koja nije u evidenciji baca jasnu grešku, ne pucanje"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        with pytest.raises(ValueError):
            trace_flow_of_funds(frame, source_addresses=['0xNePostoji'])

    def test_no_source_addresses_raises_value_error(self):
        """Prazna lista polaznih adresa baca jasnu grešku"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        with pytest.raises(ValueError):
            trace_flow_of_funds(frame, source_addresses=[])

    def test_cyclical_graph_does_not_loop_forever(self):
        """Ciklus u grafu (A->B->A) se ne prati unedogled, obrada se završava"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xB', 'recipient_address': '0xA', 'amount': 90, 'timestamp': '2026-01-01T01:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], max_levels=10)

        assert result['truncated'] is False
        assert result['flow_count'] == 2


class TestCurrencyAndAssetSafety:
    """Ethereum vs Bitcoin - bezbednost jedinica (valuta)"""

    def test_same_pair_different_currencies_are_not_summed_together(self):
        """Isti par adresa sa dve različite valute daje DVA odvojena toka, iznosi se ne mešaju"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 10, 'timestamp': '2026-01-01T00:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 25000, 'timestamp': '2026-01-01T00:01:00Z', 'currency': 'USDC'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert result['flow_count'] == 2
        assets = {flow['asset']: flow['amount'] for flow in result['address_flows']}
        assert assets == {'ETH': 10, 'USDC': 25000}

    def test_infers_eth_asset_from_address_shape_when_currency_missing(self):
        """Bez eksplicitne valute, oblik adrese (0x...) daje ETH kao pretpostavljenu imovinu"""
        sender = '0x' + '0' * 39 + 'a'
        recipient = '0x' + '0' * 39 + 'b'
        frame = frame_from_rows([
            {'sender_address': sender, 'recipient_address': recipient, 'amount': 1.5, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=[sender])

        assert result['address_flows'][0]['asset'] == 'ETH'

    def test_infers_btc_asset_from_address_shape_when_currency_missing(self):
        """Bez eksplicitne valute, oblik Bitcoin adrese daje BTC kao pretpostavljenu imovinu"""
        frame = frame_from_rows([
            {'sender_address': 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh', 'recipient_address': '1BoatSLRHtKNngkdXEeobR76b53LETtpyT', 'amount': 0.01, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh'])

        assert result['address_flows'][0]['asset'] == 'BTC'


class TestUtxoMultiOutputSignature:
    """UTXO model - jedna transakcija sa više izlaza (primalaca)"""

    def test_single_tx_hash_paying_two_recipients_is_flagged_on_both_flows(self):
        """Ista tx-hash koja plaća dva različita primaoca obeležava OBA toka kao multi-output"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 1, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': 'btc_tx_1'},
            {'sender_address': '0xA', 'recipient_address': '0xChange', 'amount': 0.5, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': 'btc_tx_1'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert all(flow['multi_output_same_tx'] is True for flow in result['address_flows'])

    def test_two_separate_tx_hashes_to_different_recipients_are_not_flagged(self):
        """Dve ODVOJENE transakcije ka različitim primaocima nisu multi-output (Ethereum obrazac)"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 1, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': 'eth_tx_1'},
            {'sender_address': '0xA', 'recipient_address': '0xC', 'amount': 1, 'timestamp': '2026-01-01T00:05:00Z', 'metadata': 'eth_tx_2'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert all(flow['multi_output_same_tx'] is False for flow in result['address_flows'])


class TestEntityAndCategoryCollapsing:
    """Agregacija na nivou entiteta/kategorije kad su pouzdano poznati"""

    def test_known_entity_collapses_address_level_flows_into_one_entity_flow(self, monkeypatch):
        """Dve adrese istog poznatog entiteta se spajaju u jedan agregirani entity_flows red"""
        from app.analytics import flow_of_funds

        known = {
            '0xExchangeHot1': {'name': 'Binance', 'category': 'exchange'},
            '0xExchangeHot2': {'name': 'Binance', 'category': 'exchange'},
        }
        monkeypatch.setattr(flow_of_funds, 'get_known_entity', lambda address: known.get(address))

        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xExchangeHot1', 'amount': 60, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': 'tx1'},
            {'sender_address': '0xA', 'recipient_address': '0xExchangeHot2', 'amount': 40, 'timestamp': '2026-01-01T00:01:00Z', 'metadata': 'tx2'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert result['flow_count'] == 2
        assert len(result['entity_flows']) == 1
        entity_flow = result['entity_flows'][0]
        assert entity_flow['target_label'] == 'Binance'
        assert entity_flow['amount'] == 100
        assert entity_flow['transaction_count'] == 2
        assert set(entity_flow['contributing_addresses']['target']) == {'0xExchangeHot1', '0xExchangeHot2'}

    def test_unknown_addresses_fall_back_to_address_level_in_entity_flows(self, monkeypatch):
        """Bez poznatog entiteta, entity_flows ostaje na nivou pojedinačne adrese"""
        from app.analytics import flow_of_funds

        monkeypatch.setattr(flow_of_funds, 'get_known_entity', lambda address: None)

        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert result['entity_flows'][0]['target'] == '0xB'

    def test_category_flows_collapse_by_category_across_different_entities(self, monkeypatch):
        """category_flows spaja RAZLIČITE entitete iste kategorije (npr. dve različite berze)"""
        from app.analytics import flow_of_funds

        known = {
            '0xBinanceHot': {'name': 'Binance', 'category': 'exchange'},
            '0xCoinbaseHot': {'name': 'Coinbase', 'category': 'exchange'},
        }
        monkeypatch.setattr(flow_of_funds, 'get_known_entity', lambda address: known.get(address))

        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xBinanceHot', 'amount': 60, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xA', 'recipient_address': '0xCoinbaseHot', 'amount': 40, 'timestamp': '2026-01-01T00:01:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        assert len(result['category_flows']) == 1
        assert result['category_flows'][0]['target_label'] == 'exchange'
        assert result['category_flows'][0]['amount'] == 100
        # entity_flows i dalje razlikuje Binance od Coinbase - samo category_flows ih spaja
        assert len(result['entity_flows']) == 2


class TestNodeRecords:
    """Sažetak čvorova (za postavljanje kolona u Sankey prikazu)"""

    def test_seed_node_has_level_zero_and_type_seed(self):
        """Polazna adresa je uvek nivo 0 i tip 'seed'"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'])

        seed_node = next(node for node in result['nodes'] if node['id'] == '0xA')
        assert seed_node['level'] == 0
        assert seed_node['type'] == 'seed'

    def test_downstream_node_reports_the_level_it_was_first_reached_at(self):
        """Čvor dobija najmanji nivo na kome je prvi put dostignut"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xB', 'recipient_address': '0xC', 'amount': 90, 'timestamp': '2026-01-01T01:00:00Z'},
        ])

        result = trace_flow_of_funds(frame, source_addresses=['0xA'], max_levels=2)

        node_c = next(node for node in result['nodes'] if node['id'] == '0xC')
        assert node_c['level'] == 2
        assert node_c['type'] == 'address'


HEADER = 'sender_address,recipient_address,amount,timestamp,metadata'


def write_csv(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / 'evidence.csv'
    path.write_text(f'{HEADER}\n{rows}', encoding='utf-8')
    return path


class TestFlowOfFundsGetRoute:
    """Ruta GET /cases/{id}/flow-of-funds (pasivna, bez lanca dokaza)"""

    def test_returns_aggregated_flows_without_writing_audit_log(self, tmp_path, monkeypatch):
        """GET vraća agregirane tokove i NE upisuje log aktivnosti (pasivna ruta)"""
        from app.features.case_flow_of_funds import router as flow_router
        from app.shared import case_access
        from app.evidence import audit_log

        monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(case_access, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z,0xtx1\n0xB,0xC,90,2026-01-01T01:00:00Z,0xtx2\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(case_access, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        # Calling the route function directly (not through FastAPI/TestClient) means its
        # Query(...) defaults are NOT resolved automatically - every Query-typed parameter
        # must be passed explicitly, same constraint as any other direct FastAPI route call.
        result = flow_router.get_case_flow_of_funds(
            case_id='c1', source=['0xA'], direction='forward', max_levels=2, min_amount=0.0, max_flows=500,
        )

        assert result['flow_count'] == 2
        assert result['levels_reached'] == 2
        assert audit_log.load_audit_log_entries(case_id='c1') == []

    def test_missing_source_query_param_is_rejected(self, tmp_path, monkeypatch):
        """Bez 'source' upit-parametra ruta vraća jasnu grešku, ne pucanje"""
        from app.features.case_flow_of_funds import router as flow_router

        with pytest.raises(HTTPException) as excinfo:
            flow_router.get_case_flow_of_funds(case_id='c1', source=[])
        assert excinfo.value.status_code == 400


class TestFlowOfFundsRunRouteAndCustody:
    """Ruta POST /cases/{id}/flow-of-funds/run (namerna, sa lancem dokaza)"""

    def test_writes_audit_log_with_flow_summary(self, tmp_path, monkeypatch):
        """POST /run upisuje flow_of_funds_run u log aktivnosti sa sažetkom rezultata"""
        from app.features.case_flow_of_funds import router as flow_router
        from app.features.case_flow_of_funds.models import FlowOfFundsRunRequest
        from app.shared import case_access
        from app.evidence import audit_log

        monkeypatch.setattr(audit_log, '_audit_log_path', lambda: tmp_path / 'audit_log.jsonl')
        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(case_access, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z,0xtx1\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(case_access, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = FlowOfFundsRunRequest(source_addresses=['0xA'])
        result = flow_router.run_case_flow_of_funds(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result['flow_count'] == 1
        entries = audit_log.load_audit_log_entries(case_id='c1')
        assert len(entries) == 1
        assert entries[0]['action'] == 'flow_of_funds_run'
        assert entries[0]['details']['source_addresses'] == ['0xA']
        assert entries[0]['details']['custody_recorded'] is False

    def test_custody_present_writes_one_row_per_evidence_transaction(self, tmp_path, monkeypatch):
        """Sa 'custody' poljem, svaki red evidencije u obuhvatu dobija red u lancu dokaza"""
        from app.features.case_flow_of_funds import router as flow_router
        from app.features.case_flow_of_funds.models import FlowOfFundsRunRequest
        from app.shared import case_access
        from app.evidence import custody_log

        monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(case_access, 'get_case', lambda case_id: case)
        rows = '0xA,0xB,100,2026-01-01T00:00:00Z,0xtx1\n0xB,0xC,90,2026-01-01T01:00:00Z,0xtx2\n'
        csv_path = write_csv(tmp_path, rows)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(case_access, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = FlowOfFundsRunRequest(
            source_addresses=['0xA'],
            max_levels=2,
            custody={
                'ime_prezime': 'Aleksandar Sekulić',
                'opis_radnje': 'Praćenje toka sredstava od 0xA',
                'signature_image': 'data:image/png;base64,AAA',
            },
        )
        result = flow_router.run_case_flow_of_funds(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert result['flow_count'] == 2
        entries = custody_log.load_custody_entries(case_id='c1')
        assert len(entries) == 2
        assert entries[0]['ime_prezime'] == 'Aleksandar Sekulić'
        assert entries[0]['run_id'] == entries[1]['run_id']

    def test_custody_absent_writes_nothing_to_custody_log(self, tmp_path, monkeypatch):
        """Bez 'custody' polja, ruta se ponaša kao ranije - nema upisa u lanac dokaza"""
        from app.features.case_flow_of_funds import router as flow_router
        from app.features.case_flow_of_funds.models import FlowOfFundsRunRequest
        from app.shared import case_access
        from app.evidence import custody_log

        monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(case_access, 'get_case', lambda case_id: case)
        csv_path = write_csv(tmp_path, '0xA,0xB,100,2026-01-01T00:00:00Z,0xtx1\n')
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        monkeypatch.setattr(case_access, 'get_case_evidence_paths', lambda case: [(evidence_entry, csv_path)])

        request = FlowOfFundsRunRequest(source_addresses=['0xA'])
        flow_router.run_case_flow_of_funds(
            case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
        )

        assert custody_log.load_custody_entries(case_id='c1') == []

    def test_invalid_direction_is_rejected(self, tmp_path, monkeypatch):
        """Nepoznata vrednost 'direction' vraća jasnu grešku, ne pucanje"""
        from app.features.case_flow_of_funds import router as flow_router
        from app.features.case_flow_of_funds.models import FlowOfFundsRunRequest
        from app.shared import case_access

        case = {'id': 'c1', 'name': 'Slučaj 1', 'evidence': []}
        monkeypatch.setattr(case_access, 'get_case', lambda case_id: case)

        request = FlowOfFundsRunRequest(source_addresses=['0xA'], direction='sideways')
        with pytest.raises(HTTPException) as excinfo:
            flow_router.run_case_flow_of_funds(
                case_id='c1', request=request, current_user={'id': '1', 'username': 'aco', 'role': 'analyst'},
            )
        assert excinfo.value.status_code == 400
