"""The two CMRU configuration filenames.

They are deliberately names, not discovery rules: a reusable ``cmru`` process
defaults only to the project document in its current directory; vbpub's root
shim explicitly supplies the orchestration document.
"""
from __future__ import annotations

PROJECT_CONFIG_FILENAME = "cmru.toml"
ORCHESTRATION_CONFIG_FILENAME = "cmru.orchestration.toml"
CONFIG_FILENAMES = frozenset({PROJECT_CONFIG_FILENAME, ORCHESTRATION_CONFIG_FILENAME})
