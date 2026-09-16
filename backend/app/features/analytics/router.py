from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.graph_building import build_transaction_graph, transaction_graph_to_node_link_json
from app.analytics.ingestion import clean_transaction_csv
from app.analytics.plugins.manager import run_plugin_pipeline
from app.paths import RAW_DIR


router = APIRouter(prefix='/analytics', tags=['analytics'])


class AnalyticsRequest(BaseModel):
    file_name: str | None = None
    plugins: list[str] | None = Field(default=None, description='Optional ordered subset of analytics plugins to run.')
    seed_addresses: list[str] | None = Field(
        default=None, description='Extra taint_analysis seed addresses, on top of anything auto-seeded from blacklist_flag.'
    )


def _raw_dir() -> Path:
    return RAW_DIR


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


@router.post('/run')
def run_analytics(request: AnalyticsRequest) -> dict[str, object]:
    csv_path = _resolve_csv_path(request.file_name)
    cleaned_frame = clean_transaction_csv(csv_path)
    graph = build_transaction_graph(cleaned_frame)

    plugin_results = run_plugin_pipeline(
        dataframe=cleaned_frame,
        graph=graph,
        plugin_names=request.plugins,
        seed_addresses=request.seed_addresses,
    )

    payload = transaction_graph_to_node_link_json(graph)
    payload['source_file'] = csv_path.name
    payload['rows'] = int(len(cleaned_frame))
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    payload['analytics'] = plugin_results
    payload['summary'] = {
        'blacklisted_nodes': sum(1 for _, attrs in graph.nodes(data=True) if bool(attrs.get('blacklist_flag'))),
        'high_risk_nodes': sum(1 for _, attrs in graph.nodes(data=True) if int(attrs.get('risk_score', 0) or 0) >= 70),
        'clusters': int(graph.graph.get('cluster_count', 0) or 0),
    }
    return payload