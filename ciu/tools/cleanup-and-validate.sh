#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="/workspaces/vbpub/.venv/bin/python"

bash "${SCRIPT_DIR}/cleanup-legacy-releases.sh"
"${PYTHON_BIN}" "${SCRIPT_DIR}/validate-wheel-latest.py"
