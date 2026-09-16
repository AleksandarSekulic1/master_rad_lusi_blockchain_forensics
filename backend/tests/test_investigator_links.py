"""Provera istražiteljskih veza (Investigator Links) između dve blockchain adrese.

Investigator link je PRETPOSTAVLJENA veza (suspected relation / investigator association)
koju istražitelj beleži na osnovu vanlančanih (off-chain) dokaza - nije dokazana činjenica
i nije grana transakcionog grafa. Ovi testovi pokrivaju: kreiranje (ID, ID slučaja,
adrese doslovno, razlog, dokaz, confidence Low/Medium/High, autor, directed=False,
vremenski pečati), validaciju (prazna polja, ista adresa sa obe strane, nevažeći
confidence), NEUSMERENO preuzimanje po adresi (poklapa se sa obe strane), izmenu koja
pomera samo „updated_at", brisanje, opseg po istrazi i brisanje zajedno sa istragom.
Nijedan postojeći graf/analiza se ne dira.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import time

import pytest

from app.features.investigation_links import repository as links_repository
from app.features.investigation_links import service as links_service
from app.features.investigation_links.models import InvestigatorLinkCreate, InvestigatorLinkUpdate
from app.features.investigation_links.service import InvestigatorLinkNotFoundError
from app.investigations import repository, service
from app.investigations.models import InvestigationCaseCreate
from app.investigations.service import InvestigationCaseNotFoundError


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirecting the investigations root also redirects the links files, since they live
    inside each investigation's own directory."""
    monkeypatch.setattr(repository, '_root', lambda: tmp_path / 'investigations')


@pytest.fixture
def investigation_id() -> str:
    return service.create_investigation(InvestigationCaseCreate(name='Op. Nightfall')).id


def _add_link(
    investigation_id: str,
    source: str = '0xABC',
    target: str = '0xDEF',
    reason: str = 'IP address from server logs connects both addresses.',
    evidence: str = 'Server log #42',
    confidence: str = 'High',
    author: str = 'inv1',
):
    return links_service.create_link(
        investigation_id,
        InvestigatorLinkCreate(
            source_address=source,
            target_address=target,
            reason=reason,
            evidence=evidence,
            confidence=confidence,
        ),
        author=author,
    )


class TestCreate:
    """Kreiranje investigator link-a"""

    def test_link_has_all_minimum_fields(self, investigation_id):
        """Link ima sva minimalna polja: ID, ID slučaja, adrese, razlog, dokaz, confidence, vreme

        Pri kreiranju created_at i updated_at su isti; directed je False (neusmerena veza).
        """
        link = _add_link(investigation_id, author='marko')

        assert link.id
        assert link.investigation_id == investigation_id
        assert link.source_address == '0xABC'
        assert link.target_address == '0xDEF'
        assert link.reason == 'IP address from server logs connects both addresses.'
        assert link.evidence == 'Server log #42'
        assert link.confidence == 'High'
        assert link.author == 'marko'
        assert link.directed is False
        assert link.created_at == link.updated_at

    def test_addresses_are_trimmed_but_case_is_preserved(self, investigation_id):
        """Adrese se trimuju ali se veličina slova ne dira"""
        link = _add_link(investigation_id, source='  0xAbC  ', target='  0xDeF  ')

        assert link.source_address == '0xAbC'
        assert link.target_address == '0xDeF'

    def test_blank_fields_are_rejected(self):
        """Prazna obavezna polja se odbijaju (adresa, razlog, dokaz)"""
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(source_address='   ', target_address='0xDEF', reason='r', evidence='e', confidence='Low')
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(source_address='0xABC', target_address='0xDEF', reason='   ', evidence='e', confidence='Low')
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(source_address='0xABC', target_address='0xDEF', reason='r', evidence='   ', confidence='Low')

    def test_same_address_on_both_sides_is_rejected(self):
        """Ista adresa sa obe strane se odbija"""
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(
                source_address=' 0xABC ', target_address='0xABC', reason='r', evidence='e', confidence='Low'
            )

    def test_confidence_must_be_low_medium_or_high(self):
        """Confidence mora biti Low, Medium ili High"""
        for value in ('Low', 'Medium', 'High'):
            InvestigatorLinkCreate(source_address='0xA', target_address='0xB', reason='r', evidence='e', confidence=value)
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(source_address='0xA', target_address='0xB', reason='r', evidence='e', confidence='Certain')
        with pytest.raises(ValueError):
            InvestigatorLinkCreate(source_address='0xA', target_address='0xB', reason='r', evidence='e', confidence='high')

    def test_create_for_unknown_investigation_raises_not_found(self):
        """Kreiranje link-a za nepostojeću istragu prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            _add_link('nepostojeci123')


class TestUndirectedRetrieval:
    """Neusmereno preuzimanje po adresi"""

    def test_link_is_found_from_either_endpoint(self, investigation_id):
        """Link se pronalazi sa obe strane (neusmerena asocijacija)"""
        link = _add_link(investigation_id, source='0xAAA', target='0xBBB')

        from_source = links_service.list_links(investigation_id, address='0xAAA')
        from_target = links_service.list_links(investigation_id, address='0xBBB')

        assert [item.id for item in from_source] == [link.id]
        assert [item.id for item in from_target] == [link.id]

    def test_address_that_is_neither_endpoint_returns_nothing(self, investigation_id):
        """Adresa koja nije nijedan kraj ne vraća ništa"""
        _add_link(investigation_id, source='0xAAA', target='0xBBB')

        assert links_service.list_links(investigation_id, address='0xZZZ') == []

    def test_without_address_returns_every_link(self, investigation_id):
        """Bez adrese vraća sve veze u istrazi"""
        _add_link(investigation_id, source='0xAAA', target='0xBBB')
        _add_link(investigation_id, source='0xCCC', target='0xDDD')

        assert len(links_service.list_links(investigation_id)) == 2

    def test_links_are_newest_created_first(self, investigation_id):
        """Veze se vraćaju od najnovije ka najstarijoj po vremenu kreiranja"""
        first = _add_link(investigation_id, source='0xAAA', target='0xBBB')
        time.sleep(0.005)
        second = _add_link(investigation_id, source='0xAAA', target='0xCCC')

        ids = [item.id for item in links_service.list_links(investigation_id, address='0xAAA')]
        assert ids == [second.id, first.id]

    def test_list_for_unknown_investigation_raises_not_found(self):
        """Listanje veza nepostojeće istrage prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigationCaseNotFoundError):
            links_service.list_links('nepostojeci123', address='0xABC')


class TestUpdate:
    """Izmena investigator link-a"""

    def test_update_changes_reason_evidence_confidence_and_advances_updated_at_only(self, investigation_id):
        """Izmena menja reason/evidence/confidence i pomera samo „updated_at"; adrese/ID/autor ostaju"""
        link = _add_link(investigation_id, confidence='Low', author='marko')
        time.sleep(0.005)

        updated = links_service.update_link(
            investigation_id,
            link.id,
            InvestigatorLinkUpdate(reason='Refined: same IP block', evidence='Server log #42, #57', confidence='High'),
        )

        assert updated.id == link.id
        assert updated.source_address == link.source_address
        assert updated.target_address == link.target_address
        assert updated.author == 'marko'
        assert updated.reason == 'Refined: same IP block'
        assert updated.evidence == 'Server log #42, #57'
        assert updated.confidence == 'High'
        assert updated.created_at == link.created_at
        assert updated.updated_at > link.created_at

    def test_empty_update_is_a_noop(self, investigation_id):
        """Prazna izmena ne menja ništa (ni „updated_at")"""
        link = _add_link(investigation_id)
        unchanged = links_service.update_link(investigation_id, link.id, InvestigatorLinkUpdate())

        assert unchanged.updated_at == link.updated_at

    def test_update_unknown_link_raises_not_found(self, investigation_id):
        """Izmena nepostojeće veze prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigatorLinkNotFoundError):
            links_service.update_link(investigation_id, 'nema123', InvestigatorLinkUpdate(confidence='Medium'))

    def test_blank_update_value_is_rejected(self):
        """Prazna vrednost pri izmeni se odbija"""
        with pytest.raises(ValueError):
            InvestigatorLinkUpdate(reason='   ')


class TestDelete:
    """Brisanje investigator link-a"""

    def test_delete_removes_only_that_link(self, investigation_id):
        """Brisanje uklanja samo tu vezu"""
        keep = _add_link(investigation_id, source='0xAAA', target='0xBBB')
        drop = _add_link(investigation_id, source='0xCCC', target='0xDDD')

        links_service.delete_link(investigation_id, drop.id)

        remaining = [item.id for item in links_service.list_links(investigation_id)]
        assert remaining == [keep.id]
        with pytest.raises(InvestigatorLinkNotFoundError):
            links_service.get_link(investigation_id, drop.id)

    def test_delete_unknown_link_raises_not_found(self, investigation_id):
        """Brisanje nepostojeće veze prijavljuje „nije pronađeno"""
        with pytest.raises(InvestigatorLinkNotFoundError):
            links_service.delete_link(investigation_id, 'nema123')


class TestScopingAndSeparation:
    """Opseg i odvojenost od blockchain podataka"""

    def test_links_are_scoped_per_investigation(self):
        """Veze su odvojene po istrazi"""
        a = service.create_investigation(InvestigationCaseCreate(name='Istraga A')).id
        b = service.create_investigation(InvestigationCaseCreate(name='Istraga B')).id
        _add_link(a, source='0xAAA', target='0xBBB')

        assert len(links_service.list_links(a)) == 1
        assert links_service.list_links(b) == []

    def test_deleting_investigation_removes_its_links(self, investigation_id):
        """Brisanje istrage briše i njene veze"""
        _add_link(investigation_id)

        service.delete_investigation(investigation_id)

        assert links_repository.load_links(investigation_id) == []
        with pytest.raises(InvestigationCaseNotFoundError):
            links_service.list_links(investigation_id)

    def test_links_are_stored_separately_from_notes_and_graph(self, investigation_id, tmp_path):
        """Veze se čuvaju u zasebnom links.json, ne diraju notes ni graf"""
        _add_link(investigation_id)
        link_file = tmp_path / 'investigations' / investigation_id / 'links.json'
        notes_file = tmp_path / 'investigations' / investigation_id / 'notes.json'

        assert link_file.exists()
        assert not notes_file.exists()
