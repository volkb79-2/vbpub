#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

services=(
  compose.nobara_fedora.yml
  compose.popos_ubuntu.yml
  compose.garuda_arch.yml
  compose.regata_opensuse.yml
  compose.bazzite_fedora_atomic.yml
)

echo "[INFO] running package probes sequentially (one distro container at a time)"
for compose_file in "${services[@]}"; do
  distro_name="${compose_file#compose.}"
  distro_name="${distro_name%.yml}"
  echo ""
  echo "===== ${distro_name} ====="
  docker compose -f "$compose_file" up -d --quiet-pull
  container_id="$(docker compose -f "$compose_file" ps -q probe)"
  if [[ -z "$container_id" ]]; then
    echo "[ERROR] failed to resolve probe container id for ${compose_file}" >&2
    docker compose -f "$compose_file" down -v --remove-orphans || true
    exit 2
  fi
  docker cp "$SCRIPT_DIR/probe-inside.sh" "$container_id:/tmp/probe-inside.sh"
  docker compose -f "$compose_file" exec -T probe bash /tmp/probe-inside.sh "$distro_name"
  docker compose -f "$compose_file" down -v --remove-orphans
done

echo ""
echo "[INFO] done"
