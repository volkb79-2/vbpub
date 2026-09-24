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
    while IFS= read -r ignored_path; do
      [ -n "$ignored_path" ] && printf '  %s\n' "$ignored_path" >&2
    done <<<"$ignored_src"
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
parent="${CGPROFILE_CGROUP_PARENT:-${CGROUP_PARENT_DEV_GATES:-}}"
[ -n "$parent" ] || die "no cgroup parent resolvable.
Set CGPROFILE_CGROUP_PARENT, or \$CGROUP_PARENT_DEV_GATES (normally
injected by devcontainer.json). Refusing to launch unplaced beside production
— AGENTS.md, 'No hardcoded fallbacks'."

# A slice systemd does not know fails OPEN into an unlimited transient slice,
# so prove it is an installed, loaded unit before trusting it. The tester image
# cannot see the host system manager by default; the short-lived probe gets
# only the host systemd runtime directory and read-only-mounted bus socket,
# then asks systemd itself for LoadState and FragmentPath. The cgroupfs path is
# checked separately as proof that the loaded unit has an instantiated cgroup.
#
# The probe itself must be placed too — "any container you start is placed" has
# no exception for a one-second probe. It cannot use the slice it is
# validating (that is the question), so it borrows the explicitly injected
# interactive tier. Falling back to the target under validation would let a
# missing trusted tier create the very cgroup path the probe then certifies.
probe_parent="${CGROUP_PARENT_DEV_INTERACTIVE:-}"
[ -n "$probe_parent" ] || die "no trusted probe parent resolvable. Set \$CGROUP_PARENT_DEV_INTERACTIVE; refusing to validate a slice from inside itself."
cockpit_name="$(cat /etc/hostname 2>/dev/null || true)"
[ -n "$cockpit_name" ] || die "cannot identify the cockpit container to verify probe placement"
cockpit_parent="$(docker inspect --format '{{.HostConfig.CgroupParent}}' "$cockpit_name" 2>/dev/null || true)"
[ -n "$cockpit_parent" ] || die "cannot read the cockpit container's actual cgroup parent"
[ "$cockpit_parent" = "$probe_parent" ] || die "trusted probe parent $probe_parent differs from cockpit's actual Docker parent $cockpit_parent"
probe_name="cgprofile-gate-probe-$$-$(date +%s%N)"
printf 'gate: placement probe container=%s parent=%s\n' "$probe_name" "$probe_parent"
# Arm exact-name cleanup before asking Docker to create it: the CLI can lose
# the response after the daemon has created the named container.
trap 'docker rm -f "$probe_name" >/dev/null 2>&1 || true' EXIT
probe_cid="$(docker run -d --name "$probe_name" \
  --cgroup-parent="$probe_parent" --network=none --cpus=3 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges --user=1003:1003 \
  --mount type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly \
  --mount type=bind,source=/run/systemd/system,target=/run/systemd/system,readonly \
  --mount type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly \
  -e DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/host-system-bus \
  "$image" \
  bash -c 'set -eu
    unit=$1
    state="$(/bin/systemctl show "$unit" --property=LoadState --value)"
    fragment="$(/bin/systemctl show "$unit" --property=FragmentPath --value)"
    control_group="$(/bin/systemctl show "$unit" --property=ControlGroup --value)"
    [ "$state" = loaded ] && [ -n "$fragment" ] || {
      printf "unit=%s LoadState=%s FragmentPath=%s\\n" "$unit" "$state" "$fragment" >&2
      exit 1
    }
    case "$fragment" in
      /run/systemd/transient/*) printf "refusing transient unit fragment: %s\\n" "$fragment" >&2; exit 1 ;;
    esac
    case "$control_group" in
      /*) ;;
      *) printf "unit=%s has no absolute ControlGroup: %s\\n" "$unit" "$control_group" >&2; exit 1 ;;
    esac
    case "$control_group" in
      /|*//*|*/./*|*/../*|*/.|*/..|*/)
        printf "unit=%s has unsafe ControlGroup: %s\\n" "$unit" "$control_group" >&2
        exit 1
        ;;
    esac
    path="/hostcg$control_group"
    [ -d "$path" ] || { printf "cgroup path missing: %s\\n" "$path" >&2; exit 1; }
    printf "unit=%s LoadState=%s FragmentPath=%s\\ncgroup=%s\\n" "$unit" "$state" "$fragment" "$path"
    sleep 1
    exit 0' _ "$parent")"
[ -n "$probe_cid" ] || die "placement probe container did not start"
docker update --cpus=3 "$probe_name" >/dev/null
probe_cap="$(docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.CgroupParent}}' "$probe_name")"
printf 'gate: placement probe cap=%s\n' "$probe_cap"
read -r probe_nano_cpus probe_actual_parent <<<"$probe_cap"
[ "$probe_nano_cpus" = "3000000000" ] || die "placement probe CPU cap is $probe_nano_cpus, expected 3000000000"
[ "$probe_actual_parent" = "$probe_parent" ] || die "placement probe parent is $probe_actual_parent, expected $probe_parent"
probe_rc="$(docker wait "$probe_name")"
probe="$(docker logs "$probe_name" 2>&1)"
docker rm "$probe_name" >/dev/null
trap - EXIT
[ -n "$probe" ] || die "host systemd/cgroup probe returned no evidence for \"$parent\""
[ "$probe_rc" = "0" ] || die "cgroup parent probe exited ${probe_rc:-unknown}"
printf 'gate: verified host slice and cgroup:\n%s\n' "$probe"

# --- run ---------------------------------------------------------------
if [ "$target" = "coverage" ]; then
  # shellcheck disable=SC2016 # TESTER_VENV expands inside the tester container.
  suite='"$TESTER_VENV"/bin/python -m coverage run -m pytest tests/ -q \
      && "$TESTER_VENV"/bin/python -m coverage report'
else
  # shellcheck disable=SC2016 # TESTER_VENV expands inside the tester container.
  suite='"$TESTER_VENV"/bin/python -m pytest tests/ -q'
fi

container_name="cgprofile-gate-$$-$(date +%s)"
printf 'gate: test container=%s parent=%s\n' "$container_name" "$parent"
# This exact, per-process name is known before create, so a lost Docker
# response still leaves the EXIT trap able to remove only our attempted gate.
trap 'docker rm -f "$container_name" >/dev/null 2>&1 || true' EXIT
cid="$(docker run -d --name "$container_name" \
  --cgroup-parent="$parent" \
  --network=none \
  --memory="${CGPROFILE_GATE_MEMORY:-3g}" \
  --memory-swap="${CGPROFILE_GATE_MEMORY_SWAP:-8g}" \
  --cpus=3 \
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
[ -n "$cid" ] || die "tester-unified container did not start"
docker update --cpus=3 "$container_name" >/dev/null
container_cap="$(docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.CgroupParent}}' "$container_name")"
printf 'gate: test container cap=%s\n' "$container_cap"
read -r container_nano_cpus container_actual_parent <<<"$container_cap"
[ "$container_nano_cpus" = "3000000000" ] || die "test container CPU cap is $container_nano_cpus, expected 3000000000"
[ "$container_actual_parent" = "$parent" ] || die "test container parent is $container_actual_parent, expected $parent"

# Read the verdict separately from the stream — see doctrine 3 above.
rc="$(docker wait "$container_name")"
docker logs "$container_name" 2>&1

exit "${rc:-1}"
