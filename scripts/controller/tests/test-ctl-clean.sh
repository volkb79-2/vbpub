#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLEAN=${CTL_CLEAN_SCRIPT:-$SCRIPT_DIR/../ctl-clean.sh}
BASH_BIN=${BASH:-$(command -v bash)}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-ctl-clean-test.XXXXXX")
EXTRA_ENV=()

cleanup() {
  local status=$?
  local proc
  for proc in "${HOLDERS[@]:-}"; do kill "$proc" 2>/dev/null || true; done
  if [[ -n ${KEEP_TEST_ROOT:-} ]]; then
    printf 'KEEP_TEST_ROOT=%s\n' "$ROOT"
    return "$status"
  fi
  if [[ -d $ROOT ]]; then
    chmod -R u+w -- "$ROOT" 2>/dev/null || true
    rm -rf -- "$ROOT"
  fi
  return "$status"
}
declare -a HOLDERS=()
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

mkdir -p "$ROOT/home"

fixture() {
  R=$ROOT/$1/repo
  mkdir -p "$R/.run-gate"
  git init -q "$R" || fail "git init failed for $R"
}

run_clean() {
  env -i HOME="$ROOT/home" PATH="${TEST_PATH:-$PATH}" LC_ALL=C TZ=UTC \
    "${EXTRA_ENV[@]}" "$BASH_BIN" "$CLEAN" "$@" > "$ROOT/out" 2> "$ROOT/err"
  RC=$?
}

expect_rc() {
  [[ $RC == "$1" ]] || {
    cat "$ROOT/out" "$ROOT/err" >&2
    fail "$2: exit $RC, wanted $1"
  }
}

said() {
  grep -qF -- "$1" "$ROOT/out" "$ROOT/err" || {
    cat "$ROOT/out" "$ROOT/err" >&2
    fail "$2 (missing: $1)"
  }
}

not_said() {
  ! grep -qF -- "$1" "$ROOT/out" "$ROOT/err" || {
    cat "$ROOT/out" "$ROOT/err" >&2
    fail "$2 (unexpected: $1)"
  }
}

contains() {
  local file=$1 needle=$2 message=$3 text
  text=$(<"$file")
  [[ $text == *"$needle"* ]] || {
    cat "$ROOT/out" "$ROOT/err" >&2
    fail "$message (missing byte-exact: $needle)"
  }
}

make_old_entry() {
  local entry=$1 when=$2 contents=${3:-scratch}
  mkdir -p "$entry"
  printf '%s\n' "$contents" > "$entry/file"
  touch -d "$when" "$entry/file" "$entry"
}

summary_line() {
  printf 'eligible %d, removed %d, failed %d, inspection-failed %d scratch entries' "$@"
}

echo '--- round 1/2: nested activity is retained'
fixture activity
old=$R/.run-gate/run-gate-gitcfg-old
active=$R/.run-gate/run-gate-gitcfg-active
make_old_entry "$old" '2020-01-01 00:00:00 UTC'
mkdir -p "$active/nested"
printf 'old\n' > "$active/nested/file"
touch -d '2020-01-01 00:00:00 UTC' "$active/nested/file" "$active/nested" "$active"
touch -d '2 hours ago' "$active/nested/file"
run_clean --apply --repo "$R"
expect_rc 0 'an old entry beside a nested-active entry'
[[ ! -e $old ]] || fail 'the wholly old entry must be removed'
[[ -e $active ]] || fail 'an old directory containing a recently touched nested file must be kept'
said 'eligible 1 of 2; older than 48h (newest file inside included)' 'the recursive threshold'
said "$(summary_line 1 1 0 0)" 'activity summary accounting'

echo '--- round 1/2: age parsing remains bounded and decimal'
fixture age-hours
make_old_entry "$R/.run-gate/run-gate-gitcfg-9h" '9 hours ago'
run_clean --age-hours 08 --repo "$R"
expect_rc 0 '08 accepted as decimal 8'
said 'age=8h' '08 is parsed with base 10'
said 'eligible 1 of 1; older than 8h (newest file inside included)' 'the 8h threshold is effective'
for bad_age in 0 8761 99999999999999999999 abc; do
  run_clean --age-hours "$bad_age" --repo "$R"
  expect_rc 2 "invalid --age-hours $bad_age"
  said '--age-hours must be an integer from 1 to 8760' "the refusal for $bad_age is clear"
done
run_clean --age-hours '' --repo "$R"
expect_rc 2 'empty --age-hours'
said 'non-empty integer from 1 to 8760' 'empty --age-hours is named'

echo '--- round 1/2: --repo remains a canonical worktree-root guard'
fixture repo-validation
mkdir -p "$R/sub"
run_clean --repo -P
expect_rc 2 'dash-leading --repo'
said "must not start with '-'" 'a dash-leading repo is refused'
run_clean --repo --
expect_rc 2 '--repo --'
mkdir -p "$ROOT/repo-validation/not-a-repo"
run_clean --repo "$ROOT/repo-validation/not-a-repo"
expect_rc 2 'a non-repository --repo'
said 'is not inside a Git worktree' 'a non-repository is refused'
run_clean --repo "$R/sub"
expect_rc 2 'a repository subdirectory as --repo'
said 'must name the worktree root' 'a subdirectory is refused'
ln -s "$R" "$ROOT/repo-validation/link"
run_clean --repo "$ROOT/repo-validation/link"
expect_rc 0 'a symlinked repository root'
said "target=$R age=48h" 'the canonical target path is printed'

echo '--- round 1/2: worktree pruning is explicit opt-in'
run_clean --help
expect_rc 0 'ctl-clean help'
said 'only from the shared' 'help carries the shared-checkout warning'
fixture worktree-prune
mkdir -p "$R/.git/worktrees/dangling"
printf 'gitdir: %s\n' "$ROOT/worktree-prune/missing/.git" > "$R/.git/worktrees/dangling/gitdir"
printf 'ref: refs/heads/main\n' > "$R/.git/worktrees/dangling/HEAD"
run_clean --apply --repo "$R"
expect_rc 0 'default apply'
[[ -d $R/.git/worktrees/dangling ]] || fail 'default apply must not prune'
not_said 'would prune:' 'default report must not consider pruning'
run_clean --prune-worktrees --repo "$R"
expect_rc 0 'explicit prune dry-run'
said 'would prune:' 'prune dry-run shows output'
[[ -d $R/.git/worktrees/dangling ]] || fail 'prune dry-run must not remove'
run_clean --prune-worktrees --apply --repo "$R"
expect_rc 0 'explicit prune apply'
[[ ! -e $R/.git/worktrees/dangling ]] || fail 'prune apply must remove the dangling entry'

echo '--- round 1/2: deletion failures propagate; priority tools fall back'
fixture deletion-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-stuck" '2020-01-01 00:00:00 UTC'
chmod 555 "$R/.run-gate"
run_clean --apply --repo "$R"
chmod 755 "$R/.run-gate"
expect_rc 1 'an undeletable entry'
[[ -e $R/.run-gate/run-gate-gitcfg-stuck ]] || fail 'undeletable entry must remain'
said "$(summary_line 1 0 1 0)" 'quarantine failure accounting'
said 'FAILED to quarantine' 'failure names the operation'

fixture priority-fallback
make_old_entry "$R/.run-gate/run-gate-gitcfg-no-priority" '2020-01-01 00:00:00 UTC'
MINIMAL_PATH=$ROOT/priority-fallback/tools
mkdir -p "$MINIMAL_PATH"
for tool in basename date du find git grep mkdir mktemp mv rm rmdir sort timeout xargs; do
  ln -s "$(command -v "$tool")" "$MINIMAL_PATH/$tool"
done
TEST_PATH=$MINIMAL_PATH run_clean --apply --repo "$R"
expect_rc 0 'apply without nice or ionice'
said 'nice/ionice unavailable; using plain xargs rm' 'plain rm fallback is announced'
[[ ! -e $R/.run-gate/run-gate-gitcfg-no-priority ]] || fail 'fallback must remove'
said "$(summary_line 1 1 0 0)" 'fallback deletion accounting'

echo '--- round 2 finding 1: atomic quarantine detects replacement'
fixture toctou
raced=$R/.run-gate/run-gate-gitcfg-race
make_old_entry "$raced" '2020-01-01 00:00:00 UTC' 'original'
race_path=$ROOT/toctou/bin
mkdir -p "$race_path"
REAL_MV=$(command -v mv)
cat > "$race_path/mv" <<SH
#!/usr/bin/env bash
if [[ ! -e $ROOT/toctou/interleaved ]]; then
  src=\${@: -2:1}
  $REAL_MV -- "\$src" "$ROOT/toctou/interloper"
  mkdir -- "\$src"
  printf 'fresh-data\\n' > "\$src/fresh"
  touch "$ROOT/toctou/interleaved"
fi
exec $REAL_MV "\$@"
SH
chmod +x "$race_path/mv"
TEST_PATH=$race_path:$PATH run_clean --apply --repo "$R"
expect_rc 1 'replacement between selection and deletion'
[[ -e $raced/fresh ]] || fail 'fresh entry created at the selected pathname must survive'
[[ -e $ROOT/toctou/interloper/file ]] || fail 'the displaced old entry must survive for inspection'
said 'activity changed after selection' 'the race is named'
said "$(summary_line 1 0 1 0)" 'race accounting'
rm -f "$race_path/mv"

echo '--- round 2 finding 2: longest lane hour budget sets the default'
fixture lane-budget
mkdir -p "$R/project"
printf '%s\n' 'budget = "72h"' > "$R/project/run-gate.toml"
printf '%s\n' 'budget = "999m"' > "$R/run-gate.toml"
held=$R/.run-gate/run-gate-gitcfg-72h30m
old_stamp="@$(( $(date +%s) - 72 * 3600 - 1800 ))"
make_old_entry "$held" "$old_stamp"
touch -d "$old_stamp" "$held/config"
touch -d "$old_stamp" "$held"
(cd "$held" && exec sleep 60) &
HOLDERS+=($!)
run_clean --repo "$R"
expect_rc 0 'dry-run derives age from the longest hour budget'
said 'age=73h' '72h lane budget yields a 73h default'
said 'longest lane hour budget=72h' 'the selected budget is reported'
said 'eligible 0 of 1' 'a 72h30m entry is protected by the 73h default'
[[ -e $held ]] || fail 'the sub-budget entry must remain'
run_clean --apply --age-hours 72 --repo "$R"
expect_rc 2 'apply below budget plus one'
said 'below the longest lane budget 72h + 1' 'the apply refusal names the budget'
[[ -e $held ]] || fail 'refused apply must not delete'
run_clean --apply --i-know-the-age --age-hours 72 --repo "$R"
expect_rc 0 'explicit age override'
said 'eligible 1 of 1' 'the override makes the 72h30m entry eligible'
if [[ -e $held ]]; then
  find "$R/.run-gate" -mindepth 1 -maxdepth 1 -printf '%P %T@\n' >&2
  cat "$ROOT/out" "$ROOT/err" >&2
  fail 'explicit override must delete the eligible 72h30m entry'
fi
said "$(summary_line 1 1 0 0)" 'budget-derived dry and apply accounting'

echo '--- round 2 finding 3: discovery failure is fail-closed'
fixture unreadable
make_old_entry "$R/.run-gate/run-gate-gitcfg-unreadable" '2020-01-01 00:00:00 UTC'
chmod 000 "$R/.run-gate"
run_clean --repo "$R"
chmod 755 "$R/.run-gate"
expect_rc 1 'an unreadable .run-gate'
said 'cannot discover' 'the discovery failure is explicit'
said 'removed 0' 'nothing is claimed removed'

fixture inspect-stop
make_old_entry "$R/.run-gate/run-gate-gitcfg-inspect" '2020-01-01 00:00:00 UTC'
inspect_path=$ROOT/inspect-stop/bin
mkdir -p "$inspect_path"
REAL_FIND=$(command -v find)
cat > "$inspect_path/find" <<SH
#!/usr/bin/env bash
case " \$* " in
  *' -newermt '*)
    touch "$ROOT/inspect-stop/failed"
    exit 1
    ;;
esac
exec $REAL_FIND "\$@"
SH
chmod +x "$inspect_path/find"
TEST_PATH=$inspect_path:$PATH run_clean --repo "$R"
expect_rc 1 'activity traversal failure'
said 'cannot inspect .run-gate activity' 'the failed traversal is named'
said "$(summary_line 0 0 0 1)" 'inspection failure is counted separately'

echo '--- round 2 finding 4: summary fields are independent'
fixture summary
make_old_entry "$R/.run-gate/run-gate-gitcfg-summary" '2020-01-01 00:00:00 UTC'
run_clean --repo "$R"
expect_rc 0 'dry-run accounting'
said "$(summary_line 1 0 0 0)" 'dry-run reports eligible separately from removed'

echo '--- round 2 finding 5: one traversal remains linear at 20k entries'
fixture performance
mkdir -p "$R/.run-gate"/run-gate-gitcfg-{00001..20000}
touch -d '2020-01-01 00:00:00 UTC' "$R/.run-gate"/run-gate-gitcfg-*
start_ns=$(date +%s%N)
run_clean --repo "$R"
end_ns=$(date +%s%N)
expect_rc 0 '20k-entry dry-run'
said "$(summary_line 20000 0 0 0)" '20k entries are all eligible'
elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
((elapsed_ms < 15000)) || fail "20k-entry dry-run took ${elapsed_ms}ms, wanted <15000ms"

echo '--- round 2 finding 6: inherited Git environment cannot redirect --repo'
fixture git-env-a
make_old_entry "$R/.run-gate/run-gate-gitcfg-a" '2020-01-01 00:00:00 UTC'
mkdir -p "$R/.git/worktrees/dangling-a"
printf 'gitdir: %s\n' "$ROOT/git-env-a/missing/.git" > "$R/.git/worktrees/dangling-a/gitdir"
printf 'ref: refs/heads/main\n' > "$R/.git/worktrees/dangling-a/HEAD"
repo_a=$R
fixture git-env-b
EXTRA_ENV=(
  "GIT_DIR=$repo_a/.git"
  "GIT_WORK_TREE=$repo_a"
  "GIT_INDEX_FILE=$repo_a/.git/index"
  "GIT_COMMON_DIR=$repo_a/.git"
  "GIT_NAMESPACE=foreign"
)
run_clean --prune-worktrees --repo "$R"
EXTRA_ENV=()
expect_rc 0 'Git environment is sanitized'
said "target=$R age=48h" 'the requested repository is reported'
not_said 'dangling-a' 'a foreign GIT_DIR must not contribute prune output'
[[ -d $repo_a/.git/worktrees/dangling-a ]] || fail 'foreign repository state changed'

echo '--- round 2 finding 7: NUL-safe reports preserve newline filenames'
fixture nul-report
nul_name=$'run-gate-gitcfg-new\nline'
nul_entry=$R/.run-gate/$nul_name
make_old_entry "$nul_entry" '20 days ago' 'payload'
run_clean --repo "$R"
expect_rc 0 'newline-containing entry dry-run'
contains "$ROOT/out" "$nul_name" 'the path must be listed byte-exactly'
grep -Eq '[1-9][0-9]* bytes' "$ROOT/out" || fail 'the newline entry size must be computed'
not_said 'invalid du output' 'NUL reporting must not parse du text'
said "$(summary_line 1 0 0 0)" 'newline entry is eligible'

echo '--- round 1/2: dry reports, sizes, examples, timeouts and suite registration'
fixture dry-report
printf '{}\n' > "$R/.run-gate/history.json"
for days in 40 30 20 10; do make_old_entry "$R/.run-gate/run-gate-gitcfg-$days" "$days days ago"; done
nested_active=$R/.run-gate/run-gate-gitcfg-nested-active
mkdir -p "$nested_active/nested"
printf 'old\n' > "$nested_active/nested/file"
touch -d '2020-01-01 00:00:00 UTC' "$nested_active/nested/file" "$nested_active/nested" "$nested_active"
touch -d '2 hours ago' "$nested_active/nested/file"
make_old_entry "$R/.run-gate/run-gate-test-bin-25" '25 days ago'
make_old_entry "$R/.run-gate/run-gate-test-bin-15" '15 days ago'
make_old_entry "$R/.run-gate/run-gate-test-invoke-50" '50 days ago'
make_old_entry "$R/.run-gate/assay-liveness-60" '60 days ago'
run_clean --repo "$R"
expect_rc 0 'the dry-run report'
grep -Eq 'run-gate-gitcfg-\*.*eligible 4 of 5; older than 48h .* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'gitcfg count and bytes'
grep -Eq 'run-gate-test-bin-\*.*eligible 2 of 2; older than 48h .* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'test-bin count and bytes'
grep -Eq 'run-gate-test-invoke\*.*eligible 1 of 1; older than 48h .* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'test-invoke count and bytes'
grep -Eq 'assay-liveness-\*.*eligible 1 of 1; older than 48h .* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'assay-liveness count and bytes'
[[ $(grep -Ec '^    oldest: .*run-gate-gitcfg-' "$ROOT/out") == 3 ]] || fail 'three oldest examples'
[[ $(grep -Ec '^    newest: .*run-gate-gitcfg-' "$ROOT/out") == 3 ]] || fail 'three newest examples'
said "$(summary_line 8 0 0 0)" 'dry-run summary'
[[ -e $R/.run-gate/history.json ]] || fail 'history must be kept'

fixture report-timeout
mkdir -p "$R/artifacts" "$R/.worktrees"
TIMEOUT_PATH=$ROOT/report-timeout/timeout-bin
mkdir -p "$TIMEOUT_PATH"
cat > "$TIMEOUT_PATH/timeout" <<'SH'
#!/usr/bin/env bash
exit 124
SH
chmod +x "$TIMEOUT_PATH/timeout"
TEST_PATH=$TIMEOUT_PATH:$PATH run_clean --repo "$R"
expect_rc 0 'report-only size timeout'
grep -Eq 'artifacts.*\(size not computed: timed out\)' "$ROOT/out" || fail 'artifacts timeout label'
grep -Eq '.worktrees.*\(size not computed: timed out\)' "$ROOT/out" || fail '.worktrees timeout label'

SUITE=$SCRIPT_DIR/run-suite.sh
grep -qF "\"\$SCRIPT_DIR/../ctl-clean.sh\"" "$SUITE" || fail 'suite syntax list has ctl-clean'
grep -qF "\"\$SCRIPT_DIR/test-ctl-clean.sh\"" "$SUITE" || fail 'suite syntax list has test-ctl-clean'
grep -qF "bash \"\$SCRIPT_DIR/test-ctl-clean.sh\" || exit \$?" "$SUITE" || fail 'suite executes test-ctl-clean'

echo 'PASS ctl-clean'
