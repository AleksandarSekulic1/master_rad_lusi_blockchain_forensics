"""Provera heurističke procene kompatibilnih vremenskih zona/regiona (nadogradnja
Behavioral Analysis rezultata - vidi behavioral_analysis.py za osnovnu raspodelu).

Cilj: dokazati da je procena zasnovana na CELOM obrascu aktivnosti (ne jednoj
transakciji), da nikad ne tvrdi fizičku lokaciju (samo "kompatibilno sa"), i da ima
eksplicitan, dokumentovan prag ispod kog se ništa ne procenjuje.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from app.analytics.timezone_heuristics import (
    INSUFFICIENT_DATA_MESSAGE,
    MIN_TRANSACTIONS_FOR_ESTIMATE,
    _format_offset_range,
    estimate_timezone_compatibility,
)


def _empty_hourly() -> dict[str, int]:
    return {f'{hour:02d}': 0 for hour in range(24)}


def _hourly_from_counts(counts: dict[str, int]) -> dict[str, int]:
    hourly = _empty_hourly()
    hourly.update(counts)
    return hourly


class TestInsufficientData:
    """Nedovoljno podataka"""

    def test_below_minimum_transaction_count_returns_insufficient_data(self):
        """Manje transakcija od praga vraća 'Insufficient data', bez pokušaja procene"""
        hourly = _hourly_from_counts({'10': MIN_TRANSACTIONS_FOR_ESTIMATE - 1})

        result = estimate_timezone_compatibility(hourly, MIN_TRANSACTIONS_FOR_ESTIMATE - 1)

        assert result == {
            'available': False,
            'reason': 'insufficient_transactions',
            'message': INSUFFICIENT_DATA_MESSAGE,
        }

    def test_uniform_activity_across_all_hours_returns_insufficient_data(self):
        """Ravnomerno raspoređena aktivnost (bez ijednog izraženog obrasca) ne dobija
        nijedan kompatibilan opseg - dovoljno podataka, ali nema signala"""
        hourly = _hourly_from_counts({f'{hour:02d}': 2 for hour in range(24)})  # 48 ukupno, ravnomerno

        result = estimate_timezone_compatibility(hourly, 48)

        assert result['available'] is False
        assert result['reason'] == 'no_compatible_offset'
        assert result['message'] == INSUFFICIENT_DATA_MESSAGE

    def test_exact_message_matches_required_fallback_string(self):
        """Poruka je tačno zahtevani string, karakter po karakter"""
        result = estimate_timezone_compatibility(_empty_hourly(), 0)

        assert result['message'] == 'Insufficient data for reliable timezone inference.'


class TestCompatibleEstimate:
    """Procena kompatibilnog opsega"""

    def test_activity_concentrated_in_utc_daytime_is_compatible_with_utc0(self):
        """Aktivnost skoncentrisana u UTC 08-20h (tipičan radni dan) je kompatibilna sa UTC+0,
        pošto tih 12h ne dodiruje 'noćni' prozor 00-05h lokalno za taj offset"""
        hourly = _hourly_from_counts({f'{hour:02d}': 3 for hour in range(8, 20)})  # 12 sati x 3 = 36

        result = estimate_timezone_compatibility(hourly, 36)

        assert result['available'] is True
        assert result['utc_offset_min'] <= 0 <= result['utc_offset_max']

    def test_offset_range_label_omits_dash_when_min_equals_max(self):
        """Kad je opseg jedan jedini offset, prikazuje se bez crte (npr. 'UTC+3', ne 'UTC+3 – UTC+3')"""
        assert _format_offset_range(3, 3) == 'UTC+3'

    def test_offset_range_label_uses_dash_when_min_differs_from_max(self):
        """Kad opseg pokriva više od jednog offseta, prikazuje se 'UTC+X – UTC+Y'"""
        assert _format_offset_range(5, 8) == 'UTC+5 – UTC+8'

    def test_wide_active_window_yields_multiple_compatible_offsets(self):
        """Širi obrazac aktivnosti (18 od 24 sata) je kompatibilan sa VIŠE susednih offseta -
        obrazac ne mora biti idealno uzan da bi dao rezultat, samo mora da izbegava dovoljno
        noćnih prozora"""
        hourly = _hourly_from_counts({f'{hour:02d}': 2 for hour in range(3, 21)})  # UTC 03-20h, 18h x 2 = 36

        result = estimate_timezone_compatibility(hourly, 36)

        assert result['available'] is True
        assert result['utc_offset_max'] > result['utc_offset_min']
        assert '–' in result['utc_offset_range_label']

    def test_offset_range_label_formats_negative_offsets_with_minus_sign(self):
        """Negativni offset se prikazuje kao 'UTC-N', ne 'UTC+-N' ili slično"""
        # Aktivnost u UTC 12-23h - noćni prozor za negativne offsete (npr -5: lokalno 00-05h
        # = UTC 05-10h) se ne poklapa sa ovim opsegom.
        hourly = _hourly_from_counts({f'{hour:02d}': 2 for hour in range(12, 24)})  # 12h x 2 = 24

        result = estimate_timezone_compatibility(hourly, 24)

        assert result['available'] is True
        assert result['utc_offset_min'] < 0
        assert result['utc_offset_range_label'].split(' – ')[0].startswith('UTC-')

    def test_multiple_compatible_regions_are_all_listed_and_deduplicated(self):
        """Kad kompatibilan opseg pokriva offsete sa preklapajućim regionima (npr. Azija i
        Okeanija na UTC+8), svi relevantni regioni se prikazuju, bez duplikata"""
        # Aktivnost u UTC 00-11h je kompatibilna i sa +5..+8 opsegom (Azija/Okeanija) - vidi
        # BEHAVIORAL-ANALIZA.md §... za stvarni demo primer preko pravih podataka.
        hourly = _hourly_from_counts({f'{hour:02d}': 2 for hour in range(0, 12)})  # UTC 00-11h

        result = estimate_timezone_compatibility(hourly, 24)

        assert result['available'] is True
        assert len(result['possible_regions']) == len(set(result['possible_regions']))
        assert result['possible_regions'] == sorted(result['possible_regions'])

    def test_response_always_carries_the_compatibility_disclaimer(self):
        """Svaki DOSTUPAN rezultat nosi tačno propisanu napomenu o ograničenju"""
        hourly = _hourly_from_counts({f'{hour:02d}': 3 for hour in range(8, 20)})

        result = estimate_timezone_compatibility(hourly, 36)

        assert result['available'] is True
        assert result['disclaimer'] == (
            'Vremenski obrazac predstavlja heuristički indikator i ne predstavlja dokaz '
            'stvarne lokacije vlasnika adrese.'
        )


class TestConfidence:
    """Nivo pouzdanosti"""

    def test_strong_pattern_with_large_sample_is_high_confidence(self):
        """Aktivnost potpuno izvan noćnog prozora, sa velikim uzorkom -> High"""
        hourly = _hourly_from_counts({f'{hour:02d}': 2 for hour in range(8, 20)})  # 24 transakcije, 0% u noći bilo kog kompatibilnog offseta

        result = estimate_timezone_compatibility(hourly, 24)

        assert result['confidence'] == 'High'

    def test_borderline_pattern_with_small_sample_is_low_confidence(self):
        """Obrazac koji jedva prolazi prag, sa malim (ali dovoljnim) uzorkom -> Low"""
        # 8 transakcija (tačno prag) je najmanji uzorak koji uopšte dobija procenu - čak i uz
        # savršen obrazac (0% u noći), premali uzorak ne dostiže Medium/High prag.
        hourly = _hourly_from_counts({f'{hour:02d}': 1 for hour in range(8, 16)})  # 8 transakcija

        result = estimate_timezone_compatibility(hourly, MIN_TRANSACTIONS_FOR_ESTIMATE)

        assert result['available'] is True
        assert result['confidence'] == 'Low'


class TestNoLocationClaim:
    """Nikad ne tvrdi fizičku lokaciju"""

    def test_disclaimer_never_omitted_when_available(self):
        """'disclaimer' polje postoji u SVAKOM dostupnom rezultatu - UI ne sme moći da ga
        preskoči jer backend prosto nije poslao"""
        hourly = _hourly_from_counts({f'{hour:02d}': 3 for hour in range(8, 20)})

        result = estimate_timezone_compatibility(hourly, 36)

        assert 'disclaimer' in result and result['disclaimer']

    def test_message_and_disclaimer_never_claim_residency(self):
        """Nijedan string koji funkcija vraća ne sadrži formulaciju 'nalazi se'/'located in' -
        samo 'kompatibilan'/'compatible'"""
        available_result = estimate_timezone_compatibility(_hourly_from_counts({f'{h:02d}': 3 for h in range(8, 20)}), 36)
        unavailable_result = estimate_timezone_compatibility(_empty_hourly(), 0)

        for text in (available_result['disclaimer'], unavailable_result['message']):
            assert 'nalazi se' not in text.lower()
            assert 'located in' not in text.lower()
