"""Provera registra izveštaja (zaštita od naknadne izmene).

Nacrtani potpis analitičara je izjava, a ne dokaz o nepromenjenosti — potpis je slika i
ostaje netaknut i kad neko izmeni sadržaj. Ono što stvarno omogućava proveru je otisak
sadržaja registrovan pri izvozu i kontrolni broj odštampan u izveštaju.

Ovi testovi proveravaju da otisak stvarno reaguje na izmenu podataka, da je ponovljiv, i
da provera jasno razlikuje tri ishoda: poklapa se, ne poklapa se, ne postoji.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.services import report_registry
from app.services.report_registry import compute_content_hash, register_report, verify_report


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi registar izveštaja."""
    monkeypatch.setattr(report_registry, '_registry_path', lambda: tmp_path / 'registry.json')


CONTENT = {
    'case_id': 'abc123',
    'seeds': ['0xThief'],
    'results': [{'address': '0xMixer', 'taint_percentage': 66.67}],
    'evidence_sha256': ['074b64912438cf0a'],
}


class TestContentHash:
    """Otisak sadržaja

    Otisak mora da se promeni na svaku izmenu podataka, a da ostane isti za iste podatke.
    """

    def test_same_data_gives_the_same_hash(self):
        """Isti podaci daju isti otisak

        Bez ovoga provera ne bi bila ponovljiva — isti izveštaj bi svaki put davao drugi
        otisak i nikad se ne bi poklopio.
        """
        assert compute_content_hash(CONTENT) == compute_content_hash(dict(CONTENT))

    def test_field_order_does_not_change_the_hash(self):
        """Redosled polja ne menja otisak

        Klijent može poslati polja bilo kojim redom; otisak sme da zavisi samo od
        vrednosti, ne od zapisa.
        """
        reordered = {
            'evidence_sha256': CONTENT['evidence_sha256'],
            'results': CONTENT['results'],
            'case_id': CONTENT['case_id'],
            'seeds': CONTENT['seeds'],
        }

        assert compute_content_hash(reordered) == compute_content_hash(CONTENT)

    def test_changing_a_percentage_changes_the_hash(self):
        """Izmena procenta menja otisak

        Suština zaštite: ako neko u izveštaju promeni 66.67% u 6.67%, otisak se više ne
        poklapa sa registrovanim.
        """
        tampered = {**CONTENT, 'results': [{'address': '0xMixer', 'taint_percentage': 6.67}]}

        assert compute_content_hash(tampered) != compute_content_hash(CONTENT)

    def test_changing_a_seed_changes_the_hash(self):
        """Izmena izvora menja otisak

        Drugačiji izvori daju drugačije procente, pa i spisak izvora mora ulaziti u otisak.
        """
        tampered = {**CONTENT, 'seeds': ['0xNekoDrugi']}

        assert compute_content_hash(tampered) != compute_content_hash(CONTENT)


class TestRegistration:
    """Registracija izveštaja

    Pri izvozu se beleži ko je, kada i šta izvezao, i dodeljuje se kontrolni broj koji se
    štampa u dokument.
    """

    def _register(self, **overrides):
        payload = {
            'case_id': 'abc123',
            'case_name': 'test 1',
            'content_hash': compute_content_hash(CONTENT),
            'analyst': 'aco',
            'declaration': 'Potvrđujem tačnost.',
            'summary': {'tainted': 10},
        }
        payload.update(overrides)
        return register_report(**payload)

    def test_verification_code_is_readable_when_copied_by_hand(self):
        """Kontrolni broj je čitljiv pri prepisivanju

        Broj se prepisuje sa odštampanog izveštaja, pa ne sme sadržati znakove koji se lako
        mešaju (0/O, 1/I).
        """
        code = self._register()['verification_code']

        assert code.startswith('LUSI-')
        assert not set('01OI') & set(code.split('-', 2)[2])

    def test_each_report_gets_a_different_code(self):
        """Svaki izveštaj dobija svoj broj

        Isti broj za dva izveštaja bi značio da se jedan zapis tiho prepisuje drugim.
        """
        codes = {self._register()['verification_code'] for _ in range(20)}

        assert len(codes) == 20

    def test_registration_records_who_and_when(self):
        """Registracija beleži ko i kada

        Bez toga se kasnije ne bi moglo utvrditi ko stoji iza dokumenta.
        """
        entry = self._register()

        assert entry['analyst'] == 'aco'
        assert entry['case_id'] == 'abc123'
        assert entry['registered_at']


class TestVerification:
    """Provera izveštaja

    Tri jasno razdvojena ishoda: poklapa se, ne poklapa se, broj ne postoji.
    """

    def _register(self):
        return register_report(
            case_id='abc123', case_name='test 1', content_hash=compute_content_hash(CONTENT),
            analyst='aco', declaration='', summary={},
        )

    def test_unchanged_report_verifies(self):
        """Nepromenjen izveštaj prolazi proveru

        Isti podaci → isti otisak → potvrda da sadržaj odgovara registrovanom.
        """
        code = self._register()['verification_code']

        result = verify_report(verification_code=code, content_hash=compute_content_hash(CONTENT))

        assert result['found'] is True
        assert result['matches'] is True

    def test_tampered_report_fails_verification(self):
        """Izmenjen izveštaj pada na proveri

        Ključni test cele funkcionalnosti: promenjen procenat mora biti otkriven.
        """
        code = self._register()['verification_code']
        tampered = {**CONTENT, 'results': [{'address': '0xMixer', 'taint_percentage': 6.67}]}

        result = verify_report(verification_code=code, content_hash=compute_content_hash(tampered))

        assert result['matches'] is False
        assert 'NE POKLAPA' in result['message']

    def test_unknown_code_is_reported_as_unknown(self):
        """Nepoznat broj se prijavljuje kao nepoznat

        Izveštaj koji nije izvezen iz ove instalacije ne sme da izgleda kao da je pao na
        proveri sadržaja — to su dva različita ishoda.
        """
        result = verify_report(verification_code='LUSI-2026-XXXX-YYYY')

        assert result['found'] is False
        assert result['matches'] is None

    def test_code_is_matched_regardless_of_letter_case(self):
        """Broj se prepoznaje bez obzira na velika slova

        Prepisan malim slovima mora da radi — inače bi provera padala iz razloga koji nema
        veze sa sadržajem.
        """
        code = self._register()['verification_code']

        assert verify_report(verification_code=code.lower())['found'] is True
