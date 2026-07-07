#!/usr/bin/env bash
# Repack and push the locally loaded release images.
#
# Prerequisites:
#   - jq
#   - skopeo
#   - docker-repack
#
# This script expects `docker buildx bake -f docker-bake.hcl all --load` to have
# already populated the local daemon with the release tags for the current build.

set -euo pipefail

BAKE_FILE="${BAKE_FILE:-docker-bake.hcl}"
GROUP="${REPACK_GROUP:-all}"
TARGET_SIZE="${REPACK_TARGET_SIZE:-2GB}"
DOCKER_REPACK_BIN="${DOCKER_REPACK_BIN:-docker-repack}"

for bin in jq skopeo; do
    command -v "${bin}" >/dev/null 2>&1 || {
        echo "[ERROR] '${bin}' not found — install it before running the repack release flow." >&2
        exit 1
    }
done
if [ ! -x "${DOCKER_REPACK_BIN}" ]; then
    command -v "${DOCKER_REPACK_BIN}" >/dev/null 2>&1 || {
        echo "[ERROR] docker-repack not found — set DOCKER_REPACK_BIN or install it." >&2
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

BAKE_JSON="$(docker buildx bake -f "${BAKE_FILE}" "${GROUP}" --print)"
mapfile -t TARGETS < <(jq -r --arg group "${GROUP}" '.group[$group].targets[]?' <<<"${BAKE_JSON}")
if [ "${#TARGETS[@]}" -eq 0 ]; then
    echo "[ERROR] No targets found in bake group '${GROUP}'." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

echo "[INFO] Repacking release images from group '${GROUP}' with target size ${TARGET_SIZE}"
for target in "${TARGETS[@]}"; do
    mapfile -t TAGS < <(jq -r --arg target "${target}" '.target[$target].tags[]?' <<<"${BAKE_JSON}")
    if [ "${#TAGS[@]}" -eq 0 ]; then
        echo "[WARN] Skipping bake target '${target}' because it has no tags." >&2
        continue
    fi

    SOURCE_TAG="${TAGS[0]}"
    SAFE_TARGET="${target//[^A-Za-z0-9._-]/_}"
    SRC_OCI="${WORK}/src-${SAFE_TARGET}"
    DST_OCI="${WORK}/repacked-${SAFE_TARGET}"

    echo "[INFO]   - target ${target}"
    echo "[INFO]     source tag ${SOURCE_TAG}"
    run_low_priority skopeo copy --quiet "docker-daemon:${SOURCE_TAG}" "oci:${SRC_OCI}:source"
    run_low_priority "${DOCKER_REPACK_BIN}" --target-size "${TARGET_SIZE}" "oci://${SRC_OCI}" "oci://${DST_OCI}"

    for DEST_TAG in "${TAGS[@]}"; do
        echo "[INFO]     push ${DEST_TAG}"
        run_low_priority skopeo copy --quiet "oci:${DST_OCI}:source" "docker://${DEST_TAG}"
    done
done

