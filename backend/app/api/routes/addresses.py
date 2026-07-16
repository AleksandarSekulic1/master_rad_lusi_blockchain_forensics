from __future__ import annotations

from fastapi import APIRouter

from app.services.address_enrichment import enrich_address

router = APIRouter(prefix='/addresses', tags=['addresses'])


@router.get('/{address}/enrich')
def enrich_address_route(address: str, network: str = 'mainnet') -> dict[str, object]:
    """Best-effort address enrichment: contract-vs-wallet type and ENS name, if any.

    Never raises for unrecognized/demo addresses - it simply returns 'unknown'/None,
    since this is inspector-panel context, not a hard dependency of any analysis.
    """
    return enrich_address(address, network)
