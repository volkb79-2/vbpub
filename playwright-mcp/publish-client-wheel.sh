#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "[ERROR] Python interpreter not found at $PYTHON_BIN" >&2
    echo "[ERROR] Set PYTHON_BIN or ensure repo-root .venv exists" >&2
    exit 1
fi

"$PYTHON_BIN" "$PROJECT_ROOT/publish-client-wheel.py"
"$PYTHON_BIN" "$PROJECT_ROOT/validate-client-wheel-latest.py"