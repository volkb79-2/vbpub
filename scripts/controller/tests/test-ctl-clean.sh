#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLEAN=${CTL_CLEAN_SCRIPT:-$SCRIPT_DIR/../ctl-clean.sh}
BASH_BIN=${BASH:-$(command -v bash)}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-ctl-clean-test.XXXXXX")
EXTRA_ENV=()
declare -a HOLDERS=()

cleanup() {
  local status=$? proc
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

wait_for_cwd() {
  local pid=$1 target=$2 i actual
  for ((i = 0; i < 100; i++)); do
    actual=$(readlink -- "/proc/$pid/cwd" 2>/dev/null || true)
    [[ $actual == "$target" ]] && return 0
    sleep 0.02
  done
  fail "process $pid did not enter $target"
}

echo '--- nested activity is retained and old siblings are removed'
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
said 'eligible 1 of 2; active-by-mtime 1, active-process 0, mounted 0; older than 48h' 'recursive activity count'
said "$(summary_line 1 1 0 0)" 'activity summary accounting'

echo '--- age parsing remains bounded and decimal'
fixture age-hours
make_old_entry "$R/.run-gate/run-gate-gitcfg-9h" '9 hours ago'
run_clean --age-hours 08 --repo "$R"
expect_rc 0 '08 accepted as decimal 8'
said 'age=8h' '08 is parsed with base 10'
said 'eligible 1 of 1; active-by-mtime 0' 'the 8h threshold is effective'
for bad_age in 0 8761 99999999999999999999 abc; do
  run_clean --age-hours "$bad_age" --repo "$R"
  expect_rc 2 "invalid --age-hours $bad_age"
  said '--age-hours must be an integer from 1 to 8760' "the refusal for $bad_age is clear"
done
run_clean --age-hours '' --repo "$R"
expect_rc 2 'empty --age-hours'
said 'non-empty integer from 1 to 8760' 'empty --age-hours is named'

echo '--- --repo remains a canonical worktree-root guard'
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

echo '--- root symlink is refused before traversal'
fixture root-symlink
rm -rf -- "$R/.run-gate"
mkdir -p "$ROOT/root-symlink/external/run-gate-gitcfg-outside"
make_old_entry "$ROOT/root-symlink/external/run-gate-gitcfg-outside" '2020-01-01 00:00:00 UTC'
ln -s "$ROOT/root-symlink/external" "$R/.run-gate"
run_clean --repo "$R"
expect_rc 1 'a .run-gate symlink at start'
said '.run-gate is a symlink' 'the symlink refusal is explicit'
said "$(summary_line 0 0 0 1)" 'nothing inspected through the symlink'
[[ -e $ROOT/root-symlink/external/run-gate-gitcfg-outside ]] || fail 'external target must remain'

echo '--- --prune-worktrees is removed'
run_clean --help
expect_rc 0 'ctl-clean help'
said 'quiet host with no gate running' 'help states the quiet-host action'
not_said '--prune-worktrees' 'help must not advertise pruning'
run_clean --prune-worktrees --repo "$ROOT/repo-validation/repo"
expect_rc 2 'the removed prune option'
said 'unknown argument --prune-worktrees' 'the removed option is refused'

echo '--- deletion failures propagate; priority tools fall back'
fixture deletion-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-stuck" '2020-01-01 00:00:00 UTC'
chmod 555 "$R/.run-gate"
run_clean --apply --repo "$R"
chmod 755 "$R/.run-gate"
expect_rc 1 'an undeletable entry'
[[ -e $R/.run-gate/run-gate-gitcfg-stuck ]] || fail 'undeletable entry must remain'
said "$(summary_line 1 0 1 0)" 'quarantine failure accounting'
said 'FAILED to create quarantine' 'failure names the operation'

fixture priority-fallback
make_old_entry "$R/.run-gate/run-gate-gitcfg-no-priority" '2020-01-01 00:00:00 UTC'
MINIMAL_PATH=$ROOT/priority-fallback/tools
mkdir -p "$MINIMAL_PATH"
for tool in basename date du find findmnt git grep mkdir mktemp mv readlink rm rmdir sort timeout xargs; do
  ln -s "$(command -v "$tool")" "$MINIMAL_PATH/$tool"
done
TEST_PATH=$MINIMAL_PATH run_clean --apply --repo "$R"
expect_rc 0 'apply without nice or ionice'
said 'nice/ionice unavailable; using plain xargs rm' 'plain rm fallback is announced'
[[ ! -e $R/.run-gate/run-gate-gitcfg-no-priority ]] || fail 'fallback must remove'
said "$(summary_line 1 1 0 0)" 'fallback deletion accounting'

echo '--- deletion uses rm --one-file-system'
fixture one-file-system
make_old_entry "$R/.run-gate/run-gate-gitcfg-onefs" '2020-01-01 00:00:00 UTC'
onefs_path=$ROOT/one-file-system/bin
mkdir -p "$onefs_path"
REAL_RM=$(command -v rm)
cat > "$onefs_path/rm" <<SH
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$ROOT/one-file-system/rm-args"
exec $REAL_RM "\$@"
SH
chmod +x "$onefs_path/rm"
TEST_PATH=$onefs_path:$PATH run_clean --apply --repo "$R"
expect_rc 0 '--one-file-system apply'
grep -qF -- '--one-file-system' "$ROOT/one-file-system/rm-args" ||
  fail 'rm must receive --one-file-system'
[[ ! -e $R/.run-gate/run-gate-gitcfg-onefs ]] || fail 'one-filesystem entry must be removed'

echo '--- quarantine detects a replacement between selection and deletion'
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
said "$(summary_line 0 0 1 0)" 'race accounting keeps eligible and failed independent'

echo '--- live process cwd is active-process, not eligible'
fixture live-process
held=$R/.run-gate/run-gate-gitcfg-live
make_old_entry "$held" '2020-01-01 00:00:00 UTC'
(cd "$held" && exec sleep 60) &
holder=$!
HOLDERS+=("$holder")
wait_for_cwd "$holder" "$held"
run_clean --apply --repo "$R"
expect_rc 0 'apply with a live process cwd'
said 'active-process: run-gate-gitcfg-live' 'the live entry is listed as active-process'
said 'eligible 0 of 1; active-by-mtime 0, active-process 1, mounted 0' 'live process is not eligible'
said "$(summary_line 0 0 0 0)" 'live process changes no removal count'
[[ -e $held ]] || fail 'live-process entry must be retained'

echo '--- mounts are read once and protect a candidate subtree'
fixture mount
mounted=$R/.run-gate/run-gate-gitcfg-mounted
make_old_entry "$mounted/nested" '2020-01-01 00:00:00 UTC'
touch -d '2020-01-01 00:00:00 UTC' "$mounted"
mount_bin=$ROOT/mount/bin
mkdir -p "$mount_bin"
cat > "$mount_bin/findmnt" <<SH
#!/usr/bin/env bash
printf x >> "$ROOT/mount/findmnt-calls"
printf '%s\n' "$mounted/nested"
SH
chmod +x "$mount_bin/findmnt"
TEST_PATH=$mount_bin:$PATH run_clean --apply --repo "$R"
expect_rc 0 'apply with a fake nested mount'
[[ $(wc -c < "$ROOT/mount/findmnt-calls") == 1 ]] || fail 'findmnt must be read exactly once'
said 'mounted: run-gate-gitcfg-mounted' 'the mounted candidate is listed'
said 'eligible 0 of 1; active-by-mtime 0, active-process 0, mounted 1' 'mounted candidate is not eligible'
[[ -e $mounted/nested ]] || fail 'mounted subtree must remain'

fixture mount-read-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-mount-read" '2020-01-01 00:00:00 UTC'
bad_mount_bin=$ROOT/mount-read-failure/bin
mkdir -p "$bad_mount_bin"
printf '#!/usr/bin/env bash\nexit 1\n' > "$bad_mount_bin/findmnt"
chmod +x "$bad_mount_bin/findmnt"
TEST_PATH=$bad_mount_bin:$PATH run_clean --repo "$R"
expect_rc 1 'a failed findmnt read'
said 'cannot inspect mount points' 'the findmnt failure is named'
said "$(summary_line 0 0 0 1)" 'findmnt failure is inspection-failed'
[[ -e $R/.run-gate/run-gate-gitcfg-mount-read ]] || fail 'failed mount inspection must not delete'

echo '--- existing quarantine leftovers block apply until removed'
fixture quarantine-leftover
make_old_entry "$R/.run-gate/run-gate-gitcfg-after-leftover" '2020-01-01 00:00:00 UTC'
mkdir -p "$R/.run-gate/.ctl-clean-quarantine-old/payload"
run_clean --repo "$R"
expect_rc 0 'dry run reports a quarantine leftover'
said 'existing quarantine entries block apply' 'the leftover is reported'
said 'remove them with: find' 'the exact removal command is printed'
run_clean --apply --repo "$R"
expect_rc 1 'apply is blocked by a quarantine leftover'
said "$(summary_line 0 0 0 0)" 'no work is claimed while blocked'
[[ -e $R/.run-gate/.ctl-clean-quarantine-old/payload ]] || fail 'leftover must remain for the operator'
[[ -e $R/.run-gate/run-gate-gitcfg-after-leftover ]] || fail 'blocked apply must not touch candidates'

echo '--- budget parsing covers every duration form and fails closed'
fixture lane-budget
mkdir -p "$R/project"
printf '%s\n' 'budget = "5000m"' > "$R/project/run-gate.toml"
sub_budget=$R/.run-gate/run-gate-gitcfg-sub-budget
boundary_stamp="@$(( $(date +%s) - 84 * 3600 - 1800 ))"
make_old_entry "$sub_budget" "$boundary_stamp"
run_clean --repo "$R"
expect_rc 0 'dry-run derives age from a minute budget'
said 'age=85h' '5000m yields max(48, ceil(5000/60 + 1))'
said 'longest lane budget=5000m (83.333333h; needs age>=85h)' 'the longest minute budget is reported'
said 'eligible 0 of 1' 'an 84h30m entry is protected by the 85h floor'
run_clean --apply --age-hours 84 --repo "$R"
expect_rc 2 'apply below a minute budget plus one'
said 'below the longest lane budget 5000m (83.333333h) + 1' 'the budget refusal names the duration'
[[ -e $sub_budget ]] || fail 'refused apply must not delete'
run_clean --apply --i-know-the-age --age-hours 84 --repo "$R"
expect_rc 0 '--i-know-the-age lowers only the budget floor'
if [[ -e $sub_budget ]]; then
  cat "$ROOT/out" "$ROOT/err" >&2
  date -u '+test now: %Y-%m-%dT%H:%M:%SZ'
  find "$sub_budget" -printf 'fixture: %P %TY-%Tm-%TdT%TH:%TM:%TSZ\n' >&2
  env TZ=UTC find "$R/.run-gate" -mindepth 1 -newermt '84 hours ago' \
    -printf 'active: %P %TY-%Tm-%TdT%TH:%TM:%TSZ\n' >&2
  fail 'explicit override must remove the 84h30m entry'
fi
said "$(summary_line 1 1 0 0)" 'override accounting'

fixture literal-and-day-budget
printf "%s\n" "budget = '72h'" > "$R/run-gate.toml"
printf '%s\n' 'budget = "2d"' > "$R/assay.toml"
run_clean --repo "$R"
expect_rc 0 'literal and day budgets'
said 'age=73h' 'the longest parsed budget wins'
said "longest lane budget=72h (72h; needs age>=73h)" 'a TOML literal budget is reported'

fixture second-budget
printf '%s\n' 'budget = "30s"' > "$R/assay.toml"
run_clean --repo "$R"
expect_rc 0 'a small second budget parses'
said 'age=48h' 'the 48h floor remains for a small budget'
said 'longest lane budget=30s (0.008333h; needs age>=48h)' 'the second budget is reported'

for bad_budget in 'budget = "seventytwo hours"' 'budget = 72' 'budget = "0s"'; do
  fixture bad-budget
  printf '%s\n' "$bad_budget" > "$R/run-gate.toml"
  make_old_entry "$R/.run-gate/run-gate-gitcfg-bad-budget" '2020-01-01 00:00:00 UTC'
  run_clean --apply --repo "$R"
  expect_rc 1 "unparsable budget: $bad_budget"
  said 'cannot read or parse lane budgets' 'unparsable budgets fail closed'
  said "$(summary_line 0 0 0 1)" 'unparsable budget is inspection-failed'
  [[ -e $R/.run-gate/run-gate-gitcfg-bad-budget ]] || fail 'unparsable budget must not delete'
done

fixture unreadable-budget
printf '%s\n' 'budget = "72h"' > "$R/run-gate.toml"
chmod 000 "$R/run-gate.toml"
run_clean --apply --repo "$R"
chmod 644 "$R/run-gate.toml"
expect_rc 1 'an unreadable budget file'
said 'cannot read or parse lane budgets' 'the unreadable file is named'
said "$(summary_line 0 0 0 1)" 'unreadable budget is inspection-failed'

echo '--- find, activity, metadata and recount failures are fail-closed'
fixture unreadable-run-gate
make_old_entry "$R/.run-gate/run-gate-gitcfg-unreadable" '2020-01-01 00:00:00 UTC'
chmod 000 "$R/.run-gate"
run_clean --repo "$R"
chmod 755 "$R/.run-gate"
expect_rc 1 'an unreadable .run-gate'
said 'cannot inspect existing ctl-clean quarantine entries' 'the failed discovery is explicit'
said "$(summary_line 0 0 0 1)" 'discovery failure is inspection-failed'

fixture activity-read-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-inspect" '2020-01-01 00:00:00 UTC'
inspect_path=$ROOT/activity-read-failure/bin
mkdir -p "$inspect_path"
REAL_FIND=$(command -v find)
cat > "$inspect_path/find" <<SH
#!/usr/bin/env bash
case " \$* " in
  *' -newermt '*) exit 1 ;;
esac
exec $REAL_FIND "\$@"
SH
chmod +x "$inspect_path/find"
TEST_PATH=$inspect_path:$PATH run_clean --repo "$R"
expect_rc 1 'activity traversal failure'
said 'cannot inspect .run-gate activity' 'the failed traversal is named'
said "$(summary_line 0 0 0 1)" 'inspection failure is counted separately'

fixture metadata-read-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-metadata" '2020-01-01 00:00:00 UTC'
metadata_path=$ROOT/metadata-read-failure/bin
mkdir -p "$metadata_path"
cat > "$metadata_path/find" <<SH
#!/usr/bin/env bash
case " \$* " in
  *'%T@'*) exit 1 ;;
esac
exec $REAL_FIND "\$@"
SH
chmod +x "$metadata_path/find"
TEST_PATH=$metadata_path:$PATH run_clean --repo "$R"
expect_rc 1 'metadata traversal failure'
said 'cannot inspect scratch sizes and timestamps' 'the metadata failure is named'
said "$(summary_line 1 0 0 1)" 'eligible remains independent from inspection failure'

fixture recount-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-recount" '2020-01-01 00:00:00 UTC'
recount_path=$ROOT/recount-failure/bin
mkdir -p "$recount_path"
cat > "$recount_path/find" <<SH
#!/usr/bin/env bash
case " \$* " in
  *' -mindepth 1 -maxdepth 1 -print0 '*) exit 1 ;;
esac
exec $REAL_FIND "\$@"
SH
chmod +x "$recount_path/find"
TEST_PATH=$recount_path:$PATH run_clean --repo "$R"
expect_rc 1 'recount failure'
said 'cannot recount .run-gate entries' 'the failed recount is named'
said "$(summary_line 1 0 0 1)" 'recount failure keeps eligible independent'

echo '--- dry-run summary fields are independent'
fixture summary
make_old_entry "$R/.run-gate/run-gate-gitcfg-summary" '2020-01-01 00:00:00 UTC'
run_clean --repo "$R"
expect_rc 0 'dry-run accounting'
said "$(summary_line 1 0 0 0)" 'dry-run reports eligible separately from removed'

echo '--- find invocation count is structural, not size-gated'
find_count_for() {
  local count=$1 fixture_name=$2 i bin real_find
  fixture "$fixture_name"
  for ((i = 1; i <= count; i++)); do
    make_old_entry "$R/.run-gate/run-gate-gitcfg-$i" '2020-01-01 00:00:00 UTC'
  done
  bin=$ROOT/$fixture_name/bin
  mkdir -p "$bin"
  real_find=$(command -v find)
  cat > "$bin/find" <<SH
#!/usr/bin/env bash
printf x >> "$ROOT/$fixture_name/find-calls"
exec $real_find "\$@"
SH
  chmod +x "$bin/find"
  TEST_PATH=$bin:$PATH run_clean --repo "$R"
  expect_rc 0 "$count-entry structural find run"
  said "$(summary_line "$count" 0 0 0)" "$count entries are eligible"
  CALL_COUNT=$(wc -c < "$ROOT/$fixture_name/find-calls")
}
find_count_for 50 find-count-small
small_find_calls=$CALL_COUNT
find_count_for 500 find-count-large
large_find_calls=$CALL_COUNT
[[ $small_find_calls == "$large_find_calls" ]] ||
  fail "find invocations grew with entries: 50 entries=$small_find_calls, 500 entries=$large_find_calls"
((small_find_calls > 0)) || fail 'find shim did not record any invocations'

echo '--- inherited Git environment cannot redirect --repo'
fixture git-env-a
printf 'a\n' > "$R/file"
git -C "$R" add file
git -C "$R" -c user.name=test -c user.email=test@example.invalid commit -qm initial
git -C "$R" worktree add -q "$ROOT/git-env-a/linked" -b linked-a
repo_a=$R
git -C "$repo_a" worktree list > "$ROOT/git-env-before"
fixture git-env-b
make_old_entry "$R/.run-gate/run-gate-gitcfg-b" '2020-01-01 00:00:00 UTC'
EXTRA_ENV=(
  "GIT_DIR=$repo_a/.git"
  "GIT_WORK_TREE=$repo_a"
  "GIT_INDEX_FILE=$repo_a/.git/index"
  "GIT_COMMON_DIR=$repo_a/.git"
  "GIT_NAMESPACE=foreign"
)
run_clean --repo "$R"
EXTRA_ENV=()
expect_rc 0 'Git environment is sanitized'
said "target=$R age=48h" 'the requested repository is reported'
said 'worktrees: 1' 'the report comes from --repo B, not foreign GIT_DIR repository A'
not_said 'run-gate-gitcfg-a' 'foreign repository candidates are never read'
git -C "$repo_a" worktree list > "$ROOT/git-env-after"
cmp -s "$ROOT/git-env-before" "$ROOT/git-env-after" ||
  fail 'foreign repository worktree state changed'

echo '--- NUL-safe reports preserve newline filenames'
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

echo '--- dry reports, sizes, examples, timeouts and suite registration'
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
grep -Eq 'run-gate-gitcfg-\*.*eligible 4 of 5; active-by-mtime 1.* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'gitcfg count and bytes'
grep -Eq 'run-gate-test-bin-\*.*eligible 2 of 2; active-by-mtime 0.* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'test-bin count and bytes'
grep -Eq 'run-gate-test-invoke\*.*eligible 1 of 1; active-by-mtime 0.* [1-9][0-9]* bytes' "$ROOT/out" ||
  fail 'test-invoke count and bytes'
grep -Eq 'assay-liveness-\*.*eligible 1 of 1; active-by-mtime 0.* [1-9][0-9]* bytes' "$ROOT/out" ||
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
expect_rc 1 'report-only size timeout is still an inspection failure'
grep -Eq 'artifacts.*\(size not computed: timed out\)' "$ROOT/out" || fail 'artifacts timeout label'
grep -Eq '.worktrees.*\(size not computed: timed out\)' "$ROOT/out" || fail '.worktrees timeout label'
said "$(summary_line 0 0 0 2)" 'both timed-out audit reads are counted'

SUITE=$SCRIPT_DIR/run-suite.sh
README=$SCRIPT_DIR/../README.md
grep -qF "\"\$SCRIPT_DIR/../ctl-clean.sh\"" "$SUITE" || fail 'suite syntax list has ctl-clean'
grep -qF "\"\$SCRIPT_DIR/test-ctl-clean.sh\"" "$SUITE" || fail 'suite syntax list has test-ctl-clean'
grep -qF "bash \"\$SCRIPT_DIR/test-ctl-clean.sh\" || exit \$?" "$SUITE" || fail 'suite executes test-ctl-clean'
grep -qF 'A hostile or concurrent replacement of' "$README" ||
  fail 'README documents the post-start symlink limit'
grep -qF 'a process in another PID' "$README" ||
  fail 'README documents the other-PID-namespace limit'
grep -qF 'lowers only the lane-budget floor' "$README" ||
  fail 'README documents --i-know-the-age scope'
grep -qF 'never prunes Git worktree registrations' "$README" ||
  fail 'README documents the worktree-prune hazard'

echo 'PASS ctl-clean'
