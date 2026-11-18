#!/usr/bin/env bash
# Run ONE registered run-gate lane on a remote gate worker through the host table.
#
# usage: remote-gate.sh <host-alias> <project-dir-relative-to-repo> <lane> <commit-or-branch> [--base <ref>] [-- extra run-gate args...]
#
# In order:
#   1. Resolve <commit-or-branch> to a full commit in the controller repo (a branch may be given as `x` or `origin/x`).
#   2. Provider identity check for the host (the host table's `api_check`, or CONTROLLER_REMOTE_API_CHECK); fail closed.
#   3. Admission: lane weight (declared resources.memory at that commit / measured peak table / one slot) -> k of N flock
#      slots. Slots are flock files, so the kernel frees them when this process dies; a ticket lock serialises acquisition so
#      a heavy lane cannot be starved and two callers cannot split the free slots between them.
#   4. Throwaway detached worktree of that commit, then scripts/netcup/runner/remote-gate.py `run`: it bundles the commit,
#      uploads and checks it out on the host, runs run-gate there, streams the log back and collects the artifacts.
#   5. Writes $CONTROLLER_LOGS_DIR/<run-name>.log (lane log + a binding line `run-gate.commit=<full sha>`), copies the
#      verdict JSON files next to it, prints the final verdict line and the commit binding, exits with the lane's code.
# Exit codes: the lane's exit code; 2 usage/refusal; 3 identity or transport failure; 75 PSI refused or slots busy;
# 76 INCONCLUSIVE-OOM (an OOM kill in the host's gate slice during the run: not a lane FAIL).
# Never prints secrets. Host, SSH key, pin and identity command come only from the private host table (README, docs/CONFIGURATION.md).
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

die() { echo "remote-gate.sh: $*" >&2; exit "${2:-2}"; }

# The program-directory tool read other names; a stale setting would be ignored silently, so name the replacement (never mapped).
for renamed in REMOTE_GATE_V1001_SLOTS:CONTROLLER_REMOTE_SLOTS REMOTE_GATE_V1001_BUDGET_MB:CONTROLLER_REMOTE_BUDGET_MB \
               REMOTE_GATE_WEIGHT_MB:CONTROLLER_REMOTE_WEIGHT_MB REMOTE_GATE_SLOT_WAIT_S:CONTROLLER_REMOTE_SLOT_WAIT_S \
               REMOTE_GATE_WAIT_PSI:CONTROLLER_REMOTE_WAIT_PSI REMOTE_GATE_DRYRUN:CONTROLLER_REMOTE_DRYRUN \
               REMOTE_GATE_PY:CONTROLLER_REMOTE_GATE_PY; do
  old_name=${renamed%%:*}
  [[ -z ${!old_name:-} ]] || echo "remote-gate.sh: WARNING: $old_name is set but not read here (not mapped); use ${renamed#*:}" >&2
done

[[ $# -ge 4 ]] || die 'usage: remote-gate.sh <host-alias> <project-dir> <lane> <commit-or-branch> [--base <ref>] [-- extra run-gate args...]'
host=$1 pdir=$2 lane=$3 ref=$4; shift 4
base=''
if [[ ${1:-} == --base ]]; then
  [[ $# -ge 2 && -n $2 ]] || die '--base needs a ref'
  base=$2; shift 2
fi
extra=()
if [[ ${1:-} == -- ]]; then shift; extra=("$@"); elif [[ $# -gt 0 ]]; then die "unexpected argument '$1' (extra run-gate arguments go after --)"; fi
# The old pass-through spelling `-- --base <ref>` (or `--base=<ref>`) reaches the same effect as the --base option: only
# remote-gate.py's own --base also bundles the base commit, so the host has it; run-gate alone would not find it there.
kept=()
while (( ${#extra[@]} > 0 )); do
  case ${extra[0]} in
    --base|--base=*)
      [[ -z $base ]] || die '--base given twice (option and pass-through)'
      if [[ ${extra[0]} == --base ]]; then
        [[ ${#extra[@]} -ge 2 && -n ${extra[1]} ]] || die '--base needs a ref'
        base=${extra[1]}; extra=("${extra[@]:2}")
      else
        base=${extra[0]#--base=}; extra=("${extra[@]:1}")
        [[ -n $base ]] || die '--base needs a ref'
      fi ;;
    *) kept+=("${extra[0]}"); extra=("${extra[@]:1}") ;;
  esac
done
extra=(${kept[@]+"${kept[@]}"})
controller_validate_host_alias "$host" || exit 2
[[ $pdir != /* && $pdir != *..* ]] || die 'project dir must be relative to the repo root and stay inside it'
[[ $lane =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die 'invalid lane name'

# Which aliases may receive a gate is local policy: the host table also names acceptance hosts that must never get one.
case " ${CONTROLLER_REMOTE_GATE_HOSTS:-} " in
  *" $host "*) ;;
  *) die "host '$host' is not in CONTROLLER_REMOTE_GATE_HOSTS (space-separated aliases allowed to receive gates; set it in controller.local.env)" ;;
esac
hosts_file=${CONTROLLER_REMOTE_HOSTS_FILE:-$CONTROLLER_HOSTS_FILE}
[[ $hosts_file == /* ]] || hosts_file=$CONTROLLER_HOME/$hosts_file
[[ -f $hosts_file ]] || die "host table missing: $hosts_file"
py=${CONTROLLER_REMOTE_PYTHON:-python3}
"$py" -c 'import tomllib' 2>/dev/null || die "$py cannot import tomllib (needs Python 3.11+); set CONTROLLER_REMOTE_PYTHON"
repo=$(git -C "$CONTROLLER_REPO" rev-parse --show-toplevel 2>/dev/null) || die "CONTROLLER_REPO is not a git checkout: $CONTROLLER_REPO"

# 1. commit
sha=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}" || git -C "$repo" rev-parse --verify --quiet "origin/$ref^{commit}") \
  || die "cannot resolve '$ref' to a commit in $repo (fetch it first)"
[[ $sha =~ ^[0-9a-f]{40}$ ]] || die "unexpected commit id '$sha'"
if [[ -n $base ]]; then
  git -C "$repo" rev-parse --verify --quiet "$base^{commit}" >/dev/null || die "--base '$base' does not resolve to a commit in $repo"
fi

api_check() {
  local -a command=()
  if [[ -n ${CONTROLLER_REMOTE_API_CHECK:-} ]]; then "$CONTROLLER_REMOTE_API_CHECK" "$host"; return $?; fi
  controller_host_argv command "$host" api_check "$hosts_file" || return 2
  (cd -- "$CONTROLLER_HOME" && "${command[@]}")
}
remote_probe() {
  # One identity-guarded read-only command on the host: the same wrapping hx.sh uses, but against this script's host table.
  local remote_command=$1 guard script quoted
  local -a ssh_argv=()
  controller_host_argv ssh_argv "$host" ssh "$hosts_file" || return 1
  guard=$(controller_host_scalar "$host" guard "$hosts_file") || return 1
  script=$(printf 'set -e\nif ! { %s\n}; then printf "GUARD-FAIL\\n" >&2; exit 98; fi\n%s\n' "$guard" "$remote_command")
  quoted=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$script") || return 1
  "${ssh_argv[@]}" "bash -c $quoted"
}

# 2. identity (fail closed)
api_check >/dev/null 2>&1 || die "provider identity check for $host FAILED; refusing to contact the host" 3

# 3. admission: weight (MiB) -> k of N flock slots
slots=${CONTROLLER_REMOTE_SLOTS:-4}      # total slots; unit = budget / slots. Keep one value for every caller.
[[ $slots =~ ^[1-8]$ ]] || die 'CONTROLLER_REMOTE_SLOTS must be 1..8'
budget_mb=${CONTROLLER_REMOTE_BUDGET_MB:-5120}   # the host gate slice's MemoryHigh (host-owned limit)
[[ $budget_mb =~ ^[0-9]+$ && $budget_mb -ge $slots ]] || die 'CONTROLLER_REMOTE_BUDGET_MB must be a positive integer MiB, at least one per slot'
unit=$(( budget_mb / slots ))
weights_file=${CONTROLLER_REMOTE_WEIGHTS_FILE:-$CONTROLLER_HOME/remote-gate-weights.tsv}
weight=$("$py" - "$repo" "$sha" "$pdir" "$lane" "$weights_file" "${CONTROLLER_REMOTE_WEIGHT_MB:-}" "$unit" <<'PYEOF'
import math, subprocess, sys, tomllib, re
repo, sha, pdir, lane, wf, forced, unit = sys.argv[1:8]
unit = int(unit)
if forced:
    print(int(forced)); sys.exit()
def mb(v):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgKMG])i?[bB]?", str(v).strip())
    if not m: return None
    return float(m.group(1)) * {"k": 1/1024, "m": 1, "g": 1024}[m.group(2).lower()]
declared = None
r = subprocess.run(["git", "-C", repo, "show", f"{sha}:{pdir.rstrip('/')}/run-gate.toml"], capture_output=True, text=True)
if r.returncode == 0:
    try:
        d = tomllib.loads(r.stdout)
        declared = mb(((d.get("lanes") or {}).get(lane) or {}).get("resources", {}).get("memory", ""))
    except Exception:
        pass
measured = None
try:
    for line in open(wf):
        if line.startswith("#") or not line.strip(): continue
        k, v = line.split("\t")[:2]
        if k == f"{pdir.rstrip('/')}:{lane}": measured = float(v) * 1.15   # measured peak + 15% headroom
except FileNotFoundError:
    pass    # no table yet is the documented fallback (declared memory, else the lane class default)
except OSError as error:
    print(f"remote-gate.sh: WARNING: weights file {wf} exists but cannot be read ({error.strerror}); falling back to the declared or class weight", file=sys.stderr)
cands = [x for x in (declared, measured) if x]
if cands:
    w = min(cands)
elif re.search(r"(^|[-_])(r2|mutation|self-qualification[-a-z]*|campaign)($|[-_])", lane):
    w = 2 * unit    # unknown R2-class lane: two slots until it is measured
else:
    w = unit        # unknown ordinary lane: one slot, record its peak afterwards
print(math.ceil(round(w, 6)))   # float noise (800 * 1.15 = 919.9999999999999) must not lose a MiB
PYEOF
)
[[ $weight =~ ^[0-9]+$ ]] || weight=$unit
need=$(( (weight + unit - 1) / unit )); (( need < 1 )) && need=1; (( need > slots )) && need=$slots
lockdir=$CONTROLLER_HOME/remote-gate-locks
mkdir -p -- "$lockdir"
admit_deadline=${CONTROLLER_REMOTE_SLOT_WAIT_S:-7200}
[[ $admit_deadline =~ ^[0-9]+$ ]] || die 'CONTROLLER_REMOTE_SLOT_WAIT_S must be a non-negative integer'
exec {mfd}>"$lockdir/$host.admit"
flock -w "$admit_deadline" "$mfd" || die "admission ticket not obtained within ${admit_deadline}s" 75
held=(); lockfds=(); waited=0
while (( ${#held[@]} < need )); do
  for i in $(seq 1 "$slots"); do
    (( ${#held[@]} >= need )) && break
    [[ " ${held[*]:-} " == *" $i "* ]] && continue
    exec {sfd}>"$lockdir/$host.slot$i"
    if flock -n "$sfd"; then held+=("$i"); lockfds+=("$sfd"); else exec {sfd}>&-; fi
  done
  (( ${#held[@]} >= need )) && break
  (( waited % 300 == 0 )) && echo "remote-gate.sh: need $need of $slots $host slots (weight ${weight} MiB), holding ${#held[@]}; waiting (${waited}s)" >&2
  (( waited >= admit_deadline )) && die "slots not free after ${waited}s" 75
  sleep 5; waited=$((waited + 5))
done
exec {mfd}>&-
echo "remote-gate.sh: $host slots [${held[*]}] of $slots (weight ${weight} MiB, unit ${unit}), commit $sha, $pdir:$lane" >&2

close_slots() {
  # Children must not inherit the slot descriptors: a runner that outlived this process would keep the slots held.
  local fd
  for fd in "${lockfds[@]}"; do eval "exec $fd>&-"; done
}
if [[ -n ${CONTROLLER_REMOTE_DRYRUN:-} ]]; then
  ( close_slots; exec sleep "${CONTROLLER_REMOTE_DRY_HOLD:-0}" )
  echo "remote-gate.sh: dry run, nothing sent to the host" >&2
  exit 0
fi

# the slice OOM counter before the run (read-only, identity-guarded): INCONCLUSIVE-OOM instead of FAIL
oom_kill() {
  api_check >/dev/null 2>&1 || { echo -1; return; }
  remote_probe "awk '/^oom_kill /{print \$2}' /sys/fs/cgroup/dev.slice/dev-gates.slice/memory.events" 2>/dev/null || echo -1
}
oom0=$(oom_kill)

# 4. run
out=${CONTROLLER_REMOTE_OUT:-$CONTROLLER_HOME/remote-gates}
logs=$CONTROLLER_LOGS_DIR
ts=$(date -u +%Y%m%dT%H%M%S)
safe=$(printf '%s' "$lane" | tr -c 'A-Za-z0-9\n' -)
name="rgw-${safe}-${ts}-${host}"; name=${name:0:60}
workroot=$CONTROLLER_HOME/remote-gate-work
mkdir -p -- "$workroot" "$CONTROLLER_HOME/tools" "$out"
wt=$workroot/rgw-$ts-$$
git -C "$repo" worktree add -q --detach "$wt" "$sha" || die 'worktree add failed' 3
# shellcheck disable=SC2329 # installed as the EXIT trap below
cleanup() { git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || echo "remote-gate.sh: could not remove $wt" >&2; }
trap cleanup EXIT

rg=${CONTROLLER_REMOTE_GATE_PY:-}
if [[ -z $rg ]]; then
  # The runner is taken from the reference branch, never from the commit under test: a gate must not run its own runner.
  rg=$CONTROLLER_HOME/tools/remote-gate-main.py
  runner_ref=${CONTROLLER_REMOTE_GATE_PY_REF:-origin/main}
  # Concurrent runs share this path: each writes its own temporary file in the same directory and renames it over the
  # snapshot (atomic), so one run can never publish or truncate the other's half-written copy.
  rg_tmp=$(mktemp "$rg.XXXXXX") || die "cannot create a temporary runner snapshot beside $rg" 3
  if git -C "$repo" show "$runner_ref:scripts/netcup/runner/remote-gate.py" > "$rg_tmp" 2>/dev/null; then
    mv -f -- "$rg_tmp" "$rg" || { rm -f -- "$rg_tmp"; die "cannot install the runner snapshot: $rg" 3; }
  else
    rm -f -- "$rg_tmp"
    die "cannot read scripts/netcup/runner/remote-gate.py from $runner_ref" 3
  fi
fi
[[ -f $rg ]] || die "remote-gate.py not found: $rg" 3

run_args=(--hosts-file "$hosts_file" --out-dir "$out" run "$host" . "$lane" --project-dir "$pdir")
run_args+=(--repo "${CONTROLLER_REMOTE_REPO_NAME:-vbpub}")   # this IS the vbpub repo: the checkout directory name must not decide
[[ -z $base ]] || run_args+=(--base "$base")
run_args+=(--name "$name" --wait-psi "${CONTROLLER_REMOTE_WAIT_PSI:-120}")
tmp=$(mktemp "$CONTROLLER_HOME/.rgw-XXXXXX.out")
( close_slots; cd "$wt" && "$py" "$rg" "${run_args[@]}" ${extra[@]+-- "${extra[@]}"} ) 2>&1 | ( close_slots; exec tee "$tmp" >&2 )
rc=${PIPESTATUS[0]}

# 5. collect and bind
run_dir=$out/$name
final=$logs/$name.log
{
  echo "remote-gate.sh: host=$host run=$name lane=$pdir:$lane requested=$ref"
  if [[ -f $run_dir/job.log ]]; then echo '--- job.log (host side) ---'; cat "$run_dir/job.log"; fi
  if [[ -f $run_dir/lane.log ]]; then echo '--- lane.log ---'; cat "$run_dir/lane.log"; fi
  echo '--- remote-gate output ---'; cat "$tmp"
} > "$final" 2>/dev/null
rm -f -- "$tmp"
n=0
if [[ -d $run_dir/artifacts ]]; then
  while IFS= read -r -d '' f; do n=$((n+1)); cp -- "$f" "$logs/$name.verdict-$n.json"; done \
    < <(find "$run_dir/artifacts" -type f -name 'verdict*.json' -print0 2>/dev/null)
fi
checked=$(grep -c "checkout .* at $sha" "$run_dir/job.log" 2>/dev/null || true)
if [[ ${checked:-0} -ge 1 ]]; then
  echo "remote-gate: bound run-gate.commit=$sha (host checkout verified at this commit) run=$name" >> "$final"
fi
oom1=$(oom_kill)
oomd=-1; [[ $oom0 =~ ^[0-9]+$ && $oom1 =~ ^[0-9]+$ ]] && oomd=$(( oom1 - oom0 ))
vline=$(grep -E '^\s*run-gate:\s*lane\b.*\bverdict\s+[A-Z_]+' "$final" | tail -1)
echo '----'
echo "log:      $final"
echo "verdicts: $n JSON file(s) copied to $logs/$name.verdict-*.json"
echo "commit:   run-gate.commit=$sha  (checkout verified on host: ${checked:-0} line(s))"
echo "verdict:  ${vline:-NO VERDICT LINE (see the log and $run_dir/result.json)}"
echo "admission: weight=${weight}MiB slots=${#held[@]}/$slots slice_oom_kill_delta=$oomd"
echo "remote-gate: admission weight=${weight}MiB slots=${#held[@]}/$slots slice_oom_kill_delta=$oomd" >> "$final"
if (( rc != 0 )) && { (( oomd > 0 )) || grep -q -E 'OOMKilled|oom-kill|exit(_code)? 137|Killed' "$run_dir/lane.log" 2>/dev/null; }; then
  echo 'verdict:  INCONCLUSIVE-OOM (an OOM kill happened in the host gate slice during this run; NOT a lane FAIL, retry with fewer neighbours)'
  echo 'remote-gate: INCONCLUSIVE-OOM' >> "$final"
  exit 76
fi
exit "$rc"
