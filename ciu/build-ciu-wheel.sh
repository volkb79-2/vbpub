#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CIU_ROOT="$SCRIPT_DIR"
REPO_ROOT="$(cd "$CIU_ROOT/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "[ERROR] Python venv not found at $PYTHON_BIN" >&2
    echo "[ERROR] Ensure .venv exists at repo root or set PYTHON_BIN" >&2
    exit 1
fi

DIST_DIR="$CIU_ROOT/dist"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

"$PYTHON_BIN" -m pip wheel . -w "$DIST_DIR"

echo "[SUCCESS] Built CIU wheel(s) in $DIST_DIR"