"""Napredna pretraga grafa (graph-db pilot) - "koji su sve adresi povezani sa X, do N
koraka" preko Neo4j-a, umesto ručnog BFS-a nad NetworkX grafom.

Ovo je namerno MALA, DODATNA mogućnost - vidi PREDLOG-GRAF-SUBP.md. Ne zamenjuje
`case_pathfinding` (BFS do TAČNO jedne odredišne adrese), nego ga dopunjuje: ovde se pita
"šta je sve u blizini X", ne "kako da stignem od X do Y". Dokazni CSV i njegov SHA-256 heš
ostaju jedini izvor istine - graf u Neo4j-u je izvedena, jednokratno sinhronizovana kopija,
tačno kao što je NetworkX graf danas.

Ako Neo4j nije pokrenut, ova ruta vraća jasan 503 - ništa drugo u aplikaciji na to ne
utiče (svaka druga ruta i dalje radi normalno, kao i ceo test suite).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.features.case_graph_search import service
from app.shared.case_access import filter_evidence_paths, get_case_evidence_paths_or_404, get_case_or_404
from app.shared.graph_db import is_available

router = APIRouter(prefix='/cases', tags=['cases'])

_UNAVAILABLE_DETAIL = (
    'Graf baza (Neo4j) nije dostupna. Ova mogućnost je opciona - pokrenite je sa '
    '"docker compose up neo4j" ako želite da je koristite; ostatak aplikacije radi '
    'normalno i bez nje.'
)


@router.get('/{case_id}/graph-search/neighborhood')
def get_case_graph_neighborhood(
    case_id: str,
    address: str = Query(min_length=1, description='Adresa čije se susedstvo traži.'),
    max_hops: int = Query(default=2, ge=1, le=5, description='Najveći broj koraka (transakcija) od polazne adrese.'),
    evidence: str | None = None,
) -> dict[str, object]:
    if not is_available():
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)

    case = get_case_or_404(case_id)
    evidence_paths = filter_evidence_paths(get_case_evidence_paths_or_404(case), evidence)
    normalized_address = address.strip()

    try:
        transactions_indexed = service.sync_case_graph(case_id, evidence_paths)
        neighbors = service.neighborhood(case_id, normalized_address, max_hops)
    except Exception as exc:  # noqa: BLE001 - Neo4j postao nedostupan usred zahteva; ne rušimo API
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from exc

    if neighbors is None:
        raise HTTPException(status_code=404, detail=f'Adresa nije pronađena u evidenciji ovog slučaja: {address}')

    return {
        'case_id': case_id,
        'evidence': evidence,
        'address': normalized_address,
        'max_hops': max_hops,
        'transactions_indexed': transactions_indexed,
        'neighbors': neighbors,
        'disclaimer': (
            'Rezultat je izveden iz iste dokazne evidencije kao i ostatak aplikacije '
            '(preko Neo4j grafa, sinhronizovanog pri svakom pozivu) - NIJE zaseban ili '
            'trajniji izvor istine od dokaznog CSV-a i njegovog SHA-256 heša.'
        ),
    }
