#!/usr/bin/env bash
# Repack pre-built OCI layouts and push them with BuildKit.
#
# The release bake step writes one source layout per target to
#   ${REPACK_WORK_DIR}/src-<target>
# This script never loads an image into dockerd and does not require skopeo.

set -euo pipefail

BAKE_FILE="${BAKE_FILE:-docker-bake.hcl}"
GROUP="${REPACK_GROUP:-all}"
WORK="${REPACK_WORK_DIR:?REPACK_WORK_DIR must point at disk-backed release scratch}"
TARGET_SIZE="${REPACK_TARGET_SIZE:?REPACK_TARGET_SIZE must be configured}"
REPACK_JOBS="${REPACK_JOBS:?REPACK_JOBS must be configured}"
REPACK_COMPRESSION_LEVEL="${REPACK_COMPRESSION_LEVEL:?REPACK_COMPRESSION_LEVEL must be configured}"
REPACK_CONCURRENCY="${REPACK_CONCURRENCY:?REPACK_CONCURRENCY must be configured}"
REPACK_VMEM_KB="${REPACK_VMEM_KB:?REPACK_VMEM_KB must be configured}"
DOCKER_REPACK_BIN="${DOCKER_REPACK_BIN:-docker-repack}"
DOCKER_REPACK_LOG="${DOCKER_REPACK_LOG:?DOCKER_REPACK_LOG must be configured}"

for bin in docker jq "${DOCKER_REPACK_BIN}"; do
    command -v "${bin}" >/dev/null 2>&1 || {
        echo "[ERROR] '${bin}' is required by the OCI-layout repack flow." >&2
        exit 3
    }
done
for value_name in REPACK_JOBS REPACK_CONCURRENCY; do
    value="${!value_name}"
    case "${value}" in
        ''|*[!0-9]*|0)
            echo "[ERROR] ${value_name} must be a positive integer." >&2
            exit 2
            ;;
    esac
done
if [[ "${REPACK_VMEM_KB}" != "unlimited" && ! "${REPACK_VMEM_KB}" =~ ^[1-9][0-9]*$ ]]; then
    echo "[ERROR] REPACK_VMEM_KB must be 'unlimited' or a positive integer." >&2
    exit 2
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

    local target="$1" src_oci="$2" dst_oci="$3" rc_file="$4" tmp_dir="$5" manifest_dir="$6"
    shift 6
    local -a tags=("$@")
    local -a push_args

    trap 'rc=$?; set +e; printf "%s\n" "$rc" >"$rc_file"; rm -rf "$src_oci" "$dst_oci" "$tmp_dir"' EXIT
    if [[ "${REPACK_VMEM_KB}" != "unlimited" ]]; then
        ulimit -v "${REPACK_VMEM_KB}"
    fi
    mkdir -p "${tmp_dir}"
    export TMPDIR="${tmp_dir}"

    [[ -f "${src_oci}/index.json" ]] || {
        echo "[ERROR] Missing source OCI layout for ${target}: ${src_oci}" >&2
        exit 1
    }

    echo "[INFO]     repack ${src_oci} -> ${dst_oci}"
    RUST_LOG="${DOCKER_REPACK_LOG}" run_low_priority "${DOCKER_REPACK_BIN}" \
        --target-size "${TARGET_SIZE}" \
        --compression-level "${REPACK_COMPRESSION_LEVEL}" \
        --concurrency "${REPACK_CONCURRENCY}" \
        "oci://${src_oci}" "oci://${dst_oci}"

    rm -rf "${manifest_dir}"
    echo "[INFO]     extract canonical manifest from repacked OCI layout"
    run_low_priority docker buildx build \
        --file scripts/repack-push.Dockerfile \
        --target manifest \
        --build-context "repacked=oci-layout://${dst_oci}" \
        --output "type=local,dest=${manifest_dir}" \
        .
    [[ -s "${manifest_dir}/manifest.md" ]] || {
        echo "[ERROR] Repacked target ${target} did not export its canonical manifest." >&2
        exit 1
    }

    push_args=(
        docker buildx build
        --file scripts/repack-push.Dockerfile
        --target publish
        --build-context "repacked=oci-layout://${dst_oci}"
        --provenance=false
        --sbom=false
        --push
    )
    for tag in "${tags[@]}"; do
        push_args+=(--tag "${tag}")
    done
    push_args+=(.)

    echo "[INFO]     push ${#tags[@]} tag(s) from repacked OCI layout"
    run_low_priority "${push_args[@]}"
    echo "[INFO]     target ${target} pushed"
}

BAKE_JSON="$(docker buildx bake -f "${BAKE_FILE}" "${GROUP}" --print)"
mapfile -t TARGETS < <(jq -r --arg group "${GROUP}" '.group[$group].targets[]?' <<<"${BAKE_JSON}")
if [[ "${#TARGETS[@]}" -eq 0 ]]; then
    echo "[ERROR] No targets found in bake group '${GROUP}'." >&2
    exit 1
fi

mkdir -p "${WORK}/logs" "${WORK}/tmp" "${WORK}/manifests"
declare -A LOG_FILES=() RC_FILES=() TAG_COUNTS=() TARGET_RCS=()
declare -a PENDING=() FAILED=()
running=0

report_finished() {
    local target="$1" rc_file="${RC_FILES[$1]}" rc=1
    [[ -f "${rc_file}" ]] && rc="$(<"${rc_file}")"
    [[ "${rc}" =~ ^[0-9]+$ ]] || rc=1
    echo "[INFO] target ${target} finished (rc=${rc})"
    if [[ "${rc}" -ne 0 ]]; then
        FAILED+=("${target}")
        TARGET_RCS["${target}"]="${rc}"
        cat "${LOG_FILES[$target]}" >&2
    else
        echo "[INFO] target ${target} pushed ${TAG_COUNTS[$target]} tag(s)"
    fi
}

collect_one() {
    wait -n || true
    for idx in "${!PENDING[@]}"; do
        target="${PENDING[$idx]}"
        if [[ -f "${RC_FILES[$target]}" ]]; then
            report_finished "${target}"
            unset 'PENDING[idx]'
            running=$((running - 1))
            return
        fi
    done
    echo "[ERROR] A repack worker exited without reporting its status." >&2
    exit 1
}

for target in "${TARGETS[@]}"; do
    mapfile -t TAGS < <(jq -r --arg target "${target}" '.target[$target].tags[]?' <<<"${BAKE_JSON}")
    [[ "${#TAGS[@]}" -gt 0 ]] || continue

    safe="${target//[^A-Za-z0-9._-]/_}"
    src="${WORK}/src-${safe}"
    dst="${WORK}/repacked-${safe}"
    log="${WORK}/logs/${safe}.log"
    rc="${WORK}/logs/${safe}.rc"
    tmp="${WORK}/tmp/${safe}"
    manifest_dir="${WORK}/manifests/${safe}"
    rm -f "${rc}"

    LOG_FILES["${target}"]="${log}"
    RC_FILES["${target}"]="${rc}"
    TAG_COUNTS["${target}"]="${#TAGS[@]}"
    PENDING+=("${target}")
    echo "[INFO] target ${target} started"
    (worker_target "${target}" "${src}" "${dst}" "${rc}" "${tmp}" "${manifest_dir}" "${TAGS[@]}") >"${log}" 2>&1 &
    running=$((running + 1))
    [[ "${running}" -lt "${REPACK_JOBS}" ]] || collect_one
done

while [[ "${running}" -gt 0 ]]; do
    collect_one
done

if [[ "${#FAILED[@]}" -gt 0 ]]; then
    for target in "${FAILED[@]}"; do
        echo "[ERROR] ${target} failed (rc=${TARGET_RCS[$target]})" >&2
    done
    exit 1
fi
