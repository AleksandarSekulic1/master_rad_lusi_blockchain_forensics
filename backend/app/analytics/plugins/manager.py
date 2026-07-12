from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd

from app.analytics.blacklist_check.service import BlacklistCheckPlugin
from app.analytics.clustering.service import WalletClusteringPlugin
from app.analytics.risk_scoring.service import RiskScoringPlugin


PLUGIN_REGISTRY = {
    'blacklist_check': BlacklistCheckPlugin,
    'wallet_clustering': WalletClusteringPlugin,
    'risk_scoring': RiskScoringPlugin,
}

DEFAULT_PIPELINE = ('blacklist_check', 'wallet_clustering', 'risk_scoring')


def run_plugin_pipeline(
    dataframe: pd.DataFrame,
    graph: nx.DiGraph,
    plugin_names: list[str] | None = None,
    **context: Any,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    selected_plugins = plugin_names or list(DEFAULT_PIPELINE)

    for plugin_name in selected_plugins:
        plugin_class = PLUGIN_REGISTRY.get(plugin_name)
        if plugin_class is None:
            raise ValueError(f'Nepoznat analytics plugin: {plugin_name}')

        plugin = plugin_class()
        results[plugin_name] = plugin(dataframe=dataframe, graph=graph, **context)

    return results