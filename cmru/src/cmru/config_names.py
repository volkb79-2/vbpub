"""The two CMRU configuration filenames.

They are deliberately names, not discovery rules: a reusable ``cmru`` process
selects the project document in its current directory, or the orchestration
document when that is the only config present there. It never searches parent
directories. The vbpub repository may pass the orchestration document explicitly
with ``--config`` or through its release convenience wrapper.
"""
from __future__ import annotations

PROJECT_CONFIG_FILENAME = "cmru.toml"
ORCHESTRATION_CONFIG_FILENAME = "cmru.orchestration.toml"
CONFIG_FILENAMES = frozenset({PROJECT_CONFIG_FILENAME, ORCHESTRATION_CONFIG_FILENAME})
