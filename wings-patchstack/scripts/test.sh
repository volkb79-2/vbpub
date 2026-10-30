#!/usr/bin/env bash
# test.sh [pterodactyl] — run the registered, cgroup-placed Wings gate.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }
[[ -z "${RUN_GATE_EXTRA_MOUNTS:-}" && -z "${DOCKER_HOST:-}" ]] || {
    echo "Wings test lane does not accept Run-Gate extra mounts or DOCKER_HOST" >&2
    exit 2
}
if [[ "${INTEGRATION:-0}" == "1" ]]; then
    echo "INTEGRATION=1 is unsupported: the Wings gate does not receive the host Docker socket" >&2
    exit 2
fi

cd "$STACK_DIR" || exit 1
run-gate wings
if [[ "${COVERAGE:-0}" == "1" ]]; then
    run-gate coverage
fi
