"""Normalizacija Bitcoin (UTXO) transakcija u standardni CSV oblik (BITCOIN-UTXO-PLAN.md).

Cilj: potvrditi da Blockstream Esplora odgovor - ulazi/izlazi po transakciji, iznosi u
satošijima - postaje isti (sender_address, recipient_address, amount, timestamp, metadata)
oblik koji ceo pipeline (izgrađen za Ethereum) već očekuje, po common input ownership
heuristici (prva ulazna adresa = pošiljalac, svaki izlaz = po jedan red primaoca). Takođe
proverava tri ivična slučaja koja ne smeju da obore uvoz: coinbase transakcija (nema
prevout na ulazu), OP_RETURN izlaz (bez adrese), i nepotvrđena (mempool) transakcija.

NAPOMENA: prva linija svakog docstring-a se prikazuje kao naziv testa na stranici
"Testovi" u aplikaciji.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from app.features.bitcoin_ingestion import service
from app.features.bitcoin_ingestion.service import fetch_address_transactions, transaction_to_rows


def _confirmed_tx(
    txid: str,
    vin: list[dict[str, Any]],
    vout: list[dict[str, Any]],
    block_time: int = 1_700_000_000,
) -> dict[str, Any]:
    return {
        'txid': txid,
        'status': {'confirmed': True, 'block_time': block_time},
        'vin': vin,
        'vout': vout,
    }


def _input(address: str, value: int = 100_000) -> dict[str, Any]:
    return {'prevout': {'scriptpubkey_address': address, 'value': value}}


def _output(address: str | None, value: int) -> dict[str, Any]:
    output: dict[str, Any] = {'value': value}
    if address is not None:
        output['scriptpubkey_address'] = address
    return output


class TestTransactionToRows:
    """Normalizacija jedne transakcije (2 ulaza / 2 izlaza -> tačan skup redova)"""

    def test_two_inputs_two_outputs_yields_expected_rows(self):
        """Transakcija sa 2 ulaza i 2 izlaza daje tačno 2 reda, pošiljalac = prva ulazna adresa"""
        tx = _confirmed_tx(
            txid='tx1',
            vin=[_input('bc1qsender1'), _input('bc1qsender2')],
            vout=[_output('bc1qrecipient1', 30_000_000), _output('bc1qrecipient2', 20_000_000)],
        )

        rows = transaction_to_rows(tx)

        assert rows == [
            {
                'sender_address': 'bc1qsender1',
                'recipient_address': 'bc1qrecipient1',
                'amount': 0.3,
                'timestamp': '2023-11-14T22:13:20+00:00',
                'metadata': 'tx1',
            },
            {
                'sender_address': 'bc1qsender1',
                'recipient_address': 'bc1qrecipient2',
                'amount': 0.2,
                'timestamp': '2023-11-14T22:13:20+00:00',
                'metadata': 'tx1',
            },
        ]

    def test_change_output_back_to_sender_is_kept_not_filtered(self):
        """Change izlaz nazad ka pošiljaocu ostaje u rezultatu kao normalan red"""
        tx = _confirmed_tx(
            txid='tx2',
            vin=[_input('bc1qsender1')],
            vout=[_output('bc1qrecipient1', 5_000_000), _output('bc1qsender1', 4_000_000)],
        )

        rows = transaction_to_rows(tx)

        assert len(rows) == 2
        assert rows[1]['sender_address'] == 'bc1qsender1'
        assert rows[1]['recipient_address'] == 'bc1qsender1'

    def test_coinbase_transaction_without_prevout_is_skipped(self):
        """Coinbase transakcija (ulaz bez prevout-a) se preskače bez rušenja"""
        tx = _confirmed_tx(
            txid='coinbase_tx',
            vin=[{'is_coinbase': True}],
            vout=[_output('bc1qminer', 625_000_000)],
        )

        rows = transaction_to_rows(tx)

        assert rows == []

    def test_op_return_output_without_address_is_skipped(self):
        """OP_RETURN izlaz (bez adrese) se preskače, ostali izlazi iz iste transakcije ostaju"""
        tx = _confirmed_tx(
            txid='tx3',
            vin=[_input('bc1qsender1')],
            vout=[_output(None, 0), _output('bc1qrecipient1', 10_000_000)],
        )

        rows = transaction_to_rows(tx)

        assert len(rows) == 1
        assert rows[0]['recipient_address'] == 'bc1qrecipient1'

    def test_unconfirmed_transaction_is_skipped(self):
        """Nepotvrđena (mempool) transakcija se preskače"""
        tx = {
            'txid': 'mempool_tx',
            'status': {'confirmed': False},
            'vin': [_input('bc1qsender1')],
            'vout': [_output('bc1qrecipient1', 10_000_000)],
        }

        rows = transaction_to_rows(tx)

        assert rows == []


class TestFetchAddressTransactions:
    """Preuzimanje istorije adrese preko Blockstream API-ja (mock HTTP, ne živi poziv)"""

    def test_returns_dataframe_with_expected_columns_and_rows(self, monkeypatch):
        """Rezultat je DataFrame sa tačnim kolonama, filtriran po pravilima normalizacije"""
        page = [
            _confirmed_tx('tx1', vin=[_input('bc1qsender1')], vout=[_output('bc1qrecipient1', 50_000_000)]),
            {'txid': 'mempool_tx', 'status': {'confirmed': False}, 'vin': [], 'vout': []},
        ]
        monkeypatch.setattr(service, '_blockstream_get', lambda path: page)

        dataframe = fetch_address_transactions('bc1qsender1')

        assert list(dataframe.columns) == ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata']
        assert len(dataframe) == 1
        assert dataframe.iloc[0]['sender_address'] == 'bc1qsender1'
        assert dataframe.iloc[0]['amount'] == 0.5

    def test_paginates_using_last_confirmed_txid_until_short_page(self, monkeypatch):
        """Paginacija ide preko txs/chain/{last_seen_txid} dok stranica ne bude kraća od 25"""
        first_page = [
            _confirmed_tx(f'tx{i}', vin=[_input('bc1qsender1')], vout=[_output('bc1qrecipient1', 1_000_000)])
            for i in range(25)
        ]
        second_page = [
            _confirmed_tx('tx25', vin=[_input('bc1qsender1')], vout=[_output('bc1qrecipient1', 1_000_000)])
        ]
        calls: list[str] = []

        def fake_get(path: str):
            calls.append(path)
            if path == '/address/bc1qsender1/txs':
                return first_page
            return second_page

        monkeypatch.setattr(service, '_blockstream_get', fake_get)

        dataframe = fetch_address_transactions('bc1qsender1')

        assert len(dataframe) == 26
        assert calls == ['/address/bc1qsender1/txs', '/address/bc1qsender1/txs/chain/tx24']

    def test_no_confirmed_transactions_returns_empty_frame(self, monkeypatch):
        """Adresa bez potvrđenih transakcija vraća prazan DataFrame istog oblika, ne grešku"""
        monkeypatch.setattr(service, '_blockstream_get', lambda path: [])

        dataframe = fetch_address_transactions('bc1qsender1')

        assert dataframe.empty
        assert list(dataframe.columns) == ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata']

    def test_network_failure_raises_runtime_error(self, monkeypatch):
        """Nedostupan Blockstream API diže RuntimeError, ne generičku HTTP grešku"""
        import requests

        def raise_error(url, timeout):
            raise requests.RequestException('boom')

        monkeypatch.setattr(service.requests, 'get', raise_error)

        with pytest.raises(RuntimeError, match='Blockstream API nije dostupan'):
            fetch_address_transactions('bc1qsender1')
