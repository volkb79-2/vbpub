#!/usr/bin/env bash
set -uo pipefail
# Every check builds its own controller root; the caller's ambient configuration must not decide anything.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CONTROLLER_REMOTE_GATE_HOSTS CONTROLLER_REMOTE_HOSTS_FILE CONTROLLER_REMOTE_API_CHECK CONTROLLER_REMOTE_PYTHON CONTROLLER_REMOTE_GATE_PY
unset CONTROLLER_REMOTE_GATE_PY_REF CONTROLLER_REMOTE_REPO_NAME CONTROLLER_REMOTE_SLOTS CONTROLLER_REMOTE_BUDGET_MB CONTROLLER_REMOTE_WEIGHTS_FILE
unset CONTROLLER_REMOTE_WEIGHT_MB CONTROLLER_REMOTE_SLOT_WAIT_S CONTROLLER_REMOTE_DRYRUN CONTROLLER_REMOTE_DRY_HOLD CONTROLLER_REMOTE_OUT CONTROLLER_REMOTE_WAIT_PSI
unset REMOTE_GATE_V1001_SLOTS REMOTE_GATE_V1001_BUDGET_MB REMOTE_GATE_WEIGHT_MB REMOTE_GATE_SLOT_WAIT_S REMOTE_GATE_WAIT_PSI REMOTE_GATE_DRYRUN REMOTE_GATE_DRY_HOLD REMOTE_GATE_PY
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
RG_SCRIPT=${REMOTE_GATE_SCRIPT:-$SCRIPT_DIR/../remote-gate.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-remote-gate-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

mkdir -p "$ROOT/home" "$ROOT/bin" "$ROOT/controller/remote-gate-locks" "$ROOT/repo"
cat > "$ROOT/bin/api-check" <<'SH'
#!/usr/bin/env bash
echo "called $*" >> "$API_LOG"
exit "${API_RC:-0}"
SH
cat > "$ROOT/bin/api-check-alt" <<'SH'
#!/usr/bin/env bash
echo "alt $*" >> "$API_ALT_LOG"
exit 0
SH
# The remote command is the last argument; the counter file makes the slice's oom_kill value change between calls.
cat > "$ROOT/bin/fake-ssh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "${*: -1}" >> "$SSH_LOG"
[[ ${FAKE_SSH_RC:-0} == 0 ]] || exit "$FAKE_SSH_RC"
n=$(cat "$OOM_STATE" 2>/dev/null || echo 0); n=$((n + 1)); printf '%s\n' "$n" > "$OOM_STATE"
echo $(( ${OOM_BASE:-0} + ${OOM_STEP:-0} * (n - 1) ))
SH
cat > "$ROOT/fake-remote-gate.py" <<'PY'
import json, os, subprocess, sys
a = sys.argv[1:]
open(os.environ["RG_LOG"], "a").write(json.dumps(a) + "\n")
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
open(os.environ["RG_LOG"] + ".cwd", "w").write(os.getcwd() + "\n" + sha + "\n")
# a slot descriptor inherited by the runner would keep the slots held after this wrapper died
def target(fd):
    try:
        return os.readlink("/proc/self/fd/" + fd)
    except OSError:  # the descriptor os.listdir itself used is already closed
        return ""
leaked = [p for p in os.listdir("/proc/self/fd") if "/remote-gate-locks/" in target(p)]
open(os.environ["RG_LOG"] + ".leak", "w").write("\n".join(leaked))
barrier = os.environ.get("RG_BARRIER")
if barrier:
    # concurrency check: this runner finishes only once a second runner is up too (a sync file, not a time bound)
    open(os.path.join(barrier, str(os.getpid()) + ".up"), "w").close()
    import time
    for _ in range(6000):
        if len(os.listdir(barrier)) >= 2:
            break
        time.sleep(0.01)
    else:
        sys.exit(97)
out = a[a.index("--out-dir") + 1]
name = a[a.index("--name") + 1]
d = os.path.join(out, name)
os.makedirs(os.path.join(d, "artifacts"))
open(os.path.join(d, "job.log"), "w").write(f"checkout /x at {sha}\n")
open(os.path.join(d, "lane.log"), "w").write(os.environ.get("FAKE_LANE_LOG", "lane ok\n"))
open(os.path.join(d, "artifacts", "verdict-a.json"), "w").write("{}")
print("run-gate: lane suite verdict PASS")
sys.exit(int(os.environ.get("FAKE_RG_RC", "0")))
PY
chmod +x "$ROOT/bin/api-check" "$ROOT/bin/api-check-alt" "$ROOT/bin/fake-ssh"
python3 - "$ROOT/controller/hosts.local.json" "$ROOT/bin" <<'PY'
import json, pathlib, sys
b = pathlib.Path(sys.argv[2])
row = {"ssh": [str(b / "fake-ssh"), "gate-a"], "api_check": [str(b / "api-check"), "gate-a"],
       "guard": "test $(hostname -s) = gate-a-marker"}
pathlib.Path(sys.argv[1]).write_text(json.dumps({"hosts": {"gate-a": row, "acceptance-b": row}}))
PY

git -C "$ROOT/repo" init -q -b main
git -C "$ROOT/repo" config user.email test@example.invalid
git -C "$ROOT/repo" config user.name test
mkdir -p "$ROOT/repo/proj" "$ROOT/repo/scripts/netcup/runner"
cat > "$ROOT/repo/proj/run-gate.toml" <<'TOML'
[lanes.small]
resources = { memory = "500m" }
[lanes.big]
resources = { memory = "3g" }
[lanes.bounded]
resources = { memory = "3g" }
[lanes.plain]
kind = "command"
[lanes.r2-camp]
kind = "command"
[lanes.measured]
kind = "command"
TOML
cp "$ROOT/fake-remote-gate.py" "$ROOT/repo/scripts/netcup/runner/remote-gate.py"
git -C "$ROOT/repo" add -A
git -C "$ROOT/repo" commit -q -m first
SHA=$(git -C "$ROOT/repo" rev-parse HEAD)
git -C "$ROOT/repo" branch topic
printf 'x\n' > "$ROOT/repo/second"; git -C "$ROOT/repo" add second; git -C "$ROOT/repo" commit -q -m second
printf '# comment\nproj:measured\t800\nproj:bounded\t100\n' > "$ROOT/controller/remote-gate-weights.tsv"

export API_LOG=$ROOT/api.log API_ALT_LOG=$ROOT/api-alt.log SSH_LOG=$ROOT/ssh.log RG_LOG=$ROOT/rg.log OOM_STATE=$ROOT/oom.state
export PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo"
export CONTROLLER_REMOTE_GATE_HOSTS=gate-a CONTROLLER_REMOTE_GATE_PY="$ROOT/fake-remote-gate.py"
export CONTROLLER_REMOTE_SLOTS=4 CONTROLLER_REMOTE_BUDGET_MB=4096
reset_logs() { rm -f -- "$API_LOG" "$API_ALT_LOG" "$SSH_LOG" "$RG_LOG" "$RG_LOG.cwd" "$OOM_STATE"; rm -rf -- "$ROOT/controller/remote-gates" "$ROOT/controller/logs"; mkdir -p "$ROOT/controller/logs"; }
rg() { "$RG_SCRIPT" "$@"; }

# --- happy path: identity first, commit-pinned throwaway worktree, binding line, verdict copies, worktree removed
reset_logs
rg gate-a proj small topic > "$ROOT/ok.out" 2> "$ROOT/ok.err" || { cat "$ROOT/ok.err" >&2; fail 'happy path failed'; }
contains "$API_LOG" 'called gate-a' 'identity check ran with the host alias'
contains "$ROOT/ok.err" "slots [1] of 4 (weight 500 MiB, unit 1024), commit $SHA, proj:small" 'admission line names slots, weight and the resolved commit'
[[ $(sed -n 2p "$RG_LOG.cwd") == "$SHA" ]] || fail 'remote-gate.py ran in a worktree that is not the requested commit'
[[ $(sed -n 1p "$RG_LOG.cwd") == "$ROOT/controller/remote-gate-work/"* ]] || fail 'throwaway worktree must live under the controller home, not in the repo'
[[ $(git -C "$ROOT/repo" worktree list | wc -l) == 1 ]] || fail 'throwaway worktree was not removed'
[[ ! -s $RG_LOG.leak ]] || fail 'the runner inherited a slot lock descriptor'
contains "$RG_LOG" '"--hosts-file", "'"$ROOT"'/controller/hosts.local.json"' 'the host table is passed to remote-gate.py'
contains "$RG_LOG" '"--project-dir", "proj"' 'project dir is forwarded'
contains "$RG_LOG" '"--wait-psi", "120"' 'default PSI wait is forwarded'
contains "$RG_LOG" '"--repo", "vbpub"' 'the runner repo name defaults to vbpub, not to the checkout directory name'
not_contains "$ROOT/ok.err" 'WARNING' 'a clean environment prints no migration warning'
not_contains "$RG_LOG" '"--base"' 'no base unless requested'
final=$(sed -n 's/^log: *//p' "$ROOT/ok.out")
[[ -f $final && $final == "$ROOT/controller/logs/"* ]] || fail 'final log must be written under CONTROLLER_LOGS_DIR'
contains "$final" "run-gate.commit=$SHA" 'binding line names the full commit'
contains "$ROOT/ok.out" 'verdict:  run-gate: lane suite verdict PASS' 'verdict line is printed'
contains "$ROOT/ok.out" 'verdicts: 1 JSON file(s)' 'verdict JSON files are counted'
[[ $(find "$ROOT/controller/logs" -name '*.verdict-1.json' | wc -l) == 1 ]] || fail 'verdict JSON was not copied next to the log'
[[ $(grep -cF 'dev-gates.slice/memory.events' "$SSH_LOG") == 2 ]] || fail 'the host is contacted exactly twice (OOM counter before and after) by this wrapper'
contains "$SSH_LOG" 'GUARD-FAIL' 'host probes carry the remote guard'
contains "$SSH_LOG" 'gate-a-marker' 'host probes carry the guard text from the host table'
contains "$SSH_LOG" 'dev-gates.slice/memory.events' 'host probe reads the gate slice OOM counter'

# --- the origin of the runner: snapshot from a reference, never from the working tree
reset_logs
CONTROLLER_REMOTE_GATE_PY='' CONTROLLER_REMOTE_GATE_PY_REF=HEAD rg gate-a proj small "$SHA" > /dev/null 2>&1 || fail 'runner snapshot from a ref failed'
[[ -f $ROOT/controller/tools/remote-gate-main.py ]] || fail 'runner snapshot missing'
reset_logs
if CONTROLLER_REMOTE_GATE_PY='' CONTROLLER_REMOTE_GATE_PY_REF=no-such-ref rg gate-a proj small "$SHA" > /dev/null 2>&1; then fail 'a runner ref that does not resolve was accepted'; else rc=$?; [[ $rc == 3 ]] || fail "unreadable runner ref exit was $rc, expected 3"; fi
[[ ! -e $RG_LOG ]] || fail 'a runner ran although its source ref was unreadable'
[[ $(find "$ROOT/controller/tools" -mindepth 1 ! -name remote-gate-main.py | wc -l) == 0 ]] || fail 'a failed snapshot left a temporary file behind'
# The snapshot is written to a private temporary file and renamed: a stale fixed-name file is never touched or reused.
reset_logs
rm -rf -- "$ROOT/controller/tools"; mkdir -p "$ROOT/controller/tools"
printf 'STALE-SENTINEL\n' > "$ROOT/controller/tools/remote-gate-main.py.new"
CONTROLLER_REMOTE_GATE_PY='' CONTROLLER_REMOTE_GATE_PY_REF=HEAD rg gate-a proj small "$SHA" > /dev/null 2>&1 || fail 'runner snapshot beside a stale fixed-name file failed'
[[ $(cat "$ROOT/controller/tools/remote-gate-main.py.new") == STALE-SENTINEL ]] || fail 'the snapshot must not use a fixed temporary name'
cmp -s "$ROOT/controller/tools/remote-gate-main.py" <(git -C "$ROOT/repo" show HEAD:scripts/netcup/runner/remote-gate.py) || fail 'the runner snapshot differs from the reference'
[[ $(find "$ROOT/controller/tools" -mindepth 1 ! -name remote-gate-main.py ! -name remote-gate-main.py.new | wc -l) == 0 ]] || fail 'a successful snapshot left a temporary file behind'
# Two runs at once (each runner waits for the other through sync files): both succeed and the snapshot is whole.
reset_logs
rm -rf -- "$ROOT/controller/tools" "$ROOT/barrier"; mkdir -p "$ROOT/barrier"
for lane in small plain; do
  ( RG_BARRIER=$ROOT/barrier CONTROLLER_REMOTE_GATE_PY='' CONTROLLER_REMOTE_GATE_PY_REF=HEAD rg gate-a proj "$lane" "$SHA" > "$ROOT/conc-$lane.out" 2>&1; echo $? > "$ROOT/conc-$lane.rc" ) &
done
wait
for lane in small plain; do [[ $(cat "$ROOT/conc-$lane.rc") == 0 ]] || { cat "$ROOT/conc-$lane.out" >&2; fail "concurrent run of lane $lane failed"; }; done
cmp -s "$ROOT/controller/tools/remote-gate-main.py" <(git -C "$ROOT/repo" show HEAD:scripts/netcup/runner/remote-gate.py) || fail 'concurrent runs left a damaged runner snapshot'
[[ $(find "$ROOT/controller/tools" -mindepth 1 ! -name remote-gate-main.py | wc -l) == 0 ]] || fail 'concurrent runs left a temporary file behind'

# --- refusals happen before the host is contacted
reset_logs
if rg gate-a proj small "$SHA" extra > /dev/null 2>&1; then fail 'a stray argument was accepted'; fi
for bad in '/abs' '../up' 'a/../b'; do
  if rg gate-a "$bad" small "$SHA" > /dev/null 2>&1; then fail "project dir $bad was accepted"; fi
done
if rg gate-a proj 'bad lane' "$SHA" > /dev/null 2>&1; then fail 'invalid lane name was accepted'; fi
if rg gate-a proj small no-such-ref > "$ROOT/noref.out" 2>&1; then fail 'unresolvable ref was accepted'; fi
contains "$ROOT/noref.out" "cannot resolve 'no-such-ref'" 'unresolvable ref is named'
if rg gate-a proj small "$SHA" --base no-such-base > "$ROOT/nobase.out" 2>&1; then fail 'unresolvable --base was accepted'; fi
contains "$ROOT/nobase.out" "--base 'no-such-base' does not resolve" 'unresolvable --base is named'
if CONTROLLER_REMOTE_GATE_HOSTS=acceptance-b rg gate-a proj small "$SHA" > "$ROOT/notallowed.out" 2>&1; then fail 'a host outside CONTROLLER_REMOTE_GATE_HOSTS was accepted'; fi
contains "$ROOT/notallowed.out" 'not in CONTROLLER_REMOTE_GATE_HOSTS' 'allow-list refusal is explained'
if CONTROLLER_REMOTE_GATE_HOSTS='' rg gate-a proj small "$SHA" > /dev/null 2>&1; then fail 'an unset allow-list accepted a host'; fi
[[ ! -e $API_LOG && ! -e $SSH_LOG && ! -e $RG_LOG ]] || fail 'a refused call contacted the identity check, the host or the runner'
# Limit refusals come after the identity check but still before the host or the runner.
if CONTROLLER_REMOTE_SLOTS=9 rg gate-a proj small "$SHA" > /dev/null 2>&1; then fail 'more than 8 slots accepted'; fi
if CONTROLLER_REMOTE_SLOT_WAIT_S=soon rg gate-a proj small "$SHA" > /dev/null 2>&1; then fail 'a non-numeric slot wait was accepted'; fi
if CONTROLLER_REMOTE_BUDGET_MB=2 rg gate-a proj small "$SHA" > /dev/null 2>&1; then fail 'a budget below one MiB per slot was accepted'; fi
[[ ! -e $SSH_LOG && ! -e $RG_LOG ]] || fail 'a refused limit reached the host or the runner'

# --- identity: a failing check refuses with exit 3 and nothing else runs
reset_logs
if API_RC=1 rg gate-a proj small "$SHA" > "$ROOT/idfail.out" 2>&1; then fail 'failed provider identity was accepted'; else rc=$?; [[ $rc == 3 ]] || fail "identity failure exit was $rc, expected 3"; fi
contains "$ROOT/idfail.out" 'identity check for gate-a FAILED' 'identity refusal is explained'
[[ ! -e $SSH_LOG && ! -e $RG_LOG ]] || fail 'host or runner reached after a failed identity check'
reset_logs
if CONTROLLER_REMOTE_GATE_HOSTS=ghost rg ghost proj small "$SHA" > /dev/null 2>&1; then fail 'an alias missing from the host table was accepted'; else rc=$?; [[ $rc == 3 ]] || fail "unknown alias exit was $rc, expected 3"; fi
reset_logs
CONTROLLER_REMOTE_API_CHECK="$ROOT/bin/api-check-alt" rg gate-a proj small "$SHA" > /dev/null 2>&1 || fail 'run with the identity-check override failed'
contains "$API_ALT_LOG" 'alt gate-a' 'CONTROLLER_REMOTE_API_CHECK replaces the host-table check'
[[ ! -e $API_LOG ]] || fail 'host-table identity check ran although an override is configured'

# --- pass-through of --base and extra run-gate arguments
reset_logs
rg gate-a proj small "$SHA" --base "$SHA" -- --jobs 3 > /dev/null 2>&1 || fail 'run with --base and extras failed'
contains "$RG_LOG" '"--base", "'"$SHA"'"' '--base reaches remote-gate.py'
contains "$RG_LOG" '"--", "--jobs", "3"' 'extra run-gate arguments are forwarded after --'
# the old spelling `-- --base <ref>` reaches the same effect: the base is bundled (remote-gate.py --base) and not sent twice
for spelling in '--base' '--base='; do
  reset_logs
  if [[ $spelling == --base ]]; then rg gate-a proj small "$SHA" -- --base "$SHA" --jobs 3 > /dev/null 2>&1 || fail 'run with pass-through --base failed'
  else rg gate-a proj small "$SHA" -- "--base=$SHA" --jobs 3 > /dev/null 2>&1 || fail 'run with pass-through --base= failed'; fi
  contains "$RG_LOG" '"--base", "'"$SHA"'"' "pass-through $spelling reaches remote-gate.py as its --base option"
  contains "$RG_LOG" '"--", "--jobs", "3"' "pass-through $spelling leaves the other extra arguments in place"
  [[ $(grep -o -- '--base' "$RG_LOG" | wc -l) == 1 ]] || fail "pass-through $spelling must not forward --base to run-gate as well"
done
reset_logs
if rg gate-a proj small "$SHA" --base "$SHA" -- --base "$SHA" > /dev/null 2>&1; then fail 'a base given as option and as pass-through was accepted'; fi
if rg gate-a proj small "$SHA" -- --base > /dev/null 2>&1; then fail 'a pass-through --base without a ref was accepted'; fi
[[ ! -e $RG_LOG ]] || fail 'a refused --base reached the runner'
reset_logs
CONTROLLER_REMOTE_REPO_NAME=named-repo rg gate-a proj small "$SHA" > /dev/null 2>&1 || fail 'run with a configured repo name failed'
contains "$RG_LOG" '"--repo", "named-repo"' 'a configured repo name is forwarded'

# --- admission weight: declared memory, measured peak, class default; dry run sends nothing
dry() { rm -f -- "$SSH_LOG" "$RG_LOG"; CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj "$1" "$SHA" 2>&1 >/dev/null | grep '^remote-gate.sh: gate-a slots'; }
[[ $(dry big) == *'slots [1 2 3] of 4 (weight 3072 MiB, unit 1024)'* ]] || fail 'declared 3g must take three 1 GiB slots'
[[ $(dry plain) == *'slots [1] of 4 (weight 1024 MiB, unit 1024)'* ]] || fail 'an unknown ordinary lane takes one slot'
[[ $(dry r2-camp) == *'slots [1 2] of 4 (weight 2048 MiB, unit 1024)'* ]] || fail 'an unknown R2-class lane takes two slots'
[[ $(dry measured) == *'(weight 920 MiB, unit 1024)'* ]] || fail 'a measured peak carries 15 percent headroom'
[[ $(dry bounded) == *'(weight 115 MiB, unit 1024)'* ]] || fail 'the smaller of declared and measured weight wins'
[[ ! -e $SSH_LOG && ! -e $RG_LOG ]] || fail 'a dry run contacted the host or the runner'
# a weights file that exists but cannot be read warns; a missing one is the documented silent fallback
mkdir -p "$ROOT/weights-dir"
CONTROLLER_REMOTE_WEIGHTS_FILE=$ROOT/weights-dir CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj plain "$SHA" > /dev/null 2> "$ROOT/wunread.err" || fail 'an unreadable weights file must not stop the run'
contains "$ROOT/wunread.err" "WARNING: weights file $ROOT/weights-dir exists but cannot be read" 'an unreadable weights file warns'
contains "$ROOT/wunread.err" 'slots [1] of 4 (weight 1024 MiB' 'an unreadable weights file falls back to the class weight'
CONTROLLER_REMOTE_WEIGHTS_FILE=$ROOT/no-such-weights CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj plain "$SHA" > /dev/null 2> "$ROOT/wmissing.err" || fail 'a missing weights file must not stop the run'
not_contains "$ROOT/wmissing.err" 'WARNING' 'a missing weights file is a silent fallback'
contains "$ROOT/wmissing.err" 'slots [1] of 4 (weight 1024 MiB' 'a missing weights file falls back to the class weight'
# the program-directory tool's env names are not mapped: every one that is set names its replacement
for pair in REMOTE_GATE_V1001_SLOTS:CONTROLLER_REMOTE_SLOTS REMOTE_GATE_V1001_BUDGET_MB:CONTROLLER_REMOTE_BUDGET_MB \
            REMOTE_GATE_WEIGHT_MB:CONTROLLER_REMOTE_WEIGHT_MB REMOTE_GATE_SLOT_WAIT_S:CONTROLLER_REMOTE_SLOT_WAIT_S \
            REMOTE_GATE_WAIT_PSI:CONTROLLER_REMOTE_WAIT_PSI REMOTE_GATE_DRYRUN:CONTROLLER_REMOTE_DRYRUN \
            REMOTE_GATE_PY:CONTROLLER_REMOTE_PYTHON REMOTE_GATE_DRY_HOLD:CONTROLLER_REMOTE_DRY_HOLD; do
  env "${pair%%:*}=7" CONTROLLER_REMOTE_DRYRUN=1 "$RG_SCRIPT" gate-a proj small "$SHA" > /dev/null 2> "$ROOT/oldenv.err" || fail "run with the old name ${pair%%:*} failed"
  contains "$ROOT/oldenv.err" "WARNING: ${pair%%:*} is set but not read here (not mapped); use ${pair#*:}" "the old name ${pair%%:*} is named in a warning"
  [[ $(grep -c WARNING "$ROOT/oldenv.err") == 1 ]] || fail "the old name ${pair%%:*} must produce exactly one warning line"
done
[[ $(CONTROLLER_REMOTE_WEIGHT_MB=2500 CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj small "$SHA" 2>&1 >/dev/null | grep -c 'slots \[1 2 3\] of 4 (weight 2500 MiB') == 1 ]] || fail 'a forced weight must be rounded up to whole slots'

# --- slot contention: partial holders are refused with exit 75, a single free slot is enough for a small lane
lockdir=$ROOT/controller/remote-gate-locks
held_fds=()
for i in 1 2 3; do exec {slot_fd}>"$lockdir/gate-a.slot$i"; held_fds+=("$slot_fd"); flock -n "$slot_fd" || fail "test could not hold slot $i"; done
if CONTROLLER_REMOTE_SLOT_WAIT_S=0 CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj big "$SHA" > "$ROOT/busy.out" 2>&1; then fail 'a 3-slot lane was admitted with one free slot'; else rc=$?; [[ $rc == 75 ]] || fail "busy exit was $rc, expected 75"; fi
contains "$ROOT/busy.out" 'slots not free after 0s' 'busy refusal is explained'
CONTROLLER_REMOTE_SLOT_WAIT_S=0 CONTROLLER_REMOTE_DRYRUN=1 rg gate-a proj small "$SHA" > "$ROOT/free4.out" 2>&1 || fail 'a 1-slot lane was refused with a slot free'
contains "$ROOT/free4.out" 'slots [4] of 4' 'the free slot is the one acquired'
for slot_fd in "${held_fds[@]}"; do eval "exec $slot_fd>&-"; done

# --- an OOM kill inside the host slice is INCONCLUSIVE-OOM (76), never a lane FAIL; a plain failure keeps its code
reset_logs
OOM_STEP=2 FAKE_RG_RC=1 rg gate-a proj small "$SHA" > "$ROOT/oom.out" 2>&1; rc=$?
[[ $rc == 76 ]] || fail "an OOM-killed failed lane exited $rc, expected 76"
contains "$ROOT/oom.out" 'INCONCLUSIVE-OOM' 'OOM verdict is printed'
contains "$ROOT/oom.out" 'slice_oom_kill_delta=2' 'the measured delta is reported'
reset_logs
OOM_STEP=0 FAKE_RG_RC=1 rg gate-a proj small "$SHA" > "$ROOT/plainfail.out" 2>&1; rc=$?
[[ $rc == 1 ]] || fail "a plain failed lane exited $rc, expected 1"
not_contains "$ROOT/plainfail.out" 'INCONCLUSIVE-OOM' 'no OOM verdict without an OOM'
reset_logs
OOM_STEP=2 FAKE_RG_RC=0 rg gate-a proj small "$SHA" > "$ROOT/oompass.out" 2>&1 || fail 'a passing lane must stay a pass even when neighbours were OOM-killed'
reset_logs
FAKE_SSH_RC=1 FAKE_RG_RC=1 rg gate-a proj small "$SHA" > "$ROOT/nossh.out" 2>&1; rc=$?
[[ $rc == 1 ]] || fail "an unreadable OOM counter must not invent an OOM verdict (exit $rc)"
contains "$ROOT/nossh.out" 'slice_oom_kill_delta=-1' 'an unreadable counter is reported as -1'
reset_logs
FAKE_LANE_LOG=$'task killed: OOMKilled\n' FAKE_RG_RC=1 rg gate-a proj small "$SHA" > "$ROOT/laneoom.out" 2>&1; rc=$?
[[ $rc == 76 ]] || fail "an OOM marker in the lane log exited $rc, expected 76"
reset_logs
FAKE_RG_RC=75 rg gate-a proj small "$SHA" > /dev/null 2>&1; rc=$?
[[ $rc == 75 ]] || fail "the runner's PSI refusal (75) must pass through, got $rc"

# --- no controller-local literal ships in the tool
! grep -nE '/home/vscode|/workspaces/|vbpub-release-program|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' "$RG_SCRIPT" || fail 'remote-gate.sh carries a controller-local literal'

echo 'PASS remote-gate wrapper'
