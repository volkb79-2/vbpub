#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLEAN=${CTL_CLEAN_SCRIPT:-$SCRIPT_DIR/../ctl-clean.sh}
BASH_BIN=${BASH:-$(command -v bash)}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-ctl-clean-test.XXXXXX")

cleanup() {
  local status=$?
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
  # $1 name; sets R to a disposable fake Git worktree root.
  R=$ROOT/$1/repo
  mkdir -p "$R/.run-gate"
  git init -q "$R" || fail "git init failed for $R"
}

run_clean() {
  env -i HOME="$ROOT/home" PATH="${TEST_PATH:-$PATH}" LC_ALL=C TZ=UTC \
    "$BASH_BIN" "$CLEAN" "$@" > "$ROOT/out" 2> "$ROOT/err"
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

make_old_entry() {
  # $1 entry; $2 touch -d expression; optional $3 file contents
  local entry=$1 when=$2 contents=${3:-scratch}
  mkdir -p "$entry"
  printf '%s\n' "$contents" > "$entry/file"
  touch -d "$when" "$entry/file" "$entry"
}

echo '--- finding 1: recursive newest mtime decides activity'
fixture activity
old=$R/.run-gate/run-gate-gitcfg-old
active=$R/.run-gate/run-gate-gitcfg-active
make_old_entry "$old" '2020-01-01 00:00:00 UTC'
mkdir -p "$active/nested"
printf 'old\n' > "$active/nested/file"
touch -d '2020-01-01 00:00:00 UTC' "$active/nested/file" "$active/nested" "$active"
printf 'active write\n' >> "$active/nested/file"
touch -d '2 hours ago' "$active/nested/file"
run_clean --apply --repo "$R"
expect_rc 0 'an old entry beside a nested-active entry'
[[ ! -e $old ]] || fail 'the wholly old entry must be removed'
[[ -e $active ]] || fail 'an old directory containing a recently touched nested file must be kept'
said 'eligible 1 of 2; older than 48h (newest file inside included)' 'the default 48h recursive threshold'
said 'removed 1, failed 0 scratch entries' 'only the actually removed entry is counted'

echo '--- finding 2: --age-hours is bounded, decimal and explicit'
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

echo '--- finding 3: --repo is a canonical worktree root and cannot look like an option'
fixture repo-validation
mkdir -p "$R/sub"
run_clean --repo -P
expect_rc 2 'dash-leading --repo'
said "must not start with '-'" 'a dash-leading repo is refused'
run_clean --repo --
expect_rc 2 '--repo --'
said "must not start with '-'" 'a literal -- repo is refused'
mkdir -p "$ROOT/repo-validation/not-a-repo"
run_clean --repo "$ROOT/repo-validation/not-a-repo"
expect_rc 2 'a non-repository --repo'
said 'is not inside a Git worktree' 'a non-repository is refused'
run_clean --repo "$R/sub"
expect_rc 2 'a repository subdirectory as --repo'
said 'must name the worktree root' 'a subdirectory is refused rather than cleaned'
ln -s "$R" "$ROOT/repo-validation/link"
run_clean --repo "$ROOT/repo-validation/link"
expect_rc 0 'a symlinked repository root'
said "target=$R age=48h" 'the canonical target path is printed before acting'

echo '--- finding 4: worktree pruning is explicit opt-in only'
run_clean --help
expect_rc 0 'ctl-clean help'
said 'only from the shared' 'the help text must carry the shared-checkout warning'
said 'checkout when no session is creating or moving worktrees' 'the help text must name the concurrency condition'
fixture worktree-prune
mkdir -p "$R/.git/worktrees/dangling"
printf 'gitdir: %s\n' "$ROOT/worktree-prune/missing/.git" > "$R/.git/worktrees/dangling/gitdir"
printf 'ref: refs/heads/main\n' > "$R/.git/worktrees/dangling/HEAD"
run_clean --apply --repo "$R"
expect_rc 0 'the default apply path'
[[ -d $R/.git/worktrees/dangling ]] || fail 'default --apply must not prune a dangling worktree entry'
not_said 'would prune:' 'the default dry report must not consider pruning'
run_clean --prune-worktrees --repo "$R"
expect_rc 0 'explicit prune dry-run'
said 'would prune:' '--prune-worktrees dry-run shows prune output'
[[ -d $R/.git/worktrees/dangling ]] || fail 'prune dry-run must not remove the entry'
run_clean --prune-worktrees --apply --repo "$R"
expect_rc 0 'explicit prune apply'
[[ ! -e $R/.git/worktrees/dangling ]] || fail '--prune-worktrees --apply must remove the dangling entry'

echo '--- finding 5: deletion failures propagate and missing priority tools fall back'
fixture deletion-failure
make_old_entry "$R/.run-gate/run-gate-gitcfg-stuck" '2020-01-01 00:00:00 UTC'
chmod 555 "$R/.run-gate"
run_clean --apply --repo "$R"
chmod 755 "$R/.run-gate"
expect_rc 1 'an undeletable entry'
[[ -e $R/.run-gate/run-gate-gitcfg-stuck ]] ||
  fail 'the undeletable entry should remain after the failed unlink'
said 'removed 0, failed 1 scratch entries' 'the failed deletion is counted'
said 'FAILED to remove' 'the failed path is named'

fixture priority-fallback
make_old_entry "$R/.run-gate/run-gate-gitcfg-no-priority" '2020-01-01 00:00:00 UTC'
MINIMAL_PATH=$ROOT/priority-fallback/tools
mkdir -p "$MINIMAL_PATH"
for tool in basename date du find git rm sed sort timeout xargs; do
  ln -s "$(command -v "$tool")" "$MINIMAL_PATH/$tool"
done
TEST_PATH=$MINIMAL_PATH run_clean --apply --repo "$R"
expect_rc 0 'apply without nice or ionice'
said 'nice/ionice unavailable; using plain xargs rm' 'the plain xargs rm fallback is announced'
[[ ! -e $R/.run-gate/run-gate-gitcfg-no-priority ]] ||
  fail 'the plain xargs rm fallback must still remove eligible entries'
said 'removed 1, failed 0 scratch entries' 'fallback deletion is counted as removed'

echo '--- finding 6: dry-run counts, sizes, examples and timeout labels'
fixture dry-report
printf '{}\n' > "$R/.run-gate/history.json"
for days in 40 30 20 10; do
  make_old_entry "$R/.run-gate/run-gate-gitcfg-$days" "$days days ago"
done
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
grep -Eq \
  'run-gate-gitcfg-\*.*eligible 4 of 5; older than 48h \(newest file inside included\); [1-9][0-9]* bytes' \
  "$ROOT/out" || {
  cat "$ROOT/out" >&2
  fail 'the gitcfg class must print eligible count, threshold wording and total bytes'
}
grep -Eq \
  'run-gate-test-bin-\*.*eligible 2 of 2; older than 48h \(newest file inside included\); [1-9][0-9]* bytes' \
  "$ROOT/out" || fail 'the test-bin class must print count and total bytes'
grep -Eq \
  'run-gate-test-invoke\*.*eligible 1 of 1; older than 48h \(newest file inside included\); [1-9][0-9]* bytes' \
  "$ROOT/out" || fail 'the test-invoke class must print count and total bytes'
grep -Eq \
  'assay-liveness-\*.*eligible 1 of 1; older than 48h \(newest file inside included\); [1-9][0-9]* bytes' \
  "$ROOT/out" || fail 'the assay-liveness class must print count and total bytes'
[[ $(grep -Ec '^    oldest: .*run-gate-gitcfg-' "$ROOT/out") == 3 ]] ||
  fail 'the report must show the three oldest gitcfg examples'
[[ $(grep -Ec '^    newest: .*run-gate-gitcfg-' "$ROOT/out") == 3 ]] ||
  fail 'the report must show the three newest gitcfg examples'
grep -Eq '^    oldest: [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z  ' "$ROOT/out" ||
  fail 'example paths must carry an ISO timestamp'
said 'selected 8 scratch entries; dry-run made no deletions' 'dry-run does not claim selected entries were removed'
[[ -e $R/.run-gate/history.json ]] || fail 'history.json must be kept'
[[ -e $nested_active ]] || fail 'the recursively active entry must be kept in the dry-run'
for path in "$R/.run-gate"/run-gate-gitcfg-40 "$R/.run-gate"/run-gate-gitcfg-30 \
            "$R/.run-gate"/run-gate-gitcfg-20 "$R/.run-gate"/run-gate-gitcfg-10 \
            "$R/.run-gate"/run-gate-test-bin-25 "$R/.run-gate"/run-gate-test-bin-15 \
            "$R/.run-gate"/run-gate-test-invoke-50 "$R/.run-gate"/assay-liveness-60; do
  [[ -e $path ]] || fail "dry-run deleted an eligible entry: $path"
done

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
expect_rc 0 'a report-only size timeout'
grep -Eq 'artifacts.*\(size not computed: timed out\)' "$ROOT/out" ||
  fail 'an artifacts size timeout must be labeled, not left blank'
grep -Eq '.worktrees.*\(size not computed: timed out\)' "$ROOT/out" ||
  fail 'a .worktrees size timeout must be labeled, not left blank'

echo '--- finding 7: controller suite checks and registers ctl-clean'
SUITE=$SCRIPT_DIR/run-suite.sh
grep -qF "\"\$SCRIPT_DIR/../ctl-clean.sh\"" "$SUITE" ||
  fail 'run-suite.sh must put ctl-clean.sh in its syntax/shellcheck list'
grep -qF "\"\$SCRIPT_DIR/test-ctl-clean.sh\"" "$SUITE" ||
  fail 'run-suite.sh must put test-ctl-clean.sh in its syntax/shellcheck list'
grep -qF "bash \"\$SCRIPT_DIR/test-ctl-clean.sh\" || exit \$?" "$SUITE" ||
  fail 'run-suite.sh must execute test-ctl-clean.sh'

echo 'PASS ctl-clean'
