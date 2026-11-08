#!/usr/bin/env bash
# Verify the host-managed remote builder used by every MDT release build.
# This script intentionally has no create/remove/container-driver path: a
# release must fail rather than create an ungoverned worker.
set -euo pipefail

: "${BUILDX_BUILDER:?BUILDX_BUILDER must come from the host endpoint record}"
if [[ "$BUILDX_BUILDER" != mdt-managed ]]; then
  echo "[ERROR] release builds require BUILDX_BUILDER=mdt-managed; got ${BUILDX_BUILDER}" >&2
  exit 2
fi
: "${BUILDKIT_HOST:?BUILDKIT_HOST must come from the host endpoint record}"
: "${MDT_BUILDKIT_SOCKET:?MDT_BUILDKIT_SOCKET must come from the host endpoint record}"
if [[ "$MDT_BUILDKIT_SOCKET" != /* || "$BUILDKIT_HOST" != "unix://$MDT_BUILDKIT_SOCKET" ]]; then
  echo "[ERROR] BUILDKIT_HOST must equal unix://MDT_BUILDKIT_SOCKET, got BUILDKIT_HOST=${BUILDKIT_HOST} MDT_BUILDKIT_SOCKET=${MDT_BUILDKIT_SOCKET}" >&2
  exit 2
fi

docker_bin="$(command -v docker || true)"
if [[ -z "$docker_bin" ]]; then
  echo "[ERROR] Docker/Buildx is unavailable; cannot verify the managed release builder" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$script_dir/mdt_buildkit_builder.py" verify \
  --docker "$docker_bin" --endpoint "$BUILDKIT_HOST"
echo "[INFO] release builder $BUILDX_BUILDER verified at $BUILDKIT_HOST"
