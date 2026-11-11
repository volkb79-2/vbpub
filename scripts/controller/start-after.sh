#!/usr/bin/env bash
# Wait until HHMM UTC, then launch the controller Codex implementer.
# Usage: start-after.sh HHMM <worktree> <prompt> <run> [resume <thread-id>]
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

usage() {
  echo "usage: $0 HHMM <worktree> <prompt> <run> [resume <thread-id>]" >&2
  exit 2
}

[[ $# -ge 4 ]] || usage
target=$1
[[ $target =~ ^([01][0-9]|2[0-3])[0-5][0-9]$ ]] || { echo "invalid UTC time: $target" >&2; exit 2; }
shift
while :; do
  now=$(date -u +%H%M) || exit $?
  [[ $now =~ ^([01][0-9]|2[0-3])[0-5][0-9]$ ]] || { echo "invalid clock result: $now" >&2; exit 2; }
  [[ $now < $target ]] || break
  sleep 20
done
impl=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/codex-impl.sh}
[[ -x $impl ]] || { echo "Codex implementer is not executable: $impl" >&2; exit 2; }
exec "$impl" "$@"
