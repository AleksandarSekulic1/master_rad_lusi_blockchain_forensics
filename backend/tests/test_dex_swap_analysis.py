"""Provera DEX Swap Analysis modula (prva verzija - heuristika, ne dokaz).

Cilj: prepoznati obrazac "adresa šalje token na poznat/verovatan DEX kontrakt, pa od
istog kontrakta ubrzo dobije nazad DRUGI token" i označiti ga kao Detected (isti tx hash)
ili Potential (samo adresa + vreme) swap - nikad kao neosporan nalaz. Testovi takođe
proveravaju da uobičajeni obrasci koji NISU swap (bounce iste valute, prekasan povratak,
povratak na drugu adresu, adresa koja nije DEX) ne budu lažno prijavljeni.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.dex_swap_analysis import (
    DEFAULT_MAX_GAP_SECONDS,
    MAX_MAX_GAP_SECONDS,
    MIN_MAX_GAP_SECONDS,
    classify_dex_node,
    detect_dex_swaps,
)

KNOWN_CONTRACTS = {
    '0x7a250d5630b4cf539739df2c5dacb4c659f2488d': {'name': 'Uniswap V2: Router 02'},
}


def frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Rows use the same column shape app.analytics.ingestion.clean_transaction_csv
    produces: sender_address, recipient_address, amount, timestamp, and optionally
    metadata (tx hash) / currency. Missing optional keys default to None, exactly like a
    CSV without those columns would after normalization."""
    columns = ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', 'currency']
    normalized = [{column: row.get(column) for column in columns} for row in rows]
    return pd.DataFrame(normalized, columns=columns)


class TestClassifyDexNode:
    """Prepoznavanje DEX čvora"""

    def test_known_address_matches_case_insensitively(self):
        """Poznata adresa se prepoznaje bez obzira na veličinu slova"""
        name, basis = classify_dex_node('0x7A250D5630B4CF539739DF2C5DACB4C659F2488D', KNOWN_CONTRACTS)

        assert name == 'Uniswap V2: Router 02'
        assert basis == 'known_address'

    def test_brand_keyword_matches_demo_style_pseudo_address(self):
        """Marka u imenu pseudo-adrese (demo podaci) prepoznaje se kao DEX"""
        name, basis = classify_dex_node('0xUniswapRouter', KNOWN_CONTRACTS)

        assert name == '0xUniswapRouter'
        assert basis is not None and basis.startswith('keyword_match_brand')

    def test_exchange_keyword_alone_is_not_classified_as_dex(self):
        """Reč 'exchange' sama po sebi NE znači DEX (izbegava sudar sa CEX adresama)"""
        name, basis = classify_dex_node('0xExchangeCounterparty', KNOWN_CONTRACTS)

        assert name is None
        assert basis is None

    def test_unrelated_address_is_not_classified(self):
        """Obična adresa bez signala se ne klasifikuje kao DEX"""
        name, basis = classify_dex_node('0xPeerWalletA', KNOWN_CONTRACTS)

        assert name is None
        assert basis is None


class TestDetectDexSwaps:
    """Detekcija swap događaja"""

    def test_shared_transaction_hash_yields_detected_confidence(self):
        """Isti transaction hash na oba kraka daje 'Detected', ne 'Potential'"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 10, 'timestamp': '2026-08-24T09:00:00Z', 'metadata': '0xswap1', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 25000, 'timestamp': '2026-08-24T09:00:00Z', 'metadata': '0xswap1', 'currency': 'USDC'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 1
        event = result['events'][0]
        assert event['confidence'] == 'Detected'
        assert event['label'] == 'Detected Swap'
        assert event['match_basis'] == 'shared_transaction_hash'
        assert event['input_token'] == 'ETH'
        assert event['input_amount'] == 10.0
        assert event['output_token'] == 'USDC'
        assert event['output_amount'] == 25000.0
        assert event['user_address'] == '0xWalletA'
        assert event['dex_address'] == '0xUniswapRouter'

    def test_time_window_without_shared_hash_yields_potential_confidence(self):
        """Bez zajedničkog hash-a, samo blizina u vremenu daje 'Potential', ne 'Detected'"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 2, 'timestamp': '2026-08-25T11:15:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 3200, 'timestamp': '2026-08-25T11:16:10Z', 'currency': 'DAI'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 1
        event = result['events'][0]
        assert event['confidence'] == 'Potential'
        assert event['match_basis'] == 'time_window'
        assert event['time_gap_seconds'] == 70

    def test_gap_beyond_max_gap_seconds_is_not_flagged(self):
        """Predugačak razmak (van prozora) se ne prijavljuje kao swap"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 4, 'timestamp': '2026-08-26T16:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 6000, 'timestamp': '2026-08-26T18:00:00Z', 'currency': 'USDT'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS, max_gap_seconds=300)

        assert result['total_events'] == 0

    def test_widening_max_gap_seconds_can_recover_the_same_pair(self):
        """Isti par se prepozna kada se prozor eksplicitno proširi (unutar dozvoljenog opsega)"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 4, 'timestamp': '2026-08-26T16:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 6000, 'timestamp': '2026-08-26T16:20:00Z', 'currency': 'USDT'},
        ])

        not_yet = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS, max_gap_seconds=300)
        widened = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS, max_gap_seconds=1800)

        assert not_yet['total_events'] == 0
        assert widened['total_events'] == 1

    def test_same_declared_currency_on_both_legs_is_not_flagged(self):
        """Isti token na oba kraka (npr. ETH -> ETH) se NE tretira kao swap"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-27T14:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 0.98, 'timestamp': '2026-08-27T14:00:30Z', 'currency': 'ETH'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 0

    def test_return_to_a_different_address_is_not_flagged(self):
        """Povratni transfer na DRUGU adresu (ne pošiljaoca) se ne uparuje kao swap"""
        frame = frame_from_rows([
            {'sender_address': '0xMuleWallet', 'recipient_address': '0xUniswapRouter', 'amount': 5, 'timestamp': '2026-08-28T10:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xDeadDropWallet', 'amount': 7000, 'timestamp': '2026-08-28T10:01:00Z', 'currency': 'USDC'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 0

    def test_ordinary_bounce_between_plain_wallets_is_not_flagged(self):
        """Obično 'ping-pong' slanje između dve običnne adrese (nema DEX signala) se ne prijavljuje"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xPeerWalletB', 'amount': 100, 'timestamp': '2026-08-29T08:00:00Z', 'currency': 'USDC'},
            {'sender_address': '0xPeerWalletB', 'recipient_address': '0xWalletA', 'amount': 100, 'timestamp': '2026-08-29T08:01:00Z', 'currency': 'USDC'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 0
        assert result['dex_nodes_considered'] == []

    def test_missing_currency_column_still_detects_by_address_and_time(self):
        """Bez currency kolone uopšte, par se i dalje prepoznaje - ali kao token=null"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-30T09:00:00Z'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 1800, 'timestamp': '2026-08-30T09:02:00Z'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 1
        event = result['events'][0]
        assert event['input_token'] is None
        assert event['output_token'] is None
        assert result['data_completeness']['currency_declared'] is False

    def test_address_filter_scopes_result_to_that_user(self):
        """`address` filtrira rezultat samo na traženog korisnika"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-24T09:00:00Z', 'metadata': '0xh1', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 1500, 'timestamp': '2026-08-24T09:00:00Z', 'metadata': '0xh1', 'currency': 'USDC'},
            {'sender_address': '0xWalletB', 'recipient_address': '0xUniswapRouter', 'amount': 2, 'timestamp': '2026-08-24T10:00:00Z', 'metadata': '0xh2', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletB', 'amount': 3000, 'timestamp': '2026-08-24T10:00:00Z', 'metadata': '0xh2', 'currency': 'USDC'},
        ])

        result = detect_dex_swaps(frame, target_address='0xWalletB', known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 1
        assert result['events'][0]['user_address'] == '0xWalletB'

    def test_unknown_address_raises_value_error(self):
        """Adresa koja se uopšte ne pojavljuje u evidenciji diže ValueError (-> 404 na ruti)"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-24T09:00:00Z', 'currency': 'ETH'},
        ])

        with pytest.raises(ValueError):
            detect_dex_swaps(frame, target_address='0xNeverSeenWallet', known_contracts=KNOWN_CONTRACTS)

    def test_address_is_matched_case_sensitively(self):
        """Adresa se traži tačnim poklapanjem (case-sensitive), isto kao Pathfinding/Behavioral"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-24T09:00:00Z', 'currency': 'ETH'},
        ])

        with pytest.raises(ValueError):
            detect_dex_swaps(frame, target_address='0xwalleta', known_contracts=KNOWN_CONTRACTS)

    def test_each_transaction_is_used_in_at_most_one_event(self):
        """Jedna transakcija se ne upotrebljava u više od jednog swap para (bez duplog brojanja)"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-24T09:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 1500, 'timestamp': '2026-08-24T09:00:30Z', 'currency': 'USDC'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 1600, 'timestamp': '2026-08-24T09:00:45Z', 'currency': 'USDC'},
        ])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 1
        assert result['events'][0]['output_amount'] == 1500.0

    def test_max_gap_seconds_is_clamped_to_documented_bounds(self):
        """max_gap_seconds van dozvoljenog opsega se ograniči, ne baci grešku"""
        frame = frame_from_rows([
            {'sender_address': '0xWalletA', 'recipient_address': '0xUniswapRouter', 'amount': 1, 'timestamp': '2026-08-24T09:00:00Z', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xWalletA', 'amount': 1500, 'timestamp': '2026-08-24T09:00:05Z', 'currency': 'USDC'},
        ])

        too_low = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS, max_gap_seconds=0)
        too_high = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS, max_gap_seconds=999_999)

        assert too_low['max_gap_seconds'] == MIN_MAX_GAP_SECONDS
        assert too_high['max_gap_seconds'] == MAX_MAX_GAP_SECONDS

    def test_response_always_carries_a_disclaimer(self):
        """Odgovor uvek nosi disclaimer, bez obzira na broj pronađenih događaja"""
        frame = frame_from_rows([])

        result = detect_dex_swaps(frame, known_contracts=KNOWN_CONTRACTS)

        assert result['total_events'] == 0
        assert 'heuristika' in result['disclaimer'].lower()

    def test_default_max_gap_seconds_is_five_minutes(self):
        """Podrazumevani prozor je 300 sekundi (5 minuta)"""
        assert DEFAULT_MAX_GAP_SECONDS == 300
