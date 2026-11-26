#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
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

# Without a pause flag, attention must come from the streak threshold itself.
rm -f -- "$HOME_ROOT/host-sat-queue.disabled.runner-a"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-streak-only.out" 2>&1
contains "$ROOT/status-streak-only.out" 'ATTENTION runner-a: INFRA streak=2' 'two INFRA results trigger attention without a pause flag'

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

FRESH_HOME=$ROOT/fresh-controller
mkdir -p "$FRESH_HOME"
printf '{"hosts":{"runner-a":{"queue_lanes":["fake/project|suite"]}}}\n' > "$FRESH_HOME/hosts.local.json"
if CONTROLLER_HOME=$FRESH_HOME CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-fresh.out" 2>&1; then
  :
else
  cat "$ROOT/status-fresh.out" >&2
  fail 'fresh controller status must succeed without logs or results TSV'
fi
not_contains "$ROOT/status-fresh.out" '--- recent host queue results' 'fresh status omits the absent results section'

printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-stalled.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-stalled.route"
touch -d '@0' "$HOME_ROOT/runs/pi-stalled.jsonl"
# pi-review.sh APPENDS REVIEW-MODIFIED-TREE after pi-impl.sh's own line 1, which
# can read `EXIT=0 OUTCOME=SUCCESS` for a reviewer commit: the run row must
# carry the flag, or ctl-status shows a tampered review as green.
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-flagged.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-review-flagged.route"
printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-MODIFIED-TREE\n' > "$HOME_ROOT/runs/pi-review-flagged.done"
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-clean.jsonl"
printf 'deepseek-pi-volker\n' > "$HOME_ROOT/runs/pi-review-clean.route"
printf 'EXIT=0 OUTCOME=SUCCESS\n' > "$HOME_ROOT/runs/pi-review-clean.done"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-pi.out" 2>&1
contains "$ROOT/status-pi.out" 'pi-stalled route=deepseek-pi-github2 RUNNING' 'status lists a pi run and its selected route'
contains "$ROOT/status-pi.out" 'STALLED?' 'a pi stream with an old last event receives the normal stall warning'
contains "$ROOT/status-pi.out" 'REVIEW-MODIFIED-TREE EXIT=0 OUTCOME=SUCCESS' \
  'status prints the modified-tree flag beside the pi-impl line instead of hiding it'
contains "$ROOT/status-pi.out" 'pi-review-clean route=deepseek-pi-volker EXIT=0 OUTCOME=SUCCESS' \
  'status keeps an unflagged review row unmarked'

printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-BUDGET\n' > "$HOME_ROOT/runs/pi-review-budget.done"
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-budget.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-review-budget.route"
printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-NO-REPORT\n' > "$HOME_ROOT/runs/pi-review-notext.done"
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-notext.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-review-notext.route"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-pi-verdicts.out" 2>&1
contains "$ROOT/status-pi-verdicts.out" 'REVIEW-BUDGET EXIT=0 OUTCOME=SUCCESS' \
  'status prints a budget-stopped review as stopped'
contains "$ROOT/status-pi-verdicts.out" 'REVIEW-NO-REPORT EXIT=0 OUTCOME=SUCCESS' \
  'status prints a text-less review beside its pi-impl line'

# The fourth verdict, and the no-marker fallback shape: pi-review.sh writes
# `EXIT=5 OUTCOME=REVIEW-BUDGET` as line 1 when the launch left no marker. The
# verdict is then already IN line 1, and the row must not print it twice.
printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\nREVIEW-NOT-SETTLED\n' > "$HOME_ROOT/runs/pi-review-notsettled.done"
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-notsettled.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-review-notsettled.route"
printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\n' > "$HOME_ROOT/runs/pi-review-line1-verdict.done"
printf '{"type":"session","id":"pi-session-fixture"}\n' > "$HOME_ROOT/runs/pi-review-line1-verdict.jsonl"
printf 'deepseek-pi-github2\n' > "$HOME_ROOT/runs/pi-review-line1-verdict.route"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO "$SRC" > "$ROOT/status-pi-more.out" 2>&1
contains "$ROOT/status-pi-more.out" 'REVIEW-NOT-SETTLED EXIT=5 OUTCOME=REVIEW-BUDGET' \
  'status prints the not-settled verdict beside its pi-impl line'
contains "$ROOT/status-pi-more.out" 'pi-review-line1-verdict route=deepseek-pi-github2 EXIT=5 OUTCOME=REVIEW-BUDGET' \
  'status does not print a verdict twice when the marker line 1 already carries it'
! grep -qF 'REVIEW-BUDGET REVIEW-BUDGET' "$ROOT/status-pi-more.out" \
  || fail 'status printed the same verdict twice for one run'
rm -f -- "$HOME_ROOT/runs/pi-review-notsettled."* "$HOME_ROOT/runs/pi-review-line1-verdict."*

rm -f -- "$HOME_ROOT/runs/pi-review-flagged."* "$HOME_ROOT/runs/pi-review-clean."* \
  "$HOME_ROOT/runs/pi-review-budget."* "$HOME_ROOT/runs/pi-review-notext."*

echo 'PASS ctl-status: repo snapshot, queue liveness, streak, pause metadata, attention, pi run stalls, review verdicts, missing config'
