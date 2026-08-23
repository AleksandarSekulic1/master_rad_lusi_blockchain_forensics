"""Provera izveštaja aktivnosti (chain of custody).

Ovaj izveštaj tvrdi "ovo su sve akcije korisnika X u periodu Y". Dve stvari moraju biti
tačne da bi ta tvrdnja stajala: da period obuhvata tačno one dane koje je korisnik izabrao
(a ne pomerene zbog vremenske zone), i da korisnik ne može da vidi tuđe akcije. Oba su
pokrivena ovde.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.routes.activity_log import _parse_users_param, _resolve_users, _validate_date
from app.evidence.audit_log import local_day_bounds_utc
from app.exports.activity_report import (
    action_label,
    build_activity_csv,
    build_activity_pdf,
    format_period,
    format_tz_label,
    summarize_details,
)
from fastapi import HTTPException


ANALYST = {'id': '1', 'username': 'aco', 'role': 'analyst'}
ADMIN = {'id': '2', 'username': 'admin', 'role': 'admin'}


class TestPeriodAndTimezone:
    """Period i vremenska zona

    Log čuva vreme u UTC, a korisnik bira dane onako kako ih vidi na ekranu - u lokalnom
    vremenu. Ako se to pogrešno prevede, akcija upadne u pogrešan dan.
    """

    def test_single_day_covers_whole_local_day(self):
        """Jedan dan obuhvata ceo lokalni dan

        Izbor 27.07. mora da obuhvati sve od lokalne ponoći do lokalne ponoći sledećeg
        dana - ni sat manje, ni sat više.
        """
        start, end = local_day_bounds_utc('2026-07-27', '2026-07-27', tz_offset_minutes=-120)

        assert start == datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc)

    def test_local_midnight_action_lands_in_the_day_the_user_sees(self):
        """Akcija oko ponoći upada u dan koji korisnik vidi

        Akcija u 00:30 po lokalnom vremenu (UTC+2) zabeležena je kao 22:30 prethodnog dana
        u UTC-u. Mora ući u izveštaj za dan koji piše na ekranu, a ne za prethodni.
        """
        start, end = local_day_bounds_utc('2026-07-27', '2026-07-27', tz_offset_minutes=-120)
        action_utc = datetime(2026, 7, 26, 22, 30, tzinfo=timezone.utc)  # 00:30 lokalno, 27.07.

        assert start <= action_utc < end

    def test_range_spans_from_first_to_last_day(self):
        """Opseg datuma obuhvata i prvi i poslednji dan

        Kod opsega od-do, poslednji dan mora biti uključen u celosti (granica je ponoć
        sledećeg dana), inače bi poslednji dan tiho ispao iz izveštaja.
        """
        start, end = local_day_bounds_utc('2026-07-01', '2026-07-31', tz_offset_minutes=0)

        assert start == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    def test_no_dates_means_everything(self):
        """Bez izabranih datuma izveštaj obuhvata sve

        Opcija "sve aktivnosti od početka korišćenja sistema" ne sme da postavi nikakvu
        vremensku granicu.
        """
        assert local_day_bounds_utc(None, None, tz_offset_minutes=-120) == (None, None)

    def test_timezone_label_is_printed_correctly(self):
        """Oznaka vremenske zone se ispisuje ispravno

        Izveštaj mora da navede u kojoj je zoni period računat, inače se ne može
        proveriti - a oznaka mora odgovarati stvarnoj zoni.
        """
        assert format_tz_label(-120) == 'UTC+02:00'
        assert format_tz_label(0) == 'UTC+00:00'
        assert format_tz_label(300) == 'UTC-05:00'

    def test_period_description_matches_the_choice(self):
        """Opis perioda odgovara izboru korisnika

        Tri ponuđene opcije (sve / jedan dan / opseg) moraju se u izveštaju i pročitati
        kao tri različite stvari.
        """
        assert 'od početka' in format_period(None, None)
        assert format_period('2026-07-27', '2026-07-27') == 'Jedan dan: 27.07.2026.'
        assert format_period('2026-07-01', '2026-07-31') == 'Od 01.07.2026. do 31.07.2026.'


class TestReportAccessControl:
    """Pravo pristupa izveštaju

    Ko sme da izveze čije aktivnosti - odlučuje se na serveru, na osnovu uloge iz tokena.
    """

    def test_analyst_always_gets_only_their_own_entries(self):
        """Analitičar dobija isključivo svoje akcije

        Bez obzira šta pošalje kao spisak korisnika, opseg se svodi na njegovo ime.
        """
        selected, is_admin = _resolve_users(ANALYST, ['admin', 'aco'])

        assert selected == ['aco']
        assert is_admin is False

    def test_analyst_cannot_widen_scope_by_editing_the_request(self):
        """Analitičar ne može da proširi opseg izmenom zahteva

        Zaštita od ručnog menjanja parametara u adresi - traženje tuđih akcija se
        ignoriše, ne poštuje.
        """
        selected, _ = _resolve_users(ANALYST, ['admin'])

        assert selected == ['aco']
        assert 'admin' not in selected

    def test_admin_can_select_a_combination_of_users(self):
        """Administrator može da izabere kombinaciju korisnika

        Admin bira jednog, više njih ili sve - izbor se prosleđuje onakav kakav jeste.
        """
        selected, is_admin = _resolve_users(ADMIN, ['admin', 'aco'])

        assert selected == ['admin', 'aco']
        assert is_admin is True

    def test_admin_without_selection_gets_everyone(self):
        """Administrator bez izbora dobija sve korisnike

        Prazan izbor znači "svi", a ne "nijedan" - inače bi podrazumevani izveštaj bio
        prazan.
        """
        selected, _ = _resolve_users(ADMIN, None)

        assert selected is None


class TestReportContent:
    """Sadržaj izveštaja

    Da li izvezeni dokument zaista sadrži ono što stranica prikazuje.
    """

    def test_csv_contains_a_row_per_action(self):
        """CSV sadrži po jedan red za svaku akciju

        Uz zaglavlje, broj redova mora odgovarati broju zapisa - bez tihog preskakanja.
        """
        entries = [
            {'timestamp': '2026-07-27T10:00:00+00:00', 'user': 'aco', 'action': 'analytics_run',
             'case_id': 'abc', 'case_name': 'test 1', 'file_name': None, 'sha256': None,
             'details': {'seed_count': 2, 'evidence_scope': 'combined'}},
            {'timestamp': '2026-07-27T11:00:00+00:00', 'user': 'aco', 'action': 'csv_upload',
             'case_id': 'abc', 'case_name': 'test 1', 'file_name': 'x.csv', 'sha256': 'deadbeef',
             'details': {'original_name': 'x.csv'}},
        ]

        csv_text = build_activity_csv(entries, tz_offset_minutes=-120)
        rows = [line for line in csv_text.strip().splitlines() if line]

        assert len(rows) == 3  # zaglavlje + 2 zapisa
        assert 'deadbeef' in csv_text

    def test_csv_keeps_both_local_and_utc_time(self):
        """CSV čuva i lokalno i UTC vreme

        Lokalno vreme je ono što je korisnik video, UTC je ono što je zapisano - izveštaj
        navodi oba da bi se mogao proveriti nezavisno od zone.
        """
        entries = [{'timestamp': '2026-07-26T22:30:00+00:00', 'user': 'aco', 'action': 'analytics_run',
                    'case_id': None, 'case_name': None, 'file_name': None, 'sha256': None, 'details': {}}]

        csv_text = build_activity_csv(entries, tz_offset_minutes=-120)

        assert '27.07.2026. 00:30:00' in csv_text  # lokalno
        assert '2026-07-26T22:30:00+00:00' in csv_text  # UTC

    def test_unknown_action_is_shown_not_hidden(self):
        """Nepoznata akcija se prikazuje, ne skriva

        Ako se doda nova vrsta akcije koju izveštaj još ne prevodi, mora se videti pod
        svojim tehničkim imenom - izveštaj koji bi je prećutao bio bi nepotpun.
        """
        assert action_label('neka_nova_akcija') == 'neka_nova_akcija'

    def test_known_actions_are_translated(self):
        """Poznate akcije se prikazuju na srpskom

        Izveštaj čita neko ko nikad neće videti kod, pa tehnički nazivi akcija ne smeju
        da procure u dokument.
        """
        assert action_label('analytics_run') == 'Pokrenuta analiza'
        assert action_label('onchain_fetch_mainnet_address') == 'Povučene transakcije sa blockchain-a'

    def test_export_record_states_format_period_and_count(self):
        """Zapis o izvozu navodi format, period i broj zapisa

        Sam izvoz izveštaja se upisuje u log. Da bi se dva izveštaja kasnije razlikovala,
        zapis mora reći koji je vremenski okvir bio izabran - inače stoji samo "izvezen
        izveštaj", što ne dokazuje ništa.
        """
        entry = {
            'action': 'activity_report_exported',
            'details': {'format': 'pdf', 'entry_count': 45, 'date_from': '2026-08-10',
                        'date_to': '2026-08-10', 'users': ['admin', 'aco']},
        }

        summary = summarize_details(entry)

        assert 'PDF' in summary
        assert '45 zapisa' in summary
        assert 'jedan dan: 10.08.2026.' in summary
        assert 'admin, aco' in summary

    def test_export_record_shows_full_range_and_all_activities(self):
        """Zapis o izvozu razlikuje opseg od svih aktivnosti

        Tri ponuđena režima (sve / jedan dan / opseg) moraju se i u logu pročitati kao
        tri različite stvari.
        """
        def summary_for(date_from, date_to):
            return summarize_details({
                'action': 'activity_report_exported',
                'details': {'format': 'csv', 'entry_count': 1, 'date_from': date_from, 'date_to': date_to},
            })

        assert 'sve aktivnosti' in summary_for(None, None)
        assert '01.07.2026. – 31.07.2026.' in summary_for('2026-07-01', '2026-07-31')

    def test_analysis_summary_states_seeds_and_scope(self):
        """Rezime analize navodi izvore i opseg evidencije

        Dve analize istog slučaja sa različitim izvorima daju različite procente, pa
        izveštaj mora reći koja je koja.
        """
        entry = {'action': 'analytics_run', 'details': {'seed_count': 2, 'evidence_scope': 'combined'}}

        assert summarize_details(entry) == '2 izvora (seed) · sva evidencija (kombinovano)'

    def test_analysis_summary_notes_custody_when_recorded(self):
        """Rezime analize navodi lanac dokaza kad je zabeležen

        Razlikuje na prvi pogled pasivno učitavanje grafa (Kontrolna tabla, Graf pri
        izboru slučaja) od namernog pristupa (Taint analiza, "Analiziraj graf") - bez ovoga
        bi se to moralo proveravati unakrsno sa posebnim fajlovima lanca dokaza.
        """
        entry = {
            'action': 'analytics_run',
            'details': {
                'seed_count': 1, 'evidence_scope': 'combined',
                'custody_recorded': True, 'custody_transaction_rows': 900, 'custody_evidence_files': 1,
            },
        }

        assert summarize_details(entry) == '1 izvora (seed) · sva evidencija (kombinovano) · lanac dokaza: 900 transakcija, 1 fajl(ova)'

    def test_analysis_summary_omits_custody_note_when_not_recorded(self):
        """Rezime analize ne pominje lanac dokaza kod pasivnog učitavanja"""
        entry = {'action': 'analytics_run', 'details': {'seed_count': 0, 'evidence_scope': 'combined', 'custody_recorded': False}}

        assert 'lanac dokaza' not in summarize_details(entry)


class TestPdfGeneration:
    """Generisanje PDF dokumenta

    Izveštaj se pravi na serveru, iz samog log fajla. Ovi testovi štite od pucanja na
    graničnim slučajevima - dokument koji se ne generiše je gori od ružnog dokumenta.
    """

    @staticmethod
    def _entry(timestamp: str, user: str = 'aco', action: str = 'analytics_run', **extra):
        base = {
            'timestamp': timestamp, 'user': user, 'action': action,
            'case_id': 'abc', 'case_name': 'test 1', 'file_name': None, 'sha256': None,
            'details': {'seed_count': 1, 'evidence_scope': 'combined'},
        }
        base.update(extra)
        return base

    def _build(self, entries):
        return build_activity_pdf(
            entries, generated_by='admin', date_from=None, date_to=None,
            tz_offset_minutes=-120, selected_users=[], scope='all',
        )

    def test_produces_a_valid_pdf(self):
        """Generisan fajl je ispravan PDF

        Osnovna provera da izlaz nije prazan ili oštećen - dokument mora početi PDF
        potpisom.
        """
        payload = self._build([self._entry('2026-07-27T10:00:00+00:00')])

        assert payload.startswith(b'%PDF')
        assert len(payload) > 1000

    def test_embeds_a_unicode_font(self):
        """PDF ugrađuje Unicode font

        Bez ugrađenog TTF-a slova č/ć/š/ž/đ ne bi mogla da se prikažu, pa bi izveštaj na
        srpskom bio neupotrebljiv.
        """
        payload = self._build([self._entry('2026-07-27T10:00:00+00:00')])

        assert b'FontFile2' in payload

    def test_handles_multiple_days_and_users(self):
        """Podnosi više dana i više korisnika

        Hronologija se grupiše po danima, a rezime po korisniku se dodaje samo kad ih ima
        više - obe grane moraju proći bez greške.
        """
        entries = [
            self._entry('2026-07-27T10:00:00+00:00', user='aco'),
            self._entry('2026-07-26T10:00:00+00:00', user='admin', action='csv_upload'),
            self._entry('2026-07-25T10:00:00+00:00', user='admin', action='test_suite_run'),
        ]

        assert self._build(entries).startswith(b'%PDF')

    def test_handles_empty_and_unknown_actions(self):
        """Podnosi prazan spisak i nepoznate akcije

        Prazan izveštaj ruta ionako odbija, ali sam generator ne sme da pukne; nepoznata
        akcija mora proći kroz obojenu oznaku bez greške.
        """
        assert self._build([]).startswith(b'%PDF')
        assert self._build([self._entry('2026-07-27T10:00:00+00:00', action='nesto_novo')]).startswith(b'%PDF')


class TestRequestValidation:
    """Provera unosa

    Neispravan unos mora biti odbijen jasnom porukom, a ne tiho protumačen.
    """

    def test_invalid_date_is_rejected(self):
        """Neispravan datum se odbija

        Pogrešan format datuma bi inače mogao da bude tiho ignorisan, pa bi izveštaj
        obuhvatio pogrešan period.
        """
        with pytest.raises(HTTPException) as error:
            _validate_date('27.07.2026', 'date_from')

        assert error.value.status_code == 400

    def test_valid_date_passes(self):
        """Ispravan datum prolazi proveru"""
        assert _validate_date('2026-07-27', 'date_from') is None

    def test_user_list_is_parsed_from_the_query(self):
        """Spisak korisnika se ispravno čita iz zahteva

        Admin bira više korisnika odjednom; prazne vrednosti i suvišni razmaci se
        odbacuju.
        """
        assert _parse_users_param('admin, aco ,') == ['admin', 'aco']
        assert _parse_users_param('') is None
        assert _parse_users_param(None) is None
