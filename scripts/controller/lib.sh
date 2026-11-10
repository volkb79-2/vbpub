#!/usr/bin/env bash
# Shared local configuration and host-table helpers for the controller kit.

controller_init() {
  : "${HOME:?HOME must be set}"
  CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
  if [[ -f $CONTROLLER_HOME/controller.local.env ]]; then
    # shellcheck disable=SC1090,SC1091
    set -a
    . "$CONTROLLER_HOME/controller.local.env"
    set +a
  fi
  CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
  mkdir -p -- "$CONTROLLER_HOME/logs" "$CONTROLLER_HOME/runs"
  CONTROLLER_HOME=$(cd -- "$CONTROLLER_HOME" && pwd -P)
  local script_dir
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  CONTROLLER_REPO=${CONTROLLER_REPO:-$(cd -- "$script_dir/../.." && pwd -P)}
  CONTROLLER_HOSTS_FILE=${CONTROLLER_HOSTS_FILE:-"$CONTROLLER_HOME/hosts.local.json"}
  [[ $CONTROLLER_HOSTS_FILE == /* ]] || CONTROLLER_HOSTS_FILE=$CONTROLLER_HOME/$CONTROLLER_HOSTS_FILE
  if [[ $CONTROLLER_REPO != /* && -d $CONTROLLER_REPO ]]; then
    CONTROLLER_REPO=$(cd -- "$CONTROLLER_REPO" && pwd -P)
  fi
  export CONTROLLER_HOME CONTROLLER_REPO CONTROLLER_HOSTS_FILE
}

controller_config_error() {
  printf 'controller: %s\n' "$*" >&2
  return 1
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
  [[ -f $file ]] || { controller_config_error "host table missing: $file"; return 1; }
  python3 - "$file" "$host" <<'PY'
import json, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))["hosts"][sys.argv[2]]
    lanes = row.get("queue_lanes", [])
    if not isinstance(lanes, list) or not all(isinstance(x, str) and "|" in x for x in lanes):
        raise ValueError("queue_lanes must be an array of project|lane strings")
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
