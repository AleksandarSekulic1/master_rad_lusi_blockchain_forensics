from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import requests

from app.services.onchain_ingestion import _etherscan_get, _require_api_key, _require_chain_id, _to_iso_timestamp, _wei_to_eth

_ADDRESS_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')

ENS_RESOLVE_URL = 'https://api.ensideas.com/ens/resolve/{address}'

KNOWN_ENTITIES_PATH = Path(__file__).parent / 'known_entities.json'

AddressType = Literal['contract', 'eoa', 'unknown']
KnownEntityCategory = Literal['exchange', 'mixer', 'sanctioned']


def _is_valid_address(address: str) -> bool:
    return bool(_ADDRESS_PATTERN.match(address))


@lru_cache(maxsize=1)
def _known_entities() -> dict[str, dict[str, str]]:
    """Curated address -> {name, category} lookup for well-known exchanges, mixers and
    OFAC-sanctioned addresses, sourced from Etherscan's own public labels (see
    known_entities.json). Local dict lookup - no network call, no rate limit."""
    with KNOWN_ENTITIES_PATH.open(encoding='utf-8') as handle:
        return json.load(handle)


def get_known_entity(address: str) -> dict[str, str] | None:
    """Whoever publicly controls this address, if it's a known exchange, mixer, or
    OFAC-sanctioned address - the practical, honest substitute for "who created this
    transaction": we can't geolocate a wallet, but a known platform has a real-world
    operator and jurisdiction that a subpoena can actually reach."""
    if not _is_valid_address(address):
        return None
    return _known_entities().get(address.lower())


def get_address_type(address: str, network: str = 'mainnet') -> AddressType:
    """Distinguishes a smart contract from a regular (externally owned) wallet address.

    Uses eth_getCode: contracts have deployed bytecode, wallets don't. Non-standard
    addresses (e.g. demo/test labels in sample CSVs) are reported as 'unknown' without
    calling out to Etherscan.
    """
    if not _is_valid_address(address):
        return 'unknown'

    chain_id = _require_chain_id(network)
    api_key = _require_api_key()

    params = {
        'chainid': chain_id,
        'module': 'proxy',
        'action': 'eth_getCode',
        'address': address,
        'tag': 'latest',
        'apikey': api_key,
    }
    payload = _etherscan_get(params)
    code = payload.get('result') or '0x'
    return 'contract' if code not in ('0x', '0x0') else 'eoa'


def get_ens_name(address: str) -> str | None:
    """Best-effort ENS reverse lookup (address -> human-readable name like vitalik.eth).

    Uses the free ENSIdeas public API since Etherscan's API has no ENS endpoint. Any
    failure (network error, no reverse record set) simply yields no name, since ENS
    is a nice-to-have, not something an investigation should depend on.
    """
    if not _is_valid_address(address):
        return None

    try:
        response = requests.get(ENS_RESOLVE_URL.format(address=address), timeout=5)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    name = data.get('name')
    return str(name) if name else None


def get_balance_eth(address: str, network: str = 'mainnet') -> float | None:
    """Current on-chain ETH balance. None if the address is invalid or Etherscan is unreachable."""
    if not _is_valid_address(address):
        return None

    try:
        chain_id = _require_chain_id(network)
        api_key = _require_api_key()
        payload = _etherscan_get(
            {
                'chainid': chain_id,
                'module': 'account',
                'action': 'balance',
                'address': address,
                'tag': 'latest',
                'apikey': api_key,
            }
        )
    except (RuntimeError, ValueError):
        return None

    if payload.get('status') != '1':
        return None

    return _wei_to_eth(payload.get('result', 0))


def _fetch_single_tx(address: str, chain_id: int, api_key: str, sort: str) -> dict[str, Any] | None:
    """One lightweight txlist call (offset=1) just to read a single transaction record."""
    payload = _etherscan_get(
        {
            'chainid': chain_id,
            'module': 'account',
            'action': 'txlist',
            'address': address,
            'startblock': 0,
            'endblock': 99999999,
            'page': 1,
            'offset': 1,
            'sort': sort,
            'apikey': api_key,
        }
    )
    if payload.get('status') != '1':
        return None

    result = payload.get('result')
    if not isinstance(result, list) or not result:
        return None

    return result[0]


def get_activity_and_funding(address: str, network: str = 'mainnet') -> dict[str, Any]:
    """First/last on-chain transaction timestamps (reveals long-dormant or freshly created
    "burner" wallets) plus the funding source: whoever sent the address's very first
    incoming transaction — a classic forensic technique for tracing where a wallet's funds,
    or the wallet itself, originally came from. Just two minimal (offset=1) calls total."""
    empty: dict[str, Any] = {
        'first_seen_onchain': None,
        'last_seen_onchain': None,
        'funding_source': None,
        'funding_amount_eth': None,
        'funding_source_type': None,
        'funding_source_ens': None,
        'funding_source_entity': None,
        'funding_source_entity_category': None,
    }
    if not _is_valid_address(address):
        return empty

    try:
        chain_id = _require_chain_id(network)
        api_key = _require_api_key()
        first_tx = _fetch_single_tx(address, chain_id, api_key, sort='asc')
        last_tx = _fetch_single_tx(address, chain_id, api_key, sort='desc')
    except (RuntimeError, ValueError):
        return empty

    result = dict(empty)
    if first_tx:
        result['first_seen_onchain'] = _to_iso_timestamp(first_tx.get('timeStamp', 0))
        # Only a genuine "funding" event if the address's very first on-chain appearance
        # was as the *recipient* (an address whose first activity was outgoing wasn't funded by it).
        if str(first_tx.get('to', '')).lower() == address.lower():
            funding_source = str(first_tx.get('from', ''))
            result['funding_source'] = funding_source
            result['funding_amount_eth'] = _wei_to_eth(first_tx.get('value', 0))
            # A shallow look at *who* funded this wallet: a known exchange/mixer contract
            # is a much bigger forensic lead than an ordinary EOA-to-EOA transfer.
            try:
                result['funding_source_type'] = get_address_type(funding_source, network)
            except (RuntimeError, ValueError):
                result['funding_source_type'] = 'unknown'
            result['funding_source_ens'] = get_ens_name(funding_source)
            entity = get_known_entity(funding_source)
            if entity:
                result['funding_source_entity'] = entity['name']
                result['funding_source_entity_category'] = entity['category']
    if last_tx:
        result['last_seen_onchain'] = _to_iso_timestamp(last_tx.get('timeStamp', 0))

    return result


def get_token_symbols(address: str, network: str = 'mainnet', limit: int = 100, display_limit: int = 10) -> dict[str, Any]:
    """Distinct ERC-20 token symbols this address has sent/received, e.g. stablecoin exposure
    for AML purposes. Limited to the `limit` most recent transfers to stay cheap for very
    active addresses (a full history isn't needed just to know *which* tokens are involved).

    Well-known addresses accumulate dozens of unsolicited spam/scam airdrop tokens (often
    reusing recognizable names to look legitimate) - real, not a bug, but not useful to dump
    in full in a side panel, so only the first `display_limit` (alphabetically) are returned
    alongside the true total count.
    """
    empty = {'tokens': [], 'tokens_total_count': 0}
    if not _is_valid_address(address):
        return empty

    try:
        chain_id = _require_chain_id(network)
        api_key = _require_api_key()
        payload = _etherscan_get(
            {
                'chainid': chain_id,
                'module': 'account',
                'action': 'tokentx',
                'address': address,
                'page': 1,
                'offset': limit,
                'sort': 'desc',
                'apikey': api_key,
            }
        )
    except (RuntimeError, ValueError):
        return empty

    if payload.get('status') != '1':
        return empty

    result = payload.get('result')
    if not isinstance(result, list):
        return empty

    symbols = {str(transfer['tokenSymbol']) for transfer in result if transfer.get('tokenSymbol')}
    ordered = sorted(symbols)
    return {'tokens': ordered[:display_limit], 'tokens_total_count': len(ordered)}


def enrich_address(address: str, network: str = 'mainnet') -> dict[str, Any]:
    address_type: AddressType = 'unknown'
    try:
        address_type = get_address_type(address, network)
    except (RuntimeError, ValueError):
        address_type = 'unknown'

    known_entity = get_known_entity(address)

    return {
        'address': address,
        'address_type': address_type,
        'ens_name': get_ens_name(address),
        'balance_eth': get_balance_eth(address, network),
        'known_entity': known_entity['name'] if known_entity else None,
        'known_entity_category': known_entity['category'] if known_entity else None,
        **get_activity_and_funding(address, network),
        **get_token_symbols(address, network),
    }
