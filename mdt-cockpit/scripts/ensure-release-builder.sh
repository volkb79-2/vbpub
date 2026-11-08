#!/usr/bin/env bash
# Verify the host-managed remote builder used by every MDT release build.
# This script intentionally has no create/remove/container-driver path: a
# release must fail rather than create an ungoverned worker.
set -euo pipefail

: "${BUILDX_BUILDER:?BUILDX_BUILDER must be mdt-managed (set by host setup/devcontainer)}"
if [[ "$BUILDX_BUILDER" != mdt-managed ]]; then
  echo "[ERROR] release builds require BUILDX_BUILDER=mdt-managed; got ${BUILDX_BUILDER}" >&2
  exit 2
fi
: "${BUILDKIT_HOST:?BUILDKIT_HOST must point at the host-managed BuildKit socket}"

docker_bin="$(command -v docker || true)"
if [[ -z "$docker_bin" ]]; then
  echo "[ERROR] Docker/Buildx is unavailable; cannot verify the managed release builder" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$script_dir/mdt_buildkit_builder.py" verify \
  --docker "$docker_bin" --endpoint "$BUILDKIT_HOST"
echo "[INFO] release builder mdt-managed verified at $BUILDKIT_HOST"
