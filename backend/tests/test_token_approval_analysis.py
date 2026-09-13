"""Provera Token Approval / Ice Phishing Analysis modula (backend deo - prikupljanje i
ekstrakcija).

Cilj: iz evidencije koja deklariše approve/permit/transferFrom redove (opcione kolone -
vidi token_approval_analysis.py modul-docstring i TOKEN-APPROVAL-IMPLEMENTATION.md #7/#8)
izvući owner/spender/token/allowance/status/rizik podatke BEZ dekodiranja bilo čega sa
lanca i bez izmišljanja podataka koje evidencija ne deklariše - testovi takođe proveravaju
da odsustvo opcionih kolona ne izazove grešku, već samo eksplicitno obeleženo ograničenje.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.token_approval_analysis import (
    DEFAULT_UNLIMITED_THRESHOLD,
    analyze_token_approvals,
)

BASE_COLUMNS = [
    'sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata',
    'event_type', 'token_address', 'owner_address', 'spender_address',
    'is_unlimited', 'block_number', 'permit_deadline', 'permit_nonce',
]


def frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Rows use the base ingestion.clean_transaction_csv shape plus the new, optional
    Token Approval columns - missing keys default to None, exactly like a CSV without
    those columns would after normalization (see ingestion._normalize_columns)."""
    normalized = [{column: row.get(column) for column in BASE_COLUMNS} for row in rows]
    return pd.DataFrame(normalized, columns=BASE_COLUMNS)


def approve_row(owner: str, spender: str, amount: float, ts: str, **extra: object) -> dict[str, object]:
    row = {
        'sender_address': owner,
        'recipient_address': spender,
        'amount': amount,
        'timestamp': ts,
        'event_type': 'approve',
    }
    row.update(extra)
    return row


def transfer_from_row(owner: str, spender: str, recipient: str, amount: float, ts: str, **extra: object) -> dict[str, object]:
    row = {
        'sender_address': owner,
        'recipient_address': recipient,
        'amount': amount,
        'timestamp': ts,
        'event_type': 'transferFrom',
        'spender_address': spender,
    }
    row.update(extra)
    return row


class TestRequiredColumns:
    """Osnovna validacija ulaza"""

    def test_missing_base_column_raises_value_error(self):
        """Nedostatak obavezne bazne kolone baca ValueError"""
        frame = pd.DataFrame({'sender_address': ['0xA'], 'amount': [1]})

        with pytest.raises(ValueError):
            analyze_token_approvals(frame)

    def test_missing_event_type_column_returns_empty_result_not_error(self):
        """Odsustvo 'event_type' kolone ne baca grešku - vraća prazan, ali validan rezultat"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 1, 'timestamp': '2026-01-01T00:00:00Z'},
        ])
        frame = frame.drop(columns=['event_type'])

        result = analyze_token_approvals(frame)

        assert result['total_approval_events'] == 0
        assert result['groups'] == []
        assert result['data_completeness']['event_type_declared'] is False
        assert any('event_type' in note for note in result['data_completeness']['notes'])


class TestOwnerSpenderExtraction:
    """Owner/spender ekstrakcija (approve/permit)"""

    def test_approve_reuses_sender_recipient_when_no_explicit_columns(self):
        """approve() bez owner_address/spender_address koristi sender/recipient kao fallback"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        assert result['total_approval_events'] == 1
        group = result['groups'][0]
        assert group['owner'] == '0xOwner'
        assert group['spender'] == '0xSpender'

    def test_explicit_owner_address_overrides_sender_for_permit(self):
        """Eksplicitan owner_address se koristi umesto sender_address (permit relayer slučaj)"""
        frame = frame_from_rows([
            {
                'sender_address': '0xRelayer', 'recipient_address': '0xSpender', 'amount': 50.0,
                'timestamp': '2026-01-01T00:00:00Z', 'event_type': 'permit', 'owner_address': '0xRealOwner',
            },
        ])

        result = analyze_token_approvals(frame)

        group = result['groups'][0]
        assert group['owner'] == '0xRealOwner'
        assert group['approvals'][0]['event_type'] == 'permit'

    def test_unrecognized_event_type_value_is_reported_not_silently_dropped(self):
        """Nepoznata vrednost event_type se prijavljuje, ne skriva se ćutke"""
        frame = frame_from_rows([
            {'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 1, 'timestamp': '2026-01-01T00:00:00Z', 'event_type': 'wrapAndApprove'},
        ])

        result = analyze_token_approvals(frame)

        assert result['total_approval_events'] == 0
        assert result['unrecognized_event_type_values'] == ['wrapAndApprove']


class TestUnlimitedDetection:
    """Detekcija 'unlimited allowance'"""

    def test_declared_is_unlimited_column_wins(self):
        """Eksplicitno deklarisan is_unlimited=true daje 'declared' osnovu"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 5.0, '2026-01-01T00:00:00Z', is_unlimited='true')])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['current_unlimited_basis'] == 'declared'

    def test_large_magnitude_without_declared_column_is_only_potential(self):
        """Veoma veliki iznos bez is_unlimited kolone je samo 'potential_by_magnitude', ne potvrđeno"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', DEFAULT_UNLIMITED_THRESHOLD * 10, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['current_unlimited_basis'] == 'potential_by_magnitude'

    def test_ordinary_amount_is_not_unlimited(self):
        """Uobičajen iznos se ne označava kao neograničen"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 250.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['current_unlimited_basis'] is None
        assert result['groups'][0]['ever_unlimited'] is False


class TestApprovalStatusSequence:
    """approve(spender, 0) / revocation i redosled odobrenja"""

    def test_zero_amount_approve_is_revoked_status(self):
        """approve(spender, 0) daje status 'revoked'"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 0.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        group = result['groups'][0]
        assert group['current_status'] == 'revoked'
        assert group['approvals'][0]['is_zero'] is True
        assert group['approvals'][0]['sequence_status'] == 'revoked'

    def test_nonzero_amount_approve_is_active_status(self):
        """approve(spender, amount>0) daje status 'active'"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 500.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['current_status'] == 'active'

    def test_earlier_approval_in_same_group_is_superseded(self):
        """Ranije odobrenje u istoj grupi postaje 'superseded' kad stigne novije"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z'),
            approve_row('0xOwner', '0xSpender', 0.0, '2026-01-02T00:00:00Z'),
        ])

        result = analyze_token_approvals(frame)

        group = result['groups'][0]
        assert group['approval_event_count'] == 2
        assert group['approvals'][0]['sequence_status'] == 'superseded'
        assert group['approvals'][1]['sequence_status'] == 'revoked'
        assert group['current_status'] == 'revoked'


class TestTransferFromLinking:
    """Uparivanje transferFrom sa odobrenjem"""

    def test_exact_owner_spender_token_match(self):
        """Tačno poklapanje (owner, spender, token) povezuje transferFrom sa odobrenjem"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 1000.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),
            transfer_from_row('0xOwner', '0xSpender', '0xThirdParty', 400.0, '2026-01-01T01:00:00Z', token_address='0xTokenA'),
        ])

        result = analyze_token_approvals(frame)

        group = result['groups'][0]
        assert group['transfer_from_count'] == 1
        assert group['total_transferred_amount'] == 400.0
        assert group['related_addresses'] == ['0xThirdParty']
        assert result['unattributed_transferfrom_count'] == 0

    def test_missing_spender_column_leaves_transfer_unattributed(self):
        """transferFrom bez spender_address ostaje neatribuiran, ne pogađa se kome pripada"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 1000.0, '2026-01-01T00:00:00Z'),
            {'sender_address': '0xOwner', 'recipient_address': '0xThirdParty', 'amount': 400.0, 'timestamp': '2026-01-01T01:00:00Z', 'event_type': 'transferFrom'},
        ])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['transfer_from_count'] == 0
        assert result['unattributed_transferfrom_count'] == 1
        assert result['unattributed_transfers'][0]['reason'] == 'no_spender_column'

    def test_ambiguous_token_with_multiple_approvals_stays_unattributed(self):
        """Kad isti (owner,spender) ima odobrenja za VIŠE tokena, transfer bez token_address ostaje neatribuiran"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),
            approve_row('0xOwner', '0xSpender', 200.0, '2026-01-01T00:00:00Z', token_address='0xTokenB'),
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 50.0, '2026-01-01T02:00:00Z'),
        ])

        result = analyze_token_approvals(frame)

        assert result['unattributed_transferfrom_count'] == 1
        assert result['unattributed_transfers'][0]['reason'] == 'ambiguous_token_multiple_approvals'

    def test_unambiguous_single_candidate_is_inferred_even_without_token_match(self):
        """Kad postoji SAMO JEDNA grupa za (owner,spender), transfer se povezuje i bez podatka o tokenu"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 50.0, '2026-01-01T02:00:00Z'),
        ])

        result = analyze_token_approvals(frame)

        assert result['groups'][0]['transfer_from_count'] == 1
        assert result['unattributed_transferfrom_count'] == 0

    def test_time_to_first_use_is_computed_against_approval_in_force(self):
        """Vreme do prvog korišćenja se računa u odnosu na odobrenje koje je bilo na snazi"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 1000.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 100.0, '2026-01-01T00:10:00Z', token_address='0xTokenA'),
        ])

        result = analyze_token_approvals(frame)

        group = result['groups'][0]
        assert group['time_to_first_use_seconds'] == pytest.approx(600.0)
        assert group['time_to_first_use_anomaly'] is False


class TestRiskIndicators:
    """Obeleženi risk indikatori"""

    def test_unlimited_never_used_is_flagged(self):
        """Neograničena, nikad iskorišćena dozvola dobija 'unlimited_never_used'"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 1.0, '2026-01-01T00:00:00Z', is_unlimited='true')])

        result = analyze_token_approvals(frame)

        codes = [indicator['code'] for indicator in result['groups'][0]['risk_indicators']]
        assert 'unlimited_never_used' in codes

    def test_unlimited_rapid_drain_requires_the_draining_grant_itself_to_be_unlimited(self):
        """Brzo povlačenje se prijavljuje SAMO kad je baš to (iskorišćeno) odobrenje bilo neograničeno"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 10.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),  # small, NOT unlimited
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 5.0, '2026-01-01T00:05:00Z', token_address='0xTokenA'),
        ])

        result = analyze_token_approvals(frame, rapid_use_seconds=3600)

        codes = [indicator['code'] for indicator in result['groups'][0]['risk_indicators']]
        assert 'unlimited_rapid_drain' not in codes

    def test_unlimited_rapid_drain_fires_when_the_draining_grant_is_unlimited(self):
        """Brzo povlačenje NEOGRANIČENOG odobrenja se prijavljuje"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 1.0, '2026-01-01T00:00:00Z', token_address='0xTokenA', is_unlimited='true'),
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 1.0, '2026-01-01T00:05:00Z', token_address='0xTokenA'),
        ])

        result = analyze_token_approvals(frame, rapid_use_seconds=3600)

        codes = [indicator['code'] for indicator in result['groups'][0]['risk_indicators']]
        assert 'unlimited_rapid_drain' in codes

    def test_revoked_after_use_is_flagged(self):
        """Opoziv posle već izvršenog korišćenja se obeležava"""
        frame = frame_from_rows([
            approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z', token_address='0xTokenA'),
            transfer_from_row('0xOwner', '0xSpender', '0xSpender', 50.0, '2026-01-01T01:00:00Z', token_address='0xTokenA'),
            approve_row('0xOwner', '0xSpender', 0.0, '2026-01-02T00:00:00Z', token_address='0xTokenA'),
        ])

        result = analyze_token_approvals(frame)

        codes = [indicator['code'] for indicator in result['groups'][0]['risk_indicators']]
        assert 'revoked_after_use' in codes

    def test_spender_approved_by_multiple_owners_is_flagged_on_every_such_group(self):
        """Spender odobren od više različitih owner-a dobija 'spender_multi_owner' na svakoj svojoj grupi"""
        frame = frame_from_rows([
            approve_row('0xOwnerA', '0xDrainer', 100.0, '2026-01-01T00:00:00Z'),
            approve_row('0xOwnerB', '0xDrainer', 200.0, '2026-01-01T00:00:00Z'),
        ])

        result = analyze_token_approvals(frame)

        for group in result['groups']:
            assert group['spender_multi_owner'] == {'distinct_owner_count': 2}
            codes = [indicator['code'] for indicator in group['risk_indicators']]
            assert 'spender_multi_owner' in codes


class TestAddressFiltering:
    """Filtriranje po adresi"""

    def test_unknown_address_raises_value_error(self):
        """Adresa koja se nigde ne pojavljuje baca ValueError (ruta to pretvara u 404)"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z')])

        with pytest.raises(ValueError):
            analyze_token_approvals(frame, target_address='0xNoSuchAddress')

    def test_filtering_by_owner_address_scopes_result(self):
        """Filtriranje po owner adresi ograničava rezultat na grupe gde se ona pojavljuje"""
        frame = frame_from_rows([
            approve_row('0xOwnerA', '0xSpenderA', 100.0, '2026-01-01T00:00:00Z'),
            approve_row('0xOwnerB', '0xSpenderB', 200.0, '2026-01-01T00:00:00Z'),
        ])

        result = analyze_token_approvals(frame, target_address='0xOwnerA')

        assert len(result['groups']) == 1
        assert result['groups'][0]['owner'] == '0xOwnerA'


class TestDataCompleteness:
    """Poštena inventura dostupnih podataka"""

    def test_absent_optional_columns_are_reported_not_hidden(self):
        """Odsustvo opcionih kolona (token/spender/owner/is_unlimited) se eksplicitno prijavljuje"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        completeness = result['data_completeness']
        assert completeness['token_address_declared'] is False
        assert completeness['spender_address_declared'] is False
        assert completeness['owner_address_declared'] is False
        assert completeness['is_unlimited_declared'] is False
        assert len(completeness['notes']) > 0

    def test_disclaimer_is_always_present(self):
        """Disclaimer je uvek u odgovoru"""
        frame = frame_from_rows([approve_row('0xOwner', '0xSpender', 100.0, '2026-01-01T00:00:00Z')])

        result = analyze_token_approvals(frame)

        assert 'heuristik' in result['disclaimer'].lower() or 'heuristič' in result['disclaimer'].lower()
