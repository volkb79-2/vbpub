#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/cleanup-legacy-releases.sh"
# Validation now lives in cmru's built-in wheel handler (no ciu-local script).
cmru resolve --project ciu
