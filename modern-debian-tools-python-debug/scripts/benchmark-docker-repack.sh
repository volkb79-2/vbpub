#!/usr/bin/env bash
# BENCHMARK SCRIPT — docker-repack size comparison for a LOCAL image.
# Source: https://github.com/orf/docker-repack   (CLI verified against v0.5.0)
#
# PURPOSE: measure how much docker-repack shrinks a built image's COMPRESSED
# (push/pull) size by re-layering + recompressing (zstd). This is a TRIAL — it is
# NOT wired into the release flow. Evaluate the % reduction before adopting.
#
# WHY skopeo: docker-repack reads a registry ref or an `oci://` layout — it does
# NOT read the local docker daemon. So we export the local image to an OCI layout
# with skopeo first (no registry/auth needed), then repack that.
#
# CRITICAL NOTE: docker-repack produces a NEW image with different layer hashes.
# If adopted into the release flow, repack MUST run BEFORE manifest/sha256
# generation + signing — the repacked image has a different digest than the one
# built by `docker buildx bake --load`. Plan accordingly.
#
# Usage:
#   ./scripts/benchmark-docker-repack.sh <local-image-tag> [target-layer-size...]
# Examples:
#   ./scripts/benchmark-docker-repack.sh ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:latest
#   ./scripts/benchmark-docker-repack.sh my/image:tag 50MB 100MB 200MB 500MB 1GB 2GB 4GB
#
# Prerequisites:
#   - skopeo            (apt-get install -y skopeo)
#   - docker-repack     (https://github.com/orf/docker-repack/releases — prebuilt binary;
#                        there is NO pypi package despite older notes. e.g.:
#                          curl -fsSL -o /tmp/dr.tgz \
#                            https://github.com/orf/docker-repack/releases/download/v0.5.0/docker-repack-Linux-x86_64.tar.gz
#                          tar -C /usr/local/bin -xzf /tmp/dr.tgz docker-repack)

set -euo pipefail

SRC_TAG="${1:-}"
shift || true
TARGET_SIZES=("$@")
if [ "${#TARGET_SIZES[@]}" -eq 0 ]; then
    TARGET_SIZES=("500MB")
fi
DOCKER_REPACK_BIN="${DOCKER_REPACK_BIN:-docker-repack}"

if [ -z "${SRC_TAG}" ]; then
    echo "[ERROR] Usage: $0 <local-image-tag> [target-layer-size]" >&2
    exit 1
fi
for bin in skopeo; do
    command -v "$bin" >/dev/null 2>&1 || { echo "[ERROR] '$bin' not found — see prerequisites in this script's header." >&2; exit 1; }
done
if [ ! -x "${DOCKER_REPACK_BIN}" ]; then
    command -v "${DOCKER_REPACK_BIN}" >/dev/null 2>&1 || {
        echo "[ERROR] docker-repack not found — set DOCKER_REPACK_BIN to a local binary or install it." >&2
        exit 1
    }
fi

run_low_priority() {
    if command -v ionice >/dev/null 2>&1; then
        ionice -c3 nice -n 19 "$@"
    else
        nice -n 19 "$@"
    fi
}

DAEMON_SIZE="$(docker image inspect "${SRC_TAG}" --format '{{.Size}}' 2>/dev/null || true)"
[ -n "${DAEMON_SIZE}" ] || { echo "[ERROR] Image '${SRC_TAG}' not in the local daemon. Build/pull it first." >&2; exit 1; }
echo "[INFO] Source image: ${SRC_TAG}"
echo "[INFO] Daemon (uncompressed) size: $(( DAEMON_SIZE / 1024 / 1024 )) MiB"
# This benchmark can sweep multiple target sizes in one source-export pass. The
# default benchmark sizes we compared are the upstream reference sizes from
# benchmark/sources.yaml: 50MB, 100MB, 200MB, and 500MB, plus 1GB, 2GB, and 4GB
# to cover the larger-end behavior on this image family.

WORK="$(mktemp -d)"; SRC_OCI="${WORK}/src-oci"; OUT_OCI="${WORK}/repacked"
trap 'rm -rf "${WORK}"' EXIT

echo "[INFO] [1/2] skopeo export local image -> OCI layout ..."
run_low_priority skopeo copy --quiet "docker-daemon:${SRC_TAG}" "oci:${SRC_OCI}:latest"
SRC_B="$(du -sb "${SRC_OCI}" | awk '{print $1}')"
echo "[INFO]       source OCI (compressed): $(( SRC_B / 1024 / 1024 )) MiB"

echo "[INFO] [2/2] docker-repack sweeps ..."
for TARGET_SIZE in "${TARGET_SIZES[@]}"; do
    OUT_OCI="${WORK}/repacked-${TARGET_SIZE}"
    LOG_FILE="${WORK}/repack-${TARGET_SIZE}.log"
    echo "[INFO]   - target-size ${TARGET_SIZE}"
    # NOTE: pass the OCI dir WITHOUT a ':tag' suffix — the tag lives in the layout's index.json.
    run_low_priority "${DOCKER_REPACK_BIN}" --target-size "${TARGET_SIZE}" "oci://${SRC_OCI}" "oci://${OUT_OCI}" 2>&1 | tee "${LOG_FILE}" >/dev/null
    DST_B="$(du -sb "${OUT_OCI}" | awk '{print $1}')"
    LAYER_COUNT="$(grep -Eo 'Wrote [0-9]+ layers|Produced [0-9]+ total layers|layers=[0-9]+' "${LOG_FILE}" | tail -1 | grep -Eo '[0-9]+' | tail -1)"
    echo "[INFO]     repacked OCI (compressed): $(( DST_B / 1024 / 1024 )) MiB"
    if [ -n "${LAYER_COUNT:-}" ]; then
        echo "[INFO]     repacked layer count: ${LAYER_COUNT}"
    fi
    awk "BEGIN { printf \"[RESULT] %s %s\\n  compressed: %d MiB -> %d MiB  (reduction %.1f%%)\\n\", \
      \"${SRC_TAG}\", \"${TARGET_SIZE}\", ${SRC_B}/1024/1024, ${DST_B}/1024/1024, (1 - ${DST_B}/${SRC_B}) * 100 }"
done
echo ""
echo "[NOTE] The repacked image has DIFFERENT layer hashes than the source. If docker-repack is"
echo "[NOTE] adopted into the release flow, it must run BEFORE manifest/sha256 + minisign signing."
