"""Rule-based seed suggestion for taint analysis.

Replaces the previous approach, which asked an outlier detector for "the 5% most unusual
addresses". That was measurably wrong for this purpose: on data with a known answer it
found 1 of 4 true origins (the thieves each make a single outgoing transfer, which is the
least unusual behaviour there is), while the addresses it did surface were mostly the
highest-volume ones - on real Ethereum data, exchanges and contracts.

The rules here work the other way round: nothing is suggested unless a named check matches
it, and every suggestion carries the reason in plain language. If no rule matches, nothing
is suggested and the caller is told so - which is a correct answer, unlike a list of
addresses picked to fill a quota.

Suggestions are split by role, because the two are not interchangeable:
  * ORIGIN candidates    - defensible starting points for taint analysis
  * LAUNDERING points    - mixers, relays, pass-through wallets. Important findings, but
                           seeding one of these as "the origin" would mark its own
                           legitimate outflows as tainted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

import networkx as nx
import pandas as pd

from app.services.address_enrichment import get_known_entity


# --- Rule thresholds. Stated as constants (and echoed to the UI) so a reader can tell
# --- exactly what was and was not checked, instead of trusting an opaque score.
PASS_THROUGH_MIN_RATIO = 0.90      # forwards at least 90% of everything it received...
PASS_THROUGH_MAX_RATIO = 1.10      # ...but not far more (that is spending its own balance)
PASS_THROUGH_MAX_MINUTES = 60      # and the first forward follows a receipt within an hour
DORMANCY_MIN_DAYS = 90             # inactive for at least three months...
STRUCTURING_MIN_COUNT = 5          # ...then at least 5 near-identical outgoing transfers
STRUCTURING_TOLERANCE = 0.10       # "near-identical" = within 10% of the median
STRUCTURING_WINDOW_HOURS = 24


def _node_transactions(graph: nx.DiGraph) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Per-address incoming/outgoing transaction lists, time-ordered."""
    incoming: dict[str, list[dict]] = defaultdict(list)
    outgoing: dict[str, list[dict]] = defaultdict(list)

    for source, target, attrs in graph.edges(data=True):
        transactions = attrs.get('transactions') or [
            {'amount': attrs.get('total_amount', 0.0), 'timestamp': attrs.get('first_seen')}
        ]
        for tx in transactions:
            timestamp = pd.to_datetime(tx.get('timestamp'), utc=True, errors='coerce')
            if pd.isna(timestamp):
                continue
            record = {
                'amount': float(tx.get('amount', 0.0) or 0.0),
                'timestamp': timestamp,
                'counterparty': target,
            }
            outgoing[str(source)].append({**record, 'counterparty': str(target)})
            incoming[str(target)].append({**record, 'counterparty': str(source)})

    for bucket in (incoming, outgoing):
        for entries in bucket.values():
            entries.sort(key=lambda item: item['timestamp'])
    return incoming, outgoing


def _find_pass_through(incoming: list[dict], outgoing: list[dict]) -> str | None:
    """Money in, nearly all of it straight back out - the signature of a relay/mule wallet
    rather than somewhere funds actually belong.

    Measured as RETENTION over the whole evidence (what came in vs what went out), not as
    a sum inside a time window: a window also sweeps up funds received earlier, which
    produced impossible figures like "forwarded 150% of what arrived". The upper bound
    matters as much as the lower one - an address that sends far more than it received is
    spending its own balance, which is not pass-through behaviour.
    """
    total_in = sum(tx['amount'] for tx in incoming)
    total_out = sum(tx['amount'] for tx in outgoing)
    if total_in <= 0 or total_out <= 0:
        return None

    ratio = total_out / total_in
    if not (PASS_THROUGH_MIN_RATIO <= ratio <= PASS_THROUGH_MAX_RATIO):
        return None

    # The "fast" half of the rule: at least one receipt must be followed by an outgoing
    # transfer shortly after, otherwise this is just an address that happens to have
    # emptied out eventually.
    fastest_minutes: float | None = None
    for received in incoming:
        later = [tx for tx in outgoing if tx['timestamp'] >= received['timestamp']]
        if not later:
            continue
        gap = (later[0]['timestamp'] - received['timestamp']).total_seconds() / 60
        if fastest_minutes is None or gap < fastest_minutes:
            fastest_minutes = gap
    if fastest_minutes is None or fastest_minutes > PASS_THROUGH_MAX_MINUTES:
        return None

    retained = (total_in - total_out) / total_in * 100
    return (
        f'Brzi prolaz — primljeno {total_in:.4f}, prosleđeno {total_out:.4f} '
        f'(zadržano {retained:.1f}%), najbrže prosleđivanje za {int(fastest_minutes)} min'
    )


def _find_dormancy(activity: list[dict]) -> str | None:
    """A long-dormant address that suddenly moves funds - classic for a wallet parked
    after a theft and reactivated once attention faded."""
    if len(activity) < 2:
        return None
    stamps = sorted(item['timestamp'] for item in activity)
    for earlier, later in zip(stamps, stamps[1:]):
        gap_days = (later - earlier).total_seconds() / 86400
        if gap_days >= DORMANCY_MIN_DAYS:
            return (
                f'Buđenje uspavane adrese — {int(gap_days)} dana bez aktivnosti, '
                f'pa ponovna aktivnost {later.strftime("%d.%m.%Y.")}'
            )
    return None


def _find_structuring(outgoing: list[dict]) -> str | None:
    """Many near-identical outgoing transfers in a short window - splitting one sum into
    pieces small enough to avoid attention."""
    if len(outgoing) < STRUCTURING_MIN_COUNT:
        return None
    for index, anchor in enumerate(outgoing):
        window_end = anchor['timestamp'] + timedelta(hours=STRUCTURING_WINDOW_HOURS)
        window = [tx for tx in outgoing[index:] if tx['timestamp'] <= window_end and tx['amount'] > 0]
        if len(window) < STRUCTURING_MIN_COUNT:
            continue
        amounts = sorted(tx['amount'] for tx in window)
        median = amounts[len(amounts) // 2]
        if median <= 0:
            continue
        similar = [amount for amount in amounts if abs(amount - median) / median <= STRUCTURING_TOLERANCE]
        if len(similar) >= STRUCTURING_MIN_COUNT:
            return (
                f'Usitnjavanje — {len(similar)} sličnih odliva (~{median:.4f}) '
                f'u roku od {STRUCTURING_WINDOW_HOURS}h'
            )
    return None


def _coverage_note(graph: nx.DiGraph) -> str | None:
    """Explains an empty result that is caused by the SHAPE of the evidence rather than by
    the data being clean.

    A single-address history pull ("all transactions of address X") returns X's
    counterparties but not what those counterparties did next. Nobody in it both receives
    and forwards, so chain-based rules cannot match no matter how the thresholds are set.
    Reporting a bare "nothing found" there would be technically true and practically
    misleading - the analyst would conclude the funds are clean instead of realising the
    evidence cannot answer the question.
    """
    if graph.number_of_nodes() < 3:
        return None
    relays = [node for node in graph.nodes if graph.in_degree(node) > 0 and graph.out_degree(node) > 0]
    if relays:
        return None
    return (
        'Ova evidencija je izvučena kao istorija jedne adrese: nijedna adresa u njoj i prima i šalje '
        'sredstva. Zato se obrasci koji prate kretanje novca (brzi prolaz, peel chain, usitnjavanje) '
        'po strukturi podataka ne mogu pojaviti — bez obzira na to da li pranja ima ili nema. '
        'Da bi se oni uopšte mogli tražiti, potrebno je proširiti evidenciju (režim "proširi pošiljaoce" '
        'pri povlačenju sa blockchain-a) ili koristiti kombinovani prikaz svih dokaza u slučaju.'
    )


def suggest_seeds(graph: nx.DiGraph) -> dict[str, Any]:
    """Runs every rule over the graph and returns categorised, explained suggestions."""
    incoming, outgoing = _node_transactions(graph)

    origins: dict[str, list[str]] = defaultdict(list)
    laundering: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    for node in graph.nodes:
        address = str(node)
        attrs = graph.nodes[node]
        node_in = incoming.get(address, [])
        node_out = outgoing.get(address, [])

        # --- Origin candidates: definitive, externally established facts only ---
        if attrs.get('blacklist_flag'):
            origins[address].append('Na crnoj listi predmeta')
            counts['blacklist'] += 1

        entity = get_known_entity(address)
        if entity and entity.get('category') == 'sanctioned':
            origins[address].append(f'OFAC sankcionisana adresa: {entity.get("name", "")}'.strip(': '))
            counts['sanctioned'] += 1

        # --- Laundering points: patterns with a written definition ---
        if entity and entity.get('category') == 'mixer':
            laundering[address].append(f'Poznat mikser: {entity.get("name", "")}'.strip(': '))
            counts['mixer'] += 1

        if attrs.get('peel_chain_flag'):
            laundering[address].append(f'Peel chain (korak {attrs.get("peel_chain_step", "?")})')
            counts['peel_chain'] += 1

        if attrs.get('chain_hop_flag'):
            laundering[address].append('Chain hopping — prebacivanje između mreža')
            counts['chain_hop'] += 1

        pass_through = _find_pass_through(node_in, node_out)
        if pass_through:
            laundering[address].append(pass_through)
            counts['pass_through'] += 1

        dormancy = _find_dormancy(node_in + node_out)
        if dormancy:
            laundering[address].append(dormancy)
            counts['dormancy'] += 1

        structuring = _find_structuring(node_out)
        if structuring:
            laundering[address].append(structuring)
            counts['structuring'] += 1

    # An address that is both a confirmed origin and shows laundering behaviour belongs in
    # the origin list only - otherwise the analyst sees it twice and the stronger finding
    # gets diluted by the weaker one.
    for address in origins:
        laundering.pop(address, None)

    checks = [
        ('blacklist', 'Crna lista predmeta', 'Adresa je označena kao poznato zlonamerna u samim podacima slučaja.', 'origin'),
        ('sanctioned', 'OFAC sankcionisane adrese', 'Poklapanje sa lokalnom bazom sankcionisanih adresa.', 'origin'),
        ('mixer', 'Poznati mikseri', 'Poklapanje sa bazom poznatih miksera (Tornado.Cash i sl.).', 'laundering'),
        ('peel_chain', 'Peel chain', 'Lanac u kome se pri svakom koraku odvaja mali deo, a ostatak ide dalje.', 'laundering'),
        ('chain_hop', 'Chain hopping', 'Prebacivanje sredstava između različitih mreža.', 'laundering'),
        ('pass_through', 'Brzi prolaz',
         f'Prosleđeno {int(PASS_THROUGH_MIN_RATIO * 100)}–{int(PASS_THROUGH_MAX_RATIO * 100)}% ukupno '
         f'primljenog (adresa gotovo ništa nije zadržala), uz prosleđivanje u roku od '
         f'{PASS_THROUGH_MAX_MINUTES} min od priliva.', 'laundering'),
        ('dormancy', 'Buđenje uspavane adrese',
         f'Najmanje {DORMANCY_MIN_DAYS} dana bez aktivnosti, pa ponovna aktivnost.', 'laundering'),
        ('structuring', 'Usitnjavanje',
         f'Najmanje {STRUCTURING_MIN_COUNT} sličnih odliva (±{int(STRUCTURING_TOLERANCE * 100)}%) '
         f'u roku od {STRUCTURING_WINDOW_HOURS}h.', 'laundering'),
    ]

    return {
        'origin_candidates': [
            {'address': address, 'reasons': reasons}
            for address, reasons in sorted(origins.items(), key=lambda item: -len(item[1]))
        ],
        'laundering_points': [
            {'address': address, 'reasons': reasons}
            for address, reasons in sorted(laundering.items(), key=lambda item: -len(item[1]))
        ],
        'checks_performed': [
            {'id': check_id, 'label': label, 'description': description, 'category': category,
             'matches': counts.get(check_id, 0)}
            for check_id, label, description, category in checks
        ],
        'total_addresses': graph.number_of_nodes(),
        'coverage_note': _coverage_note(graph),
    }
