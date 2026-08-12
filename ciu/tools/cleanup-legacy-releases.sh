#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_PUSH_PAT:?set the explicit publish credential}"
: "${GITHUB_USERNAME:?set the GitHub owner explicitly}"
: "${GITHUB_REPO:?set the GitHub repository explicitly}"

OWNER="${GITHUB_USERNAME}"
REPO="${GITHUB_REPO}"
LEGACY_TAG="ciu-wheel-latest"

RELEASE_ID=""
if GH_TOKEN="${GITHUB_PUSH_PAT}" gh api -H "Accept: application/vnd.github+json" \
  "/repos/${OWNER}/${REPO}/releases/tags/${LEGACY_TAG}" \
  --jq ".id" >/tmp/ciu_release_id.txt 2>/dev/null; then
  RELEASE_ID="$(cat /tmp/ciu_release_id.txt)"
fi

if [[ -z "${RELEASE_ID}" ]]; then
  echo "[INFO] Legacy release not found: ${LEGACY_TAG}"
  exit 0
fi

GH_TOKEN="${GITHUB_PUSH_PAT}" gh api -X DELETE -H "Accept: application/vnd.github+json" \
  "/repos/${OWNER}/${REPO}/releases/${RELEASE_ID}" >/dev/null

echo "[INFO] Deleted legacy release: ${LEGACY_TAG}"
