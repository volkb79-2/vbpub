#!/usr/bin/env bash
# Release bake wrapper for modern-debian-tools-python-debug.
#
# RELEASE_IMAGE_FLOW controls the build export strategy:
#   - load: build uses `--load`, push uses `--push`
#   - push: build uses `--push`, push becomes a no-op because the images were
#     already pushed during the build step
#   - repack: build uses `--load`, then repacks and pushes the resulting OCI
#     layouts using docker-repack (REPACK_TARGET_SIZE defaults to 2GB)

set -euo pipefail

ACTION="${1:-}"
FLOW="${RELEASE_IMAGE_FLOW:-load}"

ts() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

run_low_priority() {
    if command -v ionice >/dev/null 2>&1; then
        ionice -c3 nice -n 19 "$@"
    else
        nice -n 19 "$@"
    fi
}

if [ "${ACTION}" != "build" ] && [ "${ACTION}" != "push" ]; then
    echo "[ERROR] Usage: $0 <build|push>" >&2
    exit 1
fi

case "${FLOW}" in
    load)
        if [ "${ACTION}" = "build" ]; then
            run_low_priority docker buildx bake -f docker-bake.hcl all --load
        else
            run_low_priority docker buildx bake -f docker-bake.hcl all --push
        fi
        ;;
    repack)
        if [ "${ACTION}" = "build" ]; then
            echo "[INFO] [${FLOW}] buildx bake start $(ts)"
            run_low_priority docker buildx bake -f docker-bake.hcl all --load
            echo "[INFO] [${FLOW}] buildx bake end $(ts)"
            echo "[INFO] [${FLOW}] repack start $(ts)"
            run_low_priority bash scripts/release-repack.sh
            echo "[INFO] [${FLOW}] repack end $(ts)"
        else
            echo "[INFO] RELEASE_IMAGE_FLOW=repack: build step already repacks and pushes the images; skipping push bake."
        fi
        ;;
    push)
        if [ "${ACTION}" = "build" ]; then
            run_low_priority docker buildx bake -f docker-bake.hcl all --push
        else
            echo "[INFO] RELEASE_IMAGE_FLOW=push: build already pushed the images; skipping push bake."
        fi
        ;;
    *)
        echo "[ERROR] Unsupported RELEASE_IMAGE_FLOW=${FLOW}. Use load, push, or repack." >&2
        exit 1
        ;;
esac
