#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CIU_ROOT="$SCRIPT_DIR"
REPO_ROOT="$(cd "$CIU_ROOT/.." && pwd)"
TOOLS_DIR="$CIU_ROOT/tools"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "[ERROR] Python venv not found at $PYTHON_BIN" >&2
    echo "[ERROR] Ensure .venv exists at repo root or set PYTHON_BIN" >&2
    exit 1
fi

"$PYTHON_BIN" "$TOOLS_DIR/publish-wheel-release.py"
"$PYTHON_BIN" "$TOOLS_DIR/validate-wheel-latest.py"