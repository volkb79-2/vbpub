#!/usr/bin/env bash
# TRIAL SCRIPT — docker-repack size comparison for a LOCAL image.
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
#   ./scripts/trial-docker-repack.sh <local-image-tag> [target-layer-size]
# Examples:
#   ./scripts/trial-docker-repack.sh ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:latest
#   ./scripts/trial-docker-repack.sh my/image:tag 500MB
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
TARGET_SIZE="${2:-500MB}"

if [ -z "${SRC_TAG}" ]; then
    echo "[ERROR] Usage: $0 <local-image-tag> [target-layer-size]" >&2
    exit 1
fi
for bin in skopeo docker-repack; do
    command -v "$bin" >/dev/null 2>&1 || { echo "[ERROR] '$bin' not found — see prerequisites in this script's header." >&2; exit 1; }
done

DAEMON_SIZE="$(docker image inspect "${SRC_TAG}" --format '{{.Size}}' 2>/dev/null || true)"
[ -n "${DAEMON_SIZE}" ] || { echo "[ERROR] Image '${SRC_TAG}' not in the local daemon. Build/pull it first." >&2; exit 1; }
echo "[INFO] Source image: ${SRC_TAG}"
echo "[INFO] Daemon (uncompressed) size: $(( DAEMON_SIZE / 1024 / 1024 )) MiB"

WORK="$(mktemp -d)"; SRC_OCI="${WORK}/src-oci"; OUT_OCI="${WORK}/repacked"
trap 'rm -rf "${WORK}"' EXIT

echo "[INFO] [1/2] skopeo export local image -> OCI layout ..."
skopeo copy --quiet "docker-daemon:${SRC_TAG}" "oci:${SRC_OCI}:latest"
SRC_B="$(du -sb "${SRC_OCI}" | awk '{print $1}')"
echo "[INFO]       source OCI (compressed): $(( SRC_B / 1024 / 1024 )) MiB"

echo "[INFO] [2/2] docker-repack (--target-size ${TARGET_SIZE}) ..."
# NOTE: pass the OCI dir WITHOUT a ':tag' suffix — the tag lives in the layout's index.json.
docker-repack --target-size "${TARGET_SIZE}" "oci://${SRC_OCI}" "oci://${OUT_OCI}"
DST_B="$(du -sb "${OUT_OCI}" | awk '{print $1}')"
echo "[INFO]       repacked OCI (compressed): $(( DST_B / 1024 / 1024 )) MiB"

echo ""
awk "BEGIN { printf \"[RESULT] %s\\n  compressed: %d MiB -> %d MiB  (reduction %.1f%%)\\n\", \
  \"${SRC_TAG}\", ${SRC_B}/1024/1024, ${DST_B}/1024/1024, (1 - ${DST_B}/${SRC_B}) * 100 }"
echo ""
echo "[NOTE] The repacked image has DIFFERENT layer hashes than the source. If docker-repack is"
echo "[NOTE] adopted into the release flow, it must run BEFORE manifest/sha256 + minisign signing."
