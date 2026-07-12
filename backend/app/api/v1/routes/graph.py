from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.graph_building.service import (
    build_transaction_graph,
    transaction_graph_to_node_link_json,
)
from app.analytics.ingestion.csv_ingestion import clean_transaction_csv
from app.analytics.path_finding.service import find_transaction_paths


router = APIRouter(prefix='/graph', tags=['graph'])


class PathFindingRequest(BaseModel):
    file_name: str | None = None
    source_address: str = Field(min_length=1)
    target_address: str = Field(min_length=1)
    strategy: str = Field(default='shortest')
    cutoff: int = Field(default=6, ge=1, le=20)
    max_paths: int = Field(default=20, ge=1, le=50)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _raw_dir() -> Path:
    return _repo_root() / 'data' / 'raw'


def _resolve_csv_path(file_name: str | None = None) -> Path:
    raw_dir = _raw_dir()
    if file_name:
        candidate = raw_dir / Path(file_name).name
        if candidate.exists():
            return candidate
        raise HTTPException(status_code=404, detail=f'CSV fajl nije pronađen: {file_name}')

    csv_files = sorted(raw_dir.glob('*.csv'), key=lambda path: path.stat().st_mtime, reverse=True)
    if not csv_files:
        raise HTTPException(status_code=404, detail='Nema učitanih CSV fajlova u data/raw.')
    return csv_files[0]


@router.get('')
def get_graph(file_name: str | None = None) -> dict[str, object]:
    csv_path = _resolve_csv_path(file_name)
    cleaned_frame = clean_transaction_csv(csv_path)
    graph = build_transaction_graph(cleaned_frame)

    payload = transaction_graph_to_node_link_json(graph)
    payload['source_file'] = csv_path.name
    payload['rows'] = int(len(cleaned_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    return payload


@router.post('/path-finding')
def calculate_paths(request: PathFindingRequest) -> dict[str, object]:
    csv_path = _resolve_csv_path(request.file_name)
    cleaned_frame = clean_transaction_csv(csv_path)

    try:
        result = find_transaction_paths(
            cleaned_frame=cleaned_frame,
            source_address=request.source_address,
            target_address=request.target_address,
            strategy=request.strategy,
            cutoff=request.cutoff,
            max_paths=request.max_paths,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result['source_file'] = csv_path.name
    result['rows'] = int(len(cleaned_frame))
    return result