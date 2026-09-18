"""The two CMRU configuration filenames.

The installed ``cmru`` process searches ancestors for the nearest
``cmru.orchestration.toml``. That file establishes the CMRU root and may serve
several repositories below it; an explicit ``--config`` always wins.
"""
from __future__ import annotations

PROJECT_CONFIG_FILENAME = "cmru.toml"
ORCHESTRATION_CONFIG_FILENAME = "cmru.orchestration.toml"
CONFIG_FILENAMES = frozenset({PROJECT_CONFIG_FILENAME, ORCHESTRATION_CONFIG_FILENAME})
