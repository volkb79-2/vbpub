#!/usr/bin/env bash
# coverage.sh [pterodactyl] — run the registered Assay R1 coverage lane.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }
[[ "$TARGET" == "pterodactyl" ]] || {
    echo "coverage.sh: only pterodactyl has a registered Assay lane" >&2
    exit 2
}
[[ -z "${RUN_GATE_EXTRA_MOUNTS:-}" && -z "${DOCKER_HOST:-}" ]] || {
    echo "Wings coverage lane does not accept Run-Gate extra mounts or DOCKER_HOST" >&2
    exit 2
}

cd "$STACK_DIR" || exit 1
exec run-gate coverage
