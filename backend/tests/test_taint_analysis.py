"""Provera ispravnosti proporcionalnog ("haircut") taint algoritma.

Procenat koji ovaj modul računa je centralna tvrdnja celog alata - završava u izveštajima
i, u stvarnoj istrazi, pred ljudima koji ne mogu da pročitaju izvorni kod. Ovi testovi
fiksiraju ponašanje na kome ta tvrdnja počiva, tako da izmena koja tiho promeni matematiku
padne ovde umesto u izveštaju.

Brojevi su namerno isti kao u dokumentovanim scenarijima iz BLOCKCHAIN-UVOZ.md (sekcije
6.1 i 8.x), da dokumentacija i testovi ne mogu neprimetno da se raziđu.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji, pa treba da bude kratka i razumljiva bez čitanja koda.
"""

from __future__ import annotations

import pytest

from app.analytics.plugins.taint_analysis import run_taint_analysis


def percentages(result: dict) -> dict[str, float]:
    return {item['address']: item['taint_percentage'] for item in result['results']}


class TestHaircutDilution:
    """Razblaživanje (haircut model)

    Osnovno obećanje modela: čist priliv srazmerno spušta procenat zaprljanosti.
    """

    def test_clean_inflow_dilutes_percentage(self, graph_from_rows):
        """Čist priliv razblažuje procenat

        1000 ukradenih se meša sa 500 čistih. Rezultat mora biti 1000/1500 = 66.67%,
        tačno onaj broj koji je dokumentovan u scenariju 6.1.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert percentages(result)['0xMixer'] == pytest.approx(66.67, abs=0.01)

    def test_outflow_does_not_change_percentage(self, graph_from_rows):
        """Odliv ne menja procenat pošiljaoca

        Slanje novca odnosi zaprljani i čisti deo u istoj srazmeri, pa procenat
        pošiljaoca ostaje nepromenjen. To je definišuća osobina haircut modela, za
        razliku od "poison" modela gde bi svaki dodir zauvek ostao 100%.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
            ('0xMixer', '0xExitWallet', 750, '2026-03-01T00:10:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        percentage_by_address = percentages(result)

        assert percentage_by_address['0xMixer'] == pytest.approx(66.67, abs=0.01)
        # Primalac nasleđuje mešavinu pošiljaoca, ne njegovo poreklo.
        assert percentage_by_address['0xExitWallet'] == pytest.approx(66.67, abs=0.01)

    def test_percentage_never_exceeds_100(self, graph_from_rows):
        """Procenat nikada ne prelazi 100%

        Zaštita od povratka greške pronađene na pravom slučaju: adresa koja prvo pošalje
        sredstva (koja je imala pre početka evidencije), pa tek onda primi zaprljana,
        dobijala je negativan balans — pa je imenilac postao manji od zaprljanog iznosa i
        izveštaj je prikazivao 111.11% zaprljanosti, što je nemoguće.
        """
        graph = graph_from_rows([
            ('0xSpender', '0xSomeone', 5, '2026-03-01T00:00:00Z'),
            ('0xSpender', '0xSomeone', 5, '2026-03-01T00:01:00Z'),
            ('0xThief', '0xSpender', 50, '2026-03-01T00:02:00Z'),
            ('0xThief', '0xSpender', 50, '2026-03-01T00:03:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert all(item['taint_percentage'] <= 100.0 for item in result['results'])
        assert percentages(result)['0xSpender'] == pytest.approx(100.0, abs=0.01)

    def test_sending_more_than_received_cannot_create_taint(self, graph_from_rows):
        """Slanje više nego što je primljeno ne stvara novi taint

        Ako adresa pošalje više nego što je u evidenciji primila, višak potiče od sredstava
        van evidencije. Prosleđeno zaprljano ne sme premašiti ono što na adresi stvarno
        postoji — inače bi se zaprljani iznos umnožavao kroz lanac.
        """
        graph = graph_from_rows([
            ('0xThief', '0xRelay', 100, '2026-03-01T00:00:00Z'),
            ('0xRelay', '0xNext', 300, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        hop = next(h for h in result['tainted_hops'] if h['source'] == '0xRelay')

        assert hop['tainted_amount'] <= 100.0 + 1e-9
        assert all(item['taint_percentage'] <= 100.0 for item in result['results'])

    def test_untouched_address_stays_clean(self, graph_from_rows):
        """Nedodirnuta adresa ostaje na 0%

        Adresa koja nikad nije primila ništa iz zaprljanog toka mora ostati čista -
        inače bi algoritam "prljao" ceo graf.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xStranger', '0xOtherParty', 400, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert percentages(result)['0xOtherParty'] == 0


class TestPerSeedAttribution:
    """Raspodela po izvorima

    Ne samo "koliko je prljavo", nego "čije je" - razlaganje procenta po pojedinačnom
    izvoru (seed adresi).
    """

    def test_two_seeds_split_proportionally(self, graph_from_rows):
        """Dva izvora se dele srazmerno (60/40)

        Dva hakera uplaćuju 600 i 400 na isti hub. Ukupno je 100% zaprljano, ali
        raspodela mora pokazati čijih je koliko.
        """
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
        """Raspodela po izvorima preživljava dalji skok

        Mešavina 60/40 putuje zajedno sa novcem umesto da se svede na jedan broj -
        bez ovoga bi se posle prvog prosleđivanja izgubilo od koga potiče koji deo.
        """
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

        assert destination['taint_by_source']['0xHacker1'] == pytest.approx(60.0, abs=0.01)
        assert destination['taint_by_source']['0xHacker2'] == pytest.approx(40.0, abs=0.01)

    def test_single_seed_attributes_everything_to_itself(self, graph_from_rows):
        """Jedan izvor pripisuje sve sebi

        Kad postoji samo jedan izvor, raspodela mora biti {taj izvor: 100%}, a ne
        prazna - inače bi prikaz "po izvoru" bio prazan u najčešćem slučaju.
        """
        graph = graph_from_rows([('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        mixer = next(item for item in result['results'] if item['address'] == '0xMixer')

        assert mixer['taint_by_source'] == {'0xThief': pytest.approx(100.0, abs=0.01)}


class TestTimelineSeries:
    """Podaci za vremensku traku

    Ono što vremenska traka reprodukuje - uključujući raspodelu po izvorima na svakom
    rangu, koju "Filter po izvoru" koristi da bi bio tačan i tokom skrolovanja (8.6).
    """

    def test_series_records_percentage_after_each_event(self, graph_from_rows):
        """Istorija beleži procenat posle svakog događaja

        Traka ne sme da preskoči trenutak razblaživanja: posle prvog priliva 100%,
        posle drugog 66.67%.
        """
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
        """Svaki zapis istorije nosi svoju raspodelu po izvorima

        Zaštita od povratka greške: raspodela je ranije postojala samo kao konačan
        snimak, zbog čega je filter po izvoru bio tiho netačan tokom vremenske trake.
        """
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
        # Na rangu 1 stigao je samo Hacker1-ov novac...
        assert series[0]['taint_by_source'] == {'0xHacker1': pytest.approx(100.0, abs=0.01)}
        # ...i tek posle ranga 2 nastaje mešavina 60/40.
        assert series[1]['taint_by_source']['0xHacker1'] == pytest.approx(60.0, abs=0.01)
        assert series[1]['taint_by_source']['0xHacker2'] == pytest.approx(40.0, abs=0.01)

    def test_series_includes_direction_and_counterparty(self, graph_from_rows):
        """Istorija sadrži smer i suprotnu stranu

        Bez smera (priliv/odliv) i suprotne strane, panel "Objašnjenje procenta" ne bi
        mogao da objasni zašto se procenat promenio.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xMixer', '0xExitWallet', 400, '2026-03-01T00:10:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        series = result['node_taint_series']['0xMixer']

        assert [entry['direction'] for entry in series] == ['in', 'out']
        assert [entry['counterparty'] for entry in series] == ['0xThief', '0xExitWallet']


class TestChronology:
    """Hronologija

    Taint prati stvarno vreme kroz ceo graf, a ne granu po granu.
    """

    def test_events_are_ordered_across_different_edges(self, graph_from_rows):
        """Događaji su hronološki poređani kroz sve grane

        Čist priliv je naveden kao drugi, ali se desio PRVI. Obrada granu-po-granu bi
        razblažila naknadno i dala drugačiji (pogrešan) procenat - ovde mora biti 50%.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:05:00Z'),
            ('0xCleanUser', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        series = result['node_taint_series']['0xMixer']

        assert [entry['counterparty'] for entry in series] == ['0xCleanUser', '0xThief']
        assert percentages(result)['0xMixer'] == pytest.approx(50.0, abs=0.01)

    def test_zero_amount_transactions_are_dropped(self, graph_from_rows):
        """Transakcije sa nultim iznosom se izbacuju

        Prašina i interakcije sa ugovorima ne menjaju nijedan balans, pa ne smeju da
        zauzimaju poziciju na traci na kojoj se ništa ne dešava.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xNoise', '0xMixer', 0, '2026-03-01T00:02:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert result['timeline_max_rank'] == 1

    def test_repeated_transfers_on_one_edge_each_get_a_rank(self, graph_from_rows):
        """Ponovljeni transferi na istoj grani dobijaju svoj rang

        Dva odvojena slanja između istog para adresa su dva događaja, ne jedan -
        inače bi traka preskočila jedan od njih.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 500, '2026-03-01T00:00:00Z'),
            ('0xThief', '0xMixer', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert result['timeline_max_rank'] == 2


class TestSeedBehaviour:
    """Ponašanje izvora (seed adresa)

    Kako se same polazne adrese tretiraju - one su početak traga, ne obična stanica.
    """

    def test_seed_is_fully_tainted(self, graph_from_rows):
        """Izvor (seed) je 100% zaprljan

        Polazna adresa je po definiciji potpuno zaprljana i mora biti označena kao
        izvor u rezultatu.
        """
        graph = graph_from_rows([('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        thief = next(item for item in result['results'] if item['address'] == '0xThief')

        assert thief['is_taint_seed'] is True
        assert thief['taint_percentage'] == pytest.approx(100.0, abs=0.01)

    def test_seed_reinjects_full_taint_on_incoming_funds(self, graph_from_rows):
        """Izvor ponovo ubrizgava pun taint na priliv

        Kad izvor primi svež novac, to je novo ubrizgavanje pripisano njemu samom -
        isti akter je ponovo finansiran, nije razblaživanje prethodne mešavine.
        """
        graph = graph_from_rows([
            ('0xCleanUser', '0xThief', 1000, '2026-03-01T00:00:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)

        assert percentages(result)['0xThief'] == pytest.approx(100.0, abs=0.01)

    def test_blacklist_flag_seeds_automatically(self, graph_from_rows):
        """Adresa sa crne liste automatski postaje izvor

        Ako je adresa označena na crnoj listi, analiza je uzima kao polaznu i bez
        ručnog unosa.
        """
        graph = graph_from_rows([('0xBadActor', '0xMule', 300, '2026-03-01T00:00:00Z')])
        graph.nodes['0xBadActor']['blacklist_flag'] = True

        result = run_taint_analysis(graph=graph, seed_addresses=None, seed_from_blacklist=True)

        assert result['seed_addresses'] == ['0xBadActor']
        assert percentages(result)['0xMule'] == pytest.approx(100.0, abs=0.01)

    def test_no_seeds_means_no_taint(self, graph_from_rows):
        """Bez izvora nema zaprljanosti

        Bez ijedne polazne adrese ništa ne sme biti označeno kao zaprljano - zaštita
        od "lažne uzbune" na praznom unosu.
        """
        graph = graph_from_rows([('0xA', '0xB', 100, '2026-03-01T00:00:00Z')])

        result = run_taint_analysis(graph=graph, seed_addresses=[], seed_from_blacklist=False)

        assert result['tainted_node_count'] == 0


class TestEvidenceShape:
    """Oblik evidencije

    Koliko evidencija uopšte prati novac dalje od prvog skoka. U izvlačenju istorije jedne
    adrese skoro niko ne prima i ne prosleđuje, pa svaki list izgleda kao tačka
    unovčavanja iako su njegove dalje transakcije prosto neprikupljene.
    """

    def test_single_hop_pull_is_recognized(self, graph_from_rows):
        """Jednoslojna evidencija se prepoznaje

        Kada jedna adresa šalje na mnogo njih i niko ne prosleđuje dalje, rezultat se ne
        sme čitati kao „pronađeno je mnogo tačaka unovčavanja" — to je ivica prikupljenih
        podataka.
        """
        rows = [('0xHub', f'0xLeaf{i}', 1, f'2026-03-01T00:{i:02d}:00Z') for i in range(25)]

        result = run_taint_analysis(graph=graph_from_rows(rows), seed_addresses=['0xHub'], seed_from_blacklist=False)

        assert result['single_hop_evidence'] is True
        assert result['relay_count'] == 0

    def test_multi_hop_evidence_is_not_flagged(self, graph_from_rows):
        """Višeslojna evidencija se ne označava

        Kada sredstva stvarno prolaze kroz lance, nalazi o tačkama unovčavanja imaju
        smisla i upozorenje ne sme da se pojavi.
        """
        rows = []
        for i in range(25):
            rows.append(('0xHub', f'0xRelay{i}', 10, f'2026-03-01T00:{i:02d}:00Z'))
            rows.append((f'0xRelay{i}', f'0xEnd{i}', 9, f'2026-03-01T01:{i:02d}:00Z'))

        result = run_taint_analysis(graph=graph_from_rows(rows), seed_addresses=['0xHub'], seed_from_blacklist=False)

        assert result['single_hop_evidence'] is False
        assert result['relay_count'] == 25

    def test_tiny_graph_is_never_flagged(self, graph_from_rows):
        """Vrlo mali graf se nikad ne označava

        Na svega nekoliko adresa udeo relejnih čvorova ništa ne govori — upozorenje bi
        bilo šum, pa se primenjuje tek od 20 adresa naviše.
        """
        result = run_taint_analysis(
            graph=graph_from_rows([('0xA', '0xB', 10, '2026-03-01T00:00:00Z')]),
            seed_addresses=['0xA'],
            seed_from_blacklist=False,
        )

        assert result['single_hop_evidence'] is False


class TestTaintedHops:
    """Zaprljani skokovi

    Zapisi pojedinačnih transakcija koje su stvarno prenele zaprljana sredstva.
    """

    def test_hop_records_amount_actually_tainted(self, graph_from_rows):
        """Skok beleži stvarno zaprljan iznos

        Od 750 poslatih sa balansa koji je 66.67% prljav, zaprljano je tačno 500 -
        ovaj broj se prikazuje u panelu "Detalji transakcije" i u PDF izveštaju.
        """
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

        assert hop['tainted_amount'] == pytest.approx(500.0, abs=0.01)
        assert hop['taint_pct_at_hop'] == pytest.approx(66.67, abs=0.01)

    def test_clean_transfers_are_not_recorded_as_hops(self, graph_from_rows):
        """Čisti transferi se ne beleže kao zaprljani skokovi

        Transakcija koja nije prenela nijedan zaprljan dinar ne sme da se pojavi u
        spisku zaprljanih skokova, inače bi izveštaj optuživao nevine adrese.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xSomeoneElse', 500, '2026-03-01T00:05:00Z'),
        ])

        result = run_taint_analysis(graph=graph, seed_addresses=['0xThief'], seed_from_blacklist=False)
        pairs = {(hop['source'], hop['target']) for hop in result['tainted_hops']}

        assert ('0xCleanUser', '0xSomeoneElse') not in pairs


def test_requires_a_graph():
    """Analiza bez grafa prijavljuje grešku

    Poziv bez grafa mora jasno pući umesto da tiho vrati prazan rezultat koji bi
    izgledao kao "nema ničeg sumnjivog".
    """
    with pytest.raises(ValueError):
        run_taint_analysis(graph=None, seed_addresses=['0xThief'])
