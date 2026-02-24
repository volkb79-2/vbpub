#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="safe"
if [[ "${1:-}" == "--all" ]]; then
  MODE="all"
fi

echo "[INFO] Cleanup mode: $MODE"
echo "[INFO] Working directory: $PWD"

# Always cleanup transient temp files
find . -type f -name '.tmp_*.py' -print -delete || true
find . -type d -name '__pycache__' -print -exec rm -rf {} + || true

# Reports/transient processing artifacts
if [[ -d reports ]]; then
  find reports -maxdepth 1 -type f \( -name '*.jsonl' -o -name '*.csv' -o -name '*.txt' -o -name '*.log' \) \
    ! -name 'translation-failures.md' \
    ! -name 'translation-success.md' \
    -print -delete || true
fi

# Chunk/working output artifacts
rm -rf chunks_full output || true

if [[ "$MODE" == "all" ]]; then
  echo "[INFO] Removing build/release artifacts and canonical output snapshots"
  rm -rf dist output-all-real || true
  rm -f mt.toml mt.local.toml || true
fi

echo "[INFO] Cleanup complete"
