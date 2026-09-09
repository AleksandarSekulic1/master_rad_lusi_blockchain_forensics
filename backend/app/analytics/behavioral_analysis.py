from __future__ import annotations

from typing import Any

import networkx as nx

from app.analytics.plugins.anomaly_detection import _parse_timestamp

# Behavioral / Time-of-Day Analysis: how one address's transactions (within a case's
# graph) distribute across UTC hour-of-day and day-of-week. First version - UTC only, no
# timezone/continent inference (that is a deliberately separate, later step).

DAY_NAMES: tuple[str, ...] = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
HOUR_KEYS: tuple[str, ...] = tuple(f'{hour:02d}' for hour in range(24))


def _address_transaction_timestamps(graph: nx.DiGraph, address: str) -> list[Any]:
    """Every transaction where `address` is the sender or the recipient (or both, for a
    self-transfer), read straight off the graph's own edge data - the same
    `transactions[]` list every other analytics module reads (see
    anomaly_detection._transaction_rows_from_graph).

    Iterating `graph.edges(data=True)` rather than in_edges/out_edges separately matters
    for self-transfers: a self-loop (address -> address) is a single edge in this
    DiGraph, so it is visited - and counted - exactly once here.
    """
    timestamps: list[Any] = []
    for source, target, attrs in graph.edges(data=True):
        if source != address and target != address:
            continue
        for transaction in attrs.get('transactions', []):
            timestamp = _parse_timestamp(transaction.get('timestamp'))
            if timestamp is not None:
                timestamps.append(timestamp)
    return timestamps


def _empty_hour_buckets() -> dict[str, int]:
    return {hour: 0 for hour in HOUR_KEYS}


def _pick_max(distribution: dict[str, int]) -> tuple[str | None, int]:
    """Picks the key with the highest count. Ties go to whichever key comes first in the
    dict's own (already fixed, ascending) iteration order - 00 before 23, Monday before
    Sunday - rather than depending on insertion happenstance, same tie-break discipline
    as path_finding.find_path_to_nearest_of. Returns (None, 0) when every bucket is 0
    (no timestamped transactions at all), so the caller can tell "no data" apart from
    "most active bucket happens to have 0 transactions" (impossible, but keeps the
    contract honest).
    """
    best_key: str | None = None
    best_count = 0
    for key, count in distribution.items():
        if best_key is None or count > best_count:
            best_key, best_count = key, count
    if best_count == 0:
        return None, 0
    return best_key, best_count


def _pick_peak_period(hour_by_day: dict[str, dict[str, int]]) -> dict[str, Any] | None:
    """Busiest single (day, hour) cell across the whole grid - a more specific claim than
    'most active hour' (summed across all days) or 'most active day' (summed across all
    hours) alone. None when every cell is 0."""
    best_day: str | None = None
    best_hour: str | None = None
    best_count = 0
    for day in DAY_NAMES:
        for hour in HOUR_KEYS:
            count = hour_by_day[day][hour]
            if best_day is None or count > best_count:
                best_day, best_hour, best_count = day, hour, count
    if best_count == 0:
        return None
    return {
        'day': best_day,
        'hour': best_hour,
        'count': best_count,
        'label': f'{best_day} {best_hour}:00 UTC',
    }


def analyze_time_of_day(graph: nx.DiGraph, address: str) -> dict[str, Any]:
    """Behavioral / Time-of-Day Analysis for one address: how its transactions (in this
    case's graph) distribute across UTC hour-of-day and day-of-week.

    Requires an EXACT address match against the graph's own node ids (same convention as
    path_finding.find_transaction_paths) - raises ValueError, which the route layer turns
    into a 404, rather than silently normalizing case. All bucketing is UTC - no
    timezone/continent inference.
    """
    if address not in graph.nodes:
        raise ValueError(f'Adresa nije pronađena u grafu: {address}')

    timestamps = _address_transaction_timestamps(graph, address)
    total_transactions = len(timestamps)

    hourly_distribution = _empty_hour_buckets()
    day_of_week_distribution = {day: 0 for day in DAY_NAMES}
    hour_by_day_distribution = {day: _empty_hour_buckets() for day in DAY_NAMES}

    for timestamp in timestamps:
        hour_key = f'{timestamp.hour:02d}'
        day_key = DAY_NAMES[timestamp.dayofweek]
        hourly_distribution[hour_key] += 1
        day_of_week_distribution[day_key] += 1
        hour_by_day_distribution[day_key][hour_key] += 1

    most_active_hour, most_active_hour_count = _pick_max(hourly_distribution)
    most_active_day, most_active_day_count = _pick_max(day_of_week_distribution)
    peak_period = _pick_peak_period(hour_by_day_distribution)

    return {
        'address': address,
        'total_transactions': total_transactions,
        'hourly_distribution': hourly_distribution,
        'day_of_week_distribution': day_of_week_distribution,
        'hour_by_day_distribution': hour_by_day_distribution,
        'stats': {
            'most_active_hour': most_active_hour,
            'most_active_hour_count': most_active_hour_count,
            'most_active_day': most_active_day,
            'most_active_day_count': most_active_day_count,
            'peak_period': peak_period,
            'total_analyzed_transactions': total_transactions,
        },
    }
