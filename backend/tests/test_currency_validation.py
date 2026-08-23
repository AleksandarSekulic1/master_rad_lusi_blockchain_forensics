"""Provera valute u evidenciji.

Taint model sabira i deli iznose. To ima smisla samo ako su svi iznosi u istoj jedinici —
fajl koji meša ETH i USDT dao bi procente koji izgledaju precizno, a aritmetički su
besmisleni. Zato se takav fajl odbija pri otpremanju, a ne prihvata uz upozorenje: greška
se kasnije ne može ispraviti, jer bi svaki naredni broj bio pogrešan.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics.ingestion import clean_transaction_csv, detect_currencies


HEADER = 'sender_address,recipient_address,amount,timestamp'


def write_csv(tmp_path: Path, rows: str, header: str = HEADER) -> Path:
    path = tmp_path / 'evidence.csv'
    path.write_text(f'{header}\n{rows}', encoding='utf-8')
    return path


class TestCurrencyDetection:
    """Prepoznavanje valute

    Kolona sa valutom je opciona i može se zvati različito, ali njeno odsustvo mora biti
    jasno razlikovano od "jedna valuta".
    """

    def test_single_currency_is_detected(self, tmp_path):
        """Jedna valuta se prepoznaje

        Fajl u kome sve transakcije nose istu oznaku daje tačno jednu vrednost.
        """
        path = write_csv(
            tmp_path,
            '0xA,0xB,10,2026-03-01T00:00:00Z,ETH\n0xB,0xC,5,2026-03-01T01:00:00Z,ETH\n',
            header=f'{HEADER},currency',
        )

        assert detect_currencies(path) == ['ETH']

    def test_mixed_currencies_are_all_reported(self, tmp_path):
        """Pomešane valute se sve navode

        Poruka o grešci mora da kaže koje su valute pronađene, da analitičar zna šta da
        razdvoji.
        """
        path = write_csv(
            tmp_path,
            '0xA,0xB,10,2026-03-01T00:00:00Z,ETH\n0xB,0xC,500,2026-03-01T01:00:00Z,USDT\n',
            header=f'{HEADER},currency',
        )

        assert detect_currencies(path) == ['ETH', 'USDT']

    def test_missing_column_is_not_treated_as_one_currency(self, tmp_path):
        """Nedostatak kolone nije isto što i jedna valuta

        Stariji dokazi nemaju kolonu za valutu. Pretpostaviti da je to ETH značilo bi
        upisati nedokazivu tvrdnju u forenzički zapis — zato rezultat mora biti prazan,
        pa se u aplikaciji ispisuje "nije navedena".
        """
        path = write_csv(tmp_path, '0xA,0xB,10,2026-03-01T00:00:00Z\n')

        assert detect_currencies(path) == []

    def test_alternative_column_names_are_recognized(self, tmp_path):
        """Alternativni nazivi kolone se prepoznaju

        Izvoz iz različitih alata koristi različite nazive (valuta, token, symbol, asset),
        pa se svi prevode na isto polje.
        """
        for column in ('valuta', 'token', 'symbol', 'asset'):
            path = write_csv(
                tmp_path,
                '0xA,0xB,10,2026-03-01T00:00:00Z,USDC\n',
                header=f'{HEADER},{column}',
            )
            assert detect_currencies(path) == ['USDC'], f'nije prepoznata kolona "{column}"'

    def test_case_and_spacing_do_not_create_false_mixes(self, tmp_path):
        """Velika slova i razmaci ne prave lažno mešanje

        "eth", "ETH " i "Eth" su ista valuta — bez normalizacije bi fajl bio pogrešno
        odbijen kao pomešan.
        """
        path = write_csv(
            tmp_path,
            '0xA,0xB,10,2026-03-01T00:00:00Z,eth\n0xB,0xC,5,2026-03-01T01:00:00Z, ETH \n0xC,0xD,1,2026-03-01T02:00:00Z,Eth\n',
            header=f'{HEADER},currency',
        )

        assert detect_currencies(path) == ['ETH']

    def test_broken_file_does_not_crash_the_check(self, tmp_path):
        """Neispravan fajl ne ruši proveru

        Provera valute se izvršava pri otpremanju; ako fajl nije čitljiv, mora vratiti
        prazno umesto da izazove grešku i sruši ceo upload.
        """
        path = tmp_path / 'broken.csv'
        path.write_bytes(b'\x00\x01\x02 ovo nije csv')

        assert detect_currencies(path) == []


class TestIngestionKeepsWorking:
    """Uvoz i dalje radi

    Dodavanje opcione kolone ne sme da pokvari postojeće fajlove.
    """

    def test_file_without_currency_column_still_loads(self, tmp_path):
        """Fajl bez kolone za valutu se i dalje učitava

        Sve postojeće evidencije nemaju tu kolonu i moraju da rade nepromenjeno.
        """
        path = write_csv(tmp_path, '0xA,0xB,10,2026-03-01T00:00:00Z\n')

        frame = clean_transaction_csv(path)

        assert len(frame) == 1
        assert frame.iloc[0]['amount'] == pytest.approx(10.0)

    def test_currency_column_does_not_drop_rows(self, tmp_path):
        """Kolona sa valutom ne izbacuje redove

        Nova kolona je informativna — ne sme da utiče na to koje transakcije ulaze u
        analizu.
        """
        path = write_csv(
            tmp_path,
            '0xA,0xB,10,2026-03-01T00:00:00Z,ETH\n0xB,0xC,5,2026-03-01T01:00:00Z,ETH\n',
            header=f'{HEADER},currency',
        )

        assert len(clean_transaction_csv(path)) == 2
