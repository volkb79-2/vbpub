#!/usr/bin/env bash
set -euo pipefail

die() {
  local status="${2:-3}"
  printf 'assay release-gate: %s\n' "$1" >&2
  exit "$status"
}

[[ $# -eq 1 ]] || die 'usage: tools/release-gate.sh tester-unified|self-qualification-provisional' 2
lane="$1"
case "$lane" in
  tester-unified)
    local_argv=(bash tools/tester-unified-gate.sh ..)
    ;;
  self-qualification-provisional)
    local_argv=(run-gate self-qualification-provisional)
    ;;
  *)
    die "unsupported release-gate lane: $lane" 2
    ;;
esac

host="${ASSAY_RELEASE_GATE_HOST:-}"
host_problem=""
case "$host" in
  local)
    exec "${local_argv[@]}"
    ;;
  v1001 | r1002)
    ;;
  '')
    host_problem='ASSAY_RELEASE_GATE_HOST is unset; set it to local, v1001, or r1002'
    ;;
  *)
    host_problem='ASSAY_RELEASE_GATE_HOST must be local, v1001, or r1002'
    ;;
esac

# cmru 7.5.0 ends a step that printed nothing for runner.no_progress_timeout_seconds (1800 s
# by default, cmru/src/cmru/runner.py:43). A delegated lane takes 6-50 minutes, so the wait
# below prints one progress line per interval instead of staying silent until the watchdog
# fires; 300 s feeds the default watchdog several lines before it could trip. An unset
# variable means 300; a set but non-integer or out-of-range value is refused, because it
# states an intent this script cannot honour.
# A value this script cannot honour is one of the combined refusal's problems, so a
# single remote run names every setting an operator has to fix (B579).
heartbeat_seconds="${ASSAY_RELEASE_GATE_HEARTBEAT_S-300}"
heartbeat_problem=""
if [[ ! "$heartbeat_seconds" =~ ^[0-9]{1,4}$ ]] \
  || (( 10#$heartbeat_seconds < 30 || 10#$heartbeat_seconds > 1500 )); then
  heartbeat_problem="ASSAY_RELEASE_GATE_HEARTBEAT_S must be an integer from 30 to 1500; got '$heartbeat_seconds'"
fi

remote_gate="../scripts/netcup/runner/remote-gate.py"
[[ -f "$remote_gate" ]] \
  || die "remote-gate.py is missing at $remote_gate"

remote_gate_base_env=(
  -u GITHUB_PUSH_PAT
  -u GITHUB_TOKEN
  -u CMRU_GIT_AUTH_TOKEN
  -u GH_TOKEN
  -u GH_ENTERPRISE_TOKEN
  -i
  "PATH=$PATH"
  "HOME=${HOME:-}"
)

# ---- settings: one source each, and one refusal for all of them -------------
# Resolved once, here, before any work. The release gate used to refuse one setting
# per run, so an operator with three unset settings needed three attempts and still
# could not see what the last one would be (B579).
#
# The host is a load decision and keeps NO default. The host table and the output
# directory come from the controller's own configuration when REMOTE_GATE_* do not
# name them, because scripts/controller/remote-gate.sh already owns those values
# (hosts file at :102-103, output dir at :225) and a second copy would drift. That
# configuration is READ, never written: a release gate must not provision the
# controller. remote-gate.py's own defaults are the last resort, as they were before.
# A value that is set but unusable is REFUSED rather than silently replaced by a
# later source: it is a fact, not a default.
release_gate_problems=()
[[ -z "$host_problem" ]] || release_gate_problems+=("$host_problem")
[[ -z "$heartbeat_problem" ]] || release_gate_problems+=("$heartbeat_problem")

controller_lib="../scripts/controller/lib.sh"
controller_home=""
controller_hosts=""
controller_remote_hosts=""
controller_remote_out=""
controller_read_error=""
# The controller configuration is consulted only while at least one explicit
# REMOTE_GATE_* value is missing. A value the operator exported outranks that file
# anyway, so an unreadable controller file must not block a fully explicit pair
# (the documented "explicit first" precedence, which is also the escape hatch
# when the controller file is broken).
if [[ -n ${HOME:-} && -f "$controller_lib" && (! -v REMOTE_GATE_HOSTS || ! -v REMOTE_GATE_OUT) ]]; then
  # CONTROLLER_INIT_READONLY keeps controller_init from creating or rewriting any
  # controller file or directory (scripts/controller/lib.sh:12-45); only the
  # resolved values are read back.
  controller_stderr="$(mktemp "${TMPDIR:-/tmp}/assay-release-gate-controller.XXXXXX")" \
    || die 'cannot create a temporary file to read the controller configuration'
  # `set -uo pipefail`, without -e, is what scripts/controller/remote-gate.sh:24 runs:
  # a controller file whose bare probe fails (a `command -v` for a tool this cockpit
  # lacks, say) must not be reported as unreadable when the values it defines are
  # usable. The read fails only on a fatal error, and its last stderr line is kept.
  if controller_config="$(env CONTROLLER_INIT_READONLY=1 bash -c '
      set -uo pipefail
      . "$1"
      controller_init
      printf "%s\n%s\n%s\n%s\n" "${CONTROLLER_HOME:-}" "${CONTROLLER_HOSTS_FILE:-}" \
        "${CONTROLLER_REMOTE_HOSTS_FILE:-}" "${CONTROLLER_REMOTE_OUT:-}"
    ' controller-config "$controller_lib" 2>"$controller_stderr")"; then
    mapfile -t controller_values <<<"$controller_config"
    controller_home=${controller_values[0]:-}
    controller_hosts=${controller_values[1]:-}
    controller_remote_hosts=${controller_values[2]:-}
    controller_remote_out=${controller_values[3]:-}
  else
    # A controller configuration that exists but cannot be read is refused, not
    # treated as absent: falling through to another source would hide a broken
    # controller.local.env behind a stale default. Its own reason is appended, so
    # the refusal does not send the operator hunting through the file.
    controller_read_error="$(<"$controller_stderr")"
    controller_read_error=${controller_read_error##*$'\n'}
    release_gate_problems+=("cannot read the controller configuration ($controller_lib controller_init${controller_read_error:+: $controller_read_error})")
  fi
  rm -f -- "$controller_stderr"
fi

# remote-gate.sh (scripts/controller/remote-gate.sh:102-103) resolves a configured
# hosts file against CONTROLLER_HOME when it is not absolute; do the same here.
resolve_controller_path() {
  local path="$1"
  [[ -n "$path" ]] || { printf '%s' ''; return 0; }
  if [[ "$path" != /* ]]; then
    path="$controller_home/$path"
  fi
  printf '%s' "$path"
}

# Every problem names the source that supplied the unusable value: the operator has to
# know which of REMOTE_GATE_HOSTS, the controller configuration and remote-gate.py's own
# defaults to look at, and the label is the one the resolution chain used.
hosts_file_problem() {
  local source="$1" path="$2"
  if [[ -z "$path" ]]; then
    printf 'host table from %s resolved to an empty path' "$source"
  elif [[ ! -e "$path" ]]; then
    printf 'host table from %s is missing: %s' "$source" "$path"
  elif [[ ! -f "$path" ]]; then
    printf 'host table from %s is not a regular file: %s' "$source" "$path"
  elif [[ ! -r "$path" ]]; then
    printf 'host table from %s is not readable: %s' "$source" "$path"
  fi
}

# remote-gate.py creates a missing output directory itself (run_dir.mkdir(parents=True)),
# so a path that does not exist yet is judged by the nearest existing ancestor that
# would have to accept it.
out_dir_problem() {
  local source="$1" path="$2" ancestor
  if [[ -z "$path" ]]; then
    printf 'output directory from %s resolved to an empty path' "$source"
    return 0
  fi
  if [[ -d "$path" ]]; then
    [[ -w "$path" ]] || printf 'output directory from %s is not writable: %s' "$source" "$path"
    return 0
  fi
  if [[ -e "$path" ]]; then
    printf 'output directory from %s exists but is not a directory: %s' "$source" "$path"
    return 0
  fi
  ancestor="$path"
  while [[ ! -e "$ancestor" ]]; do
    ancestor=$(dirname -- "$ancestor")
  done
  if [[ ! -d "$ancestor" ]]; then
    printf 'output directory from %s cannot be created: %s is not a directory' "$source" "$ancestor"
  elif [[ ! -w "$ancestor" ]]; then
    printf 'output directory from %s cannot be created: %s is not writable' "$source" "$ancestor"
  fi
}

# remote-gate.py's defaults, read from its module rather than copied here. Loaded at
# most once, in THIS shell: a loader called inside `$(...)` can never keep its flag, so
# the second value used to import the module again (two python starts and a second
# bytecode-cache write under the gitignored scripts/netcup/runner/__pycache__).
remote_gate_defaults=""
remote_gate_defaults_error=""
remote_gate_defaults_loaded=false
load_remote_gate_defaults() {
  [[ "$remote_gate_defaults_loaded" == true ]] && return 0
  remote_gate_defaults_loaded=true
  local stderr_file
  stderr_file=$(mktemp "${TMPDIR:-/tmp}/assay-release-gate-defaults.XXXXXX") || return 0
  if remote_gate_defaults="$(env "${remote_gate_base_env[@]}" python3 - "$remote_gate" 2>"$stderr_file" <<'PYEOF'
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("assay_release_remote_gate", path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load remote-gate.py to resolve its defaults")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(module.DEFAULT_HOSTS)
print(module.DEFAULT_OUT)
PYEOF
)"; then
    :
  else
    # An import that failed is not an empty default: saying so keeps the refusal from
    # certifying a path the module never supplied.
    remote_gate_defaults=""
    remote_gate_defaults_error="$(<"$stderr_file")"
    remote_gate_defaults_error=${remote_gate_defaults_error##*$'\n'}
  fi
  rm -f -- "$stderr_file"
}

# Exactly one source supplies each value, and a source that is consulted supplies either
# a usable value or the problem that refuses it; nothing falls through a failed source.
remote_hosts=""
hosts_problem=""
if [[ -v REMOTE_GATE_HOSTS ]]; then
  hosts_problem=$(hosts_file_problem REMOTE_GATE_HOSTS "$REMOTE_GATE_HOSTS")
  [[ -n "$hosts_problem" ]] || remote_hosts="$REMOTE_GATE_HOSTS"
elif [[ -n "$controller_remote_hosts" ]]; then
  candidate=$(resolve_controller_path "$controller_remote_hosts")
  hosts_problem=$(hosts_file_problem CONTROLLER_REMOTE_HOSTS_FILE "$candidate")
  [[ -n "$hosts_problem" ]] || remote_hosts="$candidate"
elif [[ -n "$controller_hosts" ]]; then
  candidate=$(resolve_controller_path "$controller_hosts")
  # controller_init's own default spelling (CONTROLLER_HOME/hosts.local.json) that
  # does not exist means this controller configures no host table; it is not a
  # misconfigured path, so the remote-gate.py default still applies.
  if [[ "$candidate" != "$controller_home/hosts.local.json" || -e "$candidate" ]]; then
    hosts_problem=$(hosts_file_problem CONTROLLER_HOSTS_FILE "$candidate")
    [[ -n "$hosts_problem" ]] || remote_hosts="$candidate"
  fi
fi
if [[ -z "$remote_hosts" && -z "$hosts_problem" ]]; then
  load_remote_gate_defaults
  if [[ -n "$remote_gate_defaults_error" ]]; then
    hosts_problem="cannot read remote-gate.py's default host table: $remote_gate_defaults_error"
  else
    candidate="${remote_gate_defaults%%$'\n'*}"
    hosts_problem=$(hosts_file_problem 'remote-gate.py DEFAULT_HOSTS' "$candidate")
    [[ -n "$hosts_problem" ]] || remote_hosts="$candidate"
  fi
fi
[[ -z "$hosts_problem" ]] || release_gate_problems+=("$hosts_problem")

remote_out=""
out_problem=""
if [[ -v REMOTE_GATE_OUT ]]; then
  out_problem=$(out_dir_problem REMOTE_GATE_OUT "$REMOTE_GATE_OUT")
  [[ -n "$out_problem" ]] || remote_out="$REMOTE_GATE_OUT"
elif [[ -n "$controller_remote_out" ]]; then
  # controller_init leaves CONTROLLER_REMOTE_OUT in its configured spelling, and
  # remote-gate.sh creates it relative to its own working directory. A relative
  # value has no stable meaning here, so resolve it against CONTROLLER_HOME the
  # way controller_init resolves its own directories.
  candidate=$(resolve_controller_path "$controller_remote_out")
  out_problem=$(out_dir_problem CONTROLLER_REMOTE_OUT "$candidate")
  [[ -n "$out_problem" ]] || remote_out="$candidate"
elif [[ -d "$controller_home" ]]; then
  # controller_init's default output directory. Used only when the controller home
  # itself exists: a CONTROLLER_HOME spelling that names no directory configures
  # nothing, so remote-gate.py's own default still applies.
  candidate="$controller_home/remote-gates"
  out_problem=$(out_dir_problem CONTROLLER_HOME/remote-gates "$candidate")
  [[ -n "$out_problem" ]] || remote_out="$candidate"
fi
if [[ -z "$remote_out" && -z "$out_problem" ]]; then
  load_remote_gate_defaults
  if [[ -n "$remote_gate_defaults_error" ]]; then
    out_problem="cannot read remote-gate.py's default output directory: $remote_gate_defaults_error"
  else
    candidate="${remote_gate_defaults#*$'\n'}"
    out_problem=$(out_dir_problem 'remote-gate.py DEFAULT_OUT' "$candidate")
    [[ -n "$out_problem" ]] || remote_out="$candidate"
  fi
fi
[[ -z "$out_problem" ]] || release_gate_problems+=("$out_problem")

if [[ -z ${HOME:-} ]]; then
  release_gate_problems+=('HOME is unset; remote-gate needs it for the host table and ~/.ssh')
fi

if (( ${#release_gate_problems[@]} )); then
  combined_problem='the remote release-gate environment is not usable:'
  for problem in "${release_gate_problems[@]}"; do
    combined_problem+=$'\n'"  - $problem"
  done
  die "$combined_problem" 2
fi

# The CMRU transaction binding is a separate refusal on purpose: it can only be judged
# where CMRU states the commit, and it is not one of the settings above. It is checked
# after them so a run outside CMRU still hears about its missing settings first.
commit="${CMRU_RELEASE_GATE_COMMIT:-}"
[[ -n "$commit" ]] \
  || die 'CMRU_RELEASE_GATE_COMMIT is unset; run this from the CMRU release gate'
[[ "$commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] \
  || die 'CMRU_RELEASE_GATE_COMMIT must be a full lowercase Git object ID'

worktree="$(git rev-parse --show-toplevel)" \
  || die 'cannot resolve the vbpub worktree root'
head="$(git rev-parse HEAD)" \
  || die 'cannot resolve the release-gate worktree HEAD'
[[ "$head" == "$commit" ]] \
  || die "CMRU_RELEASE_GATE_COMMIT ($commit) does not match worktree HEAD ($head)"

remote_gate_env=(
  "${remote_gate_base_env[@]}"
  "REMOTE_GATE_OUT=$remote_out"
  "REMOTE_GATE_HOSTS=$remote_hosts"
)
if [[ -n ${SSH_AUTH_SOCK:-} ]]; then
  remote_gate_env+=("SSH_AUTH_SOCK=$SSH_AUTH_SOCK")
fi

run_remote_gate() {
  env "${remote_gate_env[@]}" python3 "$remote_gate" "$@"
}

# remote-gate bounds run IDs to 61 characters. Seven base-36 UTC epoch-second
# digits keep the full lane and 12-character commit prefix in that bound.
utc_seconds="$(python3 - <<'PYEOF'
from datetime import datetime, timezone

print(int(datetime.now(timezone.utc).timestamp()))
PYEOF
)" || die 'cannot read the current UTC time'
[[ "$utc_seconds" =~ ^[0-9]+$ ]] || die 'UTC time did not resolve to epoch seconds'
utc="$(python3 - "$utc_seconds" <<'PYEOF'
import sys

value = int(sys.argv[1])
digits = "0123456789abcdefghijklmnopqrstuvwxyz"
result = ""
while value:
    value, remainder = divmod(value, 36)
    result = digits[remainder] + result
result = result or "0"
if len(result) > 7:
    raise SystemExit("UTC epoch timestamp no longer fits remote-gate's run ID limit")
print(result.rjust(7, "0"))
PYEOF
)" || die 'cannot encode the current UTC time for a remote-gate run ID'
run_name="assay-rel-${commit:0:12}-${lane}-${utc}"
run_dir="$remote_out/$run_name"
remote_gate_pid=""
remote_gate_launching=false
heartbeat_starting=false
pending_signal=""
pending_signal_status=""
remote_gate_started_at=""
heartbeat_pid=""

# The wait below stays the plain blocking `wait "$remote_gate_pid"`, so a trapped INT/TERM
# interrupts it exactly as before and the child's exit status still reaches remote_status.
# The heartbeat is a separate subshell: its own sleep is interruptible, so the cancel path
# stops it before it cancels the remote run and nothing is left printing after the wait.
print_wait_line() {
  printf 'assay release-gate: waiting for remote run %s on %s (%ss)\n' \
    "$run_name" "$host" "$(( $(date +%s) - remote_gate_started_at ))" >&2
}

start_heartbeat() {
  local interval=$heartbeat_seconds parent_pid=$$
  (
    set +e
    sleep_pid=""
    # A bash subshell inherits the parent's `$!`, and at this point that is the
    # remote-gate child the wrapper forked moments before it started this heartbeat
    # (remote_gate_pid). `${!:-}` in the trap below is meant to name a sleep THIS loop
    # forked, so the inherited pid is consumed first: a placeholder child is forked
    # below this subshell's own jobs and reaped, which leaves `$!` naming a pid the
    # loop never owned and that is already gone. Without this line a TERM landing
    # before the loop's first `sleep ... &` would have sent TERM to remote-gate.py and
    # left the sleep it was protecting to run on.
    { : & wait "$!"; } 2>/dev/null
    # `$!` is now the pid of the most recent sleep this loop forked. The first iteration
    # forks its `sleep` BEFORE `sleep_pid=$!` records it, so a TERM landing in that
    # window would have killed the empty string, left the real sleep running for up to
    # `interval` seconds and held the caller's pipe open. `${!:-}` names that fork, which
    # is the very job the trap exists to kill; after a `wait` has reaped a sleep it names
    # that reaped pid until the next fork, and `kill` on it is a no-op unless the kernel
    # has reused it (`sleep_pid` is cleared right after every `wait`).
    trap 'kill "${!:-}" 2>/dev/null; kill "${sleep_pid:-}" 2>/dev/null; exit 0' INT TERM
    # `$$` is the wrapper's own pid inside a subshell, so a wrapper that dies by SIGKILL
    # stops this loop within one iteration: the `kill -0` check ends it, so at most one
    # more line is printed into the caller's still-open pipe and then the subshell exits.
    while kill -0 "$parent_pid" 2>/dev/null; do
      sleep "$interval" &
      sleep_pid=$!
      wait "$sleep_pid"
      sleep_pid=""
      kill -0 "$parent_pid" 2>/dev/null || break
      print_wait_line
    done
  ) &
  heartbeat_pid=$!
}

start_heartbeat_guarded() {
  # INT/TERM can arrive between the fork inside start_heartbeat and the assignment that
  # records its pid (`heartbeat_pid=$!`). Handling them there would find no pid to stop
  # and orphan a subshell that keeps printing into the caller's pipe, so the two signals
  # are deferred (never dropped) and handled below, once the pid is known. `trap '' INT
  # TERM` around the fork would discard a signal instead of deferring it.
  heartbeat_starting=true
  start_heartbeat
  heartbeat_starting=false
  if [[ -n "$pending_signal" ]]; then
    handle_remote_signal "$pending_signal" "$pending_signal_status"
  fi
}

stop_heartbeat() {
  [[ -n "$heartbeat_pid" ]] || return 0
  kill "$heartbeat_pid" 2>/dev/null || true
  wait "$heartbeat_pid" 2>/dev/null || true
  heartbeat_pid=""
}

handle_remote_signal() {
  local signal_name="$1" exit_status="$2" cancel_status
  if [[ (-z "$remote_gate_pid" && "$remote_gate_launching" == true) \
    || "$heartbeat_starting" == true ]]; then
    pending_signal="$signal_name"
    pending_signal_status="$exit_status"
    return 0
  fi

  trap ':' INT TERM
  stop_heartbeat
  if [[ -n "$remote_gate_pid" ]]; then
    kill -s "$signal_name" "$remote_gate_pid" 2>/dev/null || true
  fi
  if [[ -f "$run_dir/meta.json" ]]; then
    printf 'assay release-gate: interrupted; attempting to cancel remote run %s\n' \
      "$run_name" >&2
    if run_remote_gate cancel "$run_name"; then
      printf 'assay release-gate: remote cancellation was requested: %s\n' "$run_name" >&2
    else
      cancel_status=$?
      printf 'assay release-gate: cancel attempt for %s failed (exit %s); the host job may remain active\n' \
        "$run_name" "$cancel_status" >&2
    fi
  fi
  if [[ -n "$remote_gate_pid" ]]; then
    wait "$remote_gate_pid" 2>/dev/null || true
  fi
  exit "$exit_status"
}

trap 'handle_remote_signal INT 130' INT
trap 'handle_remote_signal TERM 143' TERM

remote_gate_launching=true
env "${remote_gate_env[@]}" python3 "$remote_gate" run \
  "$host" "$worktree" "$lane" \
  --project-dir assay --repo vbpub --name "$run_name" &
remote_gate_pid=$!
remote_gate_launching=false
if [[ -n "$pending_signal" ]]; then
  handle_remote_signal "$pending_signal" "$pending_signal_status"
fi
remote_gate_started_at=$(date +%s)
start_heartbeat_guarded
if wait "$remote_gate_pid"; then
  remote_status=0
else
  remote_status=$?
fi
stop_heartbeat
remote_gate_pid=""

if receipt="$(python3 - "$run_dir/meta.json" "$run_dir/result.json" <<'PYEOF'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        meta = json.load(stream)
    with open(sys.argv[2], encoding="utf-8") as stream:
        result = json.load(stream)
except (OSError, json.JSONDecodeError) as exc:
    print(f"cannot read remote-gate result files: {exc}", file=sys.stderr)
    raise SystemExit(2)

sha = meta.get("sha")
verdict = result.get("verdict")
exit_code = result.get("exit_code")
if not isinstance(sha, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha) is None:
    print("remote-gate meta.json has no valid full Git object ID", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(verdict, str) or not verdict:
    print("remote-gate result.json has no verdict", file=sys.stderr)
    raise SystemExit(2)
if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
    print("remote-gate result.json has no valid lane exit code", file=sys.stderr)
    raise SystemExit(2)
print(f"{sha}\t{verdict}\t{exit_code}")
PYEOF
)"; then
  IFS=$'\t' read -r meta_sha verdict lane_status <<<"$receipt"
else
  if (( remote_status != 0 )); then
    if [[ -f "$run_dir/meta.json" && ! -f "$run_dir/result.json" ]]; then
      printf 'assay release-gate: remote-gate exited without a result; attempting to cancel unattended remote run %s\n' \
        "$run_name" >&2
      if run_remote_gate cancel "$run_name"; then
        printf 'assay release-gate: remote run cancellation was requested: %s\n' \
          "$run_name" >&2
      else
        cancel_status=$?
        printf 'assay release-gate: cancel attempt for %s failed (exit %s); the host job may remain active\n' \
          "$run_name" "$cancel_status" >&2
      fi
    fi
    printf 'assay release-gate: remote-gate produced no valid result for %s (exit %s)\n' \
      "$run_name" "$remote_status" >&2
    exit "$remote_status"
  fi
  die "remote-gate produced no valid result for $run_name"
fi

[[ "$meta_sha" == "$commit" ]] \
  || die "remote-gate meta.json SHA mismatch: expected $commit, got $meta_sha"
[[ "$remote_status" == "$lane_status" ]] \
  || die "remote-gate exit status $remote_status differs from result.json lane exit code $lane_status"

if [[ "$verdict" == PASS && "$lane_status" == 0 ]]; then
  exit "$lane_status"
fi

printf 'assay release-gate: refusing %s verdict %s (lane exit code %s)\n' \
  "$lane" "$verdict" "$lane_status" >&2
if (( lane_status != 0 )); then
  exit "$lane_status"
fi
exit 3
