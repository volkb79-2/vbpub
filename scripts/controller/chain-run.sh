#!/usr/bin/env bash
# Run a fixed list of gate lanes one after another, detached-friendly, and leave a marker file for the controller.
#
# usage: chain-run.sh [--head <sha>] [--remote-host <alias>] [--stop-on-fail] <worktree> <project-subdir> <log-prefix> <lane>...
#   lane              a plain lane name: runs through gate-run.sh from <worktree>/<project-subdir>, log <prefix>-<lane>.log
#   remote:<lane>     the lane on a remote gate worker through remote-gate.sh (host from --remote-host, or the only alias in
#                     CONTROLLER_REMOTE_GATE_HOSTS), commit = the worktree HEAD, log <prefix>-remote-<lane>.log
#   remote:<lane>:--base=<sha>   the same with a comparison base that travels to the host with the commit
# Logs and the marker go to CONTROLLER_LOGS_DIR. The marker <prefix>.chain is removed at start and written atomically at the end:
#   CHAIN-DONE                 every lane ended (whatever its verdict: the chain NEVER reads a verdict, the controller does)
#   STOPPED-ON-FAIL: ...       --stop-on-fail and a lane exited non-zero (the later lanes did not run)
#   HEAD moved: ... / tree dirty / <other reason>   a refusal before the first lane (also on stderr, exit 1)
# No marker means the chain is still running or was killed. Detached launch:
#   (setsid nohup chain-run.sh ... >/dev/null 2>&1 </dev/null & disown)
# Profiling follows gate-run.sh (off unless the caller sets RUN_GATE_PROFILE). The environment is passed on untouched: no
# image tag and no profiler socket are exported here.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

usage() {
  echo 'usage: chain-run.sh [--head <sha>] [--remote-host <alias>] [--stop-on-fail] <worktree> <project-subdir> <log-prefix> <lane>...' >&2
  exit 2
}

head_pin='' remote_host='' stop_on_fail=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --head) [[ $# -ge 2 && -n $2 ]] || usage; head_pin=$2; shift 2 ;;
    --remote-host) [[ $# -ge 2 && -n $2 ]] || usage; remote_host=$2; shift 2 ;;
    --stop-on-fail) stop_on_fail=1; shift ;;
    --) shift; break ;;
    -*) echo "chain-run.sh: unknown option '$1'" >&2; usage ;;
    *) break ;;
  esac
done
[[ $# -ge 4 ]] || usage
worktree=$1 subdir=$2 prefix=$3
shift 3
lanes=("$@")
[[ $prefix =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "chain-run.sh: invalid log prefix '$prefix'" >&2; exit 2; }
marker=$CONTROLLER_LOGS_DIR/$prefix.chain
rm -f -- "$marker"

write_marker() {
  if ! printf '%s\n' "$1" > "$marker.tmp.$$" || ! mv -f -- "$marker.tmp.$$" "$marker"; then
    echo "chain-run.sh: cannot write $marker" >&2
    exit 2
  fi
}
refuse() {
  echo "chain-run.sh: $*" >&2
  write_marker "$*"
  exit 1
}

[[ -d $worktree ]] || refuse "worktree missing: $worktree"
[[ $subdir != /* && $subdir != *..* ]] || refuse "project subdir must be relative and stay inside the worktree: $subdir"
[[ -d $worktree/$subdir ]] || refuse "project subdir missing: $worktree/$subdir"

want_local=0 want_remote=0 spec=''
for spec in "${lanes[@]}"; do
  if [[ $spec == remote:* ]]; then
    want_remote=1
    [[ ${spec#remote:} =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*(:--base=[^[:space:]:]+)?$ ]] || refuse "invalid remote lane spec '$spec' (remote:<lane> or remote:<lane>:--base=<sha>)"
  else
    want_local=1
    [[ $spec =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || refuse "invalid lane name '$spec'"
  fi
done

gate_run=${CONTROLLER_GATE_RUN_SCRIPT:-$SCRIPT_DIR/gate-run.sh}
remote_gate=${CONTROLLER_REMOTE_GATE_SCRIPT:-$SCRIPT_DIR/remote-gate.sh}
(( ! want_local )) || [[ -x $gate_run ]] || refuse "lane launcher missing: $gate_run"
(( ! want_remote )) || [[ -x $remote_gate ]] || refuse "lane launcher missing: $remote_gate"
if (( want_remote )) && [[ -z $remote_host ]]; then
  read -r -a allowed <<< "${CONTROLLER_REMOTE_GATE_HOSTS:-}"
  (( ${#allowed[@]} == 1 )) || refuse 'a remote lane needs --remote-host <alias> (CONTROLLER_REMOTE_GATE_HOSTS does not name exactly one host)'
  remote_host=${allowed[0]}
fi

head=''
if [[ -n $head_pin ]] || (( want_remote )); then
  head=$(git -C "$worktree" rev-parse HEAD 2>/dev/null) || refuse "not a git worktree: $worktree"
fi
if [[ -n $head_pin ]]; then
  want=$(git -C "$worktree" rev-parse --verify --quiet "$head_pin^{commit}")
  [[ -n $want && $want == "$head" ]] || refuse "HEAD moved: $head (pinned $head_pin)"
  dirty=$(git -C "$worktree" status --short) || refuse "git status failed in $worktree"
  [[ -z $dirty ]] || refuse 'tree dirty'
fi

lane_rc=0
for spec in "${lanes[@]}"; do
  if [[ $spec == remote:* ]]; then
    rest=${spec#remote:}
    lane=${rest%%:*}
    base=''
    [[ $rest == *:--base=* ]] && base=${rest#*:--base=}
    cmd=("$remote_gate" "$remote_host" "$subdir" "$lane" "$head")
    [[ -z $base ]] || cmd+=(--base "$base")
    log=$CONTROLLER_LOGS_DIR/$prefix-remote-$lane.log
    (cd -- "$worktree" && "${cmd[@]}") > "$log" 2>&1
  else
    lane=$spec
    log=$CONTROLLER_LOGS_DIR/$prefix-$lane.log
    (cd -- "$worktree/$subdir" && "$gate_run" "$lane") > "$log" 2>&1
  fi
  lane_rc=$?
  if (( lane_rc != 0 && stop_on_fail )); then
    write_marker "STOPPED-ON-FAIL: lane $spec exited $lane_rc"
    exit 1
  fi
done
write_marker CHAIN-DONE
