"""Provera istražiteljskih beleški nad adresama/čvorovima (Case Management / Investigator Layer).

Beleška je zapažanje istražitelja o jednoj adresi (npr. „sumnja se da je ovo cold wallet").
Ovi testovi pokrivaju: kreiranje (dodela ID-ja, autora, vremenskih pečata), da se adresa
čuva DOSLOVNO (trimuje se razmak, veličina slova se ne dira - inače se beleška ne bi
poklopila sa čvorom u grafu), filtriranje po adresi, izmenu koja pomera samo „updated_at",
brisanje, i da se beleške brišu zajedno sa istragom. Beleške se ne mešaju sa blockchain
podacima i ne diraju nijedan postojeći algoritam.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import time

import pytest

from app.investigations import notes_repository, notes_service, repository, service
from app.investigations.notes_models import InvestigatorNoteCreate, InvestigatorNoteUpdate
from app.investigations.notes_service import InvestigatorNoteNotFoundError
from app.investigations.service import InvestigationCaseNotFoundError
from app.investigations.models import InvestigationCaseCreate


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirecting the investigations root also redirects the notes files, since they live
    inside each investigation's own directory."""
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')


@pytest.fixture
def investigation_id() -> str:
    return service.create_investigation(InvestigationCaseCreate(name='Op. Nightfall')).id


def _add(investigation_id: str, address: str, text: str, author: str = 'inv1'):
    return notes_service.create_note(
        investigation_id,
        InvestigatorNoteCreate(address=address, text=text),
        author=author,
    )


class TestCreate:
    """Kreiranje beleške"""

    def test_note_has_id_author_case_id_and_equal_timestamps(self, investigation_id):
        """Beleška dobija ID, autora, ID slučaja i jednake vremenske pečate

        Pri kreiranju created_at i updated_at moraju biti isti (još nije bilo izmene).
        """
        note = _add(investigation_id, '0xABC', 'Sumnja se da je ovo cold wallet.', author='marko')

        assert note.id
        assert note.investigation_id == investigation_id
        assert note.address == '0xABC'
        assert note.text == 'Sumnja se da je ovo cold wallet.'
        assert note.author == 'marko'
        assert note.created_at == note.updated_at

    def test_address_is_trimmed_but_case_is_preserved(self, investigation_id):
        """Adresa se trimuje ali se veličina slova ne dira

        Čvor u grafu se poredi bez normalizacije (tačno slovo za slovo), pa beleška mora
        da sačuva isti zapis da bi se poklopila.
        """
        note = _add(investigation_id, '  0xAbCdEf  ', 'tekst')

        assert note.address == '0xAbCdEf'

    def test_blank_text_is_rejected(self):
        """Prazan tekst beleške se odbija"""
        with pytest.raises(ValueError):
            InvestigatorNoteCreate(address='0xABC', text='   ')

    def test_blank_address_is_rejected(self):
        """Prazna adresa se odbija"""
        with pytest.raises(ValueError):
            InvestigatorNoteCreate(address='   ', text='tekst')

    def test_create_for_unknown_investigation_raises_not_found(self):
        """Kreiranje beleške za nepostojeću istragu prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            _add('nepostojeci123', '0xABC', 'tekst')


class TestRetrieveForAddress:
    """Preuzimanje beleški za adresu"""

    def test_filter_returns_only_that_addresss_notes(self, investigation_id):
        """Filtriranje vraća samo beleške tražene adrese"""
        _add(investigation_id, '0xAAA', 'prva o A')
        _add(investigation_id, '0xAAA', 'druga o A')
        _add(investigation_id, '0xBBB', 'o B')

        for_a = notes_service.list_notes(investigation_id, address='0xAAA')
        assert {note.text for note in for_a} == {'prva o A', 'druga o A'}
        assert all(note.address == '0xAAA' for note in for_a)

    def test_without_address_returns_every_note(self, investigation_id):
        """Bez adrese vraća sve beleške u istrazi"""
        _add(investigation_id, '0xAAA', 'a')
        _add(investigation_id, '0xBBB', 'b')

        assert len(notes_service.list_notes(investigation_id)) == 2

    def test_notes_are_newest_created_first(self, investigation_id):
        """Beleške se vraćaju od najnovije ka najstarijoj po vremenu kreiranja"""
        first = _add(investigation_id, '0xAAA', 'prva')
        time.sleep(0.005)
        second = _add(investigation_id, '0xAAA', 'druga')

        ids = [note.id for note in notes_service.list_notes(investigation_id, address='0xAAA')]
        assert ids == [second.id, first.id]

    def test_list_for_unknown_investigation_raises_not_found(self):
        """Listanje beleški nepostojeće istrage prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            notes_service.list_notes('nepostojeci123', address='0xABC')


class TestUpdate:
    """Izmena beleške"""

    def test_update_changes_text_and_advances_updated_at_only(self, investigation_id):
        """Izmena menja tekst i pomera samo „updated_at", a „created_at"/ID/adresa/autor ostaju"""
        note = _add(investigation_id, '0xABC', 'stari tekst', author='marko')
        time.sleep(0.005)

        updated = notes_service.update_note(investigation_id, note.id, InvestigatorNoteUpdate(text='novi tekst'))

        assert updated.id == note.id
        assert updated.address == '0xABC'
        assert updated.author == 'marko'
        assert updated.text == 'novi tekst'
        assert updated.created_at == note.created_at
        assert updated.updated_at > note.created_at

    def test_update_persists(self, investigation_id):
        """Izmena se trajno upisuje"""
        note = _add(investigation_id, '0xABC', 'v1')
        notes_service.update_note(investigation_id, note.id, InvestigatorNoteUpdate(text='v2'))

        assert notes_service.get_note(investigation_id, note.id).text == 'v2'

    def test_update_unknown_note_raises_not_found(self, investigation_id):
        """Izmena nepostojeće beleške prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigatorNoteNotFoundError):
            notes_service.update_note(investigation_id, 'nema123', InvestigatorNoteUpdate(text='x'))

    def test_blank_update_text_is_rejected(self):
        """Prazan tekst pri izmeni se odbija"""
        with pytest.raises(ValueError):
            InvestigatorNoteUpdate(text='   ')


class TestDelete:
    """Brisanje beleške"""

    def test_delete_removes_only_that_note(self, investigation_id):
        """Brisanje uklanja samo tu belešku"""
        keep = _add(investigation_id, '0xAAA', 'ostaje')
        drop = _add(investigation_id, '0xAAA', 'briše se')

        notes_service.delete_note(investigation_id, drop.id)

        remaining = [note.id for note in notes_service.list_notes(investigation_id)]
        assert remaining == [keep.id]
        with pytest.raises(InvestigatorNoteNotFoundError):
            notes_service.get_note(investigation_id, drop.id)

    def test_delete_unknown_note_raises_not_found(self, investigation_id):
        """Brisanje nepostojeće beleške prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigatorNoteNotFoundError):
            notes_service.delete_note(investigation_id, 'nema123')


class TestSeparationAndScoping:
    """Odvojenost i opseg"""

    def test_notes_are_scoped_per_investigation(self, tmp_path):
        """Beleške su odvojene po istrazi"""
        a = service.create_investigation(InvestigationCaseCreate(name='Istraga A')).id
        b = service.create_investigation(InvestigationCaseCreate(name='Istraga B')).id
        _add(a, '0xABC', 'samo u A')

        assert len(notes_service.list_notes(a)) == 1
        assert notes_service.list_notes(b) == []

    def test_deleting_investigation_removes_its_notes(self, investigation_id):
        """Brisanje istrage briše i njene beleške"""
        _add(investigation_id, '0xABC', 'tekst')

        service.delete_investigation(investigation_id)

        assert notes_repository.load_notes(investigation_id) == []
        with pytest.raises(InvestigationCaseNotFoundError):
            notes_service.list_notes(investigation_id)
