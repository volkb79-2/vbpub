#!/usr/bin/env bash
# shellcheck disable=SC2016 # the expected report lines carry a literal $CONTROLLER_HOME
set -uo pipefail
# Every check builds its own controller root; the caller's ambient configuration must not decide anything.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CONTROLLER_REMOTE_GATE_HOSTS CONTROLLER_REMOTE_HOSTS_FILE RUN_GATE_PROFILE RUN_GATE_TESTER_UNIFIED_IMAGE
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DOCTOR=${CTL_DOCTOR_SCRIPT:-$SCRIPT_DIR/../ctl-doctor.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-doctor-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
SECRETS=(TOPSECRET-envvalue-123 TOPSECRET-serverid-456 TOPSECRET-addr TOPSECRET-keybody-789 TOPSECRET-slots-999)

# A PATH that holds exactly the tools the doctor may use. Stubs stand in for the tools it only looks for.
make_tools() {
  local dir=$1 omit=" ${*:2} " tool real
  mkdir -p -- "$dir"
  # mkdir is on the PATH on purpose: a doctor that created a directory must be caught, not hidden by "command not found".
  for tool in bash python3 git grep cat dirname mktemp sed rm cut head tr mkdir; do
    [[ $omit == *" $tool "* ]] && continue
    real=$(command -v "$tool") || fail "test needs $tool"
    ln -sf -- "$real" "$dir/$tool"
  done
  for tool in flock jq shellcheck; do
    [[ $omit == *" $tool "* ]] && continue
    printf '#!/bin/sh\nexit 0\n' > "$dir/$tool"; chmod +x "$dir/$tool"
  done
  if [[ $omit != *" docker "* ]]; then
    cat > "$dir/docker" <<'SH'
#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case $DOCKER_MODE in
  present) exit 0 ;;
  missing) echo 'Error: No such image: x' >&2; exit 1 ;;
  *) echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock' >&2; exit 1 ;;
esac
SH
    chmod +x "$dir/docker"
  fi
}
TOOLS=$ROOT/tools
make_tools "$TOOLS"

REPO=$ROOT/repo
mkdir -p "$REPO"
printf '[environments.tester-unified]\nmode = "ephemeral"\nimage = "fixture-image:1"\n' > "$REPO/run-gate.root.toml"

write_env() {
  # $1 home; remaining arguments are the lines of controller.local.env
  local home=$1; shift
  printf '%s\n' '# test fixture' 'NETCUP_TOKEN=TOPSECRET-envvalue-123' "CONTROLLER_GATE_LOCK=\"$home/gate.lock\"" "CONTROLLER_GATE2_LOCK=\"$home/gate2.lock\"" "$@" > "$home/controller.local.env"
}
fixture() {
  # $1 name; sets H to a complete, passing controller home
  H=$ROOT/$1/home
  mkdir -p "$H/runs" "$H/logs" "$H/keys"
  printf 'TOPSECRET-keybody-789\n' > "$H/keys/id"; chmod 600 "$H/keys/id"
  : > "$H/keys/known_hosts"
  printf '#!/bin/sh\nexit 0\n' > "$H/keys/api-check"; chmod +x "$H/keys/api-check"
  cat > "$H/hosts.local.json" <<JSON
{"hosts": {"alpha": {
  "ssh": ["ssh", "-i", "$H/keys/id", "-o", "UserKnownHostsFile=$H/keys/known_hosts", "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "runner@TOPSECRET-addr.example.invalid"],
  "api_check": ["$H/keys/api-check", "alpha"],
  "guard": "test \"\$(cat /etc/server-id)\" = TOPSECRET-serverid-456",
  "queue_lanes": ["scripts/x|suite"]}}}
JSON
  write_env "$H" 'CONTROLLER_REMOTE_GATE_HOSTS="alpha"'
}
edit_host() {
  # $1 python statement(s) run with `row` = the alpha entry; the table is rewritten
  python3 - "$H/hosts.local.json" "$1" <<'PY'
import json, sys
path, code = sys.argv[1:3]
data = json.load(open(path))
row = data["hosts"]["alpha"]
exec(code)
json.dump(data, open(path, "w"))
PY
}
run_doctor() {
  # run_doctor [VAR=value ...] -- [doctor arguments]; sets RC, output in $ROOT/out and $ROOT/err
  local -a extra=()
  while [[ $# -gt 0 && $1 != -- ]]; do extra+=("$1"); shift; done
  shift
  env -i HOME="$ROOT/home" PATH="${TOOLS_IN_USE:-$TOOLS}" CONTROLLER_HOME="$H" CONTROLLER_REPO="$REPO" DOCKER_MODE=present DOCKER_LOG="$ROOT/docker.log" \
    ${extra[@]+"${extra[@]}"} "$BASH" "$DOCTOR" "$@" > "$ROOT/out" 2> "$ROOT/err"
  RC=$?
  local secret
  for secret in "${SECRETS[@]}"; do
    ! grep -qF -- "$secret" "$ROOT/out" "$ROOT/err" || fail "a secret-looking fixture value reached the output: $secret"
  done
}
mkdir -p "$ROOT/home"
line() { grep -qE -- "$1" "$ROOT/out" || { cat "$ROOT/out" >&2; fail "$2 (missing /$1/)"; }; }
no_line() { ! grep -qE -- "$1" "$ROOT/out" || { cat "$ROOT/out" >&2; fail "$2 (unexpected /$1/)"; }; }
expect_rc() { [[ $RC == "$1" ]] || { cat "$ROOT/out" "$ROOT/err" >&2; fail "$2: exit $RC, wanted $1"; }; }

# --- baseline: everything in place -> exit 0, every check passes, nothing was changed or created
fixture base
before=$(find "$H" -printf '%p %T@ %s\n' | sort)
run_doctor --
expect_rc 0 'a complete controller home must pass'
for ok in 'OK home' 'OK env-file' 'OK tool:flock' 'OK tool:git' 'OK tool:python3' 'OK tool:jq' 'OK tool:docker' 'OK tool:shellcheck' \
          'OK hosts:main:alpha' 'OK remote-allow-list' 'OK image' 'OK pinned-image' 'OK runs-dir' 'OK logs-dir' 'OK CONTROLLER_GATE_LOCK' 'OK CONTROLLER_GATE2_LOCK' 'OK residency:home'; do
  line "^$ok" "baseline check $ok"
done
line '^INFO run-gate-profile: effective RUN_GATE_PROFILE=off ' 'the default profile is printed as off'
line '^ctl-doctor: [0-9]+ ok, 0 warning\(s\), 0 failure\(s\)$' 'baseline summary'
grep -qxF 'image inspect --format {{.Id}} fixture-image:1' "$ROOT/docker.log" || fail 'the image must be looked up by its exact name from run-gate.root.toml'
[[ $(find "$H" -printf '%p %T@ %s\n' | sort) == "$before" ]] || fail 'the doctor must not change anything under CONTROLLER_HOME'

# --- the effective profile follows the environment
run_doctor RUN_GATE_PROFILE=on --
line '^INFO run-gate-profile: effective RUN_GATE_PROFILE=on ' 'an exported profile is reported'

# --- usage and the JSON form
run_doctor -- --bogus; expect_rc 2 'unknown argument'
run_doctor -- --scan; expect_rc 2 '--scan needs a directory'
run_doctor -- --json
expect_rc 0 'json mode of a passing home'
python3 - "$ROOT/out" <<'PY' || fail 'the --json report must be one JSON document with ok=true and the checks'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["ok"] is True and doc["fail"] == 0
assert any(c["check"] == "home" and c["status"] == "OK" for c in doc["checks"])
PY
fixture json-bad; rmdir "$H/logs"
run_doctor -- --json
expect_rc 1 'json mode of a failing home'
python3 - "$ROOT/out" <<'PY' || fail 'a failing --json report must say ok=false and carry the fix'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["ok"] is False and doc["fail"] >= 1
row = [c for c in doc["checks"] if c["check"] == "logs-dir"][0]
assert row["status"] == "FAIL" and row["fix"].startswith("mkdir -p")
PY

# --- tools: flock/git/python3/jq missing is a failure, docker and shellcheck only a warning
fixture tools
for tool in flock jq; do
  TOOLS_IN_USE=$ROOT/tools-no-$tool make_tools "$ROOT/tools-no-$tool" "$tool"
  TOOLS_IN_USE=$ROOT/tools-no-$tool run_doctor --
  expect_rc 1 "missing $tool"
  line "^FAIL tool:$tool: not found on PATH" "missing $tool is a failure"
  line '^  fix: install ' "missing $tool names the fix"
done
for tool in docker shellcheck; do
  make_tools "$ROOT/tools-no-$tool" "$tool"
  TOOLS_IN_USE=$ROOT/tools-no-$tool run_doctor --
  expect_rc 0 "missing $tool"
  line "^WARN tool:$tool: not found on PATH" "missing $tool is only a warning"
  [[ $tool != docker ]] || line '^WARN image: default tester image fixture-image:1 not checked: docker is not installed' 'no docker: the image check warns'
done

# --- CONTROLLER_HOME and controller.local.env
fixture home-missing
H=$ROOT/does-not-exist run_doctor --
expect_rc 1 'missing CONTROLLER_HOME'
line '^FAIL home: CONTROLLER_HOME does not exist' 'missing home'
line '^  fix: mkdir -p ' 'missing home names the fix'
fixture env-missing; rm "$H/controller.local.env"
run_doctor --
expect_rc 1 'missing controller.local.env'
line '^FAIL env-file: controller.local.env is missing' 'missing env file'
fixture env-syntax; printf 'BROKEN="unterminated\n' >> "$H/controller.local.env"
run_doctor --
expect_rc 1 'unparsable controller.local.env'
line '^FAIL env-file: controller.local.env does not parse as shell' 'syntax error'
fixture env-stray; printf 'touch %s\n' "$ROOT/PWNED" >> "$H/controller.local.env"
run_doctor --
expect_rc 1 'a command line in controller.local.env'
line '^FAIL env-file: controller.local.env has lines that are not NAME=value assignments' 'stray command'
[[ ! -e $ROOT/PWNED ]] || fail 'the doctor must not execute a non-assignment line of controller.local.env'
# The documented limit: the env file is SOURCED, so an assignment that carries a command substitution runs (the doctor only
# flags non-assignment lines). The docs must say so instead of claiming nothing is executed.
fixture env-substitution; printf 'SUBST=$(echo ran > %s)\n' "$ROOT/SUBST-RAN" >> "$H/controller.local.env"
run_doctor --
[[ -e $ROOT/SUBST-RAN ]] || fail 'the documented limit changed: an assignment with a command substitution is no longer executed when sourced'
! grep -qE 'refused, never executed|runs nothing' "$SCRIPT_DIR/../README.md" "$SCRIPT_DIR/../docs/CONFIGURATION.md" "$DOCTOR" || fail 'the docs claim the env file is never executed'
grep -qF 'shell-SOURCED by lib.sh' "$DOCTOR" || fail 'the doctor header must say that controller.local.env is sourced'
grep -qF 'SOURCED by `scripts/controller/lib.sh`' "$SCRIPT_DIR/../docs/CONFIGURATION.md" || fail 'CONFIGURATION must say that controller.local.env is sourced'

# --- host table
fixture hosts-missing; rm "$H/hosts.local.json"
run_doctor --
expect_rc 1 'missing host table'
line '^FAIL hosts:main: host table missing' 'host table missing'
fixture hosts-json; printf '{ not json' > "$H/hosts.local.json"
run_doctor --
expect_rc 1 'unparsable host table'
line '^FAIL hosts:main: host table does not parse' 'host table JSON'
fixture hosts-ssh; edit_host 'row["ssh"] = "ssh host"'
run_doctor --
line '^FAIL hosts:main:alpha:ssh:' "ssh must be an argv array"
fixture hosts-guard; edit_host 'row["guard"] = ""'
run_doctor --
line '^FAIL hosts:main:alpha:guard:' "guard must be set"
fixture hosts-lanes-missing; edit_host 'del row["queue_lanes"]'
run_doctor --
expect_rc 0 'a missing queue_lanes is only a warning'
line '^WARN hosts:main:alpha:queue_lanes:' 'queue_lanes missing'
fixture hosts-lanes-bad; edit_host 'row["queue_lanes"] = ["no-pair"]'
run_doctor --
expect_rc 1 'a malformed queue_lanes entry'
line '^FAIL hosts:main:alpha:queue_lanes:' 'queue_lanes shape'
fixture hosts-api; rm "$H/keys/api-check"
run_doctor --
expect_rc 1 'identity command missing'
line '^FAIL hosts:main:alpha:api_check: identity-check command is not an executable file' 'api_check'
fixture hosts-key-missing; rm "$H/keys/id"
run_doctor --
expect_rc 1 'key missing'
line '^FAIL hosts:main:alpha:key: SSH key file is missing: \$CONTROLLER_HOME/keys/id' 'key missing, path shown relative to the home'
fixture hosts-key-mode; chmod 644 "$H/keys/id"
run_doctor --
expect_rc 1 'key mode'
line '^FAIL hosts:main:alpha:key: SSH key file is readable by group or others' 'key mode'
line "^  fix: chmod 600 '" 'key mode fix'
fixture hosts-pin-missing; rm "$H/keys/known_hosts"
run_doctor --
expect_rc 1 'pin file missing'
line '^FAIL hosts:main:alpha:pin: pinned known_hosts file is missing' 'pin missing'
fixture hosts-strict; edit_host 'row["ssh"] = [a for a in row["ssh"] if a != "StrictHostKeyChecking=yes"] ; row["ssh"].insert(1, "-q")'
run_doctor --
expect_rc 0 'no strict host key checking is a warning'
line '^WARN hosts:main:alpha:strict:' 'strict host key checking'
fixture hosts-nopin; edit_host 'row["ssh"] = [a for a in row["ssh"] if not a.startswith("UserKnownHostsFile")]'
run_doctor --
line '^WARN hosts:main:alpha:pin: ssh has no UserKnownHostsFile' 'no pin option'
fixture hosts-devnull-pin; edit_host 'row["ssh"] = [("UserKnownHostsFile=/dev/null" if a.startswith("UserKnownHostsFile") else a) for a in row["ssh"]]'
run_doctor --
expect_rc 0 'UserKnownHostsFile=/dev/null is a warning'
line '^WARN hosts:main:alpha:pin: UserKnownHostsFile=/dev/null is not a pin' '/dev/null is not a pin'
line '^  fix: point -o UserKnownHostsFile at a pinned known_hosts file' '/dev/null pin fix text'
! grep -q '^OK hosts:main:alpha:' "$ROOT/out" || fail 'an alias with a /dev/null pin must not be reported OK'

# --- the local gate slots: gate-run.sh needs both lock paths (absolute, distinct, in a writable directory)
fixture locks-unset; grep -vE '^CONTROLLER_GATE2?_LOCK=' "$H/controller.local.env" > "$H/env.tmp"; mv -- "$H/env.tmp" "$H/controller.local.env"
run_doctor --
expect_rc 1 'both gate locks unset'
line '^FAIL CONTROLLER_GATE_LOCK: CONTROLLER_GATE_LOCK is not set: gate-run.sh exits 2' 'the first lock is required'
line '^FAIL CONTROLLER_GATE2_LOCK: CONTROLLER_GATE2_LOCK is not set' 'the second lock is required'
line '^  fix: set CONTROLLER_GATE_LOCK=<absolute lock file path>' 'the lock fix names the file'
fixture locks-relative; write_env "$H" 'CONTROLLER_GATE2_LOCK=relative/gate2.lock'
run_doctor --
expect_rc 1 'a relative gate lock path'
line '^FAIL CONTROLLER_GATE2_LOCK: CONTROLLER_GATE2_LOCK is not an absolute path' 'a relative lock path is refused'
fixture locks-parent; write_env "$H" "CONTROLLER_GATE_LOCK=$H/no-such-dir/gate.lock"
run_doctor --
expect_rc 1 'a gate lock in a missing directory'
line '^FAIL CONTROLLER_GATE_LOCK: the directory for the lock file is missing or not writable' 'the lock directory must be writable'
fixture locks-same; write_env "$H" "CONTROLLER_GATE2_LOCK=$H/gate.lock"
run_doctor --
expect_rc 1 'both gate locks are one path'
line '^FAIL gate-locks: CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK are the same path' 'the two locks must differ'
fixture locks-existing; : > "$H/gate.lock"
run_doctor --
expect_rc 0 'an existing writable lock file'
line '^OK CONTROLLER_GATE_LOCK: lock file exists and is writable' 'an existing lock file is accepted'
[[ ! -e $H/gate2.lock ]] || fail 'the doctor must not create a lock file'
line '^INFO gate-slots: effective local gate slots: 2 \(CONTROLLER_GATE_SLOTS is not set' 'the default slot count is printed'

# --- CONTROLLER_GATE_SLOTS: the count is printed, and every configured slot lock is checked like the first two
fixture slots-four; write_env "$H" 'CONTROLLER_GATE_SLOTS=4'
run_doctor --
expect_rc 0 'four slots in a writable directory'
line '^INFO gate-slots: effective local gate slots: 4 \(CONTROLLER_GATE_SLOTS\)' 'a configured slot count is printed'
line '^OK gate3.lock: lock file will be created in a writable directory' 'slot 3 is checked'
line '^OK gate4.lock: lock file will be created in a writable directory' 'slot 4 is checked'
[[ ! -e $H/gate3.lock && ! -e $H/gate4.lock ]] || fail 'the doctor must not create a derived lock file'
fixture slots-dir-missing; write_env "$H" 'CONTROLLER_GATE_SLOTS=3' "CONTROLLER_GATE_LOCK_DIR=$H/no-such-dir"
run_doctor --
expect_rc 1 'a derived slot in a missing directory'
line '^FAIL gate3.lock: the directory for the lock file is missing or not writable' 'a derived slot needs a writable directory'
line '^  fix: mkdir -p .*or set CONTROLLER_GATE_LOCK_DIR to another directory' 'the derived-slot fix names CONTROLLER_GATE_LOCK_DIR'
fixture slots-dir-ok; mkdir -p "$H/slotdir"; write_env "$H" 'CONTROLLER_GATE_SLOTS=3' "CONTROLLER_GATE_LOCK_DIR=$H/slotdir"
run_doctor --
expect_rc 0 'CONTROLLER_GATE_LOCK_DIR in a writable directory'
line "^OK gate3.lock: lock file will be created in a writable directory: .*/slotdir/gate3.lock" 'CONTROLLER_GATE_LOCK_DIR decides where slot 3 lives'
fixture slots-dir-relative; write_env "$H" 'CONTROLLER_GATE_SLOTS=3' 'CONTROLLER_GATE_LOCK_DIR=relative/dir'
run_doctor --
expect_rc 1 'a relative CONTROLLER_GATE_LOCK_DIR with three slots'
line '^FAIL CONTROLLER_GATE_LOCK_DIR: CONTROLLER_GATE_LOCK_DIR is not an absolute path' 'the lock directory must be absolute'
fixture slots-dir-unused; write_env "$H" 'CONTROLLER_GATE_SLOTS=2' 'CONTROLLER_GATE_LOCK_DIR=relative/dir'
run_doctor --
expect_rc 0 'a relative CONTROLLER_GATE_LOCK_DIR that two slots never use'
no_line 'CONTROLLER_GATE_LOCK_DIR' 'two slots do not look at CONTROLLER_GATE_LOCK_DIR'
fixture slots-collide; write_env "$H" 'CONTROLLER_GATE_SLOTS=3' "CONTROLLER_GATE2_LOCK=$H/gate3.lock"
run_doctor --
expect_rc 1 'slot 2 collides with the derived slot 3'
line '^FAIL gate-locks: gate slots 2 and 3 use the same lock path .*/gate3.lock: gate-run.sh refuses it' 'a collision with a derived slot is a failure'
no_line 'CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK are the same path' 'only the colliding pair is reported'
fixture slots-one; write_env "$H" 'CONTROLLER_GATE_SLOTS=1'; grep -v '^CONTROLLER_GATE2_LOCK=' "$H/controller.local.env" > "$H/env.tmp"; mv -- "$H/env.tmp" "$H/controller.local.env"
run_doctor --
expect_rc 0 'one slot needs no CONTROLLER_GATE2_LOCK'
line '^INFO gate-slots: effective local gate slots: 1 \(CONTROLLER_GATE_SLOTS\)' 'one slot is printed'
no_line 'CONTROLLER_GATE2_LOCK' 'one slot does not mention the second lock'
fixture slots-one-unset; write_env "$H" 'CONTROLLER_GATE_SLOTS=1'; grep -vE '^CONTROLLER_GATE2?_LOCK=' "$H/controller.local.env" > "$H/env.tmp"; mv -- "$H/env.tmp" "$H/controller.local.env"
run_doctor --
expect_rc 1 'one slot still needs CONTROLLER_GATE_LOCK'
line '^FAIL CONTROLLER_GATE_LOCK: CONTROLLER_GATE_LOCK is not set' 'the first lock stays required'
for bad_slots in 0 9 TOPSECRET-slots-999; do
  fixture slots-bad; write_env "$H" "CONTROLLER_GATE_SLOTS=$bad_slots"
  run_doctor --
  expect_rc 1 "CONTROLLER_GATE_SLOTS=$bad_slots"
  line '^FAIL gate-slots: CONTROLLER_GATE_SLOTS is not an integer from 1 to 8' 'an invalid slot count fails'
  line '^OK CONTROLLER_GATE2_LOCK: ' 'an invalid count still checks the default two locks'
done

# --- the remote-gate allow-list
fixture allow-unknown; write_env "$H" 'CONTROLLER_REMOTE_GATE_HOSTS="alpha ghost"'
run_doctor --
expect_rc 1 'allow-list names an unknown alias'
line "^FAIL remote-allow-list: CONTROLLER_REMOTE_GATE_HOSTS names 'ghost'" 'allow-list alias unknown'
fixture allow-unset; write_env "$H"
run_doctor --
expect_rc 0 'an unset allow-list is a warning'
line '^WARN remote-allow-list: CONTROLLER_REMOTE_GATE_HOSTS is not set' 'allow-list unset'
fixture allow-remote-file
cp "$H/hosts.local.json" "$H/remote.json"
python3 - "$H/remote.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["hosts"]["beta"] = d["hosts"].pop("alpha"); json.dump(d, open(sys.argv[1], "w"))
PY
write_env "$H" 'CONTROLLER_REMOTE_GATE_HOSTS="alpha"' "CONTROLLER_REMOTE_HOSTS_FILE=$H/remote.json"
run_doctor --
expect_rc 1 'the allow-list is judged against the remote host table'
line '^OK hosts:remote:beta' 'the remote host table is validated too'
line "^FAIL remote-allow-list: CONTROLLER_REMOTE_GATE_HOSTS names 'alpha'" 'alpha is not in the remote host table'

# --- default tester image
fixture image-missing
run_doctor DOCKER_MODE=missing --
expect_rc 1 'image absent'
line '^FAIL image: default tester image is not present locally: fixture-image:1' 'image absent'
fixture image-unreachable
run_doctor DOCKER_MODE=unreachable --
expect_rc 0 'docker unreachable is only a warning'
line '^WARN image: docker is unreachable' 'docker unreachable'
fixture image-toml; mkdir -p "$ROOT/norepo"
run_doctor CONTROLLER_REPO="$ROOT/norepo" --
line '^WARN image: cannot read \[environments.tester-unified\] image' 'no toml'
run_doctor RUN_GATE_TESTER_UNIFIED_IMAGE=other:tag --
line '^INFO image-override:' 'an image override is reported'

# --- the removed pinned tag
fixture pinned
printf 'export RUN_GATE_TESTER_UNIFIED_IMAGE=%s\n' 'tester-unified:run-gate-2560' > "$H/chain-old.sh"
printf '%s\n' 'tester-unified:run-gate-2560' > "$H/logs/ignored.log"
mkdir -p "$ROOT/olddir"; printf '%s\n' 'tester-unified:run-gate-2560' > "$ROOT/olddir/chain-x.sh"
run_doctor --
expect_rc 0 'a pinned tag is a warning'
line '^WARN pinned-image: .*\$CONTROLLER_HOME/chain-old.sh' 'the pinned tag in a file under the home'
no_line 'ignored.log' 'logs are not scanned'
no_line 'export RUN_GATE' 'the matching line itself is not printed'
no_line 'olddir' 'an unscanned directory is not reported'
run_doctor -- --scan "$ROOT/olddir"
line "^WARN pinned-image: .*$ROOT/olddir/chain-x.sh" '--scan adds a directory'

# --- runs / logs directories (read-only: a missing directory is reported, never created)
fixture dirs; rmdir "$H/runs" "$H/logs"
run_doctor --
expect_rc 1 'missing runs and logs directories'
line '^FAIL runs-dir: directory missing or not a directory: \$CONTROLLER_HOME/runs' 'runs dir missing'
line '^FAIL logs-dir: directory missing or not a directory: \$CONTROLLER_HOME/logs' 'logs dir missing'
[[ ! -e $H/runs && ! -e $H/logs ]] || fail 'the doctor must not create the runs or logs directory'
fixture dirs-file; rmdir "$H/logs"; : > "$H/logs"
run_doctor --
line '^FAIL logs-dir: directory missing or not a directory' 'logs is a file'
if [[ $(id -u) != 0 ]]; then
  fixture dirs-ro; chmod 500 "$H/runs"
  run_doctor --
  chmod 700 "$H/runs"
  expect_rc 1 'unwritable runs directory'
  line '^FAIL runs-dir: directory is not writable' 'runs not writable'
else
  echo 'SKIP ctl-doctor: unwritable-runs-dir check needs a non-root uid'
fi

# --- where the state lives: symlinks into a git work tree
WT=$ROOT/wt
git init -q "$WT"
printf 'artifacts/\n' > "$WT/.gitignore"
mkdir -p "$WT/artifacts/prog" "$WT/plain/prog"
fixture residency
run_doctor --
line '^OK residency:home: real location .* is outside any git work tree' 'a home outside any work tree'
no_line 'WARN residency' 'no residency warning for an outside home'
for sub in artifacts plain; do
  mkdir -p "$WT/$sub/prog/runs" "$WT/$sub/prog/logs"
  cp -r "$ROOT/residency/home/." "$WT/$sub/prog/"
  ln -sfn "$WT/$sub/prog" "$ROOT/link-$sub"
done
H=$ROOT/link-artifacts run_doctor --
line "^WARN residency:home: real location .*$WT/artifacts/prog \(configured as .*link-artifacts, a symlink\) is inside the git work tree .* covered by its ignore rules: 'git clean -fdx' there would DELETE it" 'an ignored symlinked home'
line '^  fix: keep the state outside any work tree, or run ctl-state-backup.sh' 'residency fix'
H=$ROOT/link-plain run_doctor --
line "^WARN residency:home: real location .*$WT/plain/prog .* NOT ignored: 'git clean -fd' would DELETE it" 'a symlinked home inside a work tree that is not ignored'
fixture residency-runs
mkdir -p "$WT/artifacts/runs-target"
write_env "$H" 'CONTROLLER_REMOTE_GATE_HOSTS="alpha"' "CONTROLLER_RUNS_DIR=$ROOT/link-runs"
ln -sfn "$WT/artifacts/runs-target" "$ROOT/link-runs"
run_doctor --
line "^WARN residency:runs: real location .*$WT/artifacts/runs-target \(configured as .*link-runs, a symlink\).* 'git clean -fdx' there would DELETE it" 'a runs directory behind a symlink into an ignored directory'
line '^OK residency:home' 'the home itself is fine'

echo 'PASS ctl-doctor'
