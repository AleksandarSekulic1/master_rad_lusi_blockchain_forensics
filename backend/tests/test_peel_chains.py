"""Provera detektora peel chain obrasca.

Peel chain je lanac u kome se pri svakom koraku odvoji mali deo, a ostatak ide dalje —
klasičan način da se veliki iznos „oljušti" u sitne isplate koje pojedinačno ne privlače
pažnju. Detektor hrani predlog čvorova (tačke pranja), pa njegova tačnost direktno utiče
na to šta se analitičaru nudi.

Ovi testovi su dodati zato što detektor ranije nije imao nijedan test — kada je na jednom
realnom slučaju vratio 0 lanaca, moralo se ručno istraživati da li je pokvaren ili takvih
lanaca prosto nema. Sada na to pitanje odgovara test.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from app.analytics.plugins.peel_chains import run_peel_chains


def chain_nodes(result: dict) -> list[list[str]]:
    """Adrese iz odgovora, svedene na mala slova.

    Detektor interno normalizuje adrese na mala slova (Ethereum adrese su u praksi
    mešovitog pisanja), pa `chains[].nodes` sadrži normalizovan oblik, dok graf zadržava
    originalni. Oznake na čvorovima se ipak postavljaju na prave ključeve — to proverava
    poseban test niže.
    """
    return [[str(node).lower() for node in chain['nodes']] for chain in result['chains']]


class TestPeelChainDetection:
    """Prepoznavanje lanca

    Detektor mora da pogodi pravi obrazac i da ne prijavljuje obične nizove transakcija.
    """

    def test_real_peel_chain_is_detected(self, graph_from_rows):
        """Pravi peel chain se prepoznaje

        Na svakom koraku se odvaja po 50 od glavnog toka, a ostatak (95%) ide dalje —
        tačno obrazac koji definicija opisuje.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xPeel1', 1000, '2026-03-01T00:00:00Z'),
            ('0xPeel1', '0xSmall1', 50, '2026-03-01T00:05:00Z'),
            ('0xPeel1', '0xPeel2', 950, '2026-03-01T00:06:00Z'),
            ('0xPeel2', '0xSmall2', 50, '2026-03-01T00:10:00Z'),
            ('0xPeel2', '0xPeel3', 900, '2026-03-01T00:11:00Z'),
        ])

        result = run_peel_chains(graph=graph)

        assert result['chain_count'] >= 1
        assert any('0xpeel1' in nodes and '0xpeel2' in nodes for nodes in chain_nodes(result))

    def test_chain_nodes_are_flagged_on_the_graph(self, graph_from_rows):
        """Čvorovi lanca se označavaju na grafu

        Oznaka `peel_chain_flag` je ono što predlog čvorova i bojenje grafa zapravo čitaju
        — bez nje bi nalaz postojao samo u odgovoru, a ne i u prikazu.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xPeel1', 1000, '2026-03-01T00:00:00Z'),
            ('0xPeel1', '0xSmall1', 50, '2026-03-01T00:05:00Z'),
            ('0xPeel1', '0xPeel2', 950, '2026-03-01T00:06:00Z'),
            ('0xPeel2', '0xSmall2', 50, '2026-03-01T00:10:00Z'),
            ('0xPeel2', '0xPeel3', 900, '2026-03-01T00:11:00Z'),
        ])

        run_peel_chains(graph=graph)

        assert graph.nodes['0xPeel1'].get('peel_chain_flag') is True
        assert graph.nodes['0xPeel1'].get('peel_chain_step') == 1

    def test_plain_forwarding_is_not_a_peel_chain(self, graph_from_rows):
        """Obično prosleđivanje nije peel chain

        Lanac u kome se ceo iznos prosleđuje dalje, bez odvajanja sitnih delova, ne
        odgovara obrascu — inače bi detektor prijavljivao svaki niz transakcija.
        """
        graph = graph_from_rows([
            ('0xA', '0xB', 1000, '2026-03-01T00:00:00Z'),
            ('0xB', '0xC', 1000, '2026-03-01T00:05:00Z'),
            ('0xC', '0xD', 1000, '2026-03-01T00:10:00Z'),
        ])

        assert run_peel_chains(graph=graph)['chain_count'] == 0

    def test_splitting_in_half_is_not_a_peel_chain(self, graph_from_rows):
        """Deljenje na pola nije peel chain

        Kod peel chain-a se odvaja MALI deo (do 35%); podela 50/50 je drugačiji obrazac i
        ne sme se prijaviti pod ovim imenom.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xSplit1', 1000, '2026-03-01T00:00:00Z'),
            ('0xSplit1', '0xHalfA', 500, '2026-03-01T00:05:00Z'),
            ('0xSplit1', '0xHalfB', 500, '2026-03-01T00:06:00Z'),
        ])

        assert run_peel_chains(graph=graph)['chain_count'] == 0

    def test_too_short_chain_is_not_reported(self, graph_from_rows):
        """Prekratak lanac se ne prijavljuje

        Jedan korak odvajanja može biti sasvim obična transakcija; obrazac postaje nalaz
        tek kad se ponovi kroz lanac.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xOne', 1000, '2026-03-01T00:00:00Z'),
            ('0xOne', '0xSmall', 50, '2026-03-01T00:05:00Z'),
            ('0xOne', '0xRest', 950, '2026-03-01T00:06:00Z'),
        ])

        assert run_peel_chains(graph=graph)['chain_count'] == 0

    def test_tiny_amounts_are_ignored(self, graph_from_rows):
        """Sitni iznosi se ignorišu

        Ista šema sa zanemarljivim iznosima nije pranje novca nego šum — postoji donja
        granica iznosa od kojeg se lanac uopšte prati.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xPeel1', 1, '2026-03-01T00:00:00Z'),
            ('0xPeel1', '0xSmall1', 0.05, '2026-03-01T00:05:00Z'),
            ('0xPeel1', '0xPeel2', 0.95, '2026-03-01T00:06:00Z'),
            ('0xPeel2', '0xSmall2', 0.05, '2026-03-01T00:10:00Z'),
            ('0xPeel2', '0xPeel3', 0.9, '2026-03-01T00:11:00Z'),
        ])

        assert run_peel_chains(graph=graph)['chain_count'] == 0

    def test_transfers_far_apart_in_time_do_not_form_a_chain(self, graph_from_rows):
        """Vremenski udaljeni transferi ne čine lanac

        Peel chain je automatizovan obrazac koji se odvija brzo. Prosleđivanje mesecima
        kasnije je druga vrsta ponašanja i ne sme se spojiti u isti lanac.
        """
        graph = graph_from_rows([
            ('0xOrigin', '0xPeel1', 1000, '2026-03-01T00:00:00Z'),
            ('0xPeel1', '0xSmall1', 50, '2026-06-01T00:00:00Z'),
            ('0xPeel1', '0xPeel2', 950, '2026-06-01T00:01:00Z'),
            ('0xPeel2', '0xSmall2', 50, '2026-09-01T00:00:00Z'),
            ('0xPeel2', '0xPeel3', 900, '2026-09-01T00:01:00Z'),
        ])

        assert run_peel_chains(graph=graph)['chain_count'] == 0

    def test_empty_graph_does_not_crash(self, graph_from_rows):
        """Prazan graf ne ruši detektor

        Detektor se pokreće u sklopu svake analize, pa mora bezbedno da prođe i kad nema
        nijedne transakcije.
        """
        result = run_peel_chains(graph=graph_from_rows([]))

        assert result['chain_count'] == 0
        assert result['chains'] == []
