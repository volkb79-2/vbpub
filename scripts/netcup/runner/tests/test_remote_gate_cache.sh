#!/usr/bin/env bash
# CTL-12: the repository-side remote-gate cache. The defect it closes is a shared
# staging name (`<cache>.new`) that two concurrent launches wrote into, so one
# `mv` failed and the failure was reported as "cannot read the tool from
# origin/main". These checks are the acceptance test in CTL-12:
#   * N concurrent starts all complete and report the SAME tool identity, and no
#     invocation ever moves another's staging file;
#   * a held staging file cannot make another invocation fail, and a held LOCK
#     makes one refuse BY NAME as a refresh failure (never as absence);
#   * a ref that does not contain the tool produces the OTHER refusal;
#   * an unresolvable ref is its own refusal.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${REMOTE_GATE_CACHE_SCRIPT:-$SCRIPT_DIR/../remote-gate-cache.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/remote-gate-cache-test.XXXXXX")
cleanup() {
  [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

REPO=$ROOT/repo
CACHE=$ROOT/cache
mkdir -p "$REPO/scripts/netcup/runner" "$CACHE"
git -C "$REPO" init -q
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
printf '#!/usr/bin/env python3\nprint("remote-gate fixture one")\n' > "$REPO/scripts/netcup/runner/remote-gate.py"
git -C "$REPO" add scripts/netcup/runner/remote-gate.py
git -C "$REPO" commit -qm 'tool v1' -m 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
git -C "$REPO" update-ref refs/remotes/origin/main "$(git -C "$REPO" rev-parse HEAD)"
v1_commit=$(git -C "$REPO" rev-parse HEAD)
v1_blob=$(git -C "$REPO" rev-parse "$v1_commit:scripts/netcup/runner/remote-gate.py")
git -C "$REPO" update-ref refs/remotes/old "$v1_commit"

# A second commit that CHANGES the tool, so identity is observable, and a
# branch that does not contain the tool path at all.
printf '#!/usr/bin/env python3\nprint("remote-gate fixture two")\n' > "$REPO/scripts/netcup/runner/remote-gate.py"
git -C "$REPO" commit -qam 'tool v2' -m 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
v2_commit=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" update-ref refs/remotes/origin/main "$v2_commit"
git -C "$REPO" switch -qc no-tool HEAD
git -C "$REPO" rm -q scripts/netcup/runner/remote-gate.py
git -C "$REPO" commit -qm 'no tool' -m 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
git -C "$REPO" switch -q -

# (1) One invocation publishes a copy and emits the identity of what it fetched.
"$SRC" origin/main --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/one.out" 2>&1 \
  || { cat "$ROOT/one.out" >&2; fail 'a plain cache refresh failed'; }
contains "$ROOT/one.out" "commit=$v2_commit" 'the refresh names the resolved commit'
contains "$ROOT/one.out" 'identity=blob:' 'the refresh names the fetched copy identity'

published=$(sed -n 's/^cache=//p' "$ROOT/one.out")
[[ -n $published && -s $published ]] || fail 'the refresh did not publish a readable copy'
grep -qF 'remote-gate fixture' "$published" || fail 'the published copy is not the tool source'
[[ -x $published ]] || fail 'the published copy is not executable'

# (1b) The publish is a RENAME, not an in-place rewrite: an observer that holds
# the previous copy must still see the previous bytes after the refresh, so no
# reader can ever observe a half-written tool file.
seeded=$CACHE/remote-gate-$v1_blob.py
mkdir -p "$CACHE"
printf 'PRE-SEEDED COPY\n' > "$seeded"
ln -f "$seeded" "$ROOT/anchor"
"$SRC" old --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/rename.out" 2>&1 \
  || { cat "$ROOT/rename.out" >&2; fail 'a refresh over a seeded copy failed'; }
grep -qF 'PRE-SEEDED COPY' "$ROOT/anchor" \
  || fail 'the published copy was rewritten in place instead of replaced by rename'
grep -qF 'remote-gate fixture one' "$seeded" \
  || fail 'the seeded copy was not replaced by the tool source'
rm -f -- "$ROOT/anchor"

# (2) N concurrent starts: every one succeeds, all report ONE identity, and none
# of them can see another's staging name. This is the defect itself.
rm -rf "$CACHE"
mkdir -p "$CACHE"
concurrent_pids=()
for index in {1..8}; do
  "$SRC" origin/main --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/concurrent-$index.out" 2>&1 &
  concurrent_pids+=("$!")
done
concurrent_failures=0
for pid in "${concurrent_pids[@]}"; do
  if ! wait "$pid"; then concurrent_failures=$((concurrent_failures + 1)); fi
done
(( concurrent_failures == 0 )) \
  || { cat "$ROOT"/concurrent-*.out >&2; fail "$concurrent_failures of 8 concurrent refreshes failed"; }
identities=$(sed -n 's/^identity=//p' "$ROOT"/concurrent-*.out | sort -u)
[[ $(printf '%s\n' "$identities" | wc -l) == 1 ]] \
  || fail "concurrent refreshes disagree about the tool identity: $identities"
[[ -z $(find "$CACHE" -maxdepth 1 -name '.remote-gate-*.stage.*' -print -quit) ]] \
  || fail 'a refresh left its staging file behind'
published_count=$(find "$CACHE" -maxdepth 1 -name 'remote-gate-*.py' -type f | wc -l)
[[ $published_count == 1 ]] \
  || fail "the cache holds $published_count published copies instead of one: $(find "$CACHE" -maxdepth 1 -type f -printf '%f ')"

# (3) A leftover staging file from some other invocation is invisible: it cannot
# make this invocation fail, and this invocation never touches it.
stray=$CACHE/.remote-gate-$v1_blob.stage.999.1
printf 'stale staging content\n' > "$stray"
"$SRC" origin/main --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/stray.out" 2>&1 \
  || { cat "$ROOT/stray.out" >&2; fail 'a leftover staging file made a refresh fail'; }
grep -qF 'stale staging content' "$stray" \
  || fail 'a refresh overwrote another invocation staging file'
rm -f -- "$stray"

# (4) A held LOCK makes one invocation refuse BY NAME as a refresh failure: the
# message must say "could not refresh", never "absent".
rm -rf "$CACHE"
mkdir -p "$CACHE"
python3 - "$CACHE/.refresh.lock" <<'PY' > "$ROOT/lock.out" 2>&1 &
import fcntl, sys, time
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
time.sleep(8)
PY
lock_holder=$!
for _ in {1..100}; do [[ -s $CACHE/.refresh.lock ]] && break; sleep 0.05; done
if REMOTE_GATE_CACHE_LOCK_WAIT=1 "$SRC" origin/main --repo "$REPO" --cache-dir "$CACHE" \
  > "$ROOT/locked.out" 2>&1; then
  fail 'a held refresh lock was ignored'
else
  rc=$?
  [[ $rc == 3 ]] || fail "a held refresh lock exited $rc, expected the refresh-failure code 3"
fi
contains "$ROOT/locked.out" 'could not refresh the cache' 'a held lock is a refresh failure'
not_contains "$ROOT/locked.out" 'absent from ref' 'a held lock is not reported as absence'
kill -- "$lock_holder" 2>/dev/null || true
wait "$lock_holder" 2>/dev/null || true

# (5) A ref that does not contain the tool is the OTHER refusal, by name.
if "$SRC" no-tool --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/absent.out" 2>&1; then
  fail 'a ref without the tool was accepted'
else
  rc=$?
  [[ $rc == 4 ]] || fail "an absent tool exited $rc, expected 4"
fi
contains "$ROOT/absent.out" 'the tool is absent from ref no-tool' 'an absent tool is named as absence'
not_contains "$ROOT/absent.out" 'could not refresh' 'absence is not reported as a refresh failure'

# (6) An unresolvable ref is its own refusal, not absence and not a refresh error.
if "$SRC" no-such-ref --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/unresolvable.out" 2>&1; then
  fail 'an unresolvable ref was accepted'
else
  rc=$?
  [[ $rc == 5 ]] || fail "an unresolvable ref exited $rc, expected 5"
fi
contains "$ROOT/unresolvable.out" 'cannot resolve ref no-such-ref' 'an unresolvable ref is named'

# (7) A ref that moves is a different identity, and both copies stay addressable.
"$SRC" old --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/old.out" 2>&1 \
  || { cat "$ROOT/old.out" >&2; fail 'a refresh of an older ref failed'; }
"$SRC" origin/main --repo "$REPO" --cache-dir "$CACHE" > "$ROOT/new.out" 2>&1 \
  || { cat "$ROOT/new.out" >&2; fail 'a refresh of the current ref failed'; }
old_identity=$(sed -n 's/^identity=//p' "$ROOT/old.out")
new_identity=$(sed -n 's/^identity=//p' "$ROOT/new.out")
[[ -n $old_identity && $old_identity != "$new_identity" ]] \
  || fail "two different tool revisions reported one identity: $old_identity / $new_identity"
[[ -s $(sed -n 's/^cache=//p' "$ROOT/old.out") ]] || fail 'the older revision was not published'
[[ -s $(sed -n 's/^cache=//p' "$ROOT/new.out") ]] || fail 'the newer revision is no longer readable'

echo 'PASS remote-gate-cache: unique staging, serialised refresh, two refusals, emitted identity'
