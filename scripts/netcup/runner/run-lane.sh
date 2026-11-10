#!/usr/bin/env bash
# Description: run one registered Run-Gate lane from a runner checkout inside the cockpit container.
# usage: run-lane.sh <checkout-commit> <project-dir-relative-to-checkout> <lane> [run-gate args...]
# Start it detached: run-job <unique-name> -- bash run-lane.sh ...   (log and EXIT marker under ~/jobs/<name>/)
# RUN_GATE_BIN is an absolute path inside the cockpit; omit it to keep the
# established cockpit default. Run-Gate >=25.4.0 is required with cgprofile
# 3.0.0; see runner/README.md for the supported pairing.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
[[ $# -ge 3 ]] || { echo "usage: $0 <checkout-commit> <project-dir> <lane> [run-gate args...]" >&2; exit 2; }
commit=$1; rel=$2; lane=$3; shift 3
[[ $commit =~ ^[0-9a-f]{40,64}$ ]] || { echo 'checkout must be a full commit id' >&2; exit 2; }
[[ $rel != /* && $rel != *..* ]] || { echo 'project dir must be relative to the checkout' >&2; exit 2; }
run_gate_bin=${RUN_GATE_BIN:-/home/vscode/.local/bin/run-gate}
[[ $run_gate_bin == /* ]] || { echo 'RUN_GATE_BIN must be an absolute path inside the cockpit' >&2; exit 2; }
gate_env=()
[[ -z ${RUN_GATE_JOB:-} ]] || gate_env+=(-e "RUN_GATE_JOB=$RUN_GATE_JOB")
[[ -z ${RUN_GATE_CAMPAIGN:-} ]] || gate_env+=(-e "RUN_GATE_CAMPAIGN=$RUN_GATE_CAMPAIGN")
exec docker exec -u vscode -w "/workspaces/$commit/$rel" \
  -e RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:local -e RUN_GATE_ALLOW_MAIN_CHECKOUT=1 -e RUN_GATE_PROFILE=off \
  "${gate_env[@]}" "$COCKPIT_NAME" nice -n "${LANE_NICE:-19}" "$run_gate_bin" "$lane" "$@"
