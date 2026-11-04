#!/usr/bin/env bash
# Description: run one registered Run-Gate lane from a runner checkout inside the cockpit container.
# usage: run-lane.sh <checkout-commit> <project-dir-relative-to-checkout> <lane> [run-gate args...]
# Start it detached: run-job <unique-name> -- bash run-lane.sh ...   (log and EXIT marker under ~/jobs/<name>/)
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
[[ $# -ge 3 ]] || { echo "usage: $0 <checkout-commit> <project-dir> <lane> [run-gate args...]" >&2; exit 2; }
commit=$1; rel=$2; lane=$3; shift 3
[[ $commit =~ ^[0-9a-f]{40,64}$ ]] || { echo 'checkout must be a full commit id' >&2; exit 2; }
[[ $rel != /* && $rel != *..* ]] || { echo 'project dir must be relative to the checkout' >&2; exit 2; }
exec docker exec -u vscode -w "/workspaces/$commit/$rel" \
  -e RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:local -e RUN_GATE_ALLOW_MAIN_CHECKOUT=1 -e RUN_GATE_PROFILE=off \
  "$COCKPIT_NAME" nice -n "${LANE_NICE:-19}" /home/vscode/.local/bin/run-gate "$lane" "$@"
