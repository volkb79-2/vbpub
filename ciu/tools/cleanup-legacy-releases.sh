#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/../.."

# Load shared env if present
if [[ -f "${ROOT_DIR}/.env" ]]; then
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
fi

if [[ -z "${GH_TOKEN:-}" && -n "${GITHUB_PUSH_PAT:-}" ]]; then
  export GH_TOKEN="${GITHUB_PUSH_PAT}"
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "[ERROR] GH_TOKEN or GITHUB_PUSH_PAT is required" >&2
  exit 1
fi

OWNER="${GITHUB_USERNAME:-volkb79-2}"
REPO="${GITHUB_REPO:-vbpub}"
LEGACY_TAG="ciu-wheel-latest"

RELEASE_ID=""
if gh api -H "Accept: application/vnd.github+json" \
  "/repos/${OWNER}/${REPO}/releases/tags/${LEGACY_TAG}" \
  --jq ".id" >/tmp/ciu_release_id.txt 2>/dev/null; then
  RELEASE_ID="$(cat /tmp/ciu_release_id.txt)"
fi

if [[ -z "${RELEASE_ID}" ]]; then
  echo "[INFO] Legacy release not found: ${LEGACY_TAG}"
  exit 0
fi

gh api -X DELETE -H "Accept: application/vnd.github+json" \
  "/repos/${OWNER}/${REPO}/releases/${RELEASE_ID}" >/dev/null

echo "[INFO] Deleted legacy release: ${LEGACY_TAG}"
