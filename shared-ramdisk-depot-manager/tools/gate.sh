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
# `coverage` runs the same Go toolchain as `unit`; only `e2e` needs the
# privileged systemd image. Building a third identical image would just make
# a cache to invalidate.
image_target="$target"
[ "$target" = "coverage" ] && image_target="unit"
image="srdm-gate:$image_target"

# The Go toolchain half of gate/Dockerfile was promoted to the shared
# vbpub/tester-unified-go image, so BOTH srdm targets now build FROM it and it
# must exist first. tools/go-base.sh owns that rule (canary-run.sh needs it
# too, and one copy of a build rule is the point of this whole promotion).
base_image="$("$here/go-base.sh" "$cgroup_parent")"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  printf 'gate: building %s\n' "$image" >&2
  docker build --cgroup-parent="$cgroup_parent" \
    --build-arg "BASE_IMAGE=$base_image" \
    -f "$project_dir/gate/Dockerfile" --target "$image_target" -t "$image" "$project_dir/gate"
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
  -fail-under "'"${SRDM_COVERAGE_FLOOR:-75}"'"'
  run_args=()
fi

# --- e2e: boot systemd, then exec the suite into it ----------------------
#
# Structurally different from the other targets: the container's job is to
# BE a systemd host, so the suite cannot be its command. It boots detached,
# the harness waits for systemd to come up, and the suite runs via exec.
if [ "$target" = "e2e" ]; then
  # A daemon that refuses privileged containers cannot run this harness.
  # That is a property of the host, not a failing change — skip loudly, at
  # the one level where it is visible, and never from inside the suite.
  if ! docker run --rm --privileged --cgroup-parent="$cgroup_parent" \
        --network=none alpine:latest true >/dev/null 2>&1; then
    printf 'gate: SKIP — this Docker daemon refuses privileged containers, so the\n' >&2
    printf '      e2e harness cannot run here. The unit and canary gates still apply.\n' >&2
    exit 0
  fi

  # --cgroupns=private: systemd wants its own cgroup namespace root, and a
  # private one also keeps the measurement self-contained rather than
  # reading a tree other containers are moving under it.
  # /tmp needs exec and room: the suite builds test binaries and mounts
  # 64 MiB tmpfs blobs beneath it.
  cid="$(docker run -d \
    --cgroup-parent="$cgroup_parent" \
    --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:exec,size=2g \
    -v "$host_repo_root:$repo_root" \
    "$image")"
  trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT

  printf 'gate: waiting for systemd in %s\n' "${cid:0:12}" >&2
  state=""
  for _ in $(seq 60); do
    state="$(docker exec "$cid" systemctl is-system-running 2>/dev/null || true)"
    case "$state" in running|degraded) break ;; esac
    sleep 1
  done
  case "$state" in
    running|degraded) printf 'gate: systemd is %s\n' "$state" >&2 ;;
    *)
      printf 'gate: systemd did not boot (state=%s)\n' "${state:-unknown}" >&2
      docker logs "$cid" 2>&1 | tail -30 >&2
      exit 1
      ;;
  esac

  # The verdict is written to a file by the run and read back in a SEPARATE
  # step. An exec stream can be truncated or its status forged by a relay;
  # a file the suite wrote cannot be.
  docker exec "$cid" bash -c \
    "cd '$work_in_container' && go test ./... -count=1 -tags=e2e -v; echo \$? > /tmp/srdm-e2e.rc" || true
  code="$(docker exec "$cid" cat /tmp/srdm-e2e.rc 2>/dev/null || true)"
  case "$code" in
    ''|*[!0-9]*)
      printf 'gate: the e2e suite left no readable verdict (%s) — treating as failure\n' "${code:-empty}" >&2
      exit 1
      ;;
  esac

  printf 'gate: e2e exited %s\n' "$code" >&2
  exit "$code"
fi

# --- unit and coverage: the container's command IS the run ----------------
# Detached: the verdict comes from `docker wait`, never from the stream.
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
