from __future__ import annotations

from pathlib import Path

import networkx as nx
import pandas as pd

from app.analytics.graph_building import build_transaction_graph
from app.analytics.ingestion import clean_transaction_csv

_EMPTY_COLUMNS = ['sender_address', 'recipient_address', 'amount', 'timestamp', 'metadata']


def clean_evidence_frames(evidence_paths: list[tuple[dict[str, object], Path]]) -> list[tuple[dict[str, object], pd.DataFrame]]:
    """Cleans each evidence CSV individually, keeping per-file frames for per-evidence stats."""
    return [(entry, clean_transaction_csv(path)) for entry, path in evidence_paths]


def combine_frames(per_evidence_frames: list[tuple[dict[str, object], pd.DataFrame]]) -> pd.DataFrame:
    """Concatenates every evidence CSV in a case into one DataFrame.

    A case's evidence is analyzed together (not just the most recently imported file)
    so later imports don't hide the analysis of earlier ones, and relationships between
    addresses pulled in from different evidence files become visible in the graph.
    """
    if not per_evidence_frames:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)

    return pd.concat([frame for _, frame in per_evidence_frames], ignore_index=True)


def build_case_graph(evidence_paths: list[tuple[dict[str, object], Path]]) -> tuple[pd.DataFrame, nx.DiGraph]:
    combined_frame = combine_frames(clean_evidence_frames(evidence_paths))
    graph = build_transaction_graph(combined_frame)
    return combined_frame, graph


def graph_summary(graph: nx.DiGraph) -> dict[str, int]:
    return {
        'blacklisted_nodes': sum(1 for _, attrs in graph.nodes(data=True) if bool(attrs.get('blacklist_flag'))),
        'high_risk_nodes': sum(1 for _, attrs in graph.nodes(data=True) if int(attrs.get('risk_score', 0) or 0) >= 70),
        'clusters': int(graph.graph.get('cluster_count', 0) or 0),
    }
