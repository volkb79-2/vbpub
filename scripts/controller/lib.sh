#!/usr/bin/env bash
# Shared local configuration and host-table helpers for the controller kit.

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
  CONTROLLER_HOME=$(cd -- "$CONTROLLER_HOME" && pwd -P)
  CONTROLLER_LOGS_DIR=${CONTROLLER_LOGS_DIR:-"$CONTROLLER_HOME/logs"}
  CONTROLLER_RUNS_DIR=${CONTROLLER_RUNS_DIR:-"$CONTROLLER_HOME/runs"}
  [[ $CONTROLLER_LOGS_DIR == /* ]] || CONTROLLER_LOGS_DIR=$CONTROLLER_HOME/$CONTROLLER_LOGS_DIR
  [[ $CONTROLLER_RUNS_DIR == /* ]] || CONTROLLER_RUNS_DIR=$CONTROLLER_HOME/$CONTROLLER_RUNS_DIR
  mkdir -p -- "$CONTROLLER_LOGS_DIR" "$CONTROLLER_HOME/runs" "$CONTROLLER_RUNS_DIR"
  CONTROLLER_LOGS_DIR=$(cd -- "$CONTROLLER_LOGS_DIR" && pwd -P)
  CONTROLLER_RUNS_DIR=$(cd -- "$CONTROLLER_RUNS_DIR" && pwd -P)
  local script_dir
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  CONTROLLER_REPO=${CONTROLLER_REPO:-$(cd -- "$script_dir/../.." && pwd -P)}
  CONTROLLER_HOSTS_FILE=${CONTROLLER_HOSTS_FILE:-"$CONTROLLER_HOME/hosts.local.json"}
  [[ $CONTROLLER_HOSTS_FILE == /* ]] || CONTROLLER_HOSTS_FILE=$CONTROLLER_HOME/$CONTROLLER_HOSTS_FILE
  if [[ $CONTROLLER_REPO != /* && -d $CONTROLLER_REPO ]]; then
    CONTROLLER_REPO=$(cd -- "$CONTROLLER_REPO" && pwd -P)
  fi
  export CONTROLLER_HOME CONTROLLER_LOGS_DIR CONTROLLER_RUNS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
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
