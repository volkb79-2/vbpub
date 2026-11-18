#!/usr/bin/env bash
# Shared local configuration and host-table helpers for the controller kit.

controller_init_resolve() {
  # $1 = variable name. Replaces its value with the physical path. With CONTROLLER_INIT_READONLY=1 (ctl-doctor.sh) a
  # directory that does not exist keeps its configured spelling, so a read-only caller can still report on it.
  local -n target=$1
  if [[ -n ${CONTROLLER_INIT_READONLY:-} && ! -d $target ]]; then return 0; fi
  target=$(cd -- "$target" && pwd -P)
}

controller_init() {
  : "${HOME:?HOME must be set}"
  CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
  local requested_runs_dir=${CONTROLLER_RUNS_DIR-}
  if [[ -f $CONTROLLER_HOME/controller.local.env ]]; then
    set -a
    # shellcheck disable=SC1090,SC1091
    . "$CONTROLLER_HOME/controller.local.env"
    set +a
  fi
  [[ -z $requested_runs_dir ]] || CONTROLLER_RUNS_DIR=$requested_runs_dir
  CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
  # The configured spellings (before symlinks are resolved) are kept for ctl-doctor.sh: the resolved paths below no
  # longer show that a directory lives behind a symlink.
  [[ $CONTROLLER_HOME == /* ]] || CONTROLLER_HOME=$PWD/$CONTROLLER_HOME
  CONTROLLER_HOME_CONFIGURED=$CONTROLLER_HOME
  controller_init_resolve CONTROLLER_HOME
  CONTROLLER_LOGS_DIR=${CONTROLLER_LOGS_DIR:-"$CONTROLLER_HOME/logs"}
  CONTROLLER_RUNS_DIR=${CONTROLLER_RUNS_DIR:-"$CONTROLLER_HOME/runs"}
  [[ $CONTROLLER_LOGS_DIR == /* ]] || CONTROLLER_LOGS_DIR=$CONTROLLER_HOME/$CONTROLLER_LOGS_DIR
  [[ $CONTROLLER_RUNS_DIR == /* ]] || CONTROLLER_RUNS_DIR=$CONTROLLER_HOME/$CONTROLLER_RUNS_DIR
  CONTROLLER_LOGS_DIR_CONFIGURED=$CONTROLLER_LOGS_DIR
  CONTROLLER_RUNS_DIR_CONFIGURED=$CONTROLLER_RUNS_DIR
  [[ -n ${CONTROLLER_INIT_READONLY:-} ]] || mkdir -p -- "$CONTROLLER_LOGS_DIR" "$CONTROLLER_HOME/runs" "$CONTROLLER_RUNS_DIR"
  controller_init_resolve CONTROLLER_LOGS_DIR
  controller_init_resolve CONTROLLER_RUNS_DIR
  local script_dir
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  CONTROLLER_REPO=${CONTROLLER_REPO:-$(cd -- "$script_dir/../.." && pwd -P)}
  CONTROLLER_HOSTS_FILE=${CONTROLLER_HOSTS_FILE:-"$CONTROLLER_HOME/hosts.local.json"}
  [[ $CONTROLLER_HOSTS_FILE == /* ]] || CONTROLLER_HOSTS_FILE=$CONTROLLER_HOME/$CONTROLLER_HOSTS_FILE
  if [[ $CONTROLLER_REPO != /* && -d $CONTROLLER_REPO ]]; then
    CONTROLLER_REPO=$(cd -- "$CONTROLLER_REPO" && pwd -P)
  fi
  export CONTROLLER_HOME CONTROLLER_LOGS_DIR CONTROLLER_RUNS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
  export CONTROLLER_HOME_CONFIGURED CONTROLLER_LOGS_DIR_CONFIGURED CONTROLLER_RUNS_DIR_CONFIGURED
}

# ---- local gate slots ------------------------------------------------------
# gate-run.sh and ctl-doctor.sh share this rule, so it is defined once. CONTROLLER_GATE_SLOTS (1..8, default 2) is the number
# of local gate slots. Slot 1 is the file CONTROLLER_GATE_LOCK and slot 2 is the file CONTROLLER_GATE2_LOCK (both names are
# unchanged, so existing env files keep working); slot N >= 3 is the file gate<N>.lock in CONTROLLER_GATE_LOCK_DIR when that is
# set, else in the directory that holds CONTROLLER_GATE_LOCK. The callers check that CONTROLLER_GATE_LOCK (and a set
# CONTROLLER_GATE_LOCK_DIR) is an absolute path before they ask for a derived slot.
controller_gate_derived_lock() {
  local number=${1:-} dir=${CONTROLLER_GATE_LOCK_DIR:-}
  [[ $number =~ ^[3-8]$ ]] || return 2
  [[ -n $dir ]] || dir=$(dirname -- "${CONTROLLER_GATE_LOCK:-}")
  printf '%s/gate%s.lock\n' "${dir%/}" "$number"
}

# ---- run-name ownership ----------------------------------------------------
# One controller run name is owned by one live wrapper at a time. Everything a
# run owns is keyed by its NAME (`<run>.done`, `.jsonl`, `.err`, `.events`,
# `.live`, `.last.txt`, `.wt`, `.route`, `.thread`), so two wrappers on one name
# interleave on the same files with no error and a reader sees one job's
# completion as the other job's result.
#
# The lock is a kernel `flock` on `<runs-dir>/<name>.lock`, never a pid test: it
# is released when the last process holding the descriptor dies, so a wrapper
# that is TERMed, KILLed or OOM-killed cannot strand it, and a recycled pid
# cannot inherit it - PROVIDED every child the wrapper starts closes its copy of
# the descriptor. A child that forked from the wrapper inherits the open file
# description, and an agent (or a daemon the agent leaves behind) can outlive
# the wrapper by hours; `controller_close_ownership_fds` is therefore called
# immediately before every exec of a child (pi-impl.sh, codex-impl.sh), so a
# killed wrapper leaves the name claimable straight away and a child can never
# hold a name hostage. The lock file itself records who STARTED the name last (the
# wrapper pid, its start epoch, the pid's own kernel start time and the boot id;
# written by controller_run_record_owner once a launch is real, never by a refused
# one) so a reader can still name the owner after it died. A claimed name whose
# launch never got that far has an empty lock file or still carries the previous
# owner's record; only the kernel lock says whether a wrapper holds it right now.
#
# A claim NEVER bypasses a live owner, not even with CONTROLLER_RUN_FORCE=1:
# force is offered only as the supported way to RETIRE the records of a dead
# owner, and it archives them instead of deleting them.
CONTROLLER_RUN_LOCK_FD=''
CONTROLLER_RUN_PREVIOUS_OWNER=''
CONTROLLER_RUN_LOCK_PATH=''

controller_run_record_extensions() {
  printf '%s\n' 'done' 'jsonl' 'err' 'events' 'live' 'last.txt' 'wt' 'route' 'thread' 'autoresume.log'
}

controller_pid_start_ticks() {
  # Field 22 of /proc/<pid>/stat, the kernel's own pid-reuse discriminator.
  local pid=${1:-} stat_line='' rest='' field='' index=0
  [[ -n $pid && -r /proc/$pid/stat ]] || { printf '%s' '-'; return 1; }
  IFS= read -r stat_line < "/proc/$pid/stat" || { printf '%s' '-'; return 1; }
  rest=${stat_line##*\) }
  for field in $rest; do
    index=$((index + 1))
    if (( index == 20 )); then printf '%s' "$field"; return 0; fi
  done
  printf '%s' '-'
  return 1
}

controller_run_claim() {
  # $1 run name. 0 = this invocation owns the name; 2 = refused (a live owner
  # holds it, or the lock cannot be opened).
  local name=${1:-} runs=${CONTROLLER_RUNS_DIR:-} lock owner_pid started started_utc ticks='' rc=0
  [[ -n $name && -n $runs ]] || { echo 'controller: controller_run_claim needs a run name and CONTROLLER_RUNS_DIR' >&2; return 2; }
  controller_run_release
  CONTROLLER_RUN_PREVIOUS_OWNER=''
  CONTROLLER_RUN_LOCK_PATH=''
  lock=$runs/$name.lock
  # Append, never truncate: a truncating open would erase the record of the very
  # owner this claim is about to refuse on.
  if ! exec {CONTROLLER_RUN_LOCK_FD}>>"$lock"; then
    CONTROLLER_RUN_LOCK_FD=''
    echo "controller: cannot open the run-name lock: $lock" >&2
    return 2
  fi
  flock -n "$CONTROLLER_RUN_LOCK_FD"
  rc=$?
  if (( rc != 0 )); then
    if (( rc == 1 )); then
      read -r owner_pid started started_utc ticks < <(sed -n 's/^pid=\([0-9]*\) started_epoch=\([0-9]*\) started_utc=\([0-9T:-]*Z\) pid_start_ticks=\([0-9-]*\).*/\1 \2 \3 \4/p' "$lock" 2>/dev/null)
      echo "controller: run name '$name' is already owned by pid ${owner_pid:-<unknown>}${started_utc:+ (started $started_utc)}" >&2
      echo "  lock: $lock" >&2
      echo "  one run name carries one run: wait for that wrapper, use a different run name, or retire the owner's records explicitly (CONTROLLER_RUN_FORCE=1, refused while an owner is alive)" >&2
      exec {CONTROLLER_RUN_LOCK_FD}>&-
      CONTROLLER_RUN_LOCK_FD=''
      return 2
    fi
    exec {CONTROLLER_RUN_LOCK_FD}>&-
    CONTROLLER_RUN_LOCK_FD=''
    echo "controller: run-name lock failed ($lock, flock exit $rc)" >&2
    return 2
  fi
  # Keep the record the previous owner left behind (it is dead: the kernel
  # released its flock) so a forced retirement can archive that identity.
  CONTROLLER_RUN_PREVIOUS_OWNER=$(head -n 1 "$lock" 2>/dev/null || true)
  # The claim itself does NOT write the owner record: the name is held from here
  # on, but an invocation that then refuses (a name that already has evidence,
  # an unreadable prompt, a bad route) must not overwrite the identity of the
  # owner it refused on - a later forced retire would archive the refuser and
  # lose the real previous owner. The record is written by the caller once its
  # own preflight passed (controller_run_record_owner).
  CONTROLLER_RUN_LOCK_PATH=$lock
  return 0
}

controller_run_record_owner() {
  # Write THIS invocation's identity into the claimed name's lock file. Call it
  # only after the claim succeeded AND the caller's own preflight passed; a
  # refusal before this point leaves the previous owner's identity readable.
  local lock=${CONTROLLER_RUN_LOCK_PATH:-} started started_utc ticks
  [[ -n $lock ]] || return 1
  started=$(date -u +%s)
  started_utc=$(date -u -d "@$started" +%Y-%m-%dT%H:%M:%SZ)
  ticks=$(controller_pid_start_ticks "$$") || ticks='-'
  printf 'pid=%s started_epoch=%s started_utc=%s pid_start_ticks=%s boot_id=%s\n' \
    "$$" "$started" "$started_utc" "$ticks" "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '%s' '-')" > "$lock"
  return 0
}

controller_run_release() {
  local fd=${CONTROLLER_RUN_LOCK_FD:-}
  [[ $fd =~ ^[0-9]+$ ]] || return 0
  eval "exec $fd>&-" 2>/dev/null || :
  CONTROLLER_RUN_LOCK_FD=''
  CONTROLLER_RUN_LOCK_PATH=''
  return 0
}

controller_close_ownership_fds() {
  # Close the ownership descriptor in THIS process (the child an invocation is
  # about to exec into): the lock belongs to the wrapper that claimed the name,
  # never to the agent or the sidecar it starts. The kernel releases the flock
  # when the wrapper dies, so a killed wrapper leaves the name claimable and a
  # child that long outlives its wrapper cannot hold the name hostage.
  local fd=${CONTROLLER_RUN_LOCK_FD:-}
  [[ $fd =~ ^[0-9]+$ ]] || return 0
  eval "exec $fd>&-" 2>/dev/null || :
  return 0
}

controller_run_owner_pid() {
  # Print the recorded owner pid of a run name, or nothing when the record is
  # absent or unreadable. This is a RECORD, not a liveness test: the caller
  # decides whether that pid is alive. The record lives in the name's own lock
  # file, which every claim rewrites, so it names the most recent owner (the one
  # whose .done may be missing).
  local name=${1:-} runs=${CONTROLLER_RUNS_DIR:-} pid=''
  [[ -n $name && -n $runs ]] || return 1
  pid=$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$runs/$name.lock" 2>/dev/null | head -n 1)
  [[ -n $pid ]] || return 1
  printf '%s\n' "$pid"
}

controller_run_retire_records() {
  # Move one run name's whole record set into retired/<name>/<utc-stamp>/ so the
  # name can be reused deliberately. Nothing is deleted: a retired record stays
  # readable, and the caller prints the identity it retired.
  local name=${1:-} runs=${CONTROLLER_RUNS_DIR:-} stamp archive extension found=0
  [[ -n $name && -n $runs ]] || return 2
  stamp=$(date -u +%Y%m%dT%H%M%S)
  archive=$runs/retired/$name/$stamp
  while IFS= read -r extension; do
    [[ -e $runs/$name.$extension ]] || continue
    if (( found == 0 )); then mkdir -p -- "$archive" || return 2; fi
    found=1
    mv -- "$runs/$name.$extension" "$archive/" || return 2
  done < <(controller_run_record_extensions)
  (( found == 1 )) || return 1
  # Name the identity that was retired; nothing about the old owner is lost.
  if [[ -n ${CONTROLLER_RUN_PREVIOUS_OWNER:-} ]]; then
    printf '%s\n' "$CONTROLLER_RUN_PREVIOUS_OWNER" > "$archive/PREVIOUS-OWNER.txt" || return 2
  fi
  printf '%s\n' "$archive"
  return 0
}

usage_retry_text() {
  python3 - "$@" <<'PY'
import json
import re
import sys

reset_pattern = re.compile(
    r"try again at\s+("
    r"[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,\s+\d{4}\s+\d{1,2}:\d{2}(?:\s*[AP]M)?"
    r"|\d{1,2}:\d{2}(?:\s*[AP]M)?"
    r")",
    re.IGNORECASE,
)


def error_text(value):
    if isinstance(value, dict):
        return str(value.get("message") or "")
    return str(value or "")


gateway_402 = re.compile(
    r"unexpected status 402 Payment Required: \[code=insufficient_balance\].*?"
    r"\bresets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
    re.DOTALL,
)


def gateway_text_reset(event):
    # Codex renders the gateway 402 as plain text on a final error/turn.failed
    # event; the "Reconnecting..." retries are deliberately not matched.
    if not isinstance(event, dict) or event.get("type") not in ("error", "turn.failed"):
        return None
    message = event.get("message")
    if message is None and isinstance(event.get("error"), dict):
        message = event["error"].get("message")
    match = gateway_402.match(message) if isinstance(message, str) else None
    return match.group(1) if match else None


def coding_plan_reset(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("code") == "insufficient_balance":
                details = current.get("details")
                if isinstance(details, dict) and isinstance(details.get("reset_at"), str):
                    return details["reset_at"]
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def pi_error_reset(event):
    if not isinstance(event, dict) or event.get("type") not in ("message_start", "message_end"):
        return None
    message = event.get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or message.get("stopReason") != "error"
    ):
        return None
    error_message = message.get("errorMessage")
    if not isinstance(error_message, str):
        return None
    match = re.match(r"^\s*402:\s*(\{.*\})\s*$", error_message, re.DOTALL)
    if match is None:
        return None
    try:
        return coding_plan_reset(json.loads(match.group(1)))
    except ValueError:
        return None


last_retry = None
last_error = ""
coding_plan_retry = None
for path in sys.argv[1:]:
    try:
        source = open(path, encoding="utf-8", errors="replace")
    except OSError:
        continue
    with source:
        for line in source:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            reset_at = gateway_text_reset(event) or coding_plan_reset(event) or pi_error_reset(event)
            if reset_at is not None:
                coding_plan_retry = reset_at
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type == "turn.started":
                last_retry = None
                last_error = ""
            elif event_type == "error":
                last_error = error_text(event.get("error") or event)
            elif event_type == "turn.failed":
                message = error_text(event.get("error")) or last_error
                if re.search(r"hit your usage limit", message, re.IGNORECASE):
                    match = reset_pattern.search(message)
                    last_retry = match.group(1).strip() if match else "unknown"
                else:
                    last_retry = None

if coding_plan_retry is not None:
    print(coding_plan_retry)
    raise SystemExit(0)

if last_retry is None:
    raise SystemExit(1)
print(last_retry)
PY
}

usage_retry_epoch() {
  local iso
  iso=$(usage_retry_iso "${1-}") || return 1
  date -d "$iso" +%s 2>/dev/null
}

usage_retry_iso() {
  local retry=${1-} normalized
  if [[ $retry =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]]; then
    date -u -d "$retry" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null
    return $?
  fi
  [[ $retry =~ ^[A-Za-z]{3,9}[[:space:]]+[0-9]{1,2}(st|nd|rd|th)?, ]] || return 1
  normalized=$(printf '%s\n' "$retry" | sed -E 's/([0-9]{1,2})(st|nd|rd|th),/\1,/')
  date -d "$normalized" +%Y-%m-%dT%H:%M:%S 2>/dev/null
}

controller_config_error() {
  printf 'controller: %s\n' "$*" >&2
  return 1
}

controller_validate_host_alias() {
  local host=${1-}
  [[ $host =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && return 0
  printf 'controller: invalid host alias: %q\n' "$host" >&2
  return 2
}

controller_host_list() {
  local file=${1:-$CONTROLLER_HOSTS_FILE}
  [[ -f $file ]] || { controller_config_error "host table missing: $file"; return 1; }
  python3 - "$file" <<'PY'
import json, re, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    hosts = data["hosts"]
    if not isinstance(hosts, dict):
        raise ValueError("hosts must be an object")
    for name in hosts:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(f"invalid host alias: {name!r}")
        print(name)
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"controller: invalid host table: {exc}", file=sys.stderr)
    sys.exit(2)
PY
}

controller_host_scalar() {
  local host=$1 key=$2 file=${3:-$CONTROLLER_HOSTS_FILE}
  controller_validate_host_alias "$host" || return 2
  [[ -f $file ]] || { controller_config_error "host table missing: $file"; return 1; }
  python3 - "$file" "$host" "$key" <<'PY'
import json, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))["hosts"][sys.argv[2]]
    value = row[sys.argv[3]]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{sys.argv[3]} must be a non-empty string")
    print(value)
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"controller: host {sys.argv[2]!r}: {exc}", file=sys.stderr)
    sys.exit(2)
PY
}

controller_host_argv() {
  local destination=$1 host=$2 key=$3 file=${4:-$CONTROLLER_HOSTS_FILE}
  local tmp
  local -n output=$destination
  output=()
  controller_validate_host_alias "$host" || return 2
  [[ -f $file ]] || { controller_config_error "host table missing: $file"; return 1; }
  tmp=$(mktemp "$CONTROLLER_HOME/.host-argv.XXXXXX") || return 1
  if ! python3 - "$file" "$host" "$key" > "$tmp" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))["hosts"][sys.argv[2]][sys.argv[3]]
    if not isinstance(value, list) or not value or not all(isinstance(part, str) and part for part in value):
        raise ValueError(f"{sys.argv[3]} must be a non-empty string array")
    for part in value:
        sys.stdout.buffer.write(part.encode() + b"\0")
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"controller: host {sys.argv[2]!r}: {exc}", file=sys.stderr)
    sys.exit(2)
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  while IFS= read -r -d '' part; do output+=("$part"); done < "$tmp"
  rm -f -- "$tmp"
  [[ ${#output[@]} -gt 0 ]]
}

controller_host_lanes() {
  local host=$1 file=${2:-$CONTROLLER_HOSTS_FILE}
  controller_validate_host_alias "$host" || return 2
  [[ -f $file ]] || { controller_config_error "host table missing: $file"; return 1; }
  python3 - "$file" "$host" <<'PY'
import json, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))["hosts"][sys.argv[2]]
    lanes = row.get("queue_lanes", [])
    if not isinstance(lanes, list) or not all(
        isinstance(x, str) and x.count("|") == 1 and "\n" not in x and "\r" not in x
        and all(part.strip() for part in x.split("|", 1))
        for x in lanes
    ):
        raise ValueError("queue_lanes entries must each be one non-empty project|lane pair")
    print("\n".join(lanes))
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"controller: host {sys.argv[2]!r}: {exc}", file=sys.stderr)
    sys.exit(2)
PY
}

controller_host_api_check() {
  local host=$1
  local -a command=()
  controller_host_argv command "$host" api_check || return 2
  (cd -- "$CONTROLLER_HOME" && "${command[@]}")
}
