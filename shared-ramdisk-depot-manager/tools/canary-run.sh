#!/usr/bin/env bash
# canary-run.sh — run tools/canary.sh inside the gate container.
#
# Same placement and fail-closed transport rules as tools/gate.sh: the
# verdict comes from `docker wait`, never from an attached stream.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
repo_root="$(cd "$project_dir/.." && pwd)"
# shellcheck source=host-path.sh
source "$here/host-path.sh"

die() { printf 'canary-run: %s\n' "$*" >&2; exit 1; }

host_repo_root="${SRDM_HOST_REPO_ROOT:-}"
if [ -z "$host_repo_root" ]; then
  host_repo_root="$(host_path_from_mounts "$repo_root")"
fi
[ -n "$host_repo_root" ] || die "set SRDM_HOST_REPO_ROOT to the host path of $repo_root"

git_common_dir="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null || true)"
primary_mount_args=()
if [ -n "$git_common_dir" ] && [ "$git_common_dir" != "$repo_root/.git" ]; then
  host_common_git="$(host_path_from_mounts "$git_common_dir")"
  if [ -z "$host_common_git" ] && [ "$host_repo_root" = "$repo_root" ]; then
    host_common_git="$git_common_dir"
  fi
  [ -n "$host_common_git" ] || die "cannot determine the host path of Git common directory $git_common_dir"
  host_primary_root="${host_common_git%/.git}"
  container_primary_root="${git_common_dir%/.git}"
  primary_mount_args=(-v "$host_primary_root:$container_primary_root:ro")
fi

cgroup_parent="$("$here/cgroup-parent.sh" "${SRDM_CGROUP_PARENT:-}")"

image="srdm-gate:unit"
# gate/Dockerfile's `unit` target is now an alias for the shared Go base
# (assay decisions.md A-043), so the base must exist first. tools/go-base.sh
# owns that rule; tools/gate.sh calls the same helper.
base_image="$("$here/go-base.sh" "$cgroup_parent")"
docker image inspect "$image" >/dev/null 2>&1 || \
  docker build --cgroup-parent="$cgroup_parent" \
    --build-arg "BASE_IMAGE=$base_image" \
    -f "$project_dir/gate/Dockerfile" --target unit -t "$image" "$project_dir/gate"

rel_project="${project_dir#"$repo_root"/}"

cid="$(docker run -d \
  --cgroup-parent="$cgroup_parent" \
  "${primary_mount_args[@]}" \
  -v "$host_repo_root:$repo_root" \
  -w "$repo_root/$rel_project" \
  "$image" bash -c 'git config --global --add safe.directory "*"; exec bash tools/canary.sh')"

code="$(docker wait "$cid")"
logs_code=0
docker logs "$cid" || logs_code=$?
docker rm -f "$cid" >/dev/null 2>&1 || true

printf 'canary-run: exited %s\n' "$code" >&2
if [ "$logs_code" -ne 0 ]; then
  printf 'canary-run: Docker log retrieval failed with %s\n' "$logs_code" >&2
  [ "$code" -ne 0 ] || exit 3
fi
exit "$code"
