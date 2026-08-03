#!/usr/bin/env bash
# gate.sh — run srdm's gate suite in srdm's own gate container.
#
#   tools/gate.sh <worktree> [target]
#
# <worktree>  path to the checkout to test, as the COCKPIT sees it
#             (nyxloom substitutes {worktree}); defaults to this checkout.
# [target]    "unit" (default) or "e2e".
#
# Three doctrines are load-bearing here, each the residue of a real failure:
#
# 1. The gate is never the cockpit. It runs in srdm-gate, whose pins are the
#    ones that ship. (The cockpit has no Go toolchain at all.)
#
# 2. Placement is verified, not assumed. tools/cgroup-parent.sh proves the
#    tier is a real configured slice — a typo'd name fails OPEN into an
#    unlimited transient slice beside production.
#
# 3. The verdict is read separately from the run, and the transport cannot
#    forge it. The container runs DETACHED; `docker wait` yields the exit
#    code and `docker logs` the output. An attached/hijacked stream over a
#    truncating relay can drop output mid-run and hand back a forged exit
#    code, so a failing gate reads as passing.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
repo_root="$(cd "$project_dir/.." && pwd)"

worktree="${1:-$repo_root}"
target="${2:-unit}"

die() { printf 'gate: %s\n' "$*" >&2; exit 1; }

case "$target" in
  unit|e2e) ;;
  *) die "unknown target \"$target\" (expected unit or e2e)" ;;
esac

# --- the host path of the repo -----------------------------------------
# Docker resolves bind sources in the HOST namespace, so the cockpit's own
# view of the path is not usable. Derive it from this container's mounts
# rather than hardcoding an operator's home directory.
host_repo_root="${SRDM_HOST_REPO_ROOT:-}"
if [ -z "$host_repo_root" ]; then
  self="$(cat /etc/hostname 2>/dev/null || true)"
  if [ -n "$self" ]; then
    host_repo_root="$(docker inspect "$self" \
      --format '{{range .Mounts}}{{if eq .Destination "'"$repo_root"'"}}{{.Source}}{{end}}{{end}}' \
      2>/dev/null || true)"
  fi
fi
[ -n "$host_repo_root" ] || die "cannot determine the host path of $repo_root.
  Set SRDM_HOST_REPO_ROOT to it (the path as the DOCKER HOST sees this repo)."

# --- placement -----------------------------------------------------------
cgroup_parent="$("$here/cgroup-parent.sh" "${SRDM_CGROUP_PARENT:-}")"

# --- image ---------------------------------------------------------------
image="srdm-gate:$target"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  printf 'gate: building %s\n' "$image" >&2
  docker build --cgroup-parent="$cgroup_parent" \
    -f "$project_dir/gate/Dockerfile" --target "$target" -t "$image" "$project_dir/gate"
fi

rel_project="${project_dir#"$repo_root"/}"
work_in_container="$worktree/$rel_project"

if [ "$target" = "unit" ]; then
  cmd='set -euo pipefail
cd "$0"
unformatted="$(gofmt -l .)"
if [ -n "$unformatted" ]; then
  printf "gofmt: these files are not formatted:\n%s\n" "$unformatted" >&2
  exit 1
fi
go build ./...
go vet ./...
go test ./... -count=1'
  run_args=()
else
  # P02 fills this in. Declared and buildable now so the gate declaration is
  # backed by a real image rather than a promise (decision D-004).
  cmd='set -euo pipefail
cd "$0"
go test ./... -count=1 -tags=e2e -run TestE2E'
  run_args=(--privileged --cgroupns=host)
fi

# Detached run: the verdict comes from `docker wait`, never from the stream.
cid="$(docker run -d \
  --cgroup-parent="$cgroup_parent" \
  "${run_args[@]}" \
  -v "$host_repo_root:$repo_root" \
  -w "$work_in_container" \
  "$image" \
  bash -c "$cmd" "$work_in_container")"

code="$(docker wait "$cid")"
docker logs "$cid"
docker rm -f "$cid" >/dev/null 2>&1 || true

printf 'gate: %s exited %s\n' "$target" "$code" >&2
exit "$code"
