#!/bin/bash
# Quick deployment script for WhisperLive streaming service (ciu-deploy)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/.env.ciu" ]]; then
  echo "[ERROR] .env.ciu missing in ${SCRIPT_DIR}" >&2
  echo "[ERROR] Run: bash ${SCRIPT_DIR}/env-workspace-setup-generate.sh" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/../.python3 scripts/ciu/ciu-deploy.py" \
  --root-folder "${SCRIPT_DIR}" \
  --groups streaming \
  --deploy
