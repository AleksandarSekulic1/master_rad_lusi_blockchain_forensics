"""Provera osnovnog modela istrage (Case Management / Investigator Layer).

Istraga je kontejner za zaključke istražitelja - beleške, prikačeni čvorovi i ručne veze
između adresa dodaju se u kasnijim koracima. Ovi testovi pokrivaju samo osnovni model:
kreiranje sa jedinstvenim ID-jem i vremenskim pečatima, čitanje/izlistavanje, izmenu koja
pomera "updated_at" a ne dira "created_at", i brisanje. Istraga je namerno odvojena od
dokaznog `Case`-a i ne dira nijedan postojeći algoritam (Graf/Taint/Pathfinding/Behavioral/DEX).

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import time

import pytest

from app.investigations import repository, service
from app.investigations.models import InvestigationCaseCreate, InvestigationCaseUpdate
from app.investigations.service import InvestigationCaseNotFoundError


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi data/investigations direktorijum."""
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')


def _create(name: str = 'Istraga A', description: str | None = None):
    return service.create_investigation(InvestigationCaseCreate(name=name, description=description))


class TestCreate:
    """Kreiranje istrage"""

    def test_gets_unique_id_and_matching_timestamps(self):
        """Kreiranje dodeljuje jedinstven ID i jednake vremenske pečate

        Bez stabilnog ID-ja beleške/veze kasnije ne bi imale za šta da se vežu; pri
        kreiranju created_at i updated_at moraju biti isti (još nije bilo izmena).
        """
        first = _create('Istraga A')
        second = _create('Istraga B')

        assert first.id and second.id
        assert first.id != second.id
        assert first.created_at == first.updated_at

    def test_trims_name_and_blank_description_becomes_none(self):
        """Naziv se trimuje, prazan opis postaje None"""
        case = _create('  Istraga  ', description='   ')

        assert case.name == 'Istraga'
        assert case.description is None

    def test_blank_name_is_rejected(self):
        """Prazan naziv se odbija"""
        with pytest.raises(ValueError):
            InvestigationCaseCreate(name='   ')


class TestReadAndList:
    """Čitanje i izlistavanje"""

    def test_get_returns_stored_case(self):
        """Dohvatanje vraća sačuvanu istragu"""
        created = _create('Istraga A', 'kratak opis')
        loaded = service.get_investigation(created.id)

        assert loaded.id == created.id
        assert loaded.name == 'Istraga A'
        assert loaded.description == 'kratak opis'

    def test_get_unknown_id_raises_not_found(self):
        """Nepoznat ID prijavljuje se kao „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            service.get_investigation('nepostojeci123')

    def test_list_is_newest_updated_first(self):
        """Lista je sortirana po poslednjoj izmeni, najnovija prva"""
        first = _create('Prva')
        _create('Druga')
        time.sleep(0.005)
        service.update_investigation(first.id, InvestigationCaseUpdate(description='naknadna izmena'))

        ids = [case.id for case in service.list_investigations()]

        assert ids[0] == first.id
        assert len(ids) == 2


class TestUpdate:
    """Izmena istrage"""

    def test_update_changes_field_and_advances_updated_at_only(self):
        """Izmena menja polje i pomera samo „updated_at", „created_at" ostaje isti"""
        created = _create('Stari naziv')
        time.sleep(0.005)

        updated = service.update_investigation(created.id, InvestigationCaseUpdate(name='Novi naziv'))

        assert updated.name == 'Novi naziv'
        assert updated.created_at == created.created_at
        assert updated.updated_at > created.updated_at

    def test_description_can_be_cleared_with_empty_string(self):
        """Opis se može obrisati praznim stringom"""
        created = _create('Istraga', description='ima opis')

        updated = service.update_investigation(created.id, InvestigationCaseUpdate(description=''))

        assert updated.description is None

    def test_update_unknown_id_raises_not_found(self):
        """Izmena nepoznate istrage prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            service.update_investigation('nepostojeci123', InvestigationCaseUpdate(name='X'))


class TestDelete:
    """Brisanje istrage"""

    def test_delete_removes_record_and_index_entry(self):
        """Brisanje uklanja i zapis i stavku iz indeksa"""
        created = _create('Za brisanje')

        service.delete_investigation(created.id)

        with pytest.raises(InvestigationCaseNotFoundError):
            service.get_investigation(created.id)
        assert all(entry['id'] != created.id for entry in repository.load_index())

    def test_delete_unknown_id_raises_not_found(self):
        """Brisanje nepoznate istrage prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            service.delete_investigation('nepostojeci123')
