from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import networkx as nx
import pandas as pd


class BasePlugin(ABC):
    name: str = 'base'
    description: str = ''

    def __call__(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        return self.run(dataframe=dataframe, graph=graph, **context)

    @abstractmethod
    def run(
        self,
        dataframe: pd.DataFrame | None = None,
        graph: nx.DiGraph | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError