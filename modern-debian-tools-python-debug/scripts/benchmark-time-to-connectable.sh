#!/usr/bin/env bash
# Cold image delivery benchmark in a disposable Docker 29 daemon.
#
# Usage:
#   scripts/benchmark-time-to-connectable.sh LABEL IMAGE_REF [containerd|overlay2] [RESULT.json]
#
# The empty DinD volume makes pull/unpack cold without deleting or perturbing
# the host daemon's cache. This measures Docker-connectable time; VS Code
# feature builds, lifecycle hooks, server installation and extension startup
# are separate phases described in docs/IMAGE-DELIVERY-BENCHMARKS.md.

set -euo pipefail

LABEL="${1:?label required}"
IMAGE_REF="${2:?image reference required}"
STORE="${3:-containerd}"
RESULT="${4:-benchmark-${LABEL}.json}"
DIN_D_IMAGE="${DIN_D_IMAGE:-docker:29.6.1-dind}"
CPUS="${BENCH_CPUS:-6}"
MEMORY="${BENCH_MEMORY:-8g}"
MEMORY_SWAP="${BENCH_MEMORY_SWAP:-12g}"

case "${STORE}" in
    containerd) daemon_args=() ;;
    overlay2) daemon_args=(--feature containerd-snapshotter=false --storage-driver overlay2) ;;
    *) echo "[ERROR] store must be containerd or overlay2" >&2; exit 2 ;;
esac

for command in docker jq; do
    command -v "${command}" >/dev/null 2>&1 || {
        echo "[ERROR] ${command} is required" >&2
        exit 2
    }
done

safe="$(tr -c 'a-zA-Z0-9_.-' '_' <<<"${LABEL}")"
daemon="mdt-connect-bench-${safe}-$$"
volume="${daemon}-data"
work="$(mktemp -d)"
samples="${RESULT%.json}.samples.tsv"

cleanup() {
    docker rm -f "${daemon}" >/dev/null 2>&1 || true
    docker volume rm "${volume}" >/dev/null 2>&1 || true
    rm -rf "${work}"
}
trap cleanup EXIT

docker volume create "${volume}" >/dev/null
docker run -d --privileged --name "${daemon}" \
    --cpus "${CPUS}" --memory "${MEMORY}" --memory-swap "${MEMORY_SWAP}" \
    -v "${volume}:/var/lib/docker" "${DIN_D_IMAGE}" \
    "${daemon_args[@]}" >/dev/null

for _ in $(seq 1 120); do
    if docker exec "${daemon}" docker info >/dev/null 2>&1; then
        break
    fi
    sleep 0.25
done
docker exec "${daemon}" docker info >/dev/null

{
    while docker inspect "${daemon}" >/dev/null 2>&1; do
        printf '%s\t' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        docker stats --no-stream --format '{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' "${daemon}"
        sleep 1
    done
} >"${samples}" &
sampler_pid=$!

duration_ns() {
    local start="$1"
    printf '%s' "$(( $(date +%s%N) - start ))"
}

start="$(date +%s%N)"
docker exec "${daemon}" docker pull "${IMAGE_REF}" >"${work}/pull.log"
pull_ns="$(duration_ns "${start}")"

image_json="$(docker exec "${daemon}" docker image inspect "${IMAGE_REF}")"
size="$(jq -r '.[0].Size' <<<"${image_json}")"
layers="$(jq -r '.[0].RootFS.Layers | length' <<<"${image_json}")"

start="$(date +%s%N)"
cid="$(docker exec "${daemon}" docker create "${IMAGE_REF}" sleep infinity)"
create_ns="$(duration_ns "${start}")"

start="$(date +%s%N)"
docker exec "${daemon}" docker start "${cid}" >/dev/null
start_ns="$(duration_ns "${start}")"

start="$(date +%s%N)"
docker exec "${daemon}" docker exec "${cid}" true
exec_ns="$(duration_ns "${start}")"

start="$(date +%s%N)"
docker exec "${daemon}" docker exec "${cid}" sh -lc \
    'node --version; npm --version; python3 --version' >"${work}/probes.log"
probes_ns="$(duration_ns "${start}")"

kill "${sampler_pid}" >/dev/null 2>&1 || true
wait "${sampler_pid}" 2>/dev/null || true

jq -n \
    --arg label "${LABEL}" \
    --arg image_ref "${IMAGE_REF}" \
    --arg store "${STORE}" \
    --arg measured_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg samples "${samples}" \
    --argjson size "${size}" \
    --argjson layers "${layers}" \
    --argjson pull_ns "${pull_ns}" \
    --argjson create_ns "${create_ns}" \
    --argjson start_ns "${start_ns}" \
    --argjson first_exec_ns "${exec_ns}" \
    --argjson probes_ns "${probes_ns}" \
    '{schema: 1, label: $label, image_ref: $image_ref, store: $store,
      measured_at: $measured_at, image: {compressed_bytes: $size, layers: $layers},
      durations_ns: {pull_unpack: $pull_ns, create: $create_ns, start: $start_ns,
        first_exec: $first_exec_ns, tool_probes: $probes_ns}, samples_tsv: $samples}' \
    >"${RESULT}"

cat "${RESULT}"
