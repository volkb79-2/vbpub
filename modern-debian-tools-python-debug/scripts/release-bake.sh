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

oci_layout_bake() {
    # Single-build, checksum-identical alternative to `--load` + a later,
    # independent `registry_bake()`: the same bytes built here are what
    # build-push.py's digest-verified `crane push` later publishes — no
    # second build, no drift between what was reviewed/manifested and what
    # actually reaches the registry.
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

    rm -rf "${OCI_LAYOUT_DIR}"
    mkdir -p "${OCI_LAYOUT_DIR}"

    local bake_json target output safe
    local -a targets bake_args
    bake_json="$(docker buildx bake -f docker-bake.hcl all --print)"
    mapfile -t targets < <(jq -r '.group.all.targets[]?' <<<"${bake_json}")
    [[ "${#targets[@]}" -gt 0 ]] || {
        echo "[ERROR] Bake group 'all' contains no targets." >&2
        return 1
    }

    output="type=oci,compression=${IMAGE_COMPRESSION},compression-level=${IMAGE_COMPRESSION_LEVEL},force-compression=${IMAGE_FORCE_COMPRESSION},oci-mediatypes=${IMAGE_OCI_MEDIA_TYPES}"
    # One target per bake invocation, not the whole "all" group in one shot: the
    # group's targets share large swaths of identical layer content (same base,
    # same staged-tool RUN steps), and baking them together makes BuildKit write
    # the same content-addressed layer from two concurrent solves into the same
    # governed builder's content store at once. Under host memory/IO pressure the
    # loser's write blocks on the winner's lock long enough to trip the builder's
    # health check and get killed mid-write ("ref layer-... locked for Ns...
    # unavailable" -> "context canceled" -> EOF). Serializing avoids the race, and
    # the second target's shared layers hit the local cache-from dir for free.
    for target in "${targets[@]}"; do
        safe="${target//[^A-Za-z0-9._-]/_}"
        bake_args=(docker buildx bake -f docker-bake.hcl "${target}")
        bake_args+=(--set "${target}.output=${output},dest=${OCI_LAYOUT_DIR}/${safe}.tar")
        bake_args+=(--set "${target}.attest=type=provenance,mode=${IMAGE_PROVENANCE_MODE}")
        if [[ "${IMAGE_SBOM}" == "true" ]]; then
            bake_args+=(--set "${target}.attest+=type=sbom")
        fi
        echo "[INFO] [oci-layout] governed single-build bake start (${target}) $(ts)"
        run_low_priority "${bake_args[@]}" "${CACHE_ARGS[@]}"
        echo "[INFO] [oci-layout] governed single-build bake end (${target}) $(ts)"
    done

    for target in "${targets[@]}"; do
        safe="${target//[^A-Za-z0-9._-]/_}"
        rm -rf "${OCI_LAYOUT_DIR}/${safe}"
        mkdir -p "${OCI_LAYOUT_DIR}/${safe}"
        tar -xf "${OCI_LAYOUT_DIR}/${safe}.tar" -C "${OCI_LAYOUT_DIR}/${safe}"
        rm -f "${OCI_LAYOUT_DIR}/${safe}.tar"
    done
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
    # See oci_layout_bake(): one target per invocation to avoid concurrent-solve
    # content-store lock races on layers shared between the group's targets.
    for target in "${targets[@]}"; do
        bake_args=(docker buildx bake -f docker-bake.hcl "${target}")
        bake_args+=(--set "${target}.output=${output}")
        bake_args+=(--set "${target}.attest=type=provenance,mode=${IMAGE_PROVENANCE_MODE}")
        if [[ "${IMAGE_SBOM}" == "true" ]]; then
            bake_args+=(--set "${target}.attest+=type=sbom")
        fi
        run_low_priority "${bake_args[@]}" "${CACHE_ARGS[@]}"
    done
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
    --set "*.cache-from=type=local,src=${CACHE_DIR}"
    --set "*.cache-to=type=local,dest=${CACHE_DIR},mode=max"
)
OCI_LAYOUT_DIR="${MDT_OCI_LAYOUT_DIR:-build/oci-layouts}"

case "${FLOW}" in
    load)
        # Do not use a short-circuit test as the branch's final command:
        # with `set -e`, a successful build action would otherwise return the
        # false status of `[[ "$ACTION" == push ]]` to the caller.
        if [[ "${ACTION}" == "build" ]]; then
            oci_layout_bake
        else
            echo "[INFO] RELEASE_IMAGE_FLOW=load: push is handled by build-push.py's" \
                 "digest-verified crane push (not this script)."
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

        # See oci_layout_bake(): one target per invocation to avoid concurrent-solve
        # content-store lock races on layers shared between the group's targets.
        echo "[INFO] [repack] governed OCI-layout bake start $(ts)"
        for target in "${targets[@]}"; do
            safe="${target//[^A-Za-z0-9._-]/_}"
            bake_args=(docker buildx bake -f docker-bake.hcl "${target}")
            bake_args+=(--set "${target}.output=type=oci,dest=${REPACK_WORK_DIR}/src-${safe}.tar")
            run_low_priority "${bake_args[@]}" "${CACHE_ARGS[@]}"
        done
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
