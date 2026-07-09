#!/usr/bin/env bash
# Repack and push the locally loaded release images.
#
# NOTE: When run via the new cmru oci-image handler (cmru.toml
# [project.xxx.oci] repack=true), docker-repack handles both OCI layout
# creation and push directly — skopeo is no longer needed for the
# cmru-driven path. This script remains for backward compatibility and
# manual/local use outside cmru.
#
# Prerequisites:
#   - jq
#   - skopeo
#   - docker-repack
#
# This script expects `docker buildx bake -f docker-bake.hcl all --load` to have
# already populated the local daemon with the release tags for the current build.
#
# Environment:
#   - BAKE_FILE=docker-bake.hcl
#   - REPACK_GROUP=all
#   - REPACK_TARGET_SIZE=2GB
#   - REPACK_JOBS=3: bounded worker count for the CPU- and disk-heavy repack
#     stage on this 16G host that also runs a game server.
#   - REPACK_COMPRESSION_LEVEL=9: zstd level passed to docker-repack; the
#     default is a deliberate speed-over-size tradeoff for release throughput.
#   - REPACK_CONCURRENCY: optional per-process concurrency cap passed through to
#     docker-repack when the operator wants to bound CPU across several workers.
#   - DOCKER_REPACK_BIN=docker-repack

set -euo pipefail

BAKE_FILE="${BAKE_FILE:-docker-bake.hcl}"
GROUP="${REPACK_GROUP:-all}"
TARGET_SIZE="${REPACK_TARGET_SIZE:-2GB}"
REPACK_JOBS="${REPACK_JOBS:-3}"
REPACK_COMPRESSION_LEVEL="${REPACK_COMPRESSION_LEVEL:-9}"
REPACK_CONCURRENCY="${REPACK_CONCURRENCY:-}"
DOCKER_REPACK_BIN="${DOCKER_REPACK_BIN:-docker-repack}"

for bin in jq skopeo; do
    command -v "${bin}" >/dev/null 2>&1 || {
        echo "[ERROR] '${bin}' not found — install it before running the repack release flow." >&2
        exit 1
    }
done
case "${REPACK_JOBS}" in
    ''|*[!0-9]*|0)
        echo "[ERROR] REPACK_JOBS must be a positive integer." >&2
        exit 1
        ;;
esac
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

worker_target() {
    set -euo pipefail

    local target="$1"
    local source_tag="$2"
    # Deliberately NOT local: the EXIT trap fires after this function has
    # RETURNED on the success path, when locals are already torn down — a
    # local here means "rc_file: unbound variable" under set -u and a missing
    # rc file, which the parent reports as failure. Each worker runs in its
    # own subshell, so these can't leak across workers.
    WORKER_SRC_OCI="$3"
    WORKER_DST_OCI="$4"
    WORKER_RC_FILE="$5"
    shift 5
    local -a tags=("$@")
    local -a repack_args=(--target-size "${TARGET_SIZE}" --compression-level "${REPACK_COMPRESSION_LEVEL}")

    if [ -n "${REPACK_CONCURRENCY}" ]; then
        repack_args+=(--concurrency "${REPACK_CONCURRENCY}")
    fi

    # Write the exit code to a file because bash `wait -n` tells the parent that
    # some worker finished, but not which one, unless we depend on nonportable
    # `wait -p` support.
    trap 'rc=$?; set +e; printf "%s\n" "$rc" >"$WORKER_RC_FILE"; rm -rf "$WORKER_SRC_OCI" "$WORKER_DST_OCI"' EXIT

    echo "[INFO]     source tag ${source_tag}"
    run_low_priority skopeo copy --quiet "docker-daemon:${source_tag}" "oci:${WORKER_SRC_OCI}:source"
    run_low_priority "${DOCKER_REPACK_BIN}" "${repack_args[@]}" "oci://${WORKER_SRC_OCI}" "oci://${WORKER_DST_OCI}"

    for DEST_TAG in "${tags[@]}"; do
        echo "[INFO]     push ${DEST_TAG}"
        # docker-repack writes an OCI layout without preserving the original tag;
        # the layout itself contains the single repacked manifest.
        run_low_priority skopeo copy --quiet "oci:${WORKER_DST_OCI}" "docker://${DEST_TAG}"
    done

    # Clean up immediately after the pushes finish so peak disk stays bounded by
    # REPACK_JOBS instead of accumulating every target's OCI layouts until exit.
    rm -rf "${WORKER_SRC_OCI}" "${WORKER_DST_OCI}"
    echo "[INFO]     pushed ${#tags[@]} tag(s)"
}

report_finished_target() {
    local target="$1"
    local rc_file="$2"
    local log_file="$3"
    local tag_count="$4"
    local rc

    if [ -f "${rc_file}" ]; then
        rc="$(<"${rc_file}")"
    else
        rc=1
    fi
    case "${rc}" in
        ''|*[!0-9]*)
            rc=1
            ;;
    esac

    echo "[INFO] target ${target} finished (rc=${rc})"
    if [ "${rc}" -eq 0 ]; then
        echo "[INFO] target ${target} pushed ${tag_count} tag(s)"
    else
        FAILED_TARGETS+=("${target}")
        TARGET_RCS["${target}"]="${rc}"
        echo "[ERROR] target ${target} failed; logfile follows:" >&2
        if [ -f "${log_file}" ]; then
            cat "${log_file}" >&2
        else
            echo "[ERROR] logfile missing: ${log_file}" >&2
        fi
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
declare -A TARGET_LOG_FILES=()
declare -A TARGET_RC_FILES=()
declare -A TARGET_TAG_COUNTS=()
declare -A TARGET_RCS=()
declare -a PENDING_TARGETS=()
declare -a FAILED_TARGETS=()

running_jobs=0

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
    LOG_FILE="${WORK}/target-${SAFE_TARGET}.log"
    RC_FILE="${WORK}/rc-${SAFE_TARGET}"

    TARGET_LOG_FILES["${target}"]="${LOG_FILE}"
    TARGET_RC_FILES["${target}"]="${RC_FILE}"
    TARGET_TAG_COUNTS["${target}"]="${#TAGS[@]}"

    printf '[INFO] target %s started\n' "${target}"
    PENDING_TARGETS+=("${target}")

    (
        worker_target "${target}" "${SOURCE_TAG}" "${SRC_OCI}" "${DST_OCI}" "${RC_FILE}" "${TAGS[@]}"
    ) >"${LOG_FILE}" 2>&1 &

    running_jobs=$((running_jobs + 1))

    if [ "${running_jobs}" -ge "${REPACK_JOBS}" ]; then
        wait_rc=0
        wait -n || wait_rc=$?

        # Pop one completed worker by looking for the rc file the worker wrote.
        # This keeps the control flow portable across bash versions that have
        # `wait -n` but not `wait -p`.
        for idx in "${!PENDING_TARGETS[@]}"; do
            target="${PENDING_TARGETS[$idx]}"
            rc_file="${TARGET_RC_FILES[$target]}"
            if [ -f "${rc_file}" ]; then
                report_finished_target "${target}" "${rc_file}" "${TARGET_LOG_FILES[$target]}" "${TARGET_TAG_COUNTS[$target]}"
                unset "PENDING_TARGETS[$idx]"
                running_jobs=$((running_jobs - 1))
                break
            fi
        done
    fi
done

while [ "${running_jobs}" -gt 0 ]; do
    wait_rc=0
    wait -n || wait_rc=$?

    for idx in "${!PENDING_TARGETS[@]}"; do
        target="${PENDING_TARGETS[$idx]}"
        rc_file="${TARGET_RC_FILES[$target]}"
        if [ -f "${rc_file}" ]; then
            report_finished_target "${target}" "${rc_file}" "${TARGET_LOG_FILES[$target]}" "${TARGET_TAG_COUNTS[$target]}"
            unset "PENDING_TARGETS[$idx]"
            running_jobs=$((running_jobs - 1))
            break
        fi
    done
done

if [ "${#FAILED_TARGETS[@]}" -ne 0 ]; then
    echo "[ERROR] Failed targets:" >&2
    for target in "${FAILED_TARGETS[@]}"; do
        echo "[ERROR]   - ${target} (rc=${TARGET_RCS[$target]:-1})" >&2
    done
    exit 1
fi
