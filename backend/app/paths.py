from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / 'data'
RAW_DIR = DATA_DIR / 'raw'
CASES_DIR = DATA_DIR / 'cases'
# Investigator layer container store - kept beside the evidence cases but in its own tree,
# so investigator-generated conclusions never share a directory with imported on-chain facts.
INVESTIGATIONS_DIR = DATA_DIR / 'investigations'
LOGS_DIR = REPO_ROOT / 'logs'
