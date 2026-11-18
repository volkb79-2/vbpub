#!/usr/bin/env bash
set -uo pipefail
# Every check builds its own controller root; the caller's ambient configuration must not decide anything.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CONTROLLER_GATE_RUN_SCRIPT CONTROLLER_REMOTE_GATE_SCRIPT CONTROLLER_REMOTE_GATE_HOSTS
unset RUN_GATE_PROFILE RUN_GATE_TESTER_UNIFIED_IMAGE CGPROFILE_CONTROL_SOCKET
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CHAIN_SRC=${CHAIN_RUN_SCRIPT:-$SCRIPT_DIR/../chain-run.sh}
LIB_SRC=$SCRIPT_DIR/../lib.sh
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-chain-run-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() {
  [[ -e $1 ]] || return 0
  grep -qF -- "$2" "$1"
  local grep_rc=$?
  case $grep_rc in
    0) fail "$3 (unexpected: $2)" ;;
    1) return 0 ;;
    *) fail "$3 (could not read: $1)" ;;
  esac
}

mkdir -p "$ROOT/home" "$ROOT/controller" "$ROOT/kit"
# Stub launchers: record the working directory, the arguments and the three environment facts the chain must not decide.
cat > "$ROOT/gate-stub.sh" <<'SH'
#!/usr/bin/env bash
if [[ -e $MARKER_PATH ]]; then marker=present; else marker=absent; fi
printf '%s %s | %s\n' "$STUB_KIND" "$PWD" "$*" >> "$STUB_LOG"
printf 'env profile=%s image=%s socket=%s marker=%s\n' "${RUN_GATE_PROFILE-UNSET}" "${RUN_GATE_TESTER_UNIFIED_IMAGE-UNSET}" "${CGPROFILE_CONTROL_SOCKET-UNSET}" "$marker" >> "$STUB_LOG"
lane=$1
[[ $STUB_KIND == local ]] || lane=$3
echo "stdout-of-$lane"
echo "stderr-of-$lane" >&2
case " ${STUB_FAIL:-} " in *" $lane "*) exit 3 ;; esac
exit 0
SH
chmod +x "$ROOT/gate-stub.sh"
cat > "$ROOT/gate-run-local" <<'SH'
#!/usr/bin/env bash
STUB_KIND=local exec "$STUB_DIR/gate-stub.sh" "$@"
SH
cat > "$ROOT/gate-run-remote" <<'SH'
#!/usr/bin/env bash
STUB_KIND=remote exec "$STUB_DIR/gate-stub.sh" "$@"
SH
chmod +x "$ROOT/gate-run-local" "$ROOT/gate-run-remote"

W=$ROOT/wt
mkdir -p "$W/proj" "$W/other"
git -C "$W" init -q -b main
git -C "$W" config user.email test@example.invalid
git -C "$W" config user.name test
printf 'a\n' > "$W/proj/f"
git -C "$W" add -A
git -C "$W" commit -q -m first
HEAD1=$(git -C "$W" rev-parse HEAD)
LOGS=$ROOT/controller/logs

export STUB_LOG=$ROOT/stub.log STUB_DIR=$ROOT MARKER_PATH=$LOGS/pfx.chain
export HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller"
export CONTROLLER_GATE_RUN_SCRIPT="$ROOT/gate-run-local" CONTROLLER_REMOTE_GATE_SCRIPT="$ROOT/gate-run-remote"
reset() { rm -rf -- "$STUB_LOG" "$LOGS"; mkdir -p "$LOGS"; unset STUB_FAIL; }
chain() { "$CHAIN_SRC" "$@"; }
marker() { cat -- "$LOGS/$1.chain" 2>/dev/null || echo '<no marker>'; }

# --- local lanes: order, working directory, one argument per launch, log names, both streams captured, marker
reset
chain "$W" proj pfx alpha beta gamma > "$ROOT/out" 2>&1 || { cat "$ROOT/out" >&2; fail 'a plain local chain failed'; }
[[ $(grep -c '^local ' "$STUB_LOG") == 3 ]] || fail 'every lane must launch exactly once'
[[ $(grep '^local ' "$STUB_LOG") == "local $W/proj | alpha
local $W/proj | beta
local $W/proj | gamma" ]] || fail 'lanes must run in the given order from <worktree>/<subdir> with the lane as the only argument'
for lane in alpha beta gamma; do
  contains "$LOGS/pfx-$lane.log" "stdout-of-$lane" "log pfx-$lane.log carries the lane's stdout"
  contains "$LOGS/pfx-$lane.log" "stderr-of-$lane" "log pfx-$lane.log carries the lane's stderr"
done
[[ $(marker pfx) == CHAIN-DONE ]] || fail "marker must be CHAIN-DONE, got: $(marker pfx)"
[[ $(grep -c '^env ' "$STUB_LOG") == 3 && $(grep -c 'profile=UNSET image=UNSET socket=UNSET' "$STUB_LOG") == 3 ]] \
  || fail 'the chain must not export a profile default, a pinned image tag or a profiler socket'
[[ $(grep -c 'marker=absent' "$STUB_LOG") == 3 ]] || fail 'a stale marker of an earlier run must be removed before the first lane'

# --- a stale marker is removed at start, and the caller's own settings pass through untouched
reset
printf 'OLD-RUN\n' > "$LOGS/pfx.chain"
RUN_GATE_PROFILE=on RUN_GATE_TESTER_UNIFIED_IMAGE=caller-image chain "$W" proj pfx alpha > /dev/null 2>&1 || fail 'chain with caller settings failed'
contains "$STUB_LOG" 'env profile=on image=caller-image socket=UNSET marker=absent' "the caller's profile and image settings reach the lane, the stale marker was gone"
[[ $(marker pfx) == CHAIN-DONE ]] || fail 'the marker was not rewritten'

# --- --unset-env removes exactly the named variables (repeatable) from what the lanes inherit
reset
RUN_GATE_PROFILE=on RUN_GATE_TESTER_UNIFIED_IMAGE=caller-image chain --unset-env RUN_GATE_TESTER_UNIFIED_IMAGE "$W" proj pfx alpha > /dev/null 2>&1 || fail 'chain with --unset-env failed'
contains "$STUB_LOG" 'env profile=on image=UNSET socket=UNSET marker=absent' '--unset-env drops the named variable and keeps the others'
reset
RUN_GATE_PROFILE=on RUN_GATE_TESTER_UNIFIED_IMAGE=caller-image chain --unset-env RUN_GATE_TESTER_UNIFIED_IMAGE --unset-env RUN_GATE_PROFILE "$W" proj pfx alpha beta > /dev/null 2>&1 || fail 'chain with two --unset-env failed'
[[ $(grep -c 'env profile=UNSET image=UNSET socket=UNSET' "$STUB_LOG") == 2 ]] || fail '--unset-env is repeatable and applies to every lane'
reset
if chain --unset-env 'BAD NAME' "$W" proj pfx alpha > /dev/null 2>&1; then fail 'an invalid --unset-env name was accepted'; else rc=$?; [[ $rc == 2 ]] || fail "invalid --unset-env name exited $rc, expected 2"; fi
if chain --unset-env > /dev/null 2>&1; then fail '--unset-env without a name was accepted'; fi
[[ ! -e $STUB_LOG ]] || fail 'a refused --unset-env still launched a lane'

# --- a failing lane never stops the chain unless asked; the chain does not read verdicts
reset
STUB_FAIL=alpha chain "$W" proj pfx alpha beta > /dev/null 2>&1 || fail 'a failed lane must not fail the chain'
[[ $(grep -c '^local ' "$STUB_LOG") == 2 ]] || fail 'a failed lane stopped the later lanes without --stop-on-fail'
[[ $(marker pfx) == CHAIN-DONE ]] || fail 'the marker of a chain with a failed lane must still be CHAIN-DONE'
reset
STUB_FAIL=beta chain --stop-on-fail "$W" proj pfx alpha beta gamma > "$ROOT/out" 2>&1; rc=$?
[[ $rc == 1 ]] || fail "--stop-on-fail exit was $rc, expected 1"
[[ $(grep -c '^local ' "$STUB_LOG") == 2 ]] || fail '--stop-on-fail must not start the lane after the failed one'
[[ -e $LOGS/pfx-alpha.log && -e $LOGS/pfx-beta.log && ! -e $LOGS/pfx-gamma.log ]] || fail '--stop-on-fail logs: lanes up to the failed one only'
[[ $(marker pfx) == 'STOPPED-ON-FAIL: lane beta exited 3' ]] || fail "unexpected stop marker: $(marker pfx)"
reset
chain --stop-on-fail "$W" proj pfx alpha beta > /dev/null 2>&1 || fail '--stop-on-fail without a failure must pass'
[[ $(marker pfx) == CHAIN-DONE ]] || fail '--stop-on-fail without a failure must end CHAIN-DONE'

# --- head pin: full and abbreviated sha accepted; a moved HEAD and a dirty tree refuse before any lane
reset
chain --head "$HEAD1" "$W" proj pfx alpha > /dev/null 2>&1 || fail 'a matching full head pin was refused'
chain --head "${HEAD1:0:9}" "$W" proj pfx alpha > /dev/null 2>&1 || fail 'a matching abbreviated head pin was refused'
reset
printf 'b\n' > "$W/proj/f2"; git -C "$W" add proj/f2; git -C "$W" commit -q -m second
HEAD2=$(git -C "$W" rev-parse HEAD)
chain --head "$HEAD1" "$W" proj pfx alpha > "$ROOT/out" 2>&1; rc=$?
[[ $rc == 1 ]] || fail "a moved HEAD exited $rc, expected 1"
[[ $(marker pfx) == "HEAD moved: $HEAD2 (pinned $HEAD1)" ]] || fail "unexpected moved-HEAD marker: $(marker pfx)"
[[ ! -e $STUB_LOG ]] || fail 'a lane started although the pinned head had moved'
reset
chain --head no-such-ref "$W" proj pfx alpha > /dev/null 2>&1 && fail 'an unresolvable head pin was accepted'
[[ $(marker pfx) == "HEAD moved: $HEAD2 (pinned no-such-ref)" ]] || fail "unexpected unresolvable-pin marker: $(marker pfx)"
reset
printf 'dirty\n' > "$W/proj/untracked"
chain --head "$HEAD2" "$W" proj pfx alpha > "$ROOT/out" 2>&1; rc=$?
[[ $rc == 1 ]] || fail "a dirty tree exited $rc, expected 1"
[[ $(marker pfx) == 'tree dirty' ]] || fail "unexpected dirty marker: $(marker pfx)"
[[ ! -e $STUB_LOG ]] || fail 'a lane started although the tree was dirty'
reset
chain "$W" proj pfx alpha > /dev/null 2>&1 || fail 'without a pin the tree state is not the chain business'
[[ $(marker pfx) == CHAIN-DONE ]] || fail 'an unpinned chain on a dirty tree must run'
rm -f -- "$W/proj/untracked"

# --- remote lanes: host from --remote-host or the only allowed alias; commit = worktree HEAD; --base travels as an option
reset
CONTROLLER_REMOTE_GATE_HOSTS='gate-a' chain "$W" proj pfx remote:suite > /dev/null 2>&1 || fail 'a remote lane with the only allowed host failed'
[[ $(grep '^remote ' "$STUB_LOG") == "remote $W | gate-a proj suite $HEAD2" ]] || fail "remote lane arguments: $(grep '^remote ' "$STUB_LOG")"
contains "$LOGS/pfx-remote-suite.log" 'stdout-of-suite' 'the remote lane log is <prefix>-remote-<lane>.log'
[[ $(marker pfx) == CHAIN-DONE ]] || fail 'remote chain marker'
reset
CONTROLLER_REMOTE_GATE_HOSTS='gate-a gate-b' chain --remote-host gate-b "$W" proj pfx remote:r2:--base=abc1234 > /dev/null 2>&1 || fail 'a remote lane with a base failed'
[[ $(grep '^remote ' "$STUB_LOG") == "remote $W | gate-b proj r2 $HEAD2 --base abc1234" ]] || fail "remote lane with base: $(grep '^remote ' "$STUB_LOG")"
reset
CONTROLLER_REMOTE_GATE_HOSTS='gate-a gate-b' chain "$W" proj pfx remote:suite > /dev/null 2>&1 && fail 'two allowed hosts and no --remote-host must refuse'
contains "$LOGS/pfx.chain" 'needs --remote-host' 'the host ambiguity is explained in the marker'
[[ ! -e $STUB_LOG ]] || fail 'a lane started although the remote host was ambiguous'
reset
chain --head "$HEAD2" --remote-host gate-a "$W" proj pfx suite remote:suite remote:other:--base=zz9 tail > /dev/null 2>&1 || fail 'a mixed chain failed'
[[ $(grep -E '^(local|remote) ' "$STUB_LOG" | cut -d'|' -f2 | tr '\n' ';') == ' suite; gate-a proj suite '"$HEAD2"'; gate-a proj other '"$HEAD2"' --base zz9; tail;' ]] \
  || fail 'a mixed chain must run its lanes in the given order'
[[ -e $LOGS/pfx-suite.log && -e $LOGS/pfx-remote-suite.log ]] || fail 'the same lane name local and remote must not share a log'
reset
STUB_FAIL=suite chain --stop-on-fail --remote-host gate-a "$W" proj pfx remote:suite tail > /dev/null 2>&1
[[ $(marker pfx) == 'STOPPED-ON-FAIL: lane remote:suite exited 3' ]] || fail "a failed remote lane must stop the chain too: $(marker pfx)"

# --- refusals write the reason into the marker and start nothing
refused() {
  local label=$1 expect=$2; shift 2
  reset
  if chain "$@" > "$ROOT/out" 2>&1; then fail "$label was accepted"; else rc=$?; [[ $rc == 1 ]] || fail "$label exited $rc, expected 1"; fi
  [[ $(marker pfx) == *"$expect"* ]] || fail "$label marker was: $(marker pfx)"
  [[ ! -e $STUB_LOG ]] || fail "$label started a lane"
}
refused 'missing worktree' 'worktree missing' "$ROOT/nowhere" proj pfx alpha
refused 'absolute subdir' 'must be relative' "$W" /abs pfx alpha
refused 'escaping subdir' 'must be relative' "$W" ../up pfx alpha
refused 'missing subdir' 'project subdir missing' "$W" nodir pfx alpha
refused 'invalid lane' "invalid lane name 'bad lane'" "$W" proj pfx 'bad lane'
refused 'invalid remote spec' "invalid remote lane spec 'remote:x:--oops=1'" "$W" proj pfx remote:x:--oops=1
reset
CONTROLLER_GATE_RUN_SCRIPT=/no/such/gate-run chain "$W" proj pfx alpha > /dev/null 2>&1 && fail 'a missing local launcher was accepted'
[[ $(marker pfx) == 'lane launcher missing: /no/such/gate-run' ]] || fail "launcher marker: $(marker pfx)"
reset
CONTROLLER_REMOTE_GATE_SCRIPT=/no/such/remote-gate chain --remote-host gate-a "$W" proj pfx alpha > /dev/null 2>&1 || fail 'a local-only chain needs no remote launcher'
CONTROLLER_REMOTE_GATE_SCRIPT=/no/such/remote-gate chain --remote-host gate-a "$W" proj pfx2 remote:alpha > /dev/null 2>&1 && fail 'a missing remote launcher was accepted'
[[ $(marker pfx2) == 'lane launcher missing: /no/such/remote-gate' ]] || fail "remote launcher marker: $(marker pfx2)"
# usage errors: exit 2, no marker for an unusable prefix
reset
chain "$W" proj > /dev/null 2>&1 && fail 'a call without lanes was accepted'
chain "$W" proj 'bad/prefix' alpha > /dev/null 2>&1; rc=$?
[[ $rc == 2 ]] || fail "an invalid prefix exited $rc, expected 2"
chain --nonsense "$W" proj pfx alpha > /dev/null 2>&1; rc=$?
[[ $rc == 2 ]] || fail "an unknown option exited $rc, expected 2"

# --- log directory: CONTROLLER_LOGS_DIR decides where logs and the marker go
reset
mkdir -p "$ROOT/elsewhere"
CONTROLLER_LOGS_DIR="$ROOT/elsewhere" chain "$W" proj pfx alpha > /dev/null 2>&1 || fail 'chain with a custom logs dir failed'
[[ -f $ROOT/elsewhere/pfx-alpha.log && $(cat "$ROOT/elsewhere/pfx.chain") == CHAIN-DONE && ! -e $LOGS/pfx.chain ]] || fail 'logs and marker must follow CONTROLLER_LOGS_DIR'

# --- the sibling launchers are the default when nothing is injected
reset
cp "$CHAIN_SRC" "$ROOT/kit/chain-run.sh"; cp "$LIB_SRC" "$ROOT/kit/lib.sh"
cp "$ROOT/gate-run-local" "$ROOT/kit/gate-run.sh"
(unset CONTROLLER_GATE_RUN_SCRIPT CONTROLLER_REMOTE_GATE_SCRIPT; "$ROOT/kit/chain-run.sh" "$W" proj pfx alpha > /dev/null 2>&1) || fail 'chain with sibling launchers failed'
contains "$STUB_LOG" "local $W/proj | alpha" 'the default launcher is the gate-run.sh next to chain-run.sh'

# --- no controller-local literal ships in the tool
! grep -nE '/home/vscode|/workspaces/|vbpub-release-program|run-gate-2560|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' "$CHAIN_SRC" || fail 'chain-run.sh carries a controller-local literal'
! grep -nE 'export +(RUN_GATE_TESTER_UNIFIED_IMAGE|CGPROFILE_CONTROL_SOCKET|RUN_GATE_PROFILE)' "$CHAIN_SRC" || fail 'chain-run.sh exports an environment variable it must leave to gate-run.sh'

echo 'PASS chain-run wrapper'
