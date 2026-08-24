from __future__ import annotations

from typing import Any

# Heuristic UTC-offset / broad-region compatibility estimate, layered ON TOP OF Behavioral
# Analysis's own hourly_distribution (see behavioral_analysis.py) - a SEPARATE, optional
# function, not a change to analyze_time_of_day() itself. The route composes both (see
# api/routes/cases.py, get_case_behavioral_analysis).
#
# ============================================================================================
# WHAT THIS DOES AND DOES NOT CLAIM (read before touching the thresholds below)
# ============================================================================================
# A blockchain timestamp is not physical-location evidence. This module never asserts where
# an address's owner IS - it only reports which UTC-offset range would make the OBSERVED
# hour-of-day pattern consistent with a human transacting mostly while awake, in that
# offset's local time. That is a claim about ARITHMETIC COMPATIBILITY between a timestamp
# pattern and a clock, not a claim about geography or identity. Every result that reaches
# the API carries `disclaimer` verbatim for this reason, and every UI/report string that
# surfaces this feature MUST use "is compatible with" phrasing ("Obrazac aktivnosti je
# kompatibilan sa regionom X"), never "is located in" / "vlasnik se nalazi u X".
# ============================================================================================

# Local hours treated as "asleep, unlikely to be transacting" - the one modeling assumption
# the whole heuristic rests on. 6 hours = 25% of the day, so a perfectly uniform (random)
# activity pattern would already put ~25% of its activity in this window for ANY offset -
# NIGHT_FRACTION_COMPATIBILITY_THRESHOLD below is set well under that, so passing it takes
# an actual pattern, not chance.
NIGHT_LOCAL_HOURS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)

# An offset is "compatible" only if at most this fraction of the address's transactions
# fall inside its local night window - i.e. the pattern demonstrably avoids that offset's
# night, not merely doesn't concentrate IN it.
NIGHT_FRACTION_COMPATIBILITY_THRESHOLD = 0.15

# Below this many transactions, no estimate is attempted at all - with few data points a
# low night-fraction is easily chance (e.g. 5 transactions that happen to avoid one 6-hour
# window is unremarkable), not a real signal.
MIN_TRANSACTIONS_FOR_ESTIMATE = 8

# Confidence tiers: BOTH the strength of the pattern (how far below the compatibility
# threshold the best-fitting offset's night-fraction actually sits) AND the sample size
# have to clear their bar - a clean-looking pattern from 9 transactions is not "High"
# confidence, and 500 transactions spread with 14% still in someone's night is not either.
HIGH_CONFIDENCE_NIGHT_FRACTION = 0.05
HIGH_CONFIDENCE_MIN_TRANSACTIONS = 20
MEDIUM_CONFIDENCE_NIGHT_FRACTION = 0.10
MEDIUM_CONFIDENCE_MIN_TRANSACTIONS = 12

INSUFFICIENT_DATA_MESSAGE = 'Insufficient data for reliable timezone inference.'

DISCLAIMER = (
    'Vremenski obrazac predstavlja heuristički indikator i ne predstavlja dokaz stvarne '
    'lokacije vlasnika adrese.'
)

# Coarse UTC-offset -> broad region(s) lookup. Deliberately CONTINENT/broad-region level
# only (never a country) - and deliberately many-to-many: a real offset genuinely spans
# multiple regions (UTC+2 is both Cairo and Helsinki; UTC+8 is both Beijing and Perth), so
# an offset is allowed to resolve to more than one region rather than picking one
# arbitrarily. This is a coarse simplification, documented as such in BEHAVIORAL-ANALIZA.md.
REGIONS_BY_UTC_OFFSET: dict[int, tuple[str, ...]] = {
    -11: ('Oceania',),
    -10: ('Oceania', 'North America'),
    -9: ('North America',),
    -8: ('North America',),
    -7: ('North America',),
    -6: ('North America',),
    -5: ('North America', 'South America'),
    -4: ('South America',),
    -3: ('South America',),
    -2: ('South America',),
    -1: ('Europe', 'Africa'),
    0: ('Europe', 'Africa'),
    1: ('Europe', 'Africa'),
    2: ('Europe', 'Africa', 'Middle East'),
    3: ('Africa', 'Middle East', 'Europe'),
    4: ('Middle East', 'Asia'),
    5: ('Asia',),
    6: ('Asia',),
    7: ('Asia',),
    8: ('Asia', 'Oceania'),
    9: ('Asia',),
    10: ('Oceania',),
    11: ('Oceania',),
    12: ('Oceania',),
}

# Whole-hour offsets only (first version) - half-hour zones (India UTC+5:30, etc.) are not
# distinguished; see BEHAVIORAL-ANALIZA.md limitations.
_CANDIDATE_OFFSETS: tuple[int, ...] = tuple(range(-11, 13))


def _format_utc_offset(offset: int) -> str:
    sign = '+' if offset >= 0 else '-'
    return f'UTC{sign}{abs(offset)}'


def _format_offset_range(min_offset: int, max_offset: int) -> str:
    if min_offset == max_offset:
        return _format_utc_offset(min_offset)
    return f'{_format_utc_offset(min_offset)} – {_format_utc_offset(max_offset)}'


def _night_fraction_for_offset(hourly_distribution: dict[str, int], total_transactions: int, offset: int) -> float:
    """Fraction of this address's transactions that fall inside `offset`'s local night
    (NIGHT_LOCAL_HOURS). A UTC hour `u` is night for `offset` iff `(u + offset) % 24` is in
    NIGHT_LOCAL_HOURS - so the local-hour-0..5 set maps back to a 6-hour band of UTC hours
    starting at `(-offset) % 24`."""
    night_utc_hours = {(local_hour - offset) % 24 for local_hour in NIGHT_LOCAL_HOURS}
    night_count = sum(hourly_distribution.get(f'{hour:02d}', 0) for hour in night_utc_hours)
    return night_count / total_transactions


def _confidence_label(best_night_fraction: float, total_transactions: int) -> str:
    if best_night_fraction <= HIGH_CONFIDENCE_NIGHT_FRACTION and total_transactions >= HIGH_CONFIDENCE_MIN_TRANSACTIONS:
        return 'High'
    if best_night_fraction <= MEDIUM_CONFIDENCE_NIGHT_FRACTION and total_transactions >= MEDIUM_CONFIDENCE_MIN_TRANSACTIONS:
        return 'Medium'
    return 'Low'


def _unavailable(reason: str) -> dict[str, Any]:
    return {'available': False, 'reason': reason, 'message': INSUFFICIENT_DATA_MESSAGE}


def estimate_timezone_compatibility(hourly_distribution: dict[str, int], total_transactions: int) -> dict[str, Any]:
    """Heuristic UTC-offset-range / broad-region compatibility estimate for one address's
    hour-of-day activity pattern. See the module docstring above for what this claim is and
    is not.

    `hourly_distribution` is the SAME zero-filled ("00".."23") dict analyze_time_of_day()
    already returns - this function does not re-read the graph or re-parse a single
    timestamp, it only re-aggregates numbers already computed.

    Two distinct situations both surface the same INSUFFICIENT_DATA_MESSAGE (the user-facing
    request for this feature asked for exactly one fallback string): too few transactions to
    say anything (`reason: 'insufficient_transactions'`), and enough transactions but no
    offset's local-night fraction clears the compatibility bar at all - e.g. activity spread
    close to uniformly across all 24 hours (`reason: 'no_compatible_offset'`). `reason` is
    kept in the response for tests/debugging even though the UI only ever shows `message`.
    """
    if total_transactions < MIN_TRANSACTIONS_FOR_ESTIMATE:
        return _unavailable('insufficient_transactions')

    offset_scores = {
        offset: _night_fraction_for_offset(hourly_distribution, total_transactions, offset) for offset in _CANDIDATE_OFFSETS
    }
    compatible_offsets = [
        offset for offset, night_fraction in offset_scores.items() if night_fraction <= NIGHT_FRACTION_COMPATIBILITY_THRESHOLD
    ]

    if not compatible_offsets:
        return _unavailable('no_compatible_offset')

    min_offset = min(compatible_offsets)
    max_offset = max(compatible_offsets)
    best_night_fraction = min(offset_scores[offset] for offset in compatible_offsets)
    possible_regions = sorted({region for offset in compatible_offsets for region in REGIONS_BY_UTC_OFFSET.get(offset, ())})

    return {
        'available': True,
        'utc_offset_min': min_offset,
        'utc_offset_max': max_offset,
        'utc_offset_range_label': _format_offset_range(min_offset, max_offset),
        'possible_regions': possible_regions,
        'confidence': _confidence_label(best_night_fraction, total_transactions),
        'best_night_fraction': round(best_night_fraction, 4),
        'disclaimer': DISCLAIMER,
    }
