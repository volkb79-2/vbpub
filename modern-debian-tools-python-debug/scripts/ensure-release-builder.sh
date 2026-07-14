#!/usr/bin/env bash
# Create/select the resource-governed BuildKit builder used by release jobs.

set -euo pipefail

BUILDER="${BUILDX_BUILDER:?BUILDX_BUILDER must be set by release configuration}"
MEMORY="${MDT_BUILDER_MEMORY:?MDT_BUILDER_MEMORY must be set}"
MEMORY_SWAP="${MDT_BUILDER_MEMORY_SWAP:?MDT_BUILDER_MEMORY_SWAP must be set}"
CPU_SHARES="${MDT_BUILDER_CPU_SHARES:?MDT_BUILDER_CPU_SHARES must be set}"
CPU_QUOTA="${MDT_BUILDER_CPU_QUOTA:?MDT_BUILDER_CPU_QUOTA must be set}"
CPU_PERIOD="${MDT_BUILDER_CPU_PERIOD:?MDT_BUILDER_CPU_PERIOD must be set}"

size_bytes() {
    local value="${1,,}" number suffix multiplier
    if [[ ! "${value}" =~ ^([0-9]+)([kmgt]i?b?|b)?$ ]]; then
        echo "[ERROR] Unsupported Docker size '${1}'; use an integer with b/k/m/g/t suffix." >&2
        return 2
    fi
    number="${BASH_REMATCH[1]}"
    suffix="${BASH_REMATCH[2]:-b}"
    case "${suffix}" in
        b) multiplier=1 ;;
        k|kb|kib) multiplier=$((1024)) ;;
        m|mb|mib) multiplier=$((1024 * 1024)) ;;
        g|gb|gib) multiplier=$((1024 * 1024 * 1024)) ;;
        t|tb|tib) multiplier=$((1024 * 1024 * 1024 * 1024)) ;;
        *) return 2 ;;
    esac
    echo $((number * multiplier))
}

create_builder() {
    echo "[INFO] Creating governed buildx builder '${BUILDER}'"
    docker buildx create \
        --name "${BUILDER}" \
        --driver docker-container \
        --driver-opt "memory=${MEMORY}" \
        --driver-opt "memory-swap=${MEMORY_SWAP}" \
        --driver-opt "cpu-shares=${CPU_SHARES}" \
        --driver-opt "cpu-quota=${CPU_QUOTA}" \
        --driver-opt "cpu-period=${CPU_PERIOD}" >/dev/null
}

if ! docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
    create_builder
fi

docker buildx inspect "${BUILDER}" --bootstrap >/dev/null

container="buildx_buildkit_${BUILDER}0"
expected_memory="$(size_bytes "${MEMORY}")"
expected_swap="$(size_bytes "${MEMORY_SWAP}")"

inspect_limits() {
    docker inspect "${container}" --format \
        '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpuShares}} {{.HostConfig.CpuQuota}} {{.HostConfig.CpuPeriod}}'
}

read -r actual_memory actual_swap actual_shares actual_quota actual_period < <(inspect_limits)
driver="$(docker buildx inspect "${BUILDER}" | awk -F: '/^Driver:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"

if [[ "${driver}" != "docker-container" || \
      "${actual_memory}" != "${expected_memory}" || \
      "${actual_swap}" != "${expected_swap}" || \
      "${actual_shares}" != "${CPU_SHARES}" || \
      "${actual_quota}" != "${CPU_QUOTA}" || \
      "${actual_period}" != "${CPU_PERIOD}" ]]; then
    echo "[WARN] Builder '${BUILDER}' does not match cmru.build.toml; recreating it." >&2
    echo "[WARN] actual: driver=${driver} memory=${actual_memory} memory+swap=${actual_swap} shares=${actual_shares} cpu=${actual_quota}/${actual_period}" >&2
    docker buildx rm "${BUILDER}" >/dev/null
    create_builder
    docker buildx inspect "${BUILDER}" --bootstrap >/dev/null
    read -r actual_memory actual_swap actual_shares actual_quota actual_period < <(inspect_limits)
fi

if [[ "${actual_memory}" != "${expected_memory}" || \
      "${actual_swap}" != "${expected_swap}" || \
      "${actual_shares}" != "${CPU_SHARES}" || \
      "${actual_quota}" != "${CPU_QUOTA}" || \
      "${actual_period}" != "${CPU_PERIOD}" ]]; then
    echo "[ERROR] Docker did not apply the configured limits to builder '${BUILDER}'." >&2
    exit 3
fi

echo "[INFO] Builder '${BUILDER}': memory=${actual_memory} total-memory+swap=${actual_swap} cpu-shares=${actual_shares} cpu=${actual_quota}/${actual_period}"
