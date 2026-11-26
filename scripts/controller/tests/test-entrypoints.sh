#!/usr/bin/env bash
set -uo pipefail
# Every immediate scripts/controller/*.sh entry point must be executable in the git index.
# Explicit exceptions are the files whose only role is to be sourced by another controller script.
# The pi signal-guard shims (scripts/controller/pi-shims/*) and PID namespace
# shell wrapper are executable entry points too: otherwise the guard is unusable.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
SOURCED=(lib.sh resume-lib.sh)
bad=()
rows=0
while read -r mode _ _ path; do
  name=${path##*/}
  for excluded in "${SOURCED[@]}"; do
    [[ $name == "$excluded" ]] && continue 2
  done
  rows=$((rows + 1))
  [[ $mode == 100755 ]] || bad+=("$path ($mode)")
done < <(git -C "$REPO_ROOT" ls-files -s -- ':(glob)scripts/controller/*.sh')
# A listing that produced nothing must not read as a pass: the process substitution swallows a `git ls-files` failure
# (no repository, a wrong REPO_ROOT, a changed pathspec), and then the loop above checks no row at all.
(( rows > 0 )) || { echo "FAIL: git ls-files listed no scripts/controller/*.sh under $REPO_ROOT" >&2; exit 1; }
kept=$(git -C "$REPO_ROOT" ls-files -- 'scripts/controller/ctl-keepalive.sh') || kept=
[[ -n $kept ]] || { echo "FAIL: ctl-keepalive.sh is not among the listed entry points (was it renamed or removed?)" >&2; exit 1; }
shims=0
# Each spelling pi-impl.sh's own preflight names must be listed, tracked and
# executable in the index. Asking per NAME (not for a count) means a future shim
# is not a refusal on an ordinary state, while a renamed or deleted one is.
for shim in pkill killall pgrep; do
  row=$(git -C "$REPO_ROOT" ls-files -s -- "scripts/controller/pi-shims/$shim") || row=
  [[ -n $row ]] || { echo "FAIL: scripts/controller/pi-shims/$shim is not tracked" >&2; exit 1; }
  shims=$((shims + 1))
  [[ ${row%% *} == 100755 ]] || bad+=("scripts/controller/pi-shims/$shim (${row%% *})")
done
pidns_wrapper_row=$(git -C "$REPO_ROOT" ls-files -s -- scripts/controller/pi-pidns-bash) || pidns_wrapper_row=
[[ -n $pidns_wrapper_row ]] || { echo 'FAIL: scripts/controller/pi-pidns-bash is not tracked' >&2; exit 1; }
[[ ${pidns_wrapper_row%% *} == 100755 ]] || bad+=("scripts/controller/pi-pidns-bash (${pidns_wrapper_row%% *})")
(( ${#bad[@]} == 0 )) || { printf 'not executable (want 100755): %s\n' "${bad[@]}" >&2; exit 1; }
echo "PASS controller entry-point modes (${rows} files plus ${shims} pi-shims and PID namespace wrapper; sourced exceptions: ${SOURCED[*]})"
