#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CIU_ROOT="$SCRIPT_DIR"

cd "$CIU_ROOT"
pytest tests -v