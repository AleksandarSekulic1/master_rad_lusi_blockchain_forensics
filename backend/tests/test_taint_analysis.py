"""Correctness tests for the proportional ("haircut") taint algorithm.

The percentage this module produces is the tool's central claim - it ends up in exported
reports and, in a real investigation, in front of people who cannot read the source. These
tests pin down the behaviour that claim rests on, so a future refactor that silently
changes the maths fails here instead of in a report.

Numbers used here deliberately match the documented scenarios in BLOCKCHAIN-UVOZ.md
(sections 6.1 and 8.x), so the docs and the test suite cannot drift apart unnoticed.
"""

from __future__ import annotations

import pytest

from app.analytics.plugins.taint_analysis import run_taint_analysis


def percentages(result: dict) -> dict[str, float]:
    return {item['address']: item['taint_percentage'] for item in result['results']}


class TestHaircutDilution:
    """The core promise: a clean inflow lowers the percentage proportionally."""

    def test_clean_inflow_dilutes_percentage(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        # 1000 tainted of 1500 total = 66.67%, the exact figure documented in 6.1.
        assert percentages(result)['0xMixer'] == pytest.approx(66.67, abs=0.01)

    def test_outflow_does_not_change_percentage(self, graph_from_rows):
        """Sending money out carries tainted and clean funds away in the same ratio, so
        the sender's own percentage is unchanged - the defining property of the haircut
        model, as opposed to "poison" models where any contact stays 100% forever."""
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
            ('0xMixer', '0xExitWallet', 750, '2026-03-01T00:10:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        percentage_by_address = percentages(result)

        assert percentage_by_address['0xMixer'] == pytest.approx(66.67, abs=0.01)
        # The recipient inherits the sender's mix, not the sender's origin.
        assert percentage_by_address['0xExitWallet'] == pytest.approx(66.67, abs=0.01)

    def test_untouched_address_stays_clean(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xStranger', '0xOtherParty', 400, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert percentages(result)['0xOtherParty'] == 0


class TestPerSeedAttribution:
    """Not just "how dirty", but "whose dirt" - the per-source breakdown."""

    def test_two_seeds_split_proportionally(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xHacker1', '0xLaunderingHub', 600, '2026-04-01T00:00:00Z'),
            ('0xHacker2', '0xLaunderingHub', 400, '2026-04-01T00:05:00Z'),
        ])

        result = run_taint_analysis(
            graph=graph,
            seed_addresses=['0xHacker1', '0xHacker2'],
            seed_from_blacklist=False,
        )
        hub = next(item for item in result['results'] if item['address'] == '0xLaunderingHub')

        assert hub['taint_percentage'] == pytest.approx(100.0, abs=0.01)
        assert hub['taint_by_source']['0xHacker1'] == pytest.approx(60.0, abs=0.01)
        assert hub['taint_by_source']['0xHacker2'] == pytest.approx(40.0, abs=0.01)

    def test_breakdown_survives_a_further_hop(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xHacker1', '0xLaunderingHub', 600, '2026-04-01T00:00:00Z'),
            ('0xHacker2', '0xLaunderingHub', 400, '2026-04-01T00:05:00Z'),
            ('0xLaunderingHub', '0xFinalDestination', 800, '2026-04-01T00:10:00Z'),
        ])

        result = run_taint_analysis(
            graph=graph,
            seed_addresses=['0xHacker1', '0xHacker2'],
            seed_from_blacklist=False,
        )
        destination = next(item for item in result['results'] if item['address'] == '0xFinalDestination')

        # The 60/40 mix travels with the money instead of collapsing into one number.
        assert destination['taint_by_source']['0xHacker1'] == pytest.approx(60.0, abs=0.01)
        assert destination['taint_by_source']['0xHacker2'] == pytest.approx(40.0, abs=0.01)

    def test_single_seed_attributes_everything_to_itself(self, graph_from_rows):
        graph = graph_from_rows([('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        mixer = next(item for item in result['results'] if item['address'] == '0xMixer')

        assert mixer['taint_by_source'] == {'0xThief': pytest.approx(100.0, abs=0.01)}


class TestTimelineSeries:
    """What the timeline scrubber replays - including the per-rank source breakdown the
    "Filter po izvoru" feature needs to stay accurate mid-scrub (see 8.6)."""

    def test_series_records_percentage_after_each_event(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        series = result['node_taint_series']['0xMixer']

        assert [entry['taint_percentage'] for entry in series] == [
            pytest.approx(100.0, abs=0.01),
            pytest.approx(66.67, abs=0.01),
        ]

    def test_every_series_entry_carries_its_own_source_breakdown(self, graph_from_rows):
        """Regression guard: the breakdown used to exist only as a final snapshot, which
        made the per-seed filter silently wrong while scrubbing the timeline."""
        graph = graph_from_rows([
            ('0xHacker1', '0xLaunderingHub', 600, '2026-04-01T00:00:00Z'),
            ('0xHacker2', '0xLaunderingHub', 400, '2026-04-01T00:05:00Z'),
        ])

        result = run_taint_analysis(
            graph=graph,
            seed_addresses=['0xHacker1', '0xHacker2'],
            seed_from_blacklist=False,
        )
        series = result['node_taint_series']['0xLaunderingHub']

        assert all('taint_by_source' in entry for entry in series)
        # As of rank 1 only Hacker1's money has arrived...
        assert series[0]['taint_by_source'] == {'0xHacker1': pytest.approx(100.0, abs=0.01)}
        # ...and only after rank 2 does it become the 60/40 mix.
        assert series[1]['taint_by_source']['0xHacker1'] == pytest.approx(60.0, abs=0.01)
        assert series[1]['taint_by_source']['0xHacker2'] == pytest.approx(40.0, abs=0.01)

    def test_series_includes_direction_and_counterparty(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xMixer', '0xExitWallet', 400, '2026-03-01T00:10:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        series = result['node_taint_series']['0xMixer']

        assert [entry['direction'] for entry in series] == ['in', 'out']
        assert [entry['counterparty'] for entry in series] == ['0xThief', '0xExitWallet']


class TestChronology:
    """Taint follows real time across the whole graph, not edge by edge."""

    def test_events_are_ordered_across_different_edges(self, graph_from_rows):
        """The clean inflow is listed last but happened FIRST. Processing edge-by-edge
        would dilute after the fact and produce a different (wrong) percentage."""
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:05:00Z'),
            ('0xCleanUser', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        series = result['node_taint_series']['0xMixer']

        assert [entry['counterparty'] for entry in series] == ['0xCleanUser', '0xThief']
        assert percentages(result)['0xMixer'] == pytest.approx(50.0, abs=0.01)

    def test_zero_amount_transactions_are_dropped(self, graph_from_rows):
        """Dust/contract-interaction transfers change no balance, so they must not occupy
        a timeline position where scrubbing appears to do nothing."""
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xNoise', '0xMixer', 0, '2026-03-01T00:02:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert result['timeline_max_rank'] == 1

    def test_repeated_transfers_on_one_edge_each_get_a_rank(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 500, '2026-03-01T00:00:00Z'),
            ('0xThief', '0xMixer', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert result['timeline_max_rank'] == 2


class TestSeedBehaviour:
    def test_seed_is_fully_tainted(self, graph_from_rows):
        graph = graph_from_rows([('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        thief = next(item for item in result['results'] if item['address'] == '0xThief')

        assert thief['is_taint_seed'] is True
        assert thief['taint_percentage'] == pytest.approx(100.0, abs=0.01)

    def test_seed_reinjects_full_taint_on_incoming_funds(self, graph_from_rows):
        """A seed receiving fresh money is a new injection attributed to itself - the same
        actor being funded again, not a dilution of the previous mix."""
        graph = graph_from_rows([
            ('0xCleanUser', '0xThief', 1000, '2026-03-01T00:00:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert percentages(result)['0xThief'] == pytest.approx(100.0, abs=0.01)

    def test_blacklist_flag_seeds_automatically(self, graph_from_rows):
        graph = graph_from_rows([('0xBadActor', '0xMule', 300, '2026-03-01T00:00:00Z')])
        graph.nodes['0xBadActor']['blacklist_flag'] = True

        result = run_taint_analysis(graph=graph, seed_addresses=None, seed_from_blacklist=True)

        assert result['seed_addresses'] == ['0xBadActor']
        assert percentages(result)['0xMule'] == pytest.approx(100.0, abs=0.01)

    def test_no_seeds_means_no_taint(self, graph_from_rows):
        graph = graph_from_rows([('0xA', '0xB', 100, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=[], seed_from_blacklist=False)

        assert result['tainted_node_count'] == 0


class TestTaintedHops:
    def test_hop_records_amount_actually_tainted(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
            ('0xMixer', '0xExitWallet', 750, '2026-03-01T00:10:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        hop = next(
            hop for hop in result['tainted_hops']
            if hop['source'] == '0xMixer' and hop['target'] == '0xExitWallet'
        )

        # 750 sent from a balance that is 66.67% dirty -> 500 of it is tainted.
        assert hop['tainted_amount'] == pytest.approx(500.0, abs=0.01)
        assert hop['taint_pct_at_hop'] == pytest.approx(66.67, abs=0.01)

    def test_clean_transfers_are_not_recorded_as_hops(self, graph_from_rows):
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xSomeoneElse', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        pairs = {(hop['source'], hop['target']) for hop in result['tainted_hops']}

        assert ('0xCleanUser', '0xSomeoneElse') not in pairs


def test_requires_a_graph():
    with pytest.raises(ValueError):
        run_taint_analysis(graph=None, seed_addresses=['0xThief'])
