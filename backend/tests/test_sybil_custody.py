"""Integracija Sybil & Bot Network Analysis sa POSTOJEĆIM lancem dokaza (chain of evidence).

Cilj: dokazati da Sybil & Bot Network Analysis koristi TAČNO ISTI mehanizam
(`record_custody_access`, `custody_log.jsonl`) kao Taint/Graph/Pathfinding/Behavioral/DEX
Swap/Token Approval - ne paralelan sistem - i da svaka transakcija koja upadne u OZNAČEN
klaster dobije strukturiran `sybil_evidence` dokaz, jasno razdvojen na blockchain_facts
(izvorni blockchain podaci: adrese, tx hash, blok, timestamp, kontrakt, funkcija) i
heuristic_conclusions (broj adresa, risk score, razlozi) - nikad pomešano.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.analytics.sybil_analysis import EVIDENCE_STORED_NAME_COLUMN, detect_sybil_clusters
from app.evidence import custody_evidence_log, custody_log
from app.evidence.tx_identity import transaction_id
from app.features.case_sybil_analysis.service import combine_frames_with_evidence_tag, sybil_custody_enrichment
from app.shared.custody_recording import TransactionCustodyEntry, record_custody_access


@pytest.fixture(autouse=True)
def isolated_custody_log(tmp_path, monkeypatch):
    """Isti obrazac kao test_token_approval_custody.py - testovi ne smeju pisati u pravi lanac."""
    monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')


def write_csv(tmp_path: Path, rows: str, name: str = 'evidence.csv', header: str | None = None) -> Path:
    header = header or 'sender_address,recipient_address,amount,timestamp,metadata,function_name'
    path = tmp_path / name
    path.write_text(f'{header}\n{rows}', encoding='utf-8')
    return path


def _clean(path: Path) -> pd.DataFrame:
    from app.analytics.ingestion import clean_transaction_csv

    return clean_transaction_csv(path)


SYNCHRONIZED_ROWS = (
    '0xAddrA,0xClaimContract,1,2026-01-01T00:00:00Z,0xa1,claim\n'
    '0xAddrB,0xClaimContract,1,2026-01-01T00:00:20Z,0xa2,claim\n'
    '0xAddrC,0xClaimContract,1,2026-01-01T00:00:40Z,0xa3,claim\n'
)


class TestCombineFramesWithEvidenceTag:
    """combine_frames_with_evidence_tag - spajanje evidencije uz oznaku porekla"""

    def test_each_row_tagged_with_its_own_evidence_file(self, tmp_path):
        """Svaki red je označen fajlom iz kog stvarno potiče"""
        path_a = write_csv(tmp_path, '0xAddrA,0xClaimContract,1,2026-01-01T00:00:00Z,0xa1,claim\n', name='a.csv')
        path_b = write_csv(tmp_path, '0xAddrB,0xClaimContract,1,2026-01-01T00:00:20Z,0xa2,claim\n', name='b.csv')
        frames = [({'stored_name': 'a.csv'}, _clean(path_a)), ({'stored_name': 'b.csv'}, _clean(path_b))]

        tagged = combine_frames_with_evidence_tag(frames)

        assert list(tagged[EVIDENCE_STORED_NAME_COLUMN]) == ['a.csv', 'b.csv']

    def test_tag_survives_burst_grouping(self, tmp_path):
        """Oznaka porekla ostaje ispravno uparena posle grupisanja/sortiranja unutar analize"""
        path_a = write_csv(tmp_path, SYNCHRONIZED_ROWS, name='a.csv')
        frames = [({'stored_name': 'a.csv'}, _clean(path_a))]
        tagged = combine_frames_with_evidence_tag(frames)

        result = detect_sybil_clusters(tagged)

        assert result['total_clusters'] == 1
        tx_ids = [tx['tx_id'] for tx in result['clusters'][0]['transactions']]
        assert all(tx_id is not None for tx_id in tx_ids)
        assert len(set(tx_ids)) == 3  # svaka transakcija dobija sopstven, različit tx_id


class TestSybilCustodyEnrichment:
    """sybil_custody_enrichment - građenje strukturiranog dokaza po tx_id"""

    def test_entry_without_tx_id_is_skipped_not_guessed(self):
        """Nalaz bez tx_id (netagovana evidencija) se preskače, ne nagađa mu se identitet"""
        full_result = {
            'clusters': [{'cluster_id': 'SYBIL-1', 'transactions': [{'tx_id': None, 'sender_address': '0xA'}]}],
            'disclaimer': '...',
        }

        enrichment = sybil_custody_enrichment(full_result)

        assert enrichment == {}

    def test_evidence_item_split_into_exactly_two_groups(self, tmp_path):
        """Dokaz je jasno razdvojen na blockchain_facts / heuristic_conclusions, ništa treće"""
        path = write_csv(tmp_path, SYNCHRONIZED_ROWS)
        frames = [({'stored_name': 'evidence.csv'}, _clean(path))]
        tagged = combine_frames_with_evidence_tag(frames)
        full_result = detect_sybil_clusters(tagged)

        enrichment = sybil_custody_enrichment(full_result)

        assert len(enrichment) == 3  # jedan po transakciji u klasteru
        item = next(iter(enrichment.values()))['sybil_evidence']
        assert item['type'] == 'SYBIL_CLUSTER'
        assert set(item.keys()) == {'type', 'blockchain_facts', 'heuristic_conclusions', 'disclaimer'}

        facts = item['blockchain_facts']
        assert set(facts.keys()) == {
            'sender_address', 'contract_address', 'amount', 'timestamp',
            'transaction_hash', 'block_number', 'function_name',
        }
        assert facts['contract_address'] == '0xClaimContract'
        assert facts['function_name'] == 'claim'

        conclusions = item['heuristic_conclusions']
        assert conclusions['cluster_id'] == 'SYBIL-1'
        assert conclusions['address_count'] == 3
        assert conclusions['risk_score'] > 0
        assert isinstance(conclusions['reasons'], list)

        assert item['disclaimer'] == full_result['disclaimer']
        # Blockchain facts nikad ne sadrže heuristička polja i obrnuto - razdvajanje mora
        # biti stvarno, ne samo imenom.
        assert 'risk_score' not in facts
        assert 'transaction_hash' not in conclusions

    def test_keyed_by_tx_id_matching_the_transaction_row(self, tmp_path):
        """Dokaz je uparen sa TAČNIM tx_id-jem sopstvenog reda"""
        path = write_csv(tmp_path, SYNCHRONIZED_ROWS)
        frames = [({'stored_name': 'evidence.csv'}, _clean(path))]
        tagged = combine_frames_with_evidence_tag(frames)
        full_result = detect_sybil_clusters(tagged)

        enrichment = sybil_custody_enrichment(full_result)

        expected_ids = {
            transaction_id({'metadata': hash_}, 'evidence.csv') for hash_ in ('0xa1', '0xa2', '0xa3')
        }
        assert set(enrichment.keys()) == expected_ids

    def test_unflagged_transactions_get_no_evidence_item(self, tmp_path):
        """Transakcije koje NISU deo označenog klastera (npr. samo 2 adrese) ne dobijaju dokaz"""
        path = write_csv(
            tmp_path,
            '0xAddrA,0xOtherContract,1,2026-02-01T00:00:00Z,0xb1,ping\n'
            '0xAddrB,0xOtherContract,1,2026-02-01T00:00:20Z,0xb2,ping\n',
        )
        frames = [({'stored_name': 'evidence.csv'}, _clean(path))]
        tagged = combine_frames_with_evidence_tag(frames)
        full_result = detect_sybil_clusters(tagged)  # samo 2 adrese, ispod praga od 3

        enrichment = sybil_custody_enrichment(full_result)

        assert enrichment == {}


class TestRecordCustodyAccessWithSybilEnrichment:
    """record_custody_access(extra_transaction_fields=...) - upis u lanac dokaza"""

    def test_only_flagged_cluster_rows_get_the_sybil_evidence(self, tmp_path):
        """Samo transakcije iz OZNAČENOG klastera dobijaju sybil_evidence - ostale ostaju kao i pre"""
        path = write_csv(
            tmp_path,
            SYNCHRONIZED_ROWS + '0xPeerA,0xPeerB,50,2026-01-02T00:00:00Z,,\n',  # obična, nepovezana transakcija
        )
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera Sybil klastera', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = detect_sybil_clusters(tagged)
        extra = sybil_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        entries = {entry['tx_id']: entry for entry in custody_log.load_custody_entries(case_id='c1')}
        assert len(entries) == 4

        flagged_ids = {transaction_id({'metadata': hash_}, 'evidence.csv') for hash_ in ('0xa1', '0xa2', '0xa3')}
        for tx_id in flagged_ids:
            assert 'sybil_evidence' in entries[tx_id]
            assert entries[tx_id]['sybil_evidence']['blockchain_facts']['sender_address'] in ('0xAddrA', '0xAddrB', '0xAddrC')

        other_entries = [entry for tx_id, entry in entries.items() if tx_id not in flagged_ids]
        assert len(other_entries) == 1
        assert 'sybil_evidence' not in other_entries[0]

    def test_existing_analyses_are_unaffected_when_extra_fields_omitted(self, tmp_path):
        """Bez extra_transaction_fields, ponašanje je identično kao pre (Taint/DEX Swap/...)"""
        path = write_csv(tmp_path, '0xThief,0xMixer,1000,2026-03-01T00:00:00Z,,\n')
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        entry = custody_log.load_custody_entries(case_id='c1')[0]
        assert 'sybil_evidence' not in entry

    def test_custody_chain_for_transaction_surfaces_the_evidence_item(self, tmp_path):
        """custody_chain_for_transaction (postojeći GET .../custody/transactions/{tx_id}) vraća dokaz"""
        path = write_csv(tmp_path, SYNCHRONIZED_ROWS)
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = detect_sybil_clusters(tagged)
        extra = sybil_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        tx_id = transaction_id({'metadata': '0xa1'}, 'evidence.csv')
        chain = custody_log.custody_chain_for_transaction('c1', tx_id)

        assert chain is not None
        assert chain['sybil_evidence']['type'] == 'SYBIL_CLUSTER'
        assert chain['sybil_evidence']['blockchain_facts']['sender_address'] == '0xAddrA'
        assert chain['sybil_evidence']['heuristic_conclusions']['cluster_id'] == 'SYBIL-1'

    def test_list_case_transactions_flags_the_finding(self, tmp_path):
        """list_case_transactions (browsing lista) označava koja transakcija ima Sybil nalaz"""
        path = write_csv(
            tmp_path,
            SYNCHRONIZED_ROWS + '0xPeerA,0xPeerB,50,2026-01-02T00:00:00Z,,\n',
        )
        frame = _clean(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        tagged = combine_frames_with_evidence_tag([(evidence_entry, frame)])
        full_result = detect_sybil_clusters(tagged)
        extra = sybil_custody_enrichment(full_result)

        record_custody_access(
            case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco',
            extra_transaction_fields=extra,
        )

        rows = {row['tx_id']: row for row in custody_log.list_case_transactions('c1')}
        flagged_ids = {transaction_id({'metadata': hash_}, 'evidence.csv') for hash_ in ('0xa1', '0xa2', '0xa3')}
        other_id = next(tx_id for tx_id in rows if tx_id not in flagged_ids)

        for tx_id in flagged_ids:
            assert rows[tx_id]['has_sybil_evidence'] is True
        assert rows[other_id]['has_sybil_evidence'] is False
