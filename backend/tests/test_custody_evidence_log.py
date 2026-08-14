"""Provera lanca dokaza po dokaznom fajlu (obrazac primenjen na CEO dokazni fajl, ne na
pojedinačnu transakciju - vidi test_custody_log.py za finiju granulaciju).

Svaki dokazni fajl (CSV/on-chain izvoz) je "izlaganje" u smislu ovog obrasca, isto kao
fizički hard disk na referentnoj slici - hronologija pristupa se vodi za fajl kao celinu,
bez obzira koliko transakcija sadrži.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.evidence import custody_evidence_log


@pytest.fixture(autouse=True)
def isolated_evidence_custody_log(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi lanac dokaza."""
    monkeypatch.setattr(custody_evidence_log, '_evidence_custody_log_path', lambda: tmp_path / 'custody_evidence_log.jsonl')


def _entry(**overrides) -> dict:
    base = {
        'timestamp': '2026-06-08T09:00:00+00:00',
        'case_id': 'c1',
        'case_name': 'Slučaj 1',
        'evidence_stored_name': 'evidence.csv',
        'evidence_file_name': 'original.csv',
        'evidence_sha256': 'abc123',
        'evidence_currency': 'ETH',
        'evidence_row_count': 8,
        'identifikator_predmeta': 'UDF',
        'identifikator_dokaznog_materijala': 'original.csv',
        'proizvodjac': 'N/A',
        'model': 'N/A',
        'serijski_broj': 'N/A',
        'ime_prezime': 'Prvi Analitičar',
        'opis_radnje': 'Prvobitni pregled',
        'user': 'aco',
        'signature_image': 'data:image/png;base64,AAA',
    }
    base.update(overrides)
    return base


class TestEvidenceCustodyChain:
    """Lanac dokaza po dokaznom fajlu

    Provera da se serija pristupa jednom dokaznom fajlu ispravno grupiše i hronološki
    numeriše, i da zaglavlje prati NAJNOVIJI unos - ista logika kao kod pojedinačne
    transakcije, primenjena na fajl kao celinu.
    """

    def test_redni_broj_follows_chronological_order(self):
        """Brojevi redova prate hronološki redosled pristupa"""
        custody_evidence_log.append_evidence_custody_batch([
            _entry(timestamp='2026-06-08T09:00:00+00:00', ime_prezime='Prvi Analitičar', serijski_broj='N/A'),
            _entry(timestamp='2026-06-09T09:00:00+00:00', ime_prezime='Drugi Analitičar', serijski_broj='BG-HDD-01'),
        ])

        chain = custody_evidence_log.custody_chain_for_evidence('c1', 'evidence.csv')

        assert [entry['redni_broj'] for entry in chain['entries']] == [1, 2]
        assert chain['entries'][0]['ime_prezime'] == 'Prvi Analitičar'
        assert chain['entries'][1]['ime_prezime'] == 'Drugi Analitičar'
        # Zaglavlje odražava NAJNOVIJI pristup.
        assert chain['serijski_broj'] == 'BG-HDD-01'
        assert chain['evidence_row_count'] == 8

    def test_unknown_evidence_file_returns_none(self):
        """Nepostojeći dokazni fajl nema lanac dokaza"""
        assert custody_evidence_log.custody_chain_for_evidence('c1', 'ne-postoji.csv') is None

    def test_different_evidence_files_get_separate_chains(self):
        """Dva različita dokazna fajla u istom slučaju imaju odvojene lance"""
        custody_evidence_log.append_evidence_custody_batch([
            _entry(evidence_stored_name='a.csv', evidence_file_name='a.csv'),
            _entry(evidence_stored_name='b.csv', evidence_file_name='b.csv'),
        ])

        chain_a = custody_evidence_log.custody_chain_for_evidence('c1', 'a.csv')
        chain_b = custody_evidence_log.custody_chain_for_evidence('c1', 'b.csv')

        assert len(chain_a['entries']) == 1
        assert len(chain_b['entries']) == 1


class TestListCaseEvidence:
    """Spisak dokaznih fajlova sa lancem dokaza

    Broji pristupe po fajlu i sortira po poslednjem pristupu - ista logika kao spisak
    transakcija, samo grupisano po fajlu.
    """

    def test_counts_accesses_and_sorts_by_last_access(self):
        """Spisak broji pristupe i sortira po poslednjem pristupu"""
        custody_evidence_log.append_evidence_custody_batch([
            _entry(timestamp='2026-06-01T00:00:00+00:00', evidence_stored_name='old.csv'),
            _entry(timestamp='2026-06-05T00:00:00+00:00', evidence_stored_name='new.csv'),
            _entry(timestamp='2026-06-06T00:00:00+00:00', evidence_stored_name='new.csv'),
        ])

        listed = custody_evidence_log.list_case_evidence('c1')

        assert [item['evidence_stored_name'] for item in listed] == ['new.csv', 'old.csv']
        assert next(item for item in listed if item['evidence_stored_name'] == 'new.csv')['access_count'] == 2


class TestEvidenceCustodyPdf:
    """Generisanje PDF-a po dokaznom fajlu

    Osnovna provera da izvoz ne puca ni sa potpisom ni bez njega, i da je izlaz zaista
    PDF dokument.
    """

    def test_builds_valid_pdf_bytes(self):
        """Izvezeni dokument je validan PDF"""
        from app.exports.custody_evidence_report import build_custody_evidence_pdf

        header = {
            'evidence_file_name': 'onchain_mainnet_....csv', 'evidence_stored_name': 'onchain.csv',
            'evidence_sha256': 'abc123', 'evidence_currency': 'ETH', 'evidence_row_count': 620,
            'identifikator_predmeta': 'UDF', 'identifikator_dokaznog_materijala': 'onchain_mainnet_....csv',
            'proizvodjac': 'N/A', 'model': 'N/A', 'serijski_broj': 'N/A',
        }
        entries = [{
            'redni_broj': 1, 'timestamp': '2026-06-08T09:00:00+00:00', 'ime_prezime': 'Aleksandar Sekulić',
            'opis_radnje': 'Analiza celokupne evidencije', 'signature_image': None,
        }]

        pdf_bytes = build_custody_evidence_pdf(header, entries)

        assert pdf_bytes.startswith(b'%PDF')

    def test_builds_pdf_with_no_entries(self):
        """Izvoz ne puca kad dokazni fajl nema nijedan zabeleženi pristup"""
        from app.exports.custody_evidence_report import build_custody_evidence_pdf

        pdf_bytes = build_custody_evidence_pdf({'evidence_file_name': 'x.csv'}, [])

        assert pdf_bytes.startswith(b'%PDF')
