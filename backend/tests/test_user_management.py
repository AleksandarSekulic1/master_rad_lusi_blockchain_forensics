"""Provera preimenovanja i trajnog brisanja korisničkih naloga.

Preimenovanje mora da čuva jedinstvenost korisničkog imena (inače bi dva naloga mogla da
se prijave pod istim imenom), a brisanje mora biti nepovratno ali NE sme dozvoliti da se
sistem zaključa bez ijednog administratora - te dve stvari proveravaju testovi ispod.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.services import user_management


@pytest.fixture(autouse=True)
def isolated_users_file(tmp_path, monkeypatch):
    """Testovi ne smeju da pišu u pravi users.json."""
    monkeypatch.setattr(user_management, '_users_path', lambda: tmp_path / 'users.json')


class TestRenameUser:
    """Preimenovanje korisničkog naloga

    Korisničko ime je jedini identifikator kojim se korisnik prijavljuje, pa promena mora
    da čuva jedinstvenost isto kao pri kreiranju naloga.
    """

    def test_username_is_updated(self):
        """Korisničko ime se ažurira"""
        created = user_management.create_user(username='aco', password='lozinka1')

        updated = user_management.rename_user(created['id'], 'analyst2')

        assert updated['username'] == 'analyst2'
        assert user_management.get_user_by_username('analyst2') is not None
        assert user_management.get_user_by_username('aco') is None

    def test_cannot_rename_into_an_already_taken_username(self):
        """Ne može se preimenovati u već zauzeto korisničko ime"""
        user_management.create_user(username='admin', password='lozinka1')
        second = user_management.create_user(username='aco', password='lozinka2')

        with pytest.raises(ValueError):
            user_management.rename_user(second['id'], 'admin')

    def test_renaming_to_its_own_current_name_is_not_a_conflict(self):
        """Preimenovanje u sopstveno trenutno ime nije konflikt"""
        created = user_management.create_user(username='aco', password='lozinka1')

        updated = user_management.rename_user(created['id'], 'aco')

        assert updated['username'] == 'aco'

    def test_unknown_user_raises_not_found(self):
        """Nepostojeći korisnik prijavljuje grešku 'nije pronađen'"""
        with pytest.raises(FileNotFoundError):
            user_management.rename_user('ne-postoji', 'novo-ime')

    def test_blank_username_is_rejected(self):
        """Prazno korisničko ime se odbija"""
        created = user_management.create_user(username='aco', password='lozinka1')

        with pytest.raises(ValueError):
            user_management.rename_user(created['id'], '   ')


class TestDeleteUser:
    """Trajno brisanje korisničkog naloga

    Brisanje je nepovratno, pa mora postojati barem jedna kočnica: sistem ne sme ostati
    bez ijednog administratora kao posledica brisanja.
    """

    def test_user_is_removed(self):
        """Korisnik nestaje iz spiska nakon brisanja"""
        admin = user_management.create_user(username='admin', password='lozinka1', role='admin')
        analyst = user_management.create_user(username='aco', password='lozinka2', role='analyst')

        user_management.delete_user(analyst['id'])

        usernames = {user['username'] for user in user_management.list_users()}
        assert usernames == {'admin'}
        assert user_management.get_user_by_id(admin['id']) is not None

    def test_unknown_user_raises_not_found(self):
        """Nepostojeći korisnik prijavljuje grešku 'nije pronađen'"""
        with pytest.raises(FileNotFoundError):
            user_management.delete_user('ne-postoji')

    def test_cannot_delete_the_only_remaining_admin(self):
        """Ne može se obrisati jedini preostali administrator"""
        admin = user_management.create_user(username='admin', password='lozinka1', role='admin')
        user_management.create_user(username='aco', password='lozinka2', role='analyst')

        with pytest.raises(ValueError):
            user_management.delete_user(admin['id'])

    def test_can_delete_an_admin_when_another_admin_remains(self):
        """Administrator se može obrisati dok postoji bar još jedan"""
        first_admin = user_management.create_user(username='admin', password='lozinka1', role='admin')
        user_management.create_user(username='admin2', password='lozinka2', role='admin')

        user_management.delete_user(first_admin['id'])

        usernames = {user['username'] for user in user_management.list_users()}
        assert usernames == {'admin2'}
