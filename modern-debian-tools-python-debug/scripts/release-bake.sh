#!/usr/bin/env bash
# Governed release build/export wrapper for modern-debian-tools-python-debug.

set -euo pipefail

ACTION="${1:-}"
FLOW="${RELEASE_IMAGE_FLOW:?RELEASE_IMAGE_FLOW must be set by release configuration}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
run_low_priority() {
    if command -v ionice >/dev/null 2>&1; then
        ionice -c3 nice -n 19 "$@"
    else
        nice -n 19 "$@"
    fi
}

registry_bake() {
    : "${IMAGE_COMPRESSION:?IMAGE_COMPRESSION must be configured}"
    : "${IMAGE_COMPRESSION_LEVEL:?IMAGE_COMPRESSION_LEVEL must be configured}"
    : "${IMAGE_FORCE_COMPRESSION:?IMAGE_FORCE_COMPRESSION must be configured}"
    : "${IMAGE_OCI_MEDIA_TYPES:?IMAGE_OCI_MEDIA_TYPES must be configured}"
    : "${IMAGE_PROVENANCE_MODE:?IMAGE_PROVENANCE_MODE must be configured}"
    : "${IMAGE_SBOM:?IMAGE_SBOM must be configured}"
    [[ "${IMAGE_SBOM}" == "true" || "${IMAGE_SBOM}" == "false" ]] || {
        echo "[ERROR] IMAGE_SBOM must be true or false." >&2
        return 2
    }

    local bake_json target output
    local -a targets bake_args
    bake_json="$(docker buildx bake -f docker-bake.hcl all --print)"
    mapfile -t targets < <(jq -r '.group.all.targets[]?' <<<"${bake_json}")
    [[ "${#targets[@]}" -gt 0 ]] || {
        echo "[ERROR] Bake group 'all' contains no targets." >&2
        return 1
    }

    output="type=registry,compression=${IMAGE_COMPRESSION},compression-level=${IMAGE_COMPRESSION_LEVEL},force-compression=${IMAGE_FORCE_COMPRESSION},oci-mediatypes=${IMAGE_OCI_MEDIA_TYPES}"
    bake_args=(docker buildx bake -f docker-bake.hcl all)
    for target in "${targets[@]}"; do
        bake_args+=(--set "${target}.output=${output}")
        bake_args+=(--set "${target}.attest=type=provenance,mode=${IMAGE_PROVENANCE_MODE}")
        if [[ "${IMAGE_SBOM}" == "true" ]]; then
            bake_args+=(--set "${target}.attest+=type=sbom")
        fi
    done
    run_low_priority "${bake_args[@]}" "${CACHE_ARGS[@]}"
}

if [[ "${ACTION}" != "build" && "${ACTION}" != "push" ]]; then
    echo "[ERROR] Usage: $0 <build|push>" >&2
    exit 2
fi

bash scripts/ensure-release-builder.sh

# The common Git directory is shared by disposable cmru release worktrees.
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
if [[ "${COMMON_GIT_DIR}" != /* ]]; then
    COMMON_GIT_DIR="$(pwd)/${COMMON_GIT_DIR}"
fi
CACHE_DIR="${MDT_BUILDKIT_CACHE_DIR:-${COMMON_GIT_DIR}/mdt-buildkit-cache}"
mkdir -p "${CACHE_DIR}"
CACHE_ARGS=(
    --cache-from "type=local,src=${CACHE_DIR}"
    --cache-to "type=local,dest=${CACHE_DIR},mode=max"
)

case "${FLOW}" in
    load)
        # Do not use a short-circuit test as the branch's final command:
        # with `set -e`, a successful build action would otherwise return the
        # false status of `[[ "$ACTION" == push ]]` to the caller.
        if [[ "${ACTION}" == "build" ]]; then
            run_low_priority docker buildx bake -f docker-bake.hcl all --load "${CACHE_ARGS[@]}"
        else
            registry_bake
        fi
        ;;
    push)
        if [[ "${ACTION}" == "build" ]]; then
            registry_bake
        else
            echo "[INFO] RELEASE_IMAGE_FLOW=push: build already published the images."
        fi
        ;;
    repack)
        if [[ "${ACTION}" == "push" ]]; then
            echo "[INFO] RELEASE_IMAGE_FLOW=repack: build already published the repacked OCI layouts."
            exit 0
        fi

        : "${REPACK_WORK_DIR:?REPACK_WORK_DIR must be configured}"
        rm -rf "${REPACK_WORK_DIR}"
        mkdir -p "${REPACK_WORK_DIR}"

        bake_json="$(docker buildx bake -f docker-bake.hcl all --print)"
        mapfile -t targets < <(jq -r '.group.all.targets[]?' <<<"${bake_json}")
        [[ "${#targets[@]}" -gt 0 ]] || {
            echo "[ERROR] Bake group 'all' contains no targets." >&2
            exit 1
        }

        bake_args=(docker buildx bake -f docker-bake.hcl all)
        for target in "${targets[@]}"; do
            safe="${target//[^A-Za-z0-9._-]/_}"
            bake_args+=(--set "${target}.output=type=oci,dest=${REPACK_WORK_DIR}/src-${safe}.tar")
        done

        echo "[INFO] [repack] governed OCI-layout bake start $(ts)"
        run_low_priority "${bake_args[@]}" "${CACHE_ARGS[@]}"
        echo "[INFO] [repack] governed OCI-layout bake end $(ts)"

        for target in "${targets[@]}"; do
            safe="${target//[^A-Za-z0-9._-]/_}"
            mkdir -p "${REPACK_WORK_DIR}/src-${safe}"
            tar -xf "${REPACK_WORK_DIR}/src-${safe}.tar" -C "${REPACK_WORK_DIR}/src-${safe}"
            rm -f "${REPACK_WORK_DIR}/src-${safe}.tar"

            # BuildKit emits one index descriptor per output tag. They can all
            # reference the same image manifest; docker-repack otherwise treats
            # those aliases as separate images and merges the same filesystem
            # repeatedly. Publication tags come from Bake below, so retain one
            # descriptor per digest/platform here.
            index="${REPACK_WORK_DIR}/src-${safe}/index.json"
            tmp_index="${index}.tmp"
            jq '.manifests |= unique_by([.digest, (.platform.os // ""), (.platform.architecture // ""), (.platform.variant // "")])' \
                "${index}" >"${tmp_index}"
            mv "${tmp_index}" "${index}"
        done

        echo "[INFO] [repack] bounded repack start $(ts)"
        run_low_priority bash scripts/release-repack.sh
        echo "[INFO] [repack] bounded repack end $(ts)"
        ;;
    *)
        echo "[ERROR] Unsupported RELEASE_IMAGE_FLOW=${FLOW}. Use load, push, or repack." >&2
        exit 2
        ;;
esac
