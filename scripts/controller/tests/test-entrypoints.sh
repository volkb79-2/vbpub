#!/usr/bin/env bash
set -uo pipefail
# Every immediate scripts/controller/*.sh entry point must be executable in the git index.
# Explicit exceptions are the files whose only role is to be sourced by another controller script.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
SOURCED=(lib.sh resume-lib.sh)
bad=()
while read -r mode _ _ path; do
  name=${path##*/}
  for excluded in "${SOURCED[@]}"; do
    [[ $name == "$excluded" ]] && continue 2
  done
  [[ $mode == 100755 ]] || bad+=("$path ($mode)")
done < <(git -C "$REPO_ROOT" ls-files -s -- ':(glob)scripts/controller/*.sh')
(( ${#bad[@]} == 0 )) || { printf 'not executable (want 100755): %s\n' "${bad[@]}" >&2; exit 1; }
echo "PASS controller entry-point modes (sourced exceptions: ${SOURCED[*]})"
