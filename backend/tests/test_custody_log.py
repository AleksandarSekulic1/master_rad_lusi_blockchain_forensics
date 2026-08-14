"""Provera lanca dokaza po transakciji (Obrazac evidencije rukovanja dokaznim materijalom).

Svako pokretanje taint analize se tretira kao ponovni pristup SVAKOJ transakciji koju
obrađuje - jedan red se dodaje u lanac dokaza baš te transakcije. Ovi testovi proveravaju
da transakcija zadrži isti identitet kroz više pokretanja (inače bi "red 2" i "red 3" tiho
opisivali dva različita transfera), da se serija pristupa ispravno broji i sortira, i da
podrazumevane vrednosti polja (N/A, naziv slučaja, naziv dokaza) rade kako je specifirano.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.analytics.ingestion import clean_transaction_csv
from app.api.routes.cases import TransactionCustodyEntry, _record_custody_access
from app.evidence import custody_evidence_log, custody_log
from app.evidence.tx_identity import transaction_id


HEADER = 'sender_address,recipient_address,amount,timestamp'


def write_csv(tmp_path: Path, rows: str, name: str = 'evidence.csv', header: str = HEADER) -> Path:
    path = tmp_path / name
    path.write_text(f'{header}\n{rows}', encoding='utf-8')
    return path


@pytest.fixture(autouse=True)
def isolated_custody_log(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi lanac dokaza (ni po transakciji ni po dokaznom
    fajlu - _record_custody_access upisuje u oba odjednom)."""
    monkeypatch.setattr(custody_log, '_custody_log_path', lambda: tmp_path / 'custody_log.jsonl')
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')


class TestTransactionId:
    """Identitet transakcije

    Isti transfer mora dobiti isti identifikator svaki put kad se izračuna (inače bi se
    istorija pristupa cepala na dve odvojene tabele za jednu te istu transakciju), a pravi
    tx_hash mora imati prednost nad izvedenim identifikatorom kad postoji.
    """

    ROW = {
        'sender_address': '0xThief',
        'recipient_address': '0xMixer',
        'amount': 1000.0,
        'timestamp': '2026-03-01T00:00:00Z',
        'metadata': None,
    }

    def test_same_row_gives_same_id_every_time(self):
        """Ista transakcija uvek dobija isti identifikator"""
        first = transaction_id(self.ROW, 'evidence.csv')
        second = transaction_id(dict(self.ROW), 'evidence.csv')

        assert first == second

    def test_real_tx_hash_wins_over_derived_id(self):
        """Pravi tx_hash ima prednost nad izvedenim identifikatorom"""
        row = {**self.ROW, 'metadata': '0xabc123'}

        assert transaction_id(row, 'evidence.csv') == '0xabc123'

    def test_missing_hash_is_salted_by_evidence_file(self):
        """Isti transfer u dva dokazna fajla ne sme dobiti isti identifikator"""
        id_a = transaction_id(self.ROW, 'evidence_a.csv')
        id_b = transaction_id(self.ROW, 'evidence_b.csv')

        assert id_a != id_b


class TestCustodyLogStorage:
    """Upis i čitanje lanca dokaza

    Provera da se serija pristupa jednoj transakciji ispravno grupiše, hronološki
    numeriše (Бр. 1, 2, 3...), i da se zaglavlje (identifikator predmeta i sl.) uzima od
    NAJNOVIJEG pristupa - ispravka jednog polja mora da se vidi na odštampanom obrascu.
    """

    def test_redni_broj_follows_chronological_order(self):
        """Brojevi redova prate hronološki redosled pristupa"""
        custody_log.append_custody_batch([
            {
                'timestamp': '2026-06-08T09:00:00+00:00', 'case_id': 'c1', 'case_name': 'Slučaj 1',
                'tx_id': 'tx-1', 'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 10.0,
                'currency': 'ETH', 'identifikator_predmeta': 'UDF', 'identifikator_dokaznog_materijala': 'onchain.csv',
                'proizvodjac': 'N/A', 'model': 'N/A', 'serijski_broj': 'N/A',
                'ime_prezime': 'Prvi Analitičar', 'opis_radnje': 'Prvobitni pregled', 'user': 'aco',
                'signature_image': 'data:image/png;base64,AAA',
            },
            {
                'timestamp': '2026-06-09T09:00:00+00:00', 'case_id': 'c1', 'case_name': 'Slučaj 1',
                'tx_id': 'tx-1', 'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 10.0,
                'currency': 'ETH', 'identifikator_predmeta': 'UDF', 'identifikator_dokaznog_materijala': 'onchain.csv',
                'proizvodjac': 'Seagate', 'model': 'ST1000DM010', 'serijski_broj': 'BG-HDD-01',
                'ime_prezime': 'Drugi Analitičar', 'opis_radnje': 'Provera nove naznake', 'user': 'mvr',
                'signature_image': 'data:image/png;base64,BBB',
            },
        ])

        chain = custody_log.custody_chain_for_transaction('c1', 'tx-1')

        assert [entry['redni_broj'] for entry in chain['entries']] == [1, 2]
        assert chain['entries'][0]['ime_prezime'] == 'Prvi Analitičar'
        assert chain['entries'][1]['ime_prezime'] == 'Drugi Analitičar'
        # Zaglavlje odražava NAJNOVIJI pristup - ispravka serijskog broja mora da se vidi.
        assert chain['serijski_broj'] == 'BG-HDD-01'

    def test_unknown_transaction_returns_none(self):
        """Nepostojeća transakcija nema lanac dokaza"""
        assert custody_log.custody_chain_for_transaction('c1', 'ne-postoji') is None

    def test_list_case_transactions_counts_and_sorts_by_last_access(self):
        """Spisak transakcija broji pristupe i sortira po poslednjem pristupu"""
        custody_log.append_custody_batch([
            {'timestamp': '2026-06-01T00:00:00+00:00', 'case_id': 'c1', 'tx_id': 'tx-old', 'sender_address': '0xA', 'recipient_address': '0xB', 'amount': 1.0, 'currency': 'ETH'},
            {'timestamp': '2026-06-05T00:00:00+00:00', 'case_id': 'c1', 'tx_id': 'tx-new', 'sender_address': '0xC', 'recipient_address': '0xD', 'amount': 2.0, 'currency': 'ETH'},
            {'timestamp': '2026-06-06T00:00:00+00:00', 'case_id': 'c1', 'tx_id': 'tx-new', 'sender_address': '0xC', 'recipient_address': '0xD', 'amount': 2.0, 'currency': 'ETH'},
        ])

        transactions = custody_log.list_case_transactions('c1')

        assert [item['tx_id'] for item in transactions] == ['tx-new', 'tx-old']
        assert next(item for item in transactions if item['tx_id'] == 'tx-new')['access_count'] == 2

    def test_field_suggestions_dedupe_and_exclude_na(self):
        """Predlozi za polja isključuju N/A i duplikate, najnoviji prvi"""
        custody_log.append_custody_batch([
            {'timestamp': '2026-06-01T00:00:00+00:00', 'case_id': 'c1', 'tx_id': 'tx-1',
             'identifikator_predmeta': 'UDF', 'proizvodjac': 'N/A', 'model': 'N/A', 'serijski_broj': 'N/A'},
            {'timestamp': '2026-06-02T00:00:00+00:00', 'case_id': 'c1', 'tx_id': 'tx-2',
             'identifikator_predmeta': 'UDF', 'proizvodjac': 'Seagate', 'model': 'ST1000DM010', 'serijski_broj': 'BG-HDD-01'},
        ])

        suggestions = custody_log.field_suggestions('c1')

        assert suggestions['identifikator_predmeta'] == ['UDF']
        assert suggestions['proizvodjac'] == ['Seagate']
        assert 'N/A' not in suggestions['serijski_broj']


class TestRecordCustodyAccessFromAnalyticsRun:
    """Upis pri pokretanju analize

    Ovo je tačka gde se `POST /cases/{id}/analytics/run` zaista kači na lanac dokaza:
    svaki red evidencije koji uđe u obračun mora dobiti tačno jedan novi red pristupa, sa
    podrazumevanim vrednostima onako kako je specifirano (N/A za uređaj, naziv slučaja i
    dokaznog fajla kad analitičar ne unese svoj identifikator).
    """

    def test_writes_one_custody_row_per_transaction_row(self, tmp_path):
        """Svaki red evidencije dobija tačno jedan red u lancu dokaza"""
        path = write_csv(tmp_path, (
            '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n'
            '0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z\n'
        ))
        frame = clean_transaction_csv(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera povezanosti', signature_image='data:image/png;base64,AAA')

        _record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        entries = custody_log.load_custody_entries(case_id='c1')
        assert len(entries) == 2
        assert {entry['tx_id'] for entry in entries} == {
            transaction_id(row, 'evidence.csv') for row in frame.to_dict('records')
        }

    def test_defaults_device_fields_to_na_and_identifiers_to_case_and_file(self, tmp_path):
        """Podrazumevane vrednosti: N/A za uređaj, naziv slučaja/fajla za identifikatore"""
        path = write_csv(tmp_path, '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n')
        frame = clean_transaction_csv(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        _record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        entry = custody_log.load_custody_entries(case_id='c1')[0]
        assert entry['proizvodjac'] == 'N/A'
        assert entry['model'] == 'N/A'
        assert entry['serijski_broj'] == 'N/A'
        assert entry['identifikator_predmeta'] == 'Slučaj 1'
        assert entry['identifikator_dokaznog_materijala'] == 'original.csv'

    def test_explicit_identifiers_override_defaults(self, tmp_path):
        """Ručno uneti identifikatori imaju prednost nad podrazumevanim"""
        path = write_csv(tmp_path, '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n')
        frame = clean_transaction_csv(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(
            ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA',
            identifikator_predmeta='UDF', proizvodjac='Seagate', serijski_broj='BG-HDD-01',
        )

        _record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        entry = custody_log.load_custody_entries(case_id='c1')[0]
        assert entry['identifikator_predmeta'] == 'UDF'
        assert entry['proizvodjac'] == 'Seagate'
        assert entry['serijski_broj'] == 'BG-HDD-01'

    def test_also_writes_one_evidence_level_row_per_file(self, tmp_path):
        """Isto pokretanje upisuje i JEDAN red po dokaznom fajlu (ne po transakciji)"""
        path = write_csv(tmp_path, (
            '0xThief,0xMixer,1000,2026-03-01T00:00:00Z\n'
            '0xCleanUser,0xMixer,500,2026-03-01T00:05:00Z\n'
        ))
        frame = clean_transaction_csv(path)
        evidence_entry = {'stored_name': 'evidence.csv', 'file_name': 'original.csv', 'sha256': 'abc123', 'currency': 'ETH'}
        case = {'id': 'c1', 'name': 'Slučaj 1'}
        custody = TransactionCustodyEntry(ime_prezime='Aleksandar Sekulić', opis_radnje='Provera', signature_image='data:image/png;base64,AAA')

        _record_custody_access(case=case, per_evidence_frames=[(evidence_entry, frame)], custody=custody, user='aco')

        # Dva reda po transakciji (jedan po redu evidencije)...
        assert len(custody_log.load_custody_entries(case_id='c1')) == 2
        # ...ali samo JEDAN red po dokaznom fajlu, koji nosi broj transakcija koje sadrži.
        evidence_entries = custody_evidence_log.load_evidence_custody_entries(case_id='c1')
        assert len(evidence_entries) == 1
        assert evidence_entries[0]['evidence_row_count'] == 2
        assert evidence_entries[0]['evidence_sha256'] == 'abc123'
        assert evidence_entries[0]['evidence_currency'] == 'ETH'


class TestCustodyPdf:
    """Generisanje PDF-a

    Osnovna provera da izvoz ne puca ni sa potpisom ni bez njega, i da je izlaz zaista
    PDF dokument.
    """

    def test_builds_valid_pdf_bytes(self):
        """Izvezeni dokument je validan PDF"""
        from app.exports.custody_report import build_custody_pdf

        header = {
            'sender_address': '0xThief', 'recipient_address': '0xMixer', 'amount': 1000.0, 'currency': 'ETH',
            'identifikator_predmeta': 'UDF', 'identifikator_dokaznog_materijala': 'onchain.csv',
            'proizvodjac': 'N/A', 'model': 'N/A', 'serijski_broj': 'N/A',
        }
        entries = [{
            'redni_broj': 1, 'timestamp': '2026-06-08T09:00:00+00:00', 'ime_prezime': 'Aleksandar Sekulić',
            'opis_radnje': 'Provera povezanosti sa žrtvom X', 'signature_image': None,
        }]

        pdf_bytes = build_custody_pdf(header, entries)

        assert pdf_bytes.startswith(b'%PDF')

    def test_builds_pdf_with_no_entries(self):
        """Izvoz ne puca kad transakcija nema nijedan zabeleženi pristup"""
        from app.exports.custody_report import build_custody_pdf

        pdf_bytes = build_custody_pdf({'sender_address': '0xA', 'recipient_address': '0xB'}, [])

        assert pdf_bytes.startswith(b'%PDF')
