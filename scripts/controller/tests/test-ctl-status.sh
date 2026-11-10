#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${CTL_STATUS_SCRIPT:-$SCRIPT_DIR/../ctl-status.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/ctl-status-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

HOME_ROOT=$ROOT/controller
REPO=$ROOT/repo
mkdir -p "$HOME_ROOT/logs/host-sat-queue" "$HOME_ROOT/runs" "$REPO"
git -C "$REPO" init -q
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
printf 'fixture\n' > "$REPO/README.md"
git -C "$REPO" add README.md && git -C "$REPO" commit -qm baseline
printf '{"hosts":{"runner-a":{"queue_lanes":["fake/project|suite"]}}}\n' > "$HOME_ROOT/hosts.local.json"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf 'utc\thost\tlane\tcommit\trun_name\tverdict\tseconds\tlog\n' > "$HOME_ROOT/logs/host-sat-results.tsv"
for n in 1 2; do printf '%s\trunner-a\tfake/project|suite\tdeadbeef\tsat-infra-%s\tINFRA:NO_FINISH\t1\tfake.log\n' "$now" "$n" >> "$HOME_ROOT/logs/host-sat-results.tsv"; done
echo "$$" > "$HOME_ROOT/logs/host-sat-queue/runner-a.pid"
printf 'sat-fake runner-a\n' > "$HOME_ROOT/logs/host-sat-queue/runner-a.current"
printf 'host-sat queue self-pause\nby=queue-auto\nhost=runner-a\nlast_result=INFRA:NO_FINISH\n' > "$HOME_ROOT/host-sat-queue.disabled.runner-a"

CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status.out" 2>&1
contains "$ROOT/status.out" 'runner-a: loop alive pid=' 'status reports configured queue liveness'
contains "$ROOT/status.out" 'INFRA streak=2' 'status derives consecutive infrastructure failures'
contains "$ROOT/status.out" 'in flight: sat-fake runner-a' 'status shows current lane'
contains "$ROOT/status.out" 'by=queue-auto' 'status prints pause metadata'
contains "$ROOT/status.out" 'ATTENTION runner-a: INFRA streak=2' 'two INFRA results trigger attention'

# A later PASS resets the streak, while a newer pause flag remains actionable.
printf '%s\trunner-a\tfake/project|suite\tdeadbeef\tsat-pass\tPASS\t1\tfake.log\n' "$now" >> "$HOME_ROOT/logs/host-sat-results.tsv"
touch -d "@$(( $(date +%s) + 5 ))" "$HOME_ROOT/host-sat-queue.disabled.runner-a"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-after-pass.out" 2>&1
contains "$ROOT/status-after-pass.out" 'INFRA streak=0' 'PASS resets the INFRA streak'
contains "$ROOT/status-after-pass.out" 'pause flag is newer than the last PASS' 'newer pause flag raises attention'

rm -f -- "$HOME_ROOT/host-sat-queue.disabled.runner-a"
touch "$HOME_ROOT/host-sat-queue.disabled"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-global.out" 2>&1
contains "$ROOT/status-global.out" 'host flag: none' 'status shows missing host flag'
contains "$ROOT/status-global.out" 'global flag present' 'status shows global pause flag'
not_contains "$ROOT/status-global.out" 'ATTENTION runner-a:' 'global flag after PASS does not invent a host-specific alert'

NO_HOST_HOME=$ROOT/no-host-config
CONTROLLER_HOME=$NO_HOST_HOME CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-no-host-config.out" 2>&1
contains "$ROOT/status-no-host-config.out" 'configure ' 'missing local host config is explained'

echo 'PASS ctl-status: repo snapshot, queue liveness, streak, pause metadata, attention, missing config'
