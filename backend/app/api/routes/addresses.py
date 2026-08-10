from __future__ import annotations

from fastapi import APIRouter

from app.services.address_enrichment import enrich_address, get_known_entity

router = APIRouter(prefix='/addresses', tags=['addresses'])


@router.get('/{address}/enrich')
def enrich_address_route(address: str, network: str = 'mainnet') -> dict[str, object]:
    """Best-effort address enrichment: contract-vs-wallet type and ENS name, if any.

    Never raises for unrecognized/demo addresses - it simply returns 'unknown'/None,
    since this is inspector-panel context, not a hard dependency of any analysis.
    """
    return enrich_address(address, network)


@router.get('/known-entities')
def known_entities_route(addresses: str) -> dict[str, dict[str, str] | None]:
    """Batch lookup against the local exchange/mixer/sanctioned registry (see
    known_entities.json) - a plain dict read, no Etherscan call and no rate limit, so it's
    safe to check an entire list of cash-out candidates in one request instead of running
    the full (network-bound) enrich_address per address just for this one field.

    `addresses` is a comma-separated list; unknown/unmatched addresses map to None rather
    than being omitted, so the frontend can tell "checked, no match" from "not checked".
    """
    address_list = [address.strip() for address in addresses.split(',') if address.strip()]
    return {address: get_known_entity(address) for address in address_list}
