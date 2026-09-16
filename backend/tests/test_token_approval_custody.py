"""Integracija Token Approval Analysis sa POSTOJEĆIM lancem dokaza (chain of evidence).

Cilj: dokazati da Token Approval Analysis koristi TAČNO ISTI mehanizam
(`record_custody_access`, `custody_log.jsonl`, `custody_evidence_log.jsonl`) kao Taint/
Graph/Pathfinding/Behavioral/DEX Swap - ne paralelan sistem - i da svaki approve()/
permit() red koji dobije nalaz nosi strukturiran `token_approval_evidence` dokaz, jasno
razdvojen na blockchain_facts / computed_indicators / heuristic_conclusions.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.analytics.token_approval_analysis import EVIDENCE_STORED_NAME_COLUMN, correlate_approval_usage
from app.evidence import custody_evidence_log, custody_log
from app.evidence.tx_identity import transaction_id
from app.features.case_token_approval_analysis.service import (
    combine_frames_with_evidence_tag,
    token_approval_custody_enrichment,
)
from app.shared.custody_recording import TransactionCustodyEntry, record_custody_access

NOW = pd.Timestamp('2026-01-10T00:00:00Z')


@pytest.fixture(autouse=True)
def isolated_custody_log(tmp_path, monkeypatch):
    """Isti obrazac kao test_custody_log.py - testovi ne smeju pisati u pravi lanac."""
    monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')


def write_csv(tmp_path: Path, rows: str, name: str = 'evidence.csv') -> Path:
    header = 'sender_address,recipient_address,amount,timestamp,metadata,event_type,token_address,spender_address,is_unlimited'
    path = tmp_path / name
    path.write_text(f'{header}\n{rows}', encoding='utf-8')
    return path


def _clean(path: Path) -> pd.DataFrame:
    from app.analytics.ingestion import clean_transaction_csv

    return clean_transaction_csv(path)


class TestCombineFramesWithEvidenceTag:
    """combine_frames_with_evidence_tag - spajanje evidencije uz oznaku porekla"""

    def test_each_row_tagged_with_its_own_evidence_file(self, tmp_path):
        """Svaki red je označen fajlom iz kog stvarno potiče"""
        path_a = write_csv(tmp_path, '0xOwner,0xSpender,100,2026-01-01T00:00:00Z,,approve,0xTokenA,0xSpender,\n', name='a.csv')
        path_b = write_csv(tmp_path, '0xOwner,0xSpender,200,2026-01-02T00:00:00Z,,approve,0xTokenA,0xSpender,\n', name='b.csv')
        frames = [({'stored_name': 'a.csv'}, _clean(path_a)), ({'stored_name': 'b.csv'}, _clean(path_b))]

        tagged = combine_frames_with_evidence_tag(frames)

        assert list(tagged[EVIDENCE_STORED_NAME_COLUMN]) == ['a.csv', 'b.csv']

    def test_tag_survives_chronological_resort(self, tmp_path):
        """Oznaka porekla ostaje ispravno uparena posle hronološkog sortiranja unutar analize"""
        # b.csv je hronološki RANIJI iako je dodat DRUGI u listu - analyze_token_approvals
        # interno sortira po vremenu, pa ovo proverava da se `_evidence_stored_name` ne
        # razdvoji od svog reda tokom tog sortiranja.
        path_a = write_csv(tmp_path, '0xOwner,0xSpender,100,2026-01-05T00:00:00Z,,approve,0xTokenA,0xSpender,\n', name='a.csv')
        path_b = write_csv(tmp_path, '0xOwner,0xSpender,200,2026-01-01T00:00:00Z,,approve,0xTokenA,0xSpender,\n', name='b.csv')
        frames = [({'stored_name': 'a.csv'}, _clean(path_a)), ({'stored_name': 'b.csv'}, _clean(path_b))]
        tagged = combine_frames_with_evidence_tag(frames)
        # Expected ids computed from the SAME tagged rows (not hand-typed literals) - the
        # point of this test is that the tag survives the sort inside correlate_approval_
        # usage, not to re-derive transaction_id's own hashing by hand.
        rows_by_amount = {int(row['amount']): row for row in tagged.to_dict('records')}
        expected_a = transaction_id(rows_by_amount[100], 'a.csv')
        expected_b = transaction_id(rows_by_amount[200], 'b.csv')

        result = correlate_approval_usage(tagged, now=NOW)

        by_amount = {int(entry['approval_amount']): entry for entry in result['correlations']}
        assert by_amount[100]['tx_id'] == expected_a
        assert by_amount[200]['tx_id'] == expected_b
        assert expected_a != expected_b


class TestTokenApprovalCustodyEnrichment:
    """token_approval_custody_enrichment - građenje strukturiranog dokaza po tx_id"""

    def test_entry_without_tx_id_is_skipped_not_guessed(self):
        """Nalaz bez tx_id (netagovana evidencija) se preskače, ne nagađa mu se identitet"""
        full_result = {
            'correlations': [{'owner': '0xOwner', 'spender': '0xSpender', 'token_address': 'T', 'tx_id': None}],
            'groups': [],
            'disclaimer': '...',
        }

        enrichment = token_approval_custody_enrichment(full_result)

        assert enrichment == {}

    def test_evidence_item_split_into_three_groups(self, tmp_path):
        """Dokaz je jasno razdvojen na blockchain_facts / computed_indicators / heuristic_conclusions"""
        path = write_csv(
            tmp_path,
            '0xOwner,0xSpender,1000000000000000000,2026-01-01T00:00:00Z,0xapprove1,approve,0xTokenA,0xSpender,true\n'
            '0xOwner,0xDest,500000000000000000,2026-01-01T01:00:00Z,0xtx1,transferFrom,0xTokenA,0xSpender,\n',
        )
        frames = [({'stored_name': 'evidence.csv'}, _clean(path))]
        tagged = combine_frames_with_evidence_tag(frames)
        full_result = correlate_approval_usage(tagged, now=NOW)

        enrichment = token_approval_custody_enrichment(full_result)

        assert len(enrichment) == 1
        item = next(iter(enrichment.values()))['token_approval_evidence']
        assert item['type'] == 'TOKEN_APPROVAL'

        facts = item['blockchain_facts']
        assert facts['owner'] == '0xOwner'
        assert facts['spender'] == '0xSpender'
        assert facts['token_contract'] == '0xTokenA'
        assert facts['allowance_amount'] == 1e18
        assert facts['approval_transaction_hash'] == '0xapprove1'

        indicators = item['computed_indicators']
        assert indicators['status'] == 'APPROVED + USED'
        assert indicators['used'] is True
        assert indicators['transfer_from_count'] == 1
        assert indicators['total_amount_transferred'] == 5e17
        assert indicators['first_transfer_from']['transaction_hash'] == '0xtx1'

        conclusions = item['heuristic_conclusions']
        assert conclusions['allowance_label'] == 'UNLIMITED'
        assert conclusions['unlimited_basis'] == 'declared'
        assert conclusions['risk_level'] in ('LOW', 'MEDIUM', 'HIGH')
        assert isinstance(conclusions['risk_indicators'], list)

        assert item['disclaimer'] == full_result['disclaimer']

    def test_keyed_by_tx_id_matching_the_approval_row(self, tmp_path):
        """Dokaz je uparen sa TAČNO tx_id-jem sopstvenog approve reda"""
        path = write_csv(tmp_path, '0xOwner,0xSpender,100,2026-01-01T00:00:00Z,0xapprove1,approve,0xTokenA,0xSpender,\n')
        frames = [({'stored_name': 'evidence.csv'}, _clean(path))]
        tagged = combine_frames_with_evidence_tag(frames)
        full_result = correlate_approval_usage(tagged, now=NOW)

        enrichment = token_approval_custody_enrichment(full_result)

        assert list(enrichment.keys()) == [transaction_id({'metadata': '0xapprove1'}, 'evidence.csv')]


class TestRecordCustodyAccessWithTokenApprovalEnrichment:
    """record_custody_access(extra_transaction_fields=...) - upis u lanac dokaza"""

    def test_only_the_matching_row_gets_the_token_approval_evidence_field(self, tmp_path):
        """Samo TAČAN red (po tx_id) dobija token_approval_evidence - ostali ostaju kao i pre"""
        path = write_csv(
            tmp_path,
            '0xOwner,0xSpender,1000,2026-01-01T00:00:00Z,0xapprove1,approve,0xTokenA,0xSpender,\n'
            '0xPeerA,0xPeerB,50,2026-01-01T02:00:00Z,,,,,\n',  # obična, nepovezana transakcija
        )
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera Token Approval nalaza', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = correlate_approval_usage(tagged, now=NOW)
        extra = token_approval_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        entries = {entry['tx_id']: entry for entry in custody_log.load_custody_entries(case_id='c1')}
        assert len(entries) == 2

        approval_tx_id = transaction_id({'metadata': '0xapprove1'}, 'evidence.csv')
        assert 'token_approval_evidence' in entries[approval_tx_id]
        assert entries[approval_tx_id]['token_approval_evidence']['blockchain_facts']['owner'] == '0xOwner'

        other_entries = [entry for tx_id, entry in entries.items() if tx_id != approval_tx_id]
        assert len(other_entries) == 1
        assert 'token_approval_evidence' not in other_entries[0]

    def test_existing_analyses_are_unaffected_when_extra_fields_omitted(self, tmp_path):
        """Bez extra_transaction_fields, ponašanje je identično kao pre (Taint/DEX Swap/...)"""
        path = write_csv(tmp_path, '0xThief,0xMixer,1000,2026-03-01T00:00:00Z,,,,,\n')
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        entry = custody_log.load_custody_entries(case_id='c1')[0]
        assert 'token_approval_evidence' not in entry

    def test_custody_chain_for_transaction_surfaces_the_evidence_item(self, tmp_path):
        """custody_chain_for_transaction (postojeći GET .../custody/transactions/{tx_id}) vraća dokaz"""
        path = write_csv(tmp_path, '0xOwner,0xSpender,1000,2026-01-01T00:00:00Z,0xapprove1,approve,0xTokenA,0xSpender,\n')
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = correlate_approval_usage(tagged, now=NOW)
        extra = token_approval_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        tx_id = transaction_id({'metadata': '0xapprove1'}, 'evidence.csv')
        chain = custody_log.custody_chain_for_transaction('c1', tx_id)

        assert chain is not None
        assert chain['token_approval_evidence']['type'] == 'TOKEN_APPROVAL'
        assert chain['token_approval_evidence']['blockchain_facts']['spender'] == '0xSpender'

    def test_list_case_transactions_flags_the_finding(self, tmp_path):
        """list_case_transactions (browsing lista) označava koja transakcija ima nalaz"""
        path = write_csv(
            tmp_path,
            '0xOwner,0xSpender,1000,2026-01-01T00:00:00Z,0xapprove1,approve,0xTokenA,0xSpender,\n'
            '0xPeerA,0xPeerB,50,2026-01-01T02:00:00Z,,,,,\n',
        )
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = correlate_approval_usage(tagged, now=NOW)
        extra = token_approval_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        rows = {row['tx_id']: row for row in custody_log.list_case_transactions('c1')}
        approval_tx_id = transaction_id({'metadata': '0xapprove1'}, 'evidence.csv')
        other_tx_id = next(tx_id for tx_id in rows if tx_id != approval_tx_id)

        assert rows[approval_tx_id]['has_token_approval_evidence'] is True
        assert rows[other_tx_id]['has_token_approval_evidence'] is False
