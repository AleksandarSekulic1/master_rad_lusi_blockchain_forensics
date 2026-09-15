"""Connection management for the graph database (Neo4j).

This is a DELIBERATELY OPTIONAL, ADDITIVE layer - see PREDLOG-GRAF-SUBP.md at the repo
root for the full reasoning. It exists only to power `case_graph_search` (Cypher-native
"neighborhood of an address" queries). Nothing else in this project depends on it:

- The evidence CSV + its SHA-256 hash remain the sole forensic source of truth (see
  app/evidence/hashing.py, app/evidence/custody_log.py) - completely untouched by this.
- Every existing analysis (taint, path finding, peel chains, chain hopping, wallet
  clustering) keeps using its in-memory NetworkX graph exactly as before.

If Neo4j is not running (or not configured), `is_available()` returns False and
`case_graph_search`'s router turns that into a plain 503 - the rest of the API, and the
whole existing test suite, are completely unaffected.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

from app.paths import REPO_ROOT

load_dotenv(REPO_ROOT / 'backend' / '.env')

# Local-dev defaults match docker-compose.yml's `neo4j` service (bolt://localhost:7687
# when Neo4j runs in Docker and the backend runs locally, which is the normal dev setup
# in this project - see requirements.txt). docker-compose.yml overrides NEO4J_URI to
# bolt://neo4j:7687 for the case where the backend itself also runs in that compose
# network.
NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'dev-insecure-password')

_driver: Driver | None = None


def get_driver() -> Driver:
    """Lazy singleton - one driver (Neo4j's own internal connection pool) for the whole
    process, created on first use rather than at import time, so importing this module
    never requires Neo4j to be reachable."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def is_available() -> bool:
    """Cheap reachability check. Used by case_graph_search's router to fail with a clear,
    expected 503 instead of a raw connection error when Neo4j simply isn't running -
    which is the normal state for anyone not deliberately using this feature."""
    try:
        get_driver().verify_connectivity()
        return True
    except Exception:
        return False


def reset_driver_for_tests() -> None:
    """Closes and drops the cached driver so a test can point NEO4J_URI/USER/PASSWORD at
    a different instance (or force a fresh reachability check). Not used outside tests."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
