#!/usr/bin/env bash
# gate.sh — run cgroup-profiler's suite in the shared vbpub gate container.
#
#   tools/gate.sh <worktree> [target]
#
# <worktree>  path to the checkout to test, as the COCKPIT sees it
#             (nyxloom substitutes {worktree}); defaults to this checkout.
# [target]    "unit" (default) or "coverage".
#
# Why tester-unified rather than an own image
# -------------------------------------------
# srdm ships its own gate container because it needs a Go toolchain and a
# privileged systemd-in-Docker harness — structural needs no shared image can
# serve. This project has neither. Its suite uses fake cgroup trees, injected
# clocks and stubbed sysfs by deliberate design (DESIGN.md §7): it never reads
# the real /sys/fs/cgroup, never starts a container, never sleeps. The only
# delta from the shared closure is this project's report-tier libraries, and
# tester-unified's own doctrine says to solve that by deriving the closure from
# the project's pyproject.toml. So we extend, and stay on the shared image.
#
# Three doctrines are load-bearing here, each the residue of a real failure
# elsewhere in this estate:
#
# 1. The gate is never the cockpit. The devcontainer's pins are not a ship
#    signal, and "green in the devcontainer venv" means nothing.
#
# 2. Placement is verified, not assumed. A cgroup-parent name systemd does not
#    know fails OPEN — systemd silently auto-creates an unlimited transient
#    slice, beside production. So the slice is proven to be a loaded unit
#    before any container starts.
#
# 3. The verdict is read separately from the run, and the transport cannot
#    forge it. The container runs DETACHED; `docker wait` yields the exit code
#    and `docker logs` the output. An attached stream over a truncating relay
#    can drop output mid-run and hand back a forged exit code, so a failing
#    gate reads as passing.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
repo_root="$(cd "$project_dir/../.." && pwd)"
project_rel="${project_dir#"$repo_root"/}"

worktree="${1:-$repo_root}"
target="${2:-unit}"

die() { printf 'gate: %s\n' "$*" >&2; exit 1; }

case "$target" in
  unit|coverage) ;;
  *) die "unknown target \"$target\" (expected unit or coverage)" ;;
esac

# --- preflight: every source file must actually be in git ------------------
# The repo's root .gitignore carries a Python-boilerplate `lib/` rule (for venv
# layouts). This package's source package is also called lib/, so every one of
# its 17 modules was silently excluded from a commit — `git add -A` reported
# success, the tests stayed green because they run against the working tree,
# and the merged branch contained a package with no library in it. The gate is
# the right place to catch that: it is the last thing that looks at the tree
# before it is trusted.
if command -v git >/dev/null 2>&1 && git -C "$project_dir" rev-parse --git-dir >/dev/null 2>&1; then
  untracked="$(cd "$project_dir" && git ls-files --others --exclude-standard --directory \
                 -- . 2>/dev/null | grep -E '\.(py|sh|toml)$' || true)"
  ignored_src="$(cd "$project_dir" && git ls-files --others --ignored --exclude-standard \
                   -- . 2>/dev/null | grep -E '^(lib|tools)/.*\.(py|sh)$' || true)"
  if [ -n "$ignored_src" ]; then
    printf 'gate: source files are IGNORED by git and would not be committed:\n' >&2
    printf '  %s\n' $ignored_src >&2
    die "add an exception to .gitignore (see !scripts/damon-analysis/lib/ for precedent)"
  fi
  [ -n "$untracked" ] && printf 'gate: warning — untracked source files:\n%s\n' "$untracked" >&2
fi

image="${CGPROFILE_GATE_IMAGE:-tester-unified:local}"
docker image inspect "$image" >/dev/null 2>&1 || die "gate image $image is not present.
Build it from the repo root:
  docker build -f tester-unified/Dockerfile -t tester-unified:local $repo_root"

# --- the host path of the worktree -------------------------------------
# Docker resolves bind sources in the HOST namespace, so the cockpit's own view
# of the path is not usable. Derive it from this container's mount table rather
# than hardcoding an operator's home directory.
host_worktree="${CGPROFILE_HOST_WORKTREE:-}"
if [ -z "$host_worktree" ]; then
  self="$(cat /etc/hostname 2>/dev/null || true)"
  if [ -n "$self" ]; then
    # Longest matching Destination wins, so a nested bind beats the repo root.
    host_worktree="$(docker inspect "$self" --format \
      '{{range .Mounts}}{{.Destination}}	{{.Source}}
{{end}}' 2>/dev/null \
      | awk -v w="$worktree" -F'\t' '
          $1 != "" && index(w, $1) == 1 && length($1) > best_len {
            best_len = length($1); best_dst = $1; best_src = $2
          }
          END { if (best_src != "") { rest = substr(w, best_len + 1); print best_src rest } }')"
  fi
fi
[ -n "$host_worktree" ] || die "cannot determine the host path of $worktree.
Set CGPROFILE_HOST_WORKTREE explicitly, or run from a bind-mounted checkout."

# --- placement ---------------------------------------------------------
parent="${CGPROFILE_CGROUP_PARENT:-${CGROUP_PARENT_DEV_BACKGROUND:-}}"
[ -n "$parent" ] || die "no cgroup parent resolvable.
Set CGPROFILE_CGROUP_PARENT, or \$CGROUP_PARENT_DEV_BACKGROUND (normally
injected by devcontainer.json). Refusing to launch unplaced beside production
— AGENTS.md, 'No hardcoded fallbacks'."

# A slice systemd does not know fails OPEN into an unlimited transient slice,
# so prove it is a real loaded unit before trusting it. The probe runs in the
# gate image itself, which has no systemctl, so ask the host cgroupfs instead:
# a configured slice has a directory, a typo has nothing.
#
# The probe itself must be placed too — "any container you start is placed" has
# no exception for a one-second `ls`. It cannot use the slice it is validating
# (that is the question), so it borrows the interactive tier, which is
# known-good because this process is already running in it.
probe_parent="${CGROUP_PARENT_DEV_INTERACTIVE:-$parent}"
probe="$(docker run --rm --cgroupns=host --network=none \
  --cgroup-parent="$probe_parent" \
  -v /sys/fs/cgroup:/hostcg:ro "$image" \
  bash -c 'ls -d "/hostcg/${1%%-*}.slice"/*"$1" 2>/dev/null | head -1' _ "$parent" \
  2>/dev/null || true)"
[ -n "$probe" ] || die "cgroup parent \"$parent\" is not a configured slice on this host.
A name systemd does not know fails OPEN into an unlimited transient slice
beside production, so this refuses rather than launching there."

# --- run ---------------------------------------------------------------
if [ "$target" = "coverage" ]; then
  suite='"$TESTER_VENV"/bin/python -m coverage run -m pytest tests/ -q \
      && "$TESTER_VENV"/bin/python -m coverage report'
else
  suite='"$TESTER_VENV"/bin/python -m pytest tests/ -q'
fi

cid="$(docker run -d \
  --cgroup-parent="$parent" \
  --network=none \
  --memory="${CGPROFILE_GATE_MEMORY:-3g}" \
  --memory-swap="${CGPROFILE_GATE_MEMORY_SWAP:-8g}" \
  --cpus="${CGPROFILE_GATE_CPUS:-1.5}" \
  -v "$host_worktree:/work:ro" \
  -w "/work/$project_rel" \
  -e PYTHONPATH="/work/$project_rel" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$image" \
  bash -c "set -euo pipefail
    # The worktree is mounted read-only: a gate must not be able to mutate the
    # tree it is judging. pytest and coverage both need somewhere to write.
    export PYTEST_ADDOPTS='-p no:cacheprovider --basetemp=/tmp/pt'
    export COVERAGE_FILE=/tmp/.coverage
    mkdir -p /tmp/pt
    $suite")"

trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT

# Read the verdict separately from the stream — see doctrine 3 above.
rc="$(docker wait "$cid")"
docker logs "$cid" 2>&1

exit "${rc:-1}"
