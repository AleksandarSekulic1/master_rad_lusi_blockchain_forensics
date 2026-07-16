from __future__ import annotations

import re
from typing import Any, Literal

import requests

from app.services.onchain_ingestion import _etherscan_get, _require_api_key, _require_chain_id

_ADDRESS_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')

ENS_RESOLVE_URL = 'https://api.ensideas.com/ens/resolve/{address}'

AddressType = Literal['contract', 'eoa', 'unknown']


def _is_valid_address(address: str) -> bool:
    return bool(_ADDRESS_PATTERN.match(address))


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


def enrich_address(address: str, network: str = 'mainnet') -> dict[str, Any]:
    address_type: AddressType = 'unknown'
    try:
        address_type = get_address_type(address, network)
    except (RuntimeError, ValueError):
        address_type = 'unknown'

    return {
        'address': address,
        'address_type': address_type,
        'ens_name': get_ens_name(address),
    }
