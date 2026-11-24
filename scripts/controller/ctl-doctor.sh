#!/usr/bin/env bash
# Read-only preflight of one controller machine: is everything the controller kit needs in place?
#
# usage: ctl-doctor.sh [--json] [--scan <dir>]...
#   --json        one JSON document instead of the text report
#   --scan <dir>  also look for the removed pinned tester image tag in this directory (repeatable; for example the directory
#                 that still holds old chain-*.sh wrappers). $CONTROLLER_HOME is always scanned.
# Text report: one `OK|WARN|FAIL|INFO <check>: <message>` line per finding, an indented `fix:` line under every WARN and FAIL.
# Exit: 0 = no FAIL (warnings allowed), 1 = at least one FAIL, 2 = usage.
# Read-only: it creates and changes nothing and runs only `docker image inspect`, read-only git queries and ctl-hygiene.sh
# (whose `HYGIENE <rule> <path>: ...` findings follow the checks, in a "hygiene" array in --json, and never change the exit
# status). It prints names and paths, never a value from controller.local.env, the host table or a key file. One caveat: controller.local.env is
# shell-SOURCED by lib.sh (as by every controller tool) and must hold NAME=value assignments only; the doctor FLAGS other lines
# but cannot make a value inert (an assignment like A=$(cmd) still runs when sourced).
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

json=0
scan_dirs=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --json) json=1; shift ;;
    --scan) [[ $# -ge 2 && -n $2 ]] || { echo 'usage: ctl-doctor.sh [--json] [--scan <dir>]...' >&2; exit 2; }; scan_dirs+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ctl-doctor.sh: unknown argument '$1'" >&2; echo 'usage: ctl-doctor.sh [--json] [--scan <dir>]...' >&2; exit 2 ;;
  esac
done

records=$(mktemp "${TMPDIR:-/tmp}/ctl-doctor.XXXXXX") || exit 2
hygiene=$(mktemp "${TMPDIR:-/tmp}/ctl-doctor.XXXXXX") || exit 2
trap 'rm -f -- "$records" "$hygiene"' EXIT
fails=0 warns=0 oks=0

record() {
  # $1 status, $2 check, $3 message, $4 optional fix
  case $1 in
    FAIL) fails=$((fails + 1)) ;;
    WARN) warns=$((warns + 1)) ;;
    OK) oks=$((oks + 1)) ;;
  esac
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}" >> "$records"
}
report() {
  local status check message fix
  if (( json )); then
    python3 - "$records" "$fails" "$warns" "$oks" "$hygiene" <<'PY'
import json, sys
rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    status, check, message, fix = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
    rows.append({"status": status, "check": check, "message": message, "fix": fix})
hygiene = [line.rstrip("\n") for line in open(sys.argv[5], encoding="utf-8")]
print(json.dumps({"ok": sys.argv[2] == "0", "fail": int(sys.argv[2]), "warn": int(sys.argv[3]), "ok_count": int(sys.argv[4]), "checks": rows, "hygiene": hygiene}, indent=2))
PY
  else
    while IFS=$'\t' read -r status check message fix; do
      printf '%s %s: %s\n' "$status" "$check" "$message"
      [[ -z $fix ]] || printf '  fix: %s\n' "$fix"
    done < "$records"
    while IFS= read -r status; do printf '%s\n' "$status"; done < "$hygiene"
    printf 'ctl-doctor: %s ok, %s warning(s), %s failure(s)\n' "$oks" "$warns" "$fails"
  fi
  (( fails == 0 )) || exit 1
  exit 0
}
disp() {
  # A path as the operator knows it: under $CONTROLLER_HOME it is shown relative to it.
  if [[ -n ${CONTROLLER_HOME:-} && $1 == "$CONTROLLER_HOME"/* ]]; then printf '%s/%s' "\$CONTROLLER_HOME" "${1#"$CONTROLLER_HOME"/}"; else printf '%s' "$1"; fi
}

# ---- prerequisites on PATH ---------------------------------------------------------------------------------------------
tool_check() {
  # $1 tool, $2 severity if missing, $3 package hint, $4 what needs it
  if command -v "$1" >/dev/null 2>&1; then record OK "tool:$1" 'present'
  else record "$2" "tool:$1" "not found on PATH ($4)" "install $3 so that '$1' is on PATH"; fi
}
tool_check flock FAIL util-linux 'gate and remote-gate slot locks'
tool_check git FAIL git 'worktrees, commit resolution'
tool_check python3 FAIL python3 'host-table parsing, weights'
tool_check jq FAIL jq 'JSON status and evidence helpers'
tool_check docker WARN 'a docker CLI' 'local gate lanes, image check'
tool_check shellcheck WARN shellcheck 'the controller suite lint step'

# ---- $CONTROLLER_HOME and controller.local.env ---------------------------------------------------------------------------
home_in=${CONTROLLER_HOME:-${HOME:-}/controller}
if [[ ! -d $home_in ]]; then
  record FAIL home "CONTROLLER_HOME does not exist: $home_in" "mkdir -p '$home_in' && cp scripts/controller/controller.local.env.example '$home_in/controller.local.env' (then edit it), or export CONTROLLER_HOME=<existing directory>"
  report
fi
record OK home "CONTROLLER_HOME exists: $home_in"
env_file=$home_in/controller.local.env
if [[ ! -f $env_file ]]; then
  record FAIL env-file 'controller.local.env is missing' "cp scripts/controller/controller.local.env.example '$env_file' and edit it"
  report
fi
if ! bash -n "$env_file" 2>/dev/null; then
  record FAIL env-file 'controller.local.env does not parse as shell' "fix the syntax error in '$env_file' (check it with: bash -n '$env_file')"
  report
fi
# Only assignments (one per line) are expected; anything else would be EXECUTED by lib.sh when it loads the file.
stray=$(grep -nEv '^[[:space:]]*(#.*)?$|^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$env_file" | cut -d: -f1 | head -n 5 | tr '\n' ' ')
if [[ -n $stray ]]; then
  record FAIL env-file "controller.local.env has lines that are not NAME=value assignments (line ${stray% }); the loader would execute them" "keep one NAME=value assignment per line (comments allowed) in '$env_file'"
  report
fi
record OK env-file 'controller.local.env parses (assignments only)'

CONTROLLER_INIT_READONLY=1
controller_init
if [[ ! -d $CONTROLLER_HOME ]]; then
  record FAIL home "CONTROLLER_HOME from controller.local.env does not exist: $CONTROLLER_HOME" "create that directory or correct CONTROLLER_HOME in '$env_file'"
  report
fi

# ---- host table ------------------------------------------------------------------------------------------------------------
hosts_file=$CONTROLLER_HOSTS_FILE
remote_file=${CONTROLLER_REMOTE_HOSTS_FILE:-$hosts_file}
[[ $remote_file == /* ]] || remote_file=$CONTROLLER_HOME/$remote_file
host_table_check() {
  # $1 file, $2 label, $3 allow-list mode (allow|none)
  local file=$1 label=$2 mode=$3 status check message fix
  if [[ ! -f $file ]]; then
    record FAIL "hosts:$label" "host table missing: $(disp "$file")" "cp scripts/controller/hosts.local.json.example '$file' and replace every placeholder"
    return 0
  fi
  while IFS=$'\t' read -r status check message fix; do
    record "$status" "$check" "$message" "$fix"
  done < <(python3 - "$file" "$CONTROLLER_HOME" "$label" "$mode" "${CONTROLLER_REMOTE_GATE_HOSTS:-}" <<'PY'
import json, os, re, shutil, sys
path, home, label, mode, allow = sys.argv[1:6]


def out(status, check, message, fix=""):
    print(f"{status}\t{check}\t{message}\t{fix}")


def resolve(value):
    value = os.path.expanduser(value)
    return value if os.path.isabs(value) else os.path.join(home, value)


def disp(value):
    return "$CONTROLLER_HOME/" + os.path.relpath(value, home) if value.startswith(home + os.sep) else value


def strings(value):
    return isinstance(value, list) and value and all(isinstance(x, str) and x for x in value)


try:
    with open(path, encoding="utf-8") as handle:
        hosts = json.load(handle)["hosts"]
    if not isinstance(hosts, dict) or not hosts:
        raise ValueError("no hosts")
except (OSError, KeyError, TypeError, ValueError) as exc:
    out("FAIL", f"hosts:{label}", f"host table does not parse as {{\"hosts\": {{<alias>: {{...}}}}}} ({type(exc).__name__})",
        "fix the JSON in the host table; start from hosts.local.json.example")
    sys.exit(0)
for alias, row in hosts.items():
    where = f"hosts:{label}:{alias}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", alias) or not isinstance(row, dict):
        out("FAIL", where, "alias is not [A-Za-z0-9][A-Za-z0-9._-]* or its entry is not an object", "rename the alias / make the entry an object")
        continue
    bad = False
    for key in ("ssh", "api_check"):
        if not strings(row.get(key)):
            out("FAIL", f"{where}:{key}", f"'{key}' must be a non-empty array of non-empty strings", f"set '{key}' for {alias} as in hosts.local.json.example")
            bad = True
    if not isinstance(row.get("guard"), str) or not row["guard"].strip():
        out("FAIL", f"{where}:guard", "'guard' must be a non-empty remote shell condition", f"set 'guard' for {alias} as in hosts.local.json.example")
        bad = True
    lanes = row.get("queue_lanes")
    if lanes is None:
        out("WARN", f"{where}:queue_lanes", "'queue_lanes' is missing (read as an empty list)", f"add \"queue_lanes\": [] to {alias}")
    elif not isinstance(lanes, list) or not all(isinstance(x, str) and x.count("|") == 1 and all(p.strip() for p in x.split("|", 1)) for x in lanes):
        out("FAIL", f"{where}:queue_lanes", "'queue_lanes' entries must each be one non-empty project|lane pair", f"correct 'queue_lanes' for {alias}")
        bad = True
    if strings(row.get("api_check")):
        command = row["api_check"][0]
        full = resolve(command) if "/" in command else shutil.which(command)
        if not full or not os.path.isfile(full) or not os.access(full, os.X_OK):
            out("FAIL", f"{where}:api_check", f"identity-check command is not an executable file: {command if '/' not in command else disp(full)}",
                f"install the provider identity-check command (or fix 'api_check') for {alias}")
            bad = True
    if strings(row.get("ssh")):
        argv = row["ssh"]
        keys, pins, strict = [], [], False
        for i, arg in enumerate(argv):
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            if arg == "-i":
                keys.append(nxt)
            elif arg == "-o" or arg.startswith("-o"):
                option = nxt if arg == "-o" else arg[2:]
                name, _, value = option.partition("=")
                if name.lower() == "identityfile":
                    keys.append(value)
                elif name.lower() == "userknownhostsfile":
                    pins.append(value)
                elif name.lower() == "stricthostkeychecking" and value.lower() == "yes":
                    strict = True
        for key in keys:
            full = resolve(key)
            if not os.path.isfile(full):
                out("FAIL", f"{where}:key", f"SSH key file is missing: {disp(full)}", f"place the private key at {full} with mode 600 (never in the repo)")
                bad = True
            elif os.stat(full).st_mode & 0o077:
                out("FAIL", f"{where}:key", f"SSH key file is readable by group or others: {disp(full)}", f"chmod 600 '{full}'")
                bad = True
        for pin in pins:
            full = resolve(pin)
            if os.path.normpath(full) == os.devnull:
                out("WARN", f"{where}:pin", "UserKnownHostsFile=/dev/null is not a pin: no host key is ever pinned",
                    f"point -o UserKnownHostsFile at a pinned known_hosts file in the ssh argv of {alias} (docs/runbooks/host-access-template.md)")
                bad = True   # no OK line: the pin is not in place
            elif not os.path.isfile(full):
                out("FAIL", f"{where}:pin", f"pinned known_hosts file is missing: {disp(full)}", f"create '{full}' with the host key pinned from the provider console (docs/runbooks/host-access-template.md)")
                bad = True
        if not pins:
            out("WARN", f"{where}:pin", "ssh has no UserKnownHostsFile=<pin file> option", f"add -o UserKnownHostsFile=<pin file> to the ssh argv of {alias}")
        if not strict:
            out("WARN", f"{where}:strict", "ssh does not set StrictHostKeyChecking=yes", f"add -o StrictHostKeyChecking=yes to the ssh argv of {alias}")
    if not bad:
        out("OK", where, "schema, key, pin and identity command are in place")
if mode == "allow":
    names = allow.split()
    if not names:
        out("WARN", "remote-allow-list", "CONTROLLER_REMOTE_GATE_HOSTS is not set: remote-gate.sh refuses every host",
            "set CONTROLLER_REMOTE_GATE_HOSTS=\"<alias> ...\" in controller.local.env (aliases that may receive a gate)")
    for name in names:
        if name not in hosts:
            out("FAIL", "remote-allow-list", f"CONTROLLER_REMOTE_GATE_HOSTS names '{name}', which is not in the host table",
                f"add '{name}' to the host table or remove it from CONTROLLER_REMOTE_GATE_HOSTS")
    if names and all(n in hosts for n in names):
        out("OK", "remote-allow-list", f"CONTROLLER_REMOTE_GATE_HOSTS names {len(names)} alias(es), all in the host table")
PY
  )
}
if [[ $remote_file == "$hosts_file" ]]; then
  host_table_check "$hosts_file" main allow
else
  host_table_check "$hosts_file" main none
  host_table_check "$remote_file" remote allow
fi

# ---- tester image ----------------------------------------------------------------------------------------------------------
toml=$CONTROLLER_REPO/run-gate.root.toml
image=''
if [[ -f $toml ]]; then
  image=$(python3 - "$toml" <<'PY' 2>/dev/null
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle)["environments"]["tester-unified"]["image"])
PY
  ) || image=''
fi
if [[ -z $image ]]; then
  record WARN image 'cannot read [environments.tester-unified] image from run-gate.root.toml (needs Python 3.11+ and a repo checkout)' 'set CONTROLLER_REPO to the vbpub checkout and use Python 3.11 or newer'
elif ! command -v docker >/dev/null 2>&1; then
  record WARN image "default tester image $image not checked: docker is not installed" 'install the docker CLI on the controller'
else
  inspect_err=$(docker image inspect --format '{{.Id}}' "$image" 2>&1 >/dev/null)
  inspect_rc=$?
  if (( inspect_rc == 0 )); then
    record OK image "default tester image is present locally: $image"
  elif [[ $inspect_err == *'No such image'* ]]; then
    record FAIL image "default tester image is not present locally: $image" "build or load the image named in run-gate.root.toml ([environments.tester-unified] image), then run: docker image inspect $image"
  else
    record WARN image "docker is unreachable, the default tester image $image was not checked" 'start the docker daemon or fix DOCKER_HOST, then re-run ctl-doctor.sh'
  fi
fi
[[ -z ${RUN_GATE_TESTER_UNIFIED_IMAGE:-} ]] || record INFO image-override 'RUN_GATE_TESTER_UNIFIED_IMAGE is set: lanes use that image instead of the default'
pinned_tag='tester-unified:run-gate-2560'
scan_roots=("$CONTROLLER_HOME")
[[ ${#scan_dirs[@]} -eq 0 ]] || scan_roots+=("${scan_dirs[@]}")
pinned_files=''
for root in "${scan_roots[@]}"; do
  [[ -d $root ]] || continue
  found=$(grep -rIlF --exclude-dir=runs --exclude-dir=logs --exclude-dir=remote-gates --exclude-dir=remote-gate-work --exclude-dir=tools --exclude-dir=retired -- "$pinned_tag" "$root" 2>/dev/null | head -n 5)
  [[ -z $found ]] || pinned_files+=$found$'\n'
done
if [[ -n $pinned_files ]]; then
  record WARN pinned-image "the removed tester image tag $pinned_tag is still referenced by: $(while IFS= read -r f; do [[ -z $f ]] || printf '%s ' "$(disp "$f")"; done <<< "$pinned_files")" 'remove the pinned tag (and any RUN_GATE_TESTER_UNIFIED_IMAGE export) from those files; migrate old chain-*.sh to chain-run.sh (README)'
else
  record OK pinned-image "no file under the scanned directories references $pinned_tag"
fi

# ---- gate profile ----------------------------------------------------------------------------------------------------------
record INFO run-gate-profile "effective RUN_GATE_PROFILE=${RUN_GATE_PROFILE:-off} (gate-run.sh defaults to off when unset)"

# ---- runs / logs directories ---------------------------------------------------------------------------------------------
dir_check() {
  # $1 label, $2 path
  if [[ ! -d $2 ]]; then
    record FAIL "$1" "directory missing or not a directory: $(disp "$2")" "mkdir -p '$2'"
  elif [[ ! -w $2 ]]; then
    record FAIL "$1" "directory is not writable: $(disp "$2")" "chmod u+w '$2' (or fix its owner)"
  else
    record OK "$1" "directory exists and is writable: $(disp "$2")"
  fi
}
dir_check runs-dir "$CONTROLLER_RUNS_DIR"
dir_check logs-dir "$CONTROLLER_LOGS_DIR"

# ---- local gate slots: gate-run.sh REQUIRES both lock paths --------------------------------------------------------------
gate_lock_check() {
  # $1 variable name (slots 1 and 2) or the label gate<N>.lock of a derived slot, $2 the path of a derived slot.
  # Nothing is created: a lock file that does not exist yet needs a writable directory.
  local name=$1 path=${2:-} parent move="point $1 at another path"
  if [[ $name == CONTROLLER_* ]]; then path=${!1:-}; else move='set CONTROLLER_GATE_LOCK_DIR to another directory'; fi
  if [[ -z $path ]]; then
    record FAIL "$name" "$name is not set: gate-run.sh exits 2 for every local lane (a chain over such lanes still ends with CHAIN-DONE)" \
      "set $name=<absolute lock file path> in '$env_file'; CONTROLLER_GATE_LOCK is always required, CONTROLLER_GATE2_LOCK from two slots on, and every slot lock must differ"
  elif [[ $path != /* ]]; then
    record FAIL "$name" "$name is not an absolute path: $path" "set $name to an absolute lock file path in '$env_file'"
  elif [[ -e $path ]]; then
    if [[ -w $path ]]; then record OK "$name" "lock file exists and is writable: $(disp "$path")"
    else record FAIL "$name" "lock file is not writable: $(disp "$path")" "chmod u+w '$path' (or fix its owner)"; fi
  else
    parent=$(dirname -- "$path")
    if [[ -d $parent && -w $parent ]]; then record OK "$name" "lock file will be created in a writable directory: $(disp "$path")"
    else record FAIL "$name" "the directory for the lock file is missing or not writable: $(disp "$parent")" "mkdir -p '$parent' and make it writable, or $move"; fi
  fi
}
gate_slots=${CONTROLLER_GATE_SLOTS:-2}
if [[ $gate_slots =~ ^[1-8]$ ]]; then
  if [[ -z ${CONTROLLER_GATE_SLOTS:-} ]]; then record INFO gate-slots "effective local gate slots: $gate_slots (CONTROLLER_GATE_SLOTS is not set: the default of 2 keeps the memory-capped controller devcontainer safe)"
  else record INFO gate-slots "effective local gate slots: $gate_slots (CONTROLLER_GATE_SLOTS)"; fi
else
  # The configured value is not echoed: it is whatever the env file holds. The lock checks below still run for the default 2.
  gate_slots=2
  record FAIL gate-slots 'CONTROLLER_GATE_SLOTS is not an integer from 1 to 8: gate-run.sh exits 2 for every local lane' "set CONTROLLER_GATE_SLOTS to a number from 1 to 8 in '$env_file', or remove it for the default 2"
fi
gate_lock_check CONTROLLER_GATE_LOCK
(( gate_slots < 2 )) || gate_lock_check CONTROLLER_GATE2_LOCK
# Slots 3.. are gate<N>.lock beside CONTROLLER_GATE_LOCK or in CONTROLLER_GATE_LOCK_DIR (controller_gate_derived_lock in lib.sh).
slot_paths=([1]="${CONTROLLER_GATE_LOCK:-}")
(( gate_slots < 2 )) || slot_paths[2]=${CONTROLLER_GATE2_LOCK:-}
if (( gate_slots > 2 )); then
  if [[ -n ${CONTROLLER_GATE_LOCK_DIR:-} && $CONTROLLER_GATE_LOCK_DIR != /* ]]; then
    record FAIL CONTROLLER_GATE_LOCK_DIR "CONTROLLER_GATE_LOCK_DIR is not an absolute path: $CONTROLLER_GATE_LOCK_DIR" "set CONTROLLER_GATE_LOCK_DIR to an absolute directory in '$env_file', or remove it to keep gate3.lock.. beside CONTROLLER_GATE_LOCK"
  elif [[ ${CONTROLLER_GATE_LOCK:-} == /* ]]; then
    for (( slot = 3; slot <= gate_slots; slot++ )); do
      slot_paths[slot]=$(controller_gate_derived_lock "$slot")
      gate_lock_check "gate${slot}.lock" "${slot_paths[slot]}"
    done
  fi
fi
for (( slot = 1; slot <= gate_slots; slot++ )); do
  for (( other = slot + 1; other <= gate_slots; other++ )); do
    [[ -n ${slot_paths[slot]:-} && ${slot_paths[slot]:-} == "${slot_paths[other]:-}" ]] || continue
    if (( slot == 1 && other == 2 )); then
      record FAIL gate-locks 'CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK are the same path: gate-run.sh refuses it' "give CONTROLLER_GATE2_LOCK its own path in '$env_file'"
    else
      record FAIL gate-locks "gate slots $slot and $other use the same lock path $(disp "${slot_paths[slot]}"): gate-run.sh refuses it" "change CONTROLLER_GATE_LOCK, CONTROLLER_GATE2_LOCK or CONTROLLER_GATE_LOCK_DIR in '$env_file' so that every slot has its own lock file (slots 3.. are gate<N>.lock)"
    fi
  done
done

# ---- where the state lives: symlinks and git work trees ----------------------------------------------------------------
behind_symlink() {
  local p=$1
  while [[ -n $p && $p != / ]]; do
    [[ -L $p ]] && return 0
    p=$(dirname -- "$p")
  done
  return 1
}
residency_check() {
  # $1 label, $2 configured path (before symlinks were resolved), $3 physical path
  local label=$1 configured=$2 real=$3 via='' top='' ignored=0
  [[ -d $real ]] || return 0
  if behind_symlink "$configured"; then via=" (configured as $(disp "$configured"), a symlink)"; fi
  if command -v git >/dev/null 2>&1; then top=$(git -C "$real" rev-parse --show-toplevel 2>/dev/null) || top=''; fi
  if [[ -z $top ]]; then
    record OK "residency:$label" "real location $(disp "$real")$via is outside any git work tree"
    return 0
  fi
  git -C "$top" check-ignore -q -- "$real" 2>/dev/null && ignored=1
  if (( ignored )); then
    record WARN "residency:$label" "real location $(disp "$real")$via is inside the git work tree $top and covered by its ignore rules: 'git clean -fdx' there would DELETE it" \
      "keep the state outside any work tree, or run ctl-state-backup.sh regularly (docs/STATE-VS-WORK.md); never run 'git clean -x' in that checkout"
  else
    record WARN "residency:$label" "real location $(disp "$real")$via is inside the git work tree $top and NOT ignored: 'git clean -fd' would DELETE it and 'git add -A' would stage it" \
      "move it outside the work tree, or add it to that checkout's ignore rules, and back it up with ctl-state-backup.sh"
  fi
}
residency_check home "$CONTROLLER_HOME_CONFIGURED" "$CONTROLLER_HOME"
# A runs directory that lives under the (already checked) home needs no second report; one that points elsewhere does.
if [[ $CONTROLLER_RUNS_DIR != "$CONTROLLER_HOME"/* ]]; then
  residency_check runs "$CONTROLLER_RUNS_DIR_CONFIGURED" "$CONTROLLER_RUNS_DIR"
fi

# ---- memory / handoff / backlog hygiene (report-only; never changes the status) -----------------------------------------------
"$BASH" "${CTL_HYGIENE_SCRIPT:-$SCRIPT_DIR/ctl-hygiene.sh}" > "$hygiene" 2>/dev/null || : > "$hygiene"   # CTL_HYGIENE_SCRIPT: test seam

report
