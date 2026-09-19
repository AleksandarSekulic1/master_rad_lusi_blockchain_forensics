"""Provera Flow of Funds obogaćivanja nalazima iz Graph/DEX/Token Approval/Taint/Sybil
analiza (backend/app/analytics/flow_of_funds_enrichment.py).

Cilj: svaka adresa iz Flow of Funds nalaza dobija TRI jasno odvojene liste -
'facts' (činjenice iz spoljnih registara), 'aggregated' (aritmetika nad stvarnim
transakcijama, bez modela) i 'heuristics' (zaključci postojećih plugin-ova/analiza, mogu biti
pogrešni) - nikad pomešane u jednu generičku listu.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici "Testovi"
u aplikaciji.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.flow_of_funds_enrichment import enrich_flow_of_funds_nodes


def frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata', 'currency']
    normalized = [{column: row.get(column) for column in columns} for row in rows]
    return pd.DataFrame(normalized, columns=columns)


class TestKnownEntityAndBlacklistFacts:
    """Činjenice iz spoljnih registara (poznati entiteti, crna lista)"""

    def test_known_entity_is_reported_as_fact(self, monkeypatch):
        """Adresa iz lokalnog registra poznatih entiteta dobija 'fact' zapis, ne heuristiku"""
        from app.analytics import flow_of_funds_enrichment

        monkeypatch.setattr(
            flow_of_funds_enrichment, 'get_known_entity',
            lambda address: {'name': 'Binance 9', 'category': 'exchange'} if address == '0xExchange' else None,
        )

        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xExchange', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        annotations = enrich_flow_of_funds_nodes(['0xA', '0xExchange'], frame, seed_addresses=['0xA'])

        facts = annotations['0xExchange']['facts']
        assert any(fact['type'] == 'known_entity' and fact['name'] == 'Binance 9' for fact in facts)
        assert annotations['0xA']['facts'] == []

    def test_blacklisted_address_is_reported_as_fact(self):
        """Adresa sa crne liste (OFAC/Chainabuse) dobija 'fact' zapis"""
        frame = frame_from_rows([
            {
                'sender_address': '0xVictim',
                'recipient_address': '0xbad0000000000000000000000000000000000001',
                'amount': 100,
                'timestamp': '2026-01-01T00:00:00Z',
            },
        ])

        annotations = enrich_flow_of_funds_nodes(
            ['0xVictim', '0xbad0000000000000000000000000000000000001'], frame, seed_addresses=['0xVictim'],
        )

        facts = annotations['0xbad0000000000000000000000000000000000001']['facts']
        assert any(fact['type'] == 'blacklist' and 'OFAC' in fact['sources'] for fact in facts)


class TestAggregatedResults:
    """Agregovani rezultati - čista aritmetika nad stvarnim transakcijama"""

    def test_flow_totals_are_reported_as_aggregated(self):
        """total_received/total_sent/net_flow su 'aggregated', ne heuristika"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xB', 'recipient_address': '0xC', 'amount': 40, 'timestamp': '2026-01-01T01:00:00Z'},
        ])

        annotations = enrich_flow_of_funds_nodes(['0xA', '0xB', '0xC'], frame, seed_addresses=['0xA'])

        totals = next(item for item in annotations['0xB']['aggregated'] if item['type'] == 'flow_totals')
        assert totals['total_received'] == 100
        assert totals['total_sent'] == 40
        assert totals['net_flow'] == 60

    def test_dex_swap_with_shared_tx_hash_is_aggregated_not_heuristic(self):
        """DEX swap potvrđen istim tx hash-om na oba kraka je 'aggregated' (Detected)"""
        frame = frame_from_rows([
            {'sender_address': '0xTrader', 'recipient_address': '0xUniswapRouter', 'amount': 10, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': '0xswap1', 'currency': 'ETH'},
            {'sender_address': '0xUniswapRouter', 'recipient_address': '0xTrader', 'amount': 25000, 'timestamp': '2026-01-01T00:00:00Z', 'metadata': '0xswap1', 'currency': 'USDC'},
        ])

        annotations = enrich_flow_of_funds_nodes(['0xTrader', '0xUniswapRouter'], frame, seed_addresses=['0xTrader'])

        aggregated_types = [item['type'] for item in annotations['0xTrader']['aggregated']]
        heuristic_types = [item['type'] for item in annotations['0xTrader']['heuristics']]
        assert 'dex_swap_detected' in aggregated_types
        assert 'dex_swap_potential' not in heuristic_types


class TestHeuristicConclusions:
    """Heuristički zaključci - modeli/plugin-ovi koji mogu pogrešiti"""

    def test_risk_score_is_reported_as_heuristic(self):
        """risk_scoring plugin rezultat je 'heuristics', ne 'facts'/'aggregated'"""
        frame = frame_from_rows([
            {'sender_address': '0xbad0000000000000000000000000000000000001', 'recipient_address': '0xB', 'amount': 500, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        annotations = enrich_flow_of_funds_nodes(
            ['0xbad0000000000000000000000000000000000001', '0xB'], frame,
            seed_addresses=['0xbad0000000000000000000000000000000000001'],
        )

        heuristics = annotations['0xbad0000000000000000000000000000000000001']['heuristics']
        assert any(item['type'] == 'risk_score' for item in heuristics)

    def test_peel_chain_role_is_reported_as_heuristic(self):
        """peel_chains plugin rezultat (uloga u lancu) je heuristika"""
        # peel_chains seeds a walk from a node's INCOMING transaction (>= min_seed_amount),
        # then follows what it does with that money - so the chain needs a funding hop INTO
        # '0xSeed' first, then several forward+peel rounds through distinct relay nodes.
        base_time = pd.Timestamp('2026-01-01T00:00:00Z')
        rows = [
            {'sender_address': '0xFunder', 'recipient_address': '0xSeed', 'amount': 1000.0, 'timestamp': base_time.isoformat()},
        ]
        node = '0xSeed'
        current_amount = 1000.0
        for step in range(4):
            next_node = f'0xRelay{step}'
            step_time = (base_time + pd.Timedelta(minutes=step * 10)).isoformat()
            rows.append({'sender_address': node, 'recipient_address': next_node, 'amount': current_amount * 0.9, 'timestamp': step_time})
            rows.append({'sender_address': node, 'recipient_address': f'0xPeeled{step}', 'amount': current_amount * 0.1, 'timestamp': step_time})
            node = next_node
            current_amount *= 0.9

        frame = frame_from_rows(rows)
        addresses = list({row['sender_address'] for row in rows} | {row['recipient_address'] for row in rows})

        annotations = enrich_flow_of_funds_nodes(addresses, frame, seed_addresses=['0xFunder'])

        assert any(item['type'] == 'peel_chain' for item in annotations['0xSeed']['heuristics'])

    def test_taint_is_opt_in_and_seeded_from_flow_of_funds_seeds(self):
        """Taint analiza se ne pokreće po difoltu; kad je uključena, koristi ISTE seed adrese"""
        frame = frame_from_rows([
            {'sender_address': '0xSeed', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        without_taint = enrich_flow_of_funds_nodes(['0xSeed', '0xB'], frame, seed_addresses=['0xSeed'])
        assert not any(item['type'] == 'taint' for item in without_taint['0xB']['heuristics'])

        with_taint = enrich_flow_of_funds_nodes(['0xSeed', '0xB'], frame, seed_addresses=['0xSeed'], include_taint=True)
        taint_entries = [item for item in with_taint['0xB']['heuristics'] if item['type'] == 'taint']
        assert len(taint_entries) == 1
        assert taint_entries[0]['percentage'] == 100.0

    def test_sybil_is_opt_in(self):
        """Sybil analiza se ne pokreće po difoltu (samo kad je include_sybil=True)"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        without_sybil = enrich_flow_of_funds_nodes(['0xA', '0xB'], frame, seed_addresses=['0xA'])
        with_sybil = enrich_flow_of_funds_nodes(['0xA', '0xB'], frame, seed_addresses=['0xA'], include_sybil=True)

        assert not any(item['type'] == 'sybil_cluster' for bucket in without_sybil.values() for item in bucket['heuristics'])
        # Nema dovoljno podataka za pravi sybil klaster u ovom malom uzorku - samo proveravamo
        # da uključivanje flag-a ne baca grešku i i dalje vraća validnu strukturu.
        assert set(with_sybil.keys()) == {'0xA', '0xB'}


class TestPeriodScoping:
    """Obogaćivanje poštuje isti vremenski period kao i sam trace"""

    def test_annotations_only_reflect_transactions_within_period(self):
        """Van izabranog perioda, tokovi se ne računaju u agregate (isto kao kod trace-a)"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 900, 'timestamp': '2026-06-01T00:00:00Z'},
        ])

        annotations = enrich_flow_of_funds_nodes(
            ['0xA', '0xB'], frame, seed_addresses=['0xA'],
            start_time='2026-01-01T00:00:00Z', end_time='2026-01-31T23:59:59Z',
        )

        totals = next(item for item in annotations['0xB']['aggregated'] if item['type'] == 'flow_totals')
        assert totals['total_received'] == 100


class TestEmptyAndMissingData:
    """Rubni slučajevi - prazna evidencija, nepostojeća adresa"""

    def test_empty_dataframe_returns_empty_buckets_without_error(self):
        """Prazna evidencija ne baca grešku - vraća prazne kante po adresi"""
        frame = frame_from_rows([])

        annotations = enrich_flow_of_funds_nodes(['0xA'], frame, seed_addresses=['0xA'])

        assert annotations == {'0xA': {'facts': [], 'aggregated': [], 'heuristics': []}}

    def test_address_not_in_any_transaction_still_gets_empty_buckets(self):
        """Adresa koja se ne pojavljuje u evidenciji i dalje dobija validnu (praznu) strukturu"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 100, 'timestamp': '2026-01-01T00:00:00Z'},
        ])

        annotations = enrich_flow_of_funds_nodes(['0xA', '0xNepostojeca'], frame, seed_addresses=['0xA'])

        assert annotations['0xNepostojeca'] == {'facts': [], 'aggregated': [], 'heuristics': []}
