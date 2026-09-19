"""Provera Sybil & Bot Network Analysis modula (heuristika, ne dokaz).

Cilj: prepoznati grupe RAZLIČITIH adresa koje u kratkom, sinhronizovanom vremenskom
periodu pozivaju isti smart contract i/ili istu funkciju, i označiti ih kao potencijalni
Sybil klaster sa confidence/risk skorom - nikad kao tvrdnju da adrese pripadaju istoj
osobi/entitetu. Testovi takođe proveravaju da se aktivnost jedne adrese, aktivnost izvan
vremenskog prozora, i nedovoljan broj adresa NE prijave kao klaster.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.sybil_analysis import (
    DEFAULT_MIN_ADDRESSES,
    DEFAULT_TIME_WINDOW_SECONDS,
    MAX_MIN_ADDRESSES,
    MAX_TIME_WINDOW_SECONDS,
    MIN_MIN_ADDRESSES,
    MIN_TIME_WINDOW_SECONDS,
    detect_sybil_clusters,
)

KNOWN_CONTRACTS: dict[str, dict[str, str]] = {}


def frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Rows use the same column shape app.analytics.ingestion.clean_transaction_csv
    produces, plus the optional `function_name` column this module reads directly (never
    touched by ingestion.py's COLUMN_ALIASES - same mechanism as `currency`/`event_type`).
    Missing optional keys default to None, exactly like a CSV without those columns would
    after normalization."""
    columns = ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', 'function_name']
    normalized = [{column: row.get(column) for column in columns} for row in rows]
    return pd.DataFrame(normalized, columns=columns)


def call(sender: str, contract: str, at: str, amount: float = 1.0, function_name: str | None = None) -> dict[str, object]:
    return {
        'sender_address': sender,
        'recipient_address': contract,
        'amount': amount,
        'timestamp': at,
        'function_name': function_name,
    }


class TestBasicClusterDetection:
    """Osnovna detekcija Sybil klastera"""

    def test_synchronized_addresses_form_a_cluster(self):
        """Tri različite adrese koje pozovu isti kontrakt u kratkom periodu čine klaster"""
        frame = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:00:30Z'),
            call('0xAddrC', '0xClaimContract', '2026-08-24T09:01:00Z'),
        ])

        result = detect_sybil_clusters(frame)

        assert result['total_clusters'] == 1
        cluster = result['clusters'][0]
        assert cluster['contract_address'] == '0xClaimContract'
        assert cluster['address_count'] == 3
        assert set(cluster['addresses']) == {'0xAddrA', '0xAddrB', '0xAddrC'}
        assert cluster['activity_count'] == 3
        assert cluster['cluster_id'] == 'SYBIL-1'
        assert cluster['risk_score'] > 0
        assert cluster['risk_level'] in ('low', 'medium', 'high', 'critical')

    def test_fewer_than_minimum_addresses_is_not_flagged(self):
        """Dve adrese ispod podrazumevanog praga (3) se ne prijavljuju kao klaster"""
        frame = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:00:30Z'),
        ])

        result = detect_sybil_clusters(frame)

        assert result['total_clusters'] == 0
        assert result['clusters'] == []

    def test_single_address_calling_repeatedly_is_not_a_sybil_signal(self):
        """Jedna adresa koja više puta poziva isti kontrakt nije Sybil obrazac"""
        frame = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z'),
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:10Z'),
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:20Z'),
        ])

        result = detect_sybil_clusters(frame)

        assert result['total_clusters'] == 0

    def test_activity_outside_time_window_is_not_merged_into_one_cluster(self):
        """Aktivnost van vremenskog prozora se ne spaja u isti klaster"""
        frame = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:01:00Z'),
            # 3+ hours later - well outside the default 300s window.
            call('0xAddrC', '0xClaimContract', '2026-08-24T12:00:00Z'),
            call('0xAddrD', '0xClaimContract', '2026-08-24T12:01:00Z'),
        ])

        result = detect_sybil_clusters(frame, min_addresses=3)
        assert result['total_clusters'] == 0

        # Lowering the threshold to 2 reveals TWO separate small clusters, not one merged
        # one spanning the whole 3-hour gap - confirms the window actually splits them.
        result_lower_threshold = detect_sybil_clusters(frame, min_addresses=2)
        assert result_lower_threshold['total_clusters'] == 2
        for cluster in result_lower_threshold['clusters']:
            assert cluster['window_duration_seconds'] < DEFAULT_TIME_WINDOW_SECONDS

    def test_missing_required_columns_raises(self):
        """Nedostatak obaveznih kolona diže ValueError"""
        frame = pd.DataFrame([{'sender_address': '0xAddrA'}])

        with pytest.raises(ValueError):
            detect_sybil_clusters(frame)


class TestFunctionGrouping:
    """Grupisanje po funkciji (kad je deklarisana u evidenciji)"""

    def test_different_functions_on_same_contract_form_separate_clusters(self):
        """Različite funkcije na istom kontraktu se ne mešaju u jedan klaster"""
        frame = frame_from_rows([
            call('0xAddrA', '0xVaultContract', '2026-08-24T09:00:00Z', function_name='claim'),
            call('0xAddrB', '0xVaultContract', '2026-08-24T09:00:20Z', function_name='claim'),
            call('0xAddrC', '0xVaultContract', '2026-08-24T09:00:40Z', function_name='claim'),
            call('0xAddrD', '0xVaultContract', '2026-08-24T09:00:05Z', function_name='mint'),
            call('0xAddrE', '0xVaultContract', '2026-08-24T09:00:25Z', function_name='mint'),
            call('0xAddrF', '0xVaultContract', '2026-08-24T09:00:45Z', function_name='mint'),
        ])

        result = detect_sybil_clusters(frame)

        assert result['function_data_available'] is True
        assert result['total_clusters'] == 2
        functions = {cluster['function_name'] for cluster in result['clusters']}
        assert functions == {'claim', 'mint'}
        for cluster in result['clusters']:
            assert cluster['address_count'] == 3

    def test_function_data_unavailable_falls_back_to_contract_only_grouping(self):
        """Bez function_name kolone, grupisanje radi samo po kontraktu"""
        frame = frame_from_rows([
            call('0xAddrA', '0xVaultContract', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xVaultContract', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xVaultContract', '2026-08-24T09:00:40Z'),
        ]).drop(columns=['function_name'])

        result = detect_sybil_clusters(frame)

        assert result['function_data_available'] is False
        assert result['total_clusters'] == 1
        assert result['clusters'][0]['function_name'] is None


class TestRiskScoring:
    """Confidence/risk skor po klasteru"""

    def test_identical_amounts_increase_risk_score(self):
        """Identičan iznos kod većine transakcija povećava risk skor (bot obrazac)"""
        varied_amounts = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z', amount=10),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:00:20Z', amount=25),
            call('0xAddrC', '0xClaimContract', '2026-08-24T09:00:40Z', amount=37.5),
        ])
        identical_amounts = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z', amount=10),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:00:20Z', amount=10),
            call('0xAddrC', '0xClaimContract', '2026-08-24T09:00:40Z', amount=10),
        ])

        varied_result = detect_sybil_clusters(varied_amounts)
        identical_result = detect_sybil_clusters(identical_amounts)

        assert identical_result['clusters'][0]['risk_score'] > varied_result['clusters'][0]['risk_score']
        assert identical_result['clusters'][0]['identical_amount_ratio'] == 1.0

    def test_addresses_repeated_across_clusters_increase_risk_score(self):
        """Kohorta adresa koja se ponavlja u više klastera dobija veći risk skor"""
        # Cluster 1: A, B, C on contract X. Cluster 2: A, B, D on contract Y (disjoint time
        # window). A and B recur - repeated_address_count should be 2 for both clusters.
        frame = frame_from_rows([
            call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xContractX', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xContractX', '2026-08-24T09:00:40Z'),
            call('0xAddrA', '0xContractY', '2026-08-25T09:00:00Z'),
            call('0xAddrB', '0xContractY', '2026-08-25T09:00:20Z'),
            call('0xAddrD', '0xContractY', '2026-08-25T09:00:40Z'),
        ])
        # A control case with the same shape but no address overlap between clusters.
        frame_no_overlap = frame_from_rows([
            call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xContractX', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xContractX', '2026-08-24T09:00:40Z'),
            call('0xAddrE', '0xContractY', '2026-08-25T09:00:00Z'),
            call('0xAddrF', '0xContractY', '2026-08-25T09:00:20Z'),
            call('0xAddrG', '0xContractY', '2026-08-25T09:00:40Z'),
        ])

        result = detect_sybil_clusters(frame)
        control = detect_sybil_clusters(frame_no_overlap)

        assert result['total_clusters'] == 2
        for cluster in result['clusters']:
            assert cluster['repeated_address_count'] == 2

        assert all(cluster['repeated_address_count'] == 0 for cluster in control['clusters'])
        assert min(c['risk_score'] for c in result['clusters']) > min(c['risk_score'] for c in control['clusters'])

    def test_more_addresses_yield_higher_risk_score(self):
        """Više sinhronizovanih adresa u istom klasteru daje veći risk skor"""
        small = frame_from_rows([
            call('0xAddrA', '0xClaimContract', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xClaimContract', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xClaimContract', '2026-08-24T09:00:40Z'),
        ])
        large = frame_from_rows([
            call(f'0xAddr{i}', '0xClaimContract', f'2026-08-24T09:0{i}:00Z')
            for i in range(8)
        ])

        small_result = detect_sybil_clusters(small)
        large_result = detect_sybil_clusters(large)

        assert large_result['clusters'][0]['risk_score'] > small_result['clusters'][0]['risk_score']


class TestFiltering:
    """Filtriranje po adresi i kontraktu"""

    def test_filters_by_target_address(self):
        """Filtriranje po adresi vraća samo klastere u kojima ta adresa učestvuje"""
        frame = frame_from_rows([
            call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xContractX', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xContractX', '2026-08-24T09:00:40Z'),
            call('0xAddrD', '0xContractY', '2026-08-25T09:00:00Z'),
            call('0xAddrE', '0xContractY', '2026-08-25T09:00:20Z'),
            call('0xAddrF', '0xContractY', '2026-08-25T09:00:40Z'),
        ])

        result = detect_sybil_clusters(frame, target_address='0xAddrA')

        assert result['total_clusters'] == 1
        assert '0xAddrA' in result['clusters'][0]['addresses']

    def test_unknown_target_address_raises(self):
        """Adresa koja se nigde ne pojavljuje u evidenciji diže ValueError"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        with pytest.raises(ValueError):
            detect_sybil_clusters(frame, target_address='0xNeverSeen')

    def test_filters_by_contract(self):
        """Filtriranje po kontraktu ograničava analizu na taj kontrakt"""
        frame = frame_from_rows([
            call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z'),
            call('0xAddrB', '0xContractX', '2026-08-24T09:00:20Z'),
            call('0xAddrC', '0xContractX', '2026-08-24T09:00:40Z'),
            call('0xAddrD', '0xContractY', '2026-08-25T09:00:00Z'),
            call('0xAddrE', '0xContractY', '2026-08-25T09:00:20Z'),
            call('0xAddrF', '0xContractY', '2026-08-25T09:00:40Z'),
        ])

        result = detect_sybil_clusters(frame, contract='0xContractX')

        assert result['total_clusters'] == 1
        assert result['clusters'][0]['contract_address'] == '0xContractX'

    def test_unknown_contract_raises(self):
        """Kontrakt/adresa koja nikad nije primalac transakcije diže ValueError"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        with pytest.raises(ValueError):
            detect_sybil_clusters(frame, contract='0xNeverSeenAsRecipient')


class TestParameterClamping:
    """Ograničavanje parametara na dozvoljen opseg"""

    def test_time_window_seconds_is_clamped(self):
        """time_window_seconds se ograničava na [MIN, MAX]"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        assert detect_sybil_clusters(frame, time_window_seconds=1)['time_window_seconds'] == MIN_TIME_WINDOW_SECONDS
        assert detect_sybil_clusters(frame, time_window_seconds=999999)['time_window_seconds'] == MAX_TIME_WINDOW_SECONDS

    def test_min_addresses_is_clamped(self):
        """min_addresses se ograničava na [MIN, MAX]"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        assert detect_sybil_clusters(frame, min_addresses=1)['min_addresses'] == MIN_MIN_ADDRESSES
        assert detect_sybil_clusters(frame, min_addresses=999)['min_addresses'] == MAX_MIN_ADDRESSES

    def test_defaults_match_module_constants(self):
        """Podrazumevani parametri se poklapaju sa konstantama modula"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        result = detect_sybil_clusters(frame)

        assert result['time_window_seconds'] == DEFAULT_TIME_WINDOW_SECONDS
        assert result['min_addresses'] == DEFAULT_MIN_ADDRESSES


class TestDisclaimer:
    """Disclaimer - nikad tvrdnja o zajedničkom vlasništvu"""

    def test_disclaimer_is_always_present_and_never_claims_common_ownership(self):
        """Disclaimer je uvek prisutan i eksplicitno kaže da ovo nije dokaz vlasništva"""
        frame = frame_from_rows([call('0xAddrA', '0xContractX', '2026-08-24T09:00:00Z')])

        result = detect_sybil_clusters(frame)

        assert result['disclaimer']
        assert 'NIKADA' in result['disclaimer'] or 'nikada' in result['disclaimer'].lower()
        assert 'iste osobe' in result['disclaimer'] or 'istoj osobi' in result['disclaimer']
