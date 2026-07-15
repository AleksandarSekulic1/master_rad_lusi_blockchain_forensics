from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analytics.graph_building import (
    build_transaction_graph,
    transaction_graph_to_node_link_json,
)
from app.analytics.ingestion import clean_transaction_csv
from app.analytics.path_finding import find_transaction_paths
from app.paths import RAW_DIR


router = APIRouter(prefix="/graph", tags=["graph"])


class PathFindingRequest(BaseModel):
    file_name: str | None = None
    source_address: str = Field(min_length=1)
    target_address: str = Field(min_length=1)
    strategy: str = Field(default="shortest")
    cutoff: int = Field(default=6, ge=1, le=20)
    max_paths: int = Field(default=20, ge=1, le=50)


def _raw_dir() -> Path:
    return RAW_DIR


def _resolve_csv_path(file_name: str | None = None) -> Path:
    raw_dir = _raw_dir()
    if file_name:
        candidate = raw_dir / Path(file_name).name
        if candidate.exists():
            return candidate
        raise HTTPException(
            status_code=404, detail=f"CSV fajl nije pronađen: {file_name}"
        )

    csv_files = sorted(
        raw_dir.glob("*.csv"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not csv_files:
        raise HTTPException(
            status_code=404, detail="Nema učitanih CSV fajlova u data/raw."
        )
    return csv_files[0]


def enrich_node_metadata(payload: dict[str, object]) -> dict[str, object]:
    """Obogaćuje čvorove grafa forenzičkim podacima za prikaz u desnom panelu na frontendu."""
    nodes = payload.get("nodes", [])
    if not isinstance(nodes, list):
        return payload

    for node in nodes:
        if not isinstance(node, dict):
            continue

        # Podržavamo i direktne propertije i 'data' rečnik (Cytoscape format)
        data = node.get("data", node)
        if not isinstance(data, dict):
            continue

        node_id = str(data.get("id", data.get("label", ""))).lower()

        # Podrazumevane (bazne) vrednosti za normalan saobraćaj
        score = 0
        flags = "None"
        blacklisted = "No"
        peel_role = "n/a"
        chain_hop = "No"
        cluster = "Regular P2P Traffic"

        # Detekcija po ključnim rečima iz forenzičkih uzoraka
        if "bad0" in node_id or "sanctioned" in node_id or "blacklist" in node_id:
            score = 100
            flags = "OFAC Sanctioned Entity"
            blacklisted = "Yes (OFAC List)"
            cluster = "High-Risk Blacklist"
        elif (
            "hacker" in node_id
            or "exploit" in node_id
            or "drainer" in node_id
            or "phishing" in node_id
        ):
            score = 85
            flags = "Theft / Exploit Origin"
            cluster = "Illicit Source"
        elif "peel" in node_id or "mule" in node_id or "consolidator" in node_id:
            score = 45
            flags = "Suspicious Layering"
            peel_role = (
                "Peel Chain Member" if "peel" in node_id else "Terminal Mule"
            )
            cluster = "Money Laundering (Peel Chain)"
        elif "bridge" in node_id or "hop" in node_id:
            score = 30
            flags = "Cross-Chain Transfer"
            chain_hop = "Yes (Bridge Exit)"
            cluster = "Cross-Chain Hop"
        elif (
            "binance" in node_id
            or "coinbase" in node_id
            or "exchange" in node_id
        ):
            score = 10
            flags = "KYC Exchange Endpoint"
            cluster = "Centralized Exchange"

        # Upisujemo vrednosti u svim formatima (snake_case i camelCase) zbog Angular bindinga
        data["risk_score"] = data["riskScore"] = data["risk"] = score
        data["flags"] = flags
        data["blacklists"] = data["blacklisted"] = blacklisted
        data["peel_role"] = data["peelRole"] = peel_role
        data["chain_hop"] = data["chainHop"] = chain_hop
        data["cluster"] = cluster

    return payload


@router.get("")
def get_graph(file_name: str | None = None) -> dict[str, object]:
    csv_path = _resolve_csv_path(file_name)
    cleaned_frame = clean_transaction_csv(csv_path)
    graph = build_transaction_graph(cleaned_frame)

    payload = transaction_graph_to_node_link_json(graph)
    payload["source_file"] = csv_path.name
    payload["rows"] = int(len(cleaned_frame))
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Pozivamo funkciju za obogaćivanje metapodataka pre slanja na frontend
    return enrich_node_metadata(payload)


@router.post("/path-finding")
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

    result["source_file"] = csv_path.name
    result["rows"] = int(len(cleaned_frame))
    return result