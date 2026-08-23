"""Provera Behavioral / Time-of-Day Analysis modula (prva verzija - samo UTC, bez
zaključivanja vremenske zone).

Cilj je jednostavan: za jednu adresu, izbrojati njene transakcije (iz grafa slučaja) po
UTC satu (0-23), po danu u nedelji, i po kombinaciji (dan, sat), i izračunati osnovne
statistike (najaktivniji sat/dan, najgušći (dan,sat) period, ukupan broj).

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

import pytest

from app.analytics.behavioral_analysis import DAY_NAMES, HOUR_KEYS, analyze_time_of_day


class TestAnalyzeTimeOfDay:
    """Analiza vremenskog obrasca po adresi"""

    def test_counts_transactions_where_address_is_sender_or_recipient(self, graph_from_rows):
        """Broji i odlazne i dolazne transakcije adrese, ne samo jedan smer"""
        graph = graph_from_rows([
            ('0xA', '0xB', 10.0, '2026-01-05T08:00:00Z'),  # 0xA je pošiljalac
            ('0xC', '0xA', 5.0, '2026-01-05T09:00:00Z'),  # 0xA je primalac
            ('0xC', '0xB', 3.0, '2026-01-05T10:00:00Z'),  # 0xA nije ni pošiljalac ni primalac
        ])

        result = analyze_time_of_day(graph, '0xA')

        assert result['total_transactions'] == 2
        assert result['stats']['total_analyzed_transactions'] == 2

    def test_counts_self_transfer_exactly_once(self, graph_from_rows):
        """Transakcija adrese samoj sebi se broji jednom, ne dvaput (nije i odlazna i dolazna)"""
        graph = graph_from_rows([('0xA', '0xA', 1.0, '2026-01-05T08:00:00Z')])

        result = analyze_time_of_day(graph, '0xA')

        assert result['total_transactions'] == 1

    def test_hourly_distribution_is_zero_filled_for_all_24_hours(self, graph_from_rows):
        """Svih 24 sata su prisutna u rezultatu, čak i kad nemaju nijednu transakciju"""
        graph = graph_from_rows([('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z')])

        result = analyze_time_of_day(graph, '0xA')

        assert set(result['hourly_distribution'].keys()) == set(HOUR_KEYS)
        assert result['hourly_distribution']['08'] == 1
        assert result['hourly_distribution']['09'] == 0

    def test_day_of_week_distribution_is_zero_filled_for_all_7_days(self, graph_from_rows):
        """Svih 7 dana je prisutno u rezultatu, čak i kad nemaju nijednu transakciju.

        2026-01-05 je ponedeljak."""
        graph = graph_from_rows([('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z')])

        result = analyze_time_of_day(graph, '0xA')

        assert set(result['day_of_week_distribution'].keys()) == set(DAY_NAMES)
        assert result['day_of_week_distribution']['Monday'] == 1
        assert result['day_of_week_distribution']['Tuesday'] == 0

    def test_hour_by_day_distribution_places_count_in_correct_cell(self, graph_from_rows):
        """Kombinovana (dan, sat) matrica upisuje broj u tačnu ćeliju.

        2026-01-06 je utorak."""
        graph = graph_from_rows([
            ('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z'),  # ponedeljak, 08h
            ('0xA', '0xB', 1.0, '2026-01-05T08:30:00Z'),  # ponedeljak, 08h (isti sat)
            ('0xA', '0xB', 1.0, '2026-01-06T14:00:00Z'),  # utorak, 14h
        ])

        result = analyze_time_of_day(graph, '0xA')

        assert result['hour_by_day_distribution']['Monday']['08'] == 2
        assert result['hour_by_day_distribution']['Tuesday']['14'] == 1
        assert result['hour_by_day_distribution']['Monday']['14'] == 0
        assert result['hour_by_day_distribution']['Tuesday']['08'] == 0

    def test_bucketing_converts_non_utc_timestamps_to_utc(self, graph_from_rows):
        """Ulazni timestamp sa drugim offset-om se pre bucket-ovanja konvertuje u UTC.

        2026-01-01 23:30 -05:00 => 2026-01-02 04:30 UTC (petak, sat 04)."""
        graph = graph_from_rows([('0xA', '0xB', 1.0, '2026-01-01T23:30:00-05:00')])

        result = analyze_time_of_day(graph, '0xA')

        assert result['hourly_distribution']['04'] == 1
        assert result['day_of_week_distribution']['Friday'] == 1

    def test_stats_report_most_active_hour_and_day(self, graph_from_rows):
        """Statistike prijavljuju najaktivniji sat i najaktivniji dan (sa brojem)"""
        graph = graph_from_rows([
            ('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z'),
            ('0xA', '0xB', 1.0, '2026-01-05T08:30:00Z'),
            ('0xA', '0xB', 1.0, '2026-01-06T14:00:00Z'),
        ])

        result = analyze_time_of_day(graph, '0xA')
        stats = result['stats']

        assert stats['most_active_hour'] == '08'
        assert stats['most_active_hour_count'] == 2
        assert stats['most_active_day'] == 'Monday'
        assert stats['most_active_day_count'] == 2

    def test_stats_report_peak_day_hour_period(self, graph_from_rows):
        """'Period najveće aktivnosti' je konkretna (dan, sat) ćelija sa najviše transakcija,
        ne isto što i samostalno najaktivniji sat ili samostalno najaktivniji dan"""
        graph = graph_from_rows([
            ('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z'),
            ('0xA', '0xB', 1.0, '2026-01-06T08:00:00Z'),
            ('0xA', '0xB', 1.0, '2026-01-06T08:30:00Z'),
            ('0xA', '0xB', 1.0, '2026-01-06T08:45:00Z'),
        ])

        result = analyze_time_of_day(graph, '0xA')

        assert result['stats']['peak_period'] == {
            'day': 'Tuesday',
            'hour': '08',
            'count': 3,
            'label': 'Tuesday 08:00 UTC',
        }

    def test_ties_break_towards_earlier_hour_and_day(self, graph_from_rows):
        """Kad je više sati/dana izjednačeno po broju, bira se raniji po fiksnom redosledu
        (00->23, Monday->Sunday), a ne poslednji obrađen"""
        graph = graph_from_rows([
            ('0xA', '0xB', 1.0, '2026-01-06T14:00:00Z'),  # utorak, 14h
            ('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z'),  # ponedeljak, 08h (isti broj = 1)
        ])

        result = analyze_time_of_day(graph, '0xA')

        assert result['stats']['most_active_hour'] == '08'
        assert result['stats']['most_active_day'] == 'Monday'

    def test_raises_for_address_not_present_in_graph(self, graph_from_rows):
        """Nepostojeća adresa (nema nijedne transakcije u grafu) diže ValueError, koji ruta
        pretvara u 404 - ne vraća se tiho prazan rezultat"""
        graph = graph_from_rows([('0xA', '0xB', 1.0, '2026-01-05T08:00:00Z')])

        with pytest.raises(ValueError):
            analyze_time_of_day(graph, '0xZZZ')

    def test_address_match_is_exact_not_case_insensitive(self, graph_from_rows):
        """Adresa se traži tačnim poklapanjem sa ID-jem čvora u grafu, isto kao
        path_finding.find_transaction_paths - druga velika/mala slova nisu isti čvor"""
        graph = graph_from_rows([('0xAbC', '0xB', 1.0, '2026-01-05T08:00:00Z')])

        with pytest.raises(ValueError):
            analyze_time_of_day(graph, '0xabc')
