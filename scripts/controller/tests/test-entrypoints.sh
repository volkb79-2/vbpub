#!/usr/bin/env bash
set -uo pipefail
# Every immediate scripts/controller/*.sh entry point must be executable in the git index.
# Explicit exceptions are the files whose only role is to be sourced by another controller script.
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
(( ${#bad[@]} == 0 )) || { printf 'not executable (want 100755): %s\n' "${bad[@]}" >&2; exit 1; }
echo "PASS controller entry-point modes (${rows} files; sourced exceptions: ${SOURCED[*]})"
