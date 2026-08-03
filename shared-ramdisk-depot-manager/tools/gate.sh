#!/usr/bin/env bash
# gate.sh — run srdm's gate suite in srdm's own gate container.
#
#   tools/gate.sh <worktree> [target]
#
# <worktree>  path to the checkout to test, as the COCKPIT sees it
#             (nyxloom substitutes {worktree}); defaults to this checkout.
# [target]    "unit" (default), "coverage" or "e2e".
#
# The `coverage` target measures CHANGED-line coverage against a base ref
# ($SRDM_COVERAGE_BASE, default main) and so needs a committed tree — it
# exits 3 (NO MEASUREMENT) rather than reporting a vacuous percentage when
# the diff cannot see the work. That is why it is a separate target from
# `unit`, which is meaningful on a dirty tree.
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
  unit|coverage|e2e) ;;
  *) die "unknown target \"$target\" (expected unit, coverage or e2e)" ;;
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
elif [ "$target" = "coverage" ]; then
  # -coverpkg=./... so packages with no test files of their own still appear
  # in the profile. Without it they are simply absent, and "absent" is
  # indistinguishable from "excluded by a build tag" — the gate would have to
  # guess, and guessing in the lenient direction is how a floor stops binding.
  cmd='set -euo pipefail
cd "$0"
go test ./... -count=1 -coverpkg=./... -covermode=atomic -coverprofile=/tmp/srdm-cover.out >/dev/null
exec go run ./tools/covergate \
  -profile /tmp/srdm-cover.out \
  -base "'"${SRDM_COVERAGE_BASE:-main}"'" \
  -source internal \
  -fail-under "'"${SRDM_COVERAGE_FLOOR:-80}"'"'
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
