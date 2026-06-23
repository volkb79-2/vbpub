#!/usr/bin/env bash
# TRIAL SCRIPT — docker-repack size comparison.
# Source: https://github.com/orf/docker-repack
#
# PURPOSE: Given a built mdt image tag, repacks it with docker-repack and prints
# a before/after size comparison. This is a TRIAL — it is NOT wired into the
# release flow. Evaluate the % reduction before adopting.
#
# CRITICAL NOTE: docker-repack produces a NEW image with a different layer hash.
# If adopted into the release flow, repack MUST run BEFORE manifest/sha256
# generation + signing — the repacked image will have a different digest than the
# one built by `docker buildx bake --load`. Plan accordingly.
#
# Usage:
#   ./scripts/trial-docker-repack.sh <source-tag> [<repacked-tag>]
#
# Examples:
#   ./scripts/trial-docker-repack.sh ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-20260623
#   ./scripts/trial-docker-repack.sh ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-20260623 my-repo/mdt:trixie-py3.14-20260623-repacked
#
# Prerequisites: docker-repack installed (see https://github.com/orf/docker-repack).
#   pip install docker-repack   # or: cargo install docker-repack

set -euo pipefail

SOURCE_TAG="${1:-}"
REPACKED_TAG="${2:-}"

if [ -z "${SOURCE_TAG}" ]; then
    echo "[ERROR] Usage: $0 <source-tag> [<repacked-tag>]" >&2
    exit 1
fi

if [ -z "${REPACKED_TAG}" ]; then
    REPACKED_TAG="${SOURCE_TAG}-repacked"
fi

# Verify docker-repack is available.
if ! command -v docker-repack >/dev/null 2>&1; then
    echo "[ERROR] docker-repack not found. Install it first:" >&2
    echo "  pip install docker-repack" >&2
    echo "  # or: cargo install docker-repack" >&2
    exit 1
fi

echo "[INFO] Source image:   ${SOURCE_TAG}"
echo "[INFO] Repacked image: ${REPACKED_TAG}"
echo ""

# Before: size of the source image (bytes).
SIZE_BEFORE="$(docker image inspect "${SOURCE_TAG}" --format '{{.Size}}' 2>/dev/null || true)"
if [ -z "${SIZE_BEFORE}" ]; then
    echo "[ERROR] Could not inspect source image '${SOURCE_TAG}'. Is it built locally?" >&2
    exit 1
fi

echo "[INFO] Size before repack: ${SIZE_BEFORE} bytes ($(( SIZE_BEFORE / 1024 / 1024 )) MiB)"

# Run docker-repack.
echo "[INFO] Running docker-repack ..."
docker-repack "${SOURCE_TAG}" --tag "${REPACKED_TAG}"

# After: size of the repacked image (bytes).
SIZE_AFTER="$(docker image inspect "${REPACKED_TAG}" --format '{{.Size}}' 2>/dev/null || true)"
if [ -z "${SIZE_AFTER}" ]; then
    echo "[ERROR] Could not inspect repacked image '${REPACKED_TAG}'." >&2
    exit 1
fi

echo "[INFO] Size after repack:  ${SIZE_AFTER} bytes ($(( SIZE_AFTER / 1024 / 1024 )) MiB)"

# Compute % reduction using awk (avoids bash integer division precision issues).
REDUCTION="$(awk "BEGIN { printf \"%.1f\", (1 - ${SIZE_AFTER} / ${SIZE_BEFORE}) * 100 }")"
echo ""
echo "[RESULT] Before: $(( SIZE_BEFORE / 1024 / 1024 )) MiB  |  After: $(( SIZE_AFTER / 1024 / 1024 )) MiB  |  Reduction: ${REDUCTION}%"
echo ""
echo "[NOTE] The repacked image '${REPACKED_TAG}' has a DIFFERENT layer hash than '${SOURCE_TAG}'."
echo "[NOTE] If docker-repack is adopted into the release flow, it must run BEFORE"
echo "[NOTE] manifest/sha256 generation and minisign signing, not after."
