"""Provera zakačenih čvorova (pinned nodes) - Case Management / Investigator Layer, korak 10.

Zakačen čvor je adresa koju je istražitelj označio kao važnu i fiksirao na grafu. Od
koraka 10 se TRAJNO čuva (u data/investigations/<id>/pinned_nodes.json), da bi preživeo
osvežavanje stranice, i pripada istrazi kao i beleške i veze. Ovi testovi pokrivaju:
zakačivanje (adresa doslovno, pozicija, autor, vremenski pečati), upsert po adresi
(ponovno slanje samo menja poziciju), listanje, otkačivanje, opseg po istrazi i brisanje
zajedno sa istragom.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.features.investigation_pins import repository as pins_repository
from app.features.investigation_pins import service as pins_service
from app.features.investigation_pins.models import PinNodeRequest
from app.features.investigation_pins.service import PinnedNodeNotFoundError
from app.investigations import repository, service
from app.investigations.models import InvestigationCaseCreate
from app.investigations.service import InvestigationCaseNotFoundError


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi data/investigations direktorijum."""
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')


@pytest.fixture
def investigation_id() -> str:
    return service.create_investigation(InvestigationCaseCreate(name='Op. Nightfall')).id


def _pin(investigation_id: str, address: str = '0xAAA', x: float | None = 10.0, y: float | None = 20.0, by: str = 'inv1'):
    return pins_service.set_pin(investigation_id, PinNodeRequest(address=address, x=x, y=y), pinned_by=by)


class TestPinCreate:
    """Zakačivanje čvora"""

    def test_pin_stores_address_position_author_and_timestamps(self, investigation_id):
        """Zakačivanje čuva adresu, poziciju, autora i vremenske pečate"""
        pin = _pin(investigation_id, '0xAbC', x=123.5, y=-77.25, by='marko')

        assert pin.investigation_id == investigation_id
        assert pin.address == '0xAbC'
        assert pin.x == 123.5 and pin.y == -77.25
        assert pin.pinned_by == 'marko'
        assert pin.pinned_at == pin.updated_at

    def test_address_is_trimmed_case_preserved(self, investigation_id):
        """Adresa se trimuje, veličina slova se ne dira"""
        assert _pin(investigation_id, '  0xAbCdEf  ').address == '0xAbCdEf'

    def test_blank_address_rejected(self):
        """Prazna adresa se odbija"""
        with pytest.raises(ValueError):
            PinNodeRequest(address='   ')

    def test_pin_for_unknown_investigation_raises_not_found(self):
        """Zakačivanje u nepostojećoj istrazi prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            _pin('nepostojeci123')

    def test_repinning_same_address_is_an_upsert_not_a_duplicate(self, investigation_id):
        """Ponovno zakačivanje iste adrese menja poziciju, ne pravi duplikat"""
        first = _pin(investigation_id, '0xAAA', x=1, y=1)
        second = pins_service.set_pin(investigation_id, PinNodeRequest(address='0xAAA', x=99, y=88), pinned_by='inv1')

        pins = pins_service.list_pins(investigation_id)
        assert len(pins) == 1
        assert pins[0].x == 99 and pins[0].y == 88
        assert second.pinned_at == first.pinned_at  # kept
        assert second.updated_at >= first.updated_at


class TestListAndClear:
    """Listanje i otkačivanje"""

    def test_list_returns_all_pins(self, investigation_id):
        """Listanje vraća sve zakačene čvorove"""
        _pin(investigation_id, '0xAAA')
        _pin(investigation_id, '0xBBB')
        assert {p.address for p in pins_service.list_pins(investigation_id)} == {'0xAAA', '0xBBB'}

    def test_list_for_unknown_investigation_raises_not_found(self):
        """Listanje za nepostojeću istragu prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            pins_service.list_pins('nepostojeci123')

    def test_clear_removes_only_that_address(self, investigation_id):
        """Otkačivanje uklanja samo tu adresu"""
        _pin(investigation_id, '0xAAA')
        _pin(investigation_id, '0xBBB')

        pins_service.clear_pin(investigation_id, '0xAAA')

        assert {p.address for p in pins_service.list_pins(investigation_id)} == {'0xBBB'}

    def test_clear_unknown_address_raises_not_found(self, investigation_id):
        """Otkačivanje adrese koja nije zakačena prijavljuje „nije pronađeno"""
        with pytest.raises(PinnedNodeNotFoundError):
            pins_service.clear_pin(investigation_id, '0xNEMA')


class TestScopingAndCascade:
    """Opseg i brisanje sa istragom"""

    def test_pins_are_scoped_per_investigation(self):
        """Zakačeni čvorovi su odvojeni po istrazi"""
        a = service.create_investigation(InvestigationCaseCreate(name='A')).id
        b = service.create_investigation(InvestigationCaseCreate(name='B')).id
        _pin(a, '0xAAA')

        assert len(pins_service.list_pins(a)) == 1
        assert pins_service.list_pins(b) == []

    def test_deleting_investigation_removes_its_pins(self, investigation_id):
        """Brisanje istrage briše i njene zakačene čvorove"""
        _pin(investigation_id, '0xAAA')

        service.delete_investigation(investigation_id)

        assert pins_repository.load_pins(investigation_id) == []
        with pytest.raises(InvestigationCaseNotFoundError):
            pins_service.list_pins(investigation_id)
