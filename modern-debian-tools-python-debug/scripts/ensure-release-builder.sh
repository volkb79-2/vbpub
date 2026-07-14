#!/usr/bin/env bash
# Create/select the resource-governed BuildKit builder used by release jobs.

set -euo pipefail

BUILDER="${BUILDX_BUILDER:?BUILDX_BUILDER must be set by release configuration}"
MEMORY="${MDT_BUILDER_MEMORY:?MDT_BUILDER_MEMORY must be set}"
MEMORY_SWAP="${MDT_BUILDER_MEMORY_SWAP:?MDT_BUILDER_MEMORY_SWAP must be set}"
CPU_SHARES="${MDT_BUILDER_CPU_SHARES:?MDT_BUILDER_CPU_SHARES must be set}"
CPU_QUOTA="${MDT_BUILDER_CPU_QUOTA:?MDT_BUILDER_CPU_QUOTA must be set}"
CPU_PERIOD="${MDT_BUILDER_CPU_PERIOD:?MDT_BUILDER_CPU_PERIOD must be set}"

if ! docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
    echo "[INFO] Creating governed buildx builder '${BUILDER}'"
    docker buildx create \
        --name "${BUILDER}" \
        --driver docker-container \
        --driver-opt "memory=${MEMORY}" \
        --driver-opt "memory-swap=${MEMORY_SWAP}" \
        --driver-opt "cpu-shares=${CPU_SHARES}" \
        --driver-opt "cpu-quota=${CPU_QUOTA}" \
        --driver-opt "cpu-period=${CPU_PERIOD}" >/dev/null
fi

docker buildx inspect "${BUILDER}" --bootstrap >/dev/null

container="buildx_buildkit_${BUILDER}0"
read -r actual_memory actual_swap actual_shares actual_quota actual_period < <(
    docker inspect "${container}" --format \
        '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpuShares}} {{.HostConfig.CpuQuota}} {{.HostConfig.CpuPeriod}}'
)

if [[ "${actual_memory}" == "0" || "${actual_swap}" == "0" || \
      "${actual_shares}" == "0" || "${actual_quota}" == "0" ]]; then
    echo "[ERROR] Builder '${BUILDER}' exists without the required resource limits." >&2
    echo "[ERROR] Remove it with 'docker buildx rm ${BUILDER}' and retry." >&2
    exit 3
fi

echo "[INFO] Builder '${BUILDER}': memory=${actual_memory} total-memory+swap=${actual_swap} cpu-shares=${actual_shares} cpu=${actual_quota}/${actual_period}"
