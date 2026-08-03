#!/usr/bin/env bash
# canary-run.sh — run tools/canary.sh inside the gate container.
#
# Same placement and fail-closed transport rules as tools/gate.sh: the
# verdict comes from `docker wait`, never from an attached stream.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
repo_root="$(cd "$project_dir/.." && pwd)"

die() { printf 'canary-run: %s\n' "$*" >&2; exit 1; }

host_repo_root="${SRDM_HOST_REPO_ROOT:-}"
if [ -z "$host_repo_root" ]; then
  self="$(cat /etc/hostname 2>/dev/null || true)"
  if [ -n "$self" ]; then
    host_repo_root="$(docker inspect "$self" \
      --format '{{range .Mounts}}{{if eq .Destination "'"$repo_root"'"}}{{.Source}}{{end}}{{end}}' \
      2>/dev/null || true)"
  fi
fi
[ -n "$host_repo_root" ] || die "set SRDM_HOST_REPO_ROOT to the host path of $repo_root"

cgroup_parent="$("$here/cgroup-parent.sh" "${SRDM_CGROUP_PARENT:-}")"

image="srdm-gate:unit"
docker image inspect "$image" >/dev/null 2>&1 || \
  docker build --cgroup-parent="$cgroup_parent" \
    -f "$project_dir/gate/Dockerfile" --target unit -t "$image" "$project_dir/gate"

rel_project="${project_dir#"$repo_root"/}"

cid="$(docker run -d \
  --cgroup-parent="$cgroup_parent" \
  -v "$host_repo_root:$repo_root" \
  -w "$repo_root/$rel_project" \
  "$image" bash tools/canary.sh)"

code="$(docker wait "$cid")"
docker logs "$cid"
docker rm -f "$cid" >/dev/null 2>&1 || true

printf 'canary-run: exited %s\n' "$code" >&2
exit "$code"
