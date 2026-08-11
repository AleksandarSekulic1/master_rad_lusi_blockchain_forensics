"""Provera predloga čvorova za taint analizu.

Raniji predlog je koristio detektor statističkih anomalija i uvek izdvajao 5% adresa. Na
podacima sa poznatim odgovorom pronašao je 1 od 4 prava izvora, a predlagao je uglavnom
adrese sa najvećim prometom — na pravom Ethereumu to su berze, ne kriminalci.

Nova pravila rade obrnuto: ništa se ne predlaže bez navedenog razloga. Ovi testovi
proveravaju da svako pravilo pogađa ono što treba, da ne pogađa ono što ne treba, i —
najvažnije — da se kod praznog rezultata ne izmišljaju predlozi.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from app.analytics.seed_suggestion import suggest_seeds


# Stvarne adrese iz lokalne baze poznatih entiteta (backend/app/services/known_entities.json).
SANCTIONED = '0x03893a7c7463ae47d46bc7f091665f1893656003'  # Tornado.Cash, OFAC lista
MIXER = '0x00d5ec4cdf59374b2a47e842b799027356eac02b'       # Tornado.Cash


def addresses(items: list[dict]) -> set[str]:
    return {item['address'] for item in items}


def reasons_for(items: list[dict], address: str) -> list[str]:
    return next((item['reasons'] for item in items if item['address'] == address), [])


class TestOriginCandidates:
    """Kandidati za izvor

    Kao izvor se predlaže samo ono što je utvrđena činjenica, a ne statistička procena.
    """

    def test_blacklisted_address_is_an_origin_candidate(self, graph_from_rows):
        """Adresa sa crne liste je kandidat za izvor

        Oznaka sa crne liste je spoljna činjenica o adresi, a ne zaključak iz podataka -
        zato je to jedan od dva jedina osnova da se nešto predloži kao poreklo.
        """
        graph = graph_from_rows([('0xBad', '0xVictimFunds', 100, '2026-03-01T00:00:00Z')])
        graph.nodes['0xBad']['blacklist_flag'] = True

        result = suggest_seeds(graph)

        assert '0xBad' in addresses(result['origin_candidates'])
        assert 'crnoj listi' in reasons_for(result['origin_candidates'], '0xBad')[0]

    def test_sanctioned_address_is_an_origin_candidate(self, graph_from_rows):
        """OFAC sankcionisana adresa je kandidat za izvor

        Poklapanje sa listom sankcionisanih adresa je definitivan nalaz - ne procenjuje
        se, nego se proverava u bazi.
        """
        graph = graph_from_rows([(SANCTIONED, '0xReceiver', 100, '2026-03-01T00:00:00Z')])

        result = suggest_seeds(graph)

        assert SANCTIONED in addresses(result['origin_candidates'])

    def test_mixer_is_not_offered_as_an_origin(self, graph_from_rows):
        """Mikser se ne nudi kao izvor

        Mikser jeste važan nalaz, ali kao seed adresa bi bio pogrešan: označio bi sve
        njegove isplate kao zaprljane, uključujući tuđa sredstva koja sa slučajem nemaju
        veze.
        """
        graph = graph_from_rows([('0xSender', MIXER, 100, '2026-03-01T00:00:00Z')])

        result = suggest_seeds(graph)

        assert MIXER not in addresses(result['origin_candidates'])
        assert MIXER in addresses(result['laundering_points'])

    def test_confirmed_origin_is_not_repeated_among_laundering_points(self, graph_from_rows):
        """Potvrđen izvor se ne ponavlja među tačkama pranja

        Ako ista adresa zadovolji i jak i slab kriterijum, prikazuje se samo kao izvor -
        inače bi se jači nalaz razvodnio ponavljanjem.
        """
        graph = graph_from_rows([
            ('0xFunder', '0xBad', 100, '2026-03-01T00:00:00Z'),
            ('0xBad', '0xNext', 100, '2026-03-01T00:10:00Z'),
        ])
        graph.nodes['0xBad']['blacklist_flag'] = True

        result = suggest_seeds(graph)

        assert '0xBad' in addresses(result['origin_candidates'])
        assert '0xBad' not in addresses(result['laundering_points'])


class TestPassThroughRule:
    """Pravilo brzog prolaza

    Adresa koja primi novac i skoro sve odmah prosledi ponaša se kao relej (mula), a ne
    kao mesto gde sredstva pripadaju.
    """

    def test_relay_that_keeps_nothing_is_detected(self, graph_from_rows):
        """Relej koji ništa ne zadrži se prepoznaje

        Primljeno 100, prosleđeno 100 u roku od 10 minuta - tipično ponašanje mule.
        """
        graph = graph_from_rows([
            ('0xSource', '0xRelay', 100, '2026-03-01T00:00:00Z'),
            ('0xRelay', '0xNext', 100, '2026-03-01T00:10:00Z'),
        ])

        result = suggest_seeds(graph)

        assert '0xRelay' in addresses(result['laundering_points'])
        assert 'Brzi prolaz' in reasons_for(result['laundering_points'], '0xRelay')[0]

    def test_address_that_keeps_most_funds_is_not_flagged(self, graph_from_rows):
        """Adresa koja zadrži većinu sredstava se ne označava

        Zaštita od povratka greške: mikser iz demo scenarija prima 1500 a šalje 750 -
        zadržao je pola, dakle nije prolaz. Ranije je bio pogrešno označen.
        """
        graph = graph_from_rows([
            ('0xThief', '0xMixer', 1000, '2026-03-01T00:00:00Z'),
            ('0xCleanUser', '0xMixer', 500, '2026-03-01T00:05:00Z'),
            ('0xMixer', '0xExit', 750, '2026-03-01T00:10:00Z'),
        ])

        result = suggest_seeds(graph)
        mixer_reasons = reasons_for(result['laundering_points'], '0xMixer')

        assert not any('Brzi prolaz' in reason for reason in mixer_reasons)

    def test_address_spending_more_than_it_received_is_not_flagged(self, graph_from_rows):
        """Adresa koja pošalje više nego što je primila se ne označava

        Zaštita od povratka greške: raniji račun je sabirao odlive u vremenskom prozoru i
        prijavljivao nemoguće vrednosti tipa "prosleđeno 150% primljenog". Takva adresa
        troši sopstvena sredstva, što nije prolaz.
        """
        graph = graph_from_rows([
            ('0xSource', '0xSpender', 10, '2026-03-01T00:00:00Z'),
            ('0xSpender', '0xNext', 100, '2026-03-01T00:10:00Z'),
        ])

        result = suggest_seeds(graph)
        spender_reasons = reasons_for(result['laundering_points'], '0xSpender')

        assert not any('Brzi prolaz' in reason for reason in spender_reasons)

    def test_slow_forwarding_is_not_a_fast_pass_through(self, graph_from_rows):
        """Sporo prosleđivanje nije brzi prolaz

        Adresa koja prosledi sve, ali tek posle nekoliko dana, ne pokazuje isti obrazac -
        vremenska komponenta je deo definicije pravila.
        """
        graph = graph_from_rows([
            ('0xSource', '0xSlow', 100, '2026-03-01T00:00:00Z'),
            ('0xSlow', '0xNext', 100, '2026-03-05T00:00:00Z'),
        ])

        result = suggest_seeds(graph)
        slow_reasons = reasons_for(result['laundering_points'], '0xSlow')

        assert not any('Brzi prolaz' in reason for reason in slow_reasons)

    def test_reason_states_the_actual_numbers(self, graph_from_rows):
        """Razlog navodi stvarne iznose

        Analitičar mora moći da proveri nalaz bez otvaranja koda, pa razlog sadrži
        primljeno, prosleđeno i koliko je zadržano.
        """
        graph = graph_from_rows([
            ('0xSource', '0xRelay', 100, '2026-03-01T00:00:00Z'),
            ('0xRelay', '0xNext', 98, '2026-03-01T00:05:00Z'),
        ])

        reason = reasons_for(suggest_seeds(graph)['laundering_points'], '0xRelay')[0]

        assert 'primljeno' in reason and 'prosleđeno' in reason and 'zadržano' in reason


class TestPatternRules:
    """Ostali obrasci pranja

    Buđenje uspavanih adresa i usitnjavanje - obrasci sa napisanom definicijom.
    """

    def test_dormant_address_waking_up_is_detected(self, graph_from_rows):
        """Buđenje uspavane adrese se prepoznaje

        Novčanik ostavljen posle krađe pa aktiviran kad pažnja splasne - ovde 120 dana
        tišine pa ponovna aktivnost.
        """
        graph = graph_from_rows([
            ('0xSource', '0xDormant', 100, '2026-01-01T00:00:00Z'),
            ('0xDormant', '0xNext', 40, '2026-05-15T00:00:00Z'),
        ])

        reasons = reasons_for(suggest_seeds(graph)['laundering_points'], '0xDormant')

        assert any('uspavane' in reason for reason in reasons)

    def test_continuous_activity_is_not_dormancy(self, graph_from_rows):
        """Neprekidna aktivnost nije buđenje

        Adresa koja radi redovno ne sme da se označi - inače bi pravilo hvatalo svakoga
        ko je duže u opticaju.
        """
        graph = graph_from_rows([
            ('0xSource', '0xActive', 100, '2026-01-01T00:00:00Z'),
            ('0xActive', '0xA', 10, '2026-01-05T00:00:00Z'),
            ('0xActive', '0xB', 10, '2026-01-20T00:00:00Z'),
        ])

        reasons = reasons_for(suggest_seeds(graph)['laundering_points'], '0xActive')

        assert not any('uspavane' in reason for reason in reasons)

    def test_splitting_into_similar_amounts_is_detected(self, graph_from_rows):
        """Usitnjavanje na slične iznose se prepoznaje

        Jedan iznos razbijen na više gotovo istih delova u kratkom roku - klasičan pokušaj
        da pojedinačni transferi ostanu ispod praga pažnje.
        """
        rows = [('0xSource', '0xSplitter', 500, '2026-03-01T00:00:00Z')]
        rows += [('0xSplitter', f'0xPart{i}', 100, f'2026-03-01T0{i + 1}:00:00Z') for i in range(5)]

        reasons = reasons_for(suggest_seeds(graph_from_rows(rows))['laundering_points'], '0xSplitter')

        assert any('Usitnjavanje' in reason for reason in reasons)


class TestNoInventedSuggestions:
    """Bez izmišljenih predloga

    Najvažnija promena u odnosu na raniji pristup: kad nema razloga, nema ni predloga.
    """

    def test_ordinary_traffic_produces_no_suggestions(self, graph_from_rows):
        """Obične transakcije ne daju nijedan predlog

        Raniji pristup je uvek izdvajao 5% adresa, pa je i na potpuno običnim podacima
        "pronalazio" sumnjive adrese. Sada prazan rezultat znači prazan rezultat.
        """
        graph = graph_from_rows([
            ('0xAlice', '0xBob', 10, '2026-03-01T00:00:00Z'),
            ('0xBob', '0xCarol', 3, '2026-04-01T00:00:00Z'),
            ('0xCarol', '0xDave', 1, '2026-05-01T00:00:00Z'),
        ])

        result = suggest_seeds(graph)

        assert result['origin_candidates'] == []
        assert result['laundering_points'] == []

    def test_every_suggestion_carries_a_reason(self, graph_from_rows):
        """Svaki predlog nosi razlog

        Adresa bez objašnjenja zašto je predložena je šum - takva ne sme da se pojavi u
        rezultatu ni u jednom slučaju.
        """
        graph = graph_from_rows([
            ('0xSource', '0xRelay', 100, '2026-03-01T00:00:00Z'),
            ('0xRelay', '0xNext', 100, '2026-03-01T00:05:00Z'),
            (SANCTIONED, '0xSomewhere', 50, '2026-03-01T00:20:00Z'),
        ])

        result = suggest_seeds(graph)
        every = result['origin_candidates'] + result['laundering_points']

        assert every, 'ocekivan bar jedan predlog u ovom scenariju'
        assert all(item['reasons'] for item in every)

    def test_single_address_pull_explains_why_nothing_can_be_found(self, graph_from_rows):
        """Istorija jedne adrese objašnjava zašto nema nalaza

        Kad su svi dokazi izvučeni kao istorija jedne adrese, niko u njima ne prima i ne
        šalje — pa lančani obrasci strukturno ne mogu da se pojave. Prazan rezultat tu ne
        znači "čisto je", nego "podaci ne mogu da odgovore na pitanje", i to mora biti
        napisano da analitičar ne izvede pogrešan zaključak.
        """
        rows = [(f'0xSender{i}', '0xHub', 10, f'2026-03-0{i + 1}T00:00:00Z') for i in range(5)]

        result = suggest_seeds(graph_from_rows(rows))

        assert result['origin_candidates'] == []
        assert result['laundering_points'] == []
        assert result['coverage_note'] is not None
        assert 'istorija jedne adrese' in result['coverage_note']

    def test_multi_hop_evidence_gets_no_such_warning(self, graph_from_rows):
        """Višeslojna evidencija nema tu napomenu

        Kada u dokazima postoji bar jedna adresa koja i prima i prosleđuje, lanci se mogu
        tražiti — pa prazan rezultat tada stvarno znači da obrazaca nema.
        """
        graph = graph_from_rows([
            ('0xA', '0xB', 10, '2026-03-01T00:00:00Z'),
            ('0xB', '0xC', 3, '2026-06-01T00:00:00Z'),
        ])

        assert suggest_seeds(graph)['coverage_note'] is None

    def test_performed_checks_are_always_reported(self, graph_from_rows):
        """Izvršene provere se uvek navode

        I kada ništa nije pronađeno, analitičar mora videti ŠTA je provereno - inače ne
        može znati da li je nalaz "čisto" ili "nije ni gledano".
        """
        graph = graph_from_rows([('0xAlice', '0xBob', 10, '2026-03-01T00:00:00Z')])

        checks = suggest_seeds(graph)['checks_performed']

        assert len(checks) >= 8
        assert all(check['label'] and check['description'] for check in checks)
        assert {check['category'] for check in checks} == {'origin', 'laundering'}
