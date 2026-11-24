#!/usr/bin/env bash
set -uo pipefail
# resume-read.sh against a FAKE tree (state directory, programme directory, git repository): exact
# slices per role, caps with a visible truncation marker, MISSING headers, the successor-newer rule,
# exit 2 for an unknown role, and that nothing is written. No real controller state is read.
while IFS= read -r name; do unset "$name"; done < <(compgen -v | grep -E '^RESUME_READ_' | grep -vx RESUME_READ_SCRIPT)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT=${RESUME_READ_SCRIPT:-$SCRIPT_DIR/../resume-read.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-resume-read-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

STATE=$ROOT/state
PROG=$STATE/PROGRAM-2026-11-24
mkdir -p "$PROG" "$STATE/platform" "$STATE/core" "$STATE/handovers" "$ROOT/repo"

# --- fixtures -----------------------------------------------------------------------------------
printf 'PRIME-MAIN-MARKER\nrules of the main controller\n' > "$PROG/PRIME-main-controller.md"
printf 'PRIME-PLATFORM-MARKER\n' > "$PROG/PRIME-platform.md"
printf 'PRIME-CORE-MARKER\n' > "$PROG/PRIME-core.md"
cat > "$STATE/HANDOFF.md" <<'MD'
# Handoff
## RESUME HERE #1 (old)
OLD-BLOCK-MARKER
(#1 09:00Z) OLD-UPDATE-MARKER
(#2 08:00Z) UPDATE-BEFORE-BLOCK
## RESUME HERE #2 (2026-11-24T10:00Z, main controller)
FIRST ACTION: NEW-BLOCK-FIRST-ACTION
BODY-LINE-TWO
(#2 10:00Z) UPDATE-INSIDE-BLOCK
## Later section
LATER-SECTION-MARKER
(#2 11:00Z) UPDATE-AFTER-BLOCK
(#20 09:00Z) NOT-MINE-NUMBER-TWENTY
(#3 12:00Z) OTHER-NUMBER-UPDATE
MD
{
  printf '# Programme\n## 0. Why\nZERO-BODY\n## 1. Binding\nONE-BODY\n## 2. Process\nTWO-BODY\n## 3. Topics\nTHREE-BODY\n## 4. Sub-controllers\nFOUR-BODY\n'
  printf '## 8. State log (newest last)\n'
  for n in $(seq 1 60); do printf 'S8-LINE-%d\n' "$n"; done
  printf '## 9. After\nNINE-MARKER\n'
} > "$PROG/PROGRAM.md"
printf '## A first\nA-BODY\n## B second\nB-BODY\n## C third\nC-BODY\n## D fourth\nD-BODY\n' > "$STATE/operator-decisions.md"
printf 'STATE-PLATFORM-MARKER\n' > "$STATE/platform/STATE.md"
printf 'STATE-CORE-MARKER\n' > "$STATE/core/STATE.md"
cat > "$STATE/platform/HANDOFF.md" <<'MD'
## RESUME HERE #11 (old)
PLAT-OLD-BLOCK
## RESUME HERE #12 (new)
PLAT-NEW-BLOCK
(#12 17:00Z) PLAT-UPDATE-LINE
MD
printf '## RESUME HERE #1\nCORE-HANDOFF-MARKER\n' > "$STATE/core/HANDOFF.md"
printf 'SUCCESSOR-PLATFORM-MARKER\n' > "$STATE/handovers/vbpub-platform-successor.md"
printf 'SUCCESSOR-CORE-MARKER\n' > "$STATE/handovers/vbpub-core-successor.md"
touch -d '2 hours ago' "$STATE/platform/STATE.md"   # platform successor is newer
touch -d '1 hour ago' "$STATE/handovers/vbpub-platform-successor.md"
touch -d '3 hours ago' "$STATE/handovers/vbpub-core-successor.md"   # core successor is older than its STATE.md
git init -q "$ROOT/repo"
for n in $(seq 1 10); do
  git -C "$ROOT/repo" -c user.email=t@example.invalid -c user.name=t -c commit.gpgsign=false \
    commit -q --allow-empty -m "commit-subject-$n" || fail 'could not create a fixture commit'
done
git -C "$ROOT/repo" update-ref refs/remotes/origin/main HEAD
git -C "$ROOT/repo" worktree add -q --detach "$ROOT/repo-wt" HEAD || fail 'could not create a fixture worktree'

tree_sum() { (cd "$ROOT" && find state repo -path repo/.git -prune -o -type f -print | sort | xargs md5sum | md5sum); }

rr() { # rr <role> [caps as VAR=val ...]: output in $ROOT/out, stderr in $ROOT/err, status in $RC
  local role=$1
  shift
  env RESUME_READ_STATE="$STATE" RESUME_READ_REPO="$ROOT/repo" "$@" bash "$SCRIPT" "$role" > "$ROOT/out" 2> "$ROOT/err"
  RC=$?
}

# --- 1. main: exact slices ------------------------------------------------------------------------
rr main
[[ $RC == 0 ]] || fail "main exited $RC"
O=$ROOT/out
contains "$O" "== $PROG/PRIME-main-controller.md [PRIME] (" 'the PRIME slice has its header'
contains "$O" 'PRIME-MAIN-MARKER' 'the PRIME file is printed'
contains "$O" "== $STATE/HANDOFF.md [newest RESUME HERE #2 block, lines 6-9] (" 'the newest block header names the number and lines'
contains "$O" 'NEW-BLOCK-FIRST-ACTION' 'the newest block is printed'
contains "$O" 'BODY-LINE-TWO' 'the whole newest block is printed'
contains "$O" 'UPDATE-INSIDE-BLOCK' 'an update line inside the block stays in the block'
not_contains "$O" 'OLD-BLOCK-MARKER' 'an older block is not printed'
not_contains "$O" 'LATER-SECTION-MARKER' 'the block stops at the next ## section'
contains "$O" "[update lines (#2 ...)]" 'the update lines have their own header'
contains "$O" 'UPDATE-AFTER-BLOCK' 'an update line after the block is printed'
contains "$O" 'UPDATE-BEFORE-BLOCK' 'an update line before the block is printed'
not_contains "$O" 'OLD-UPDATE-MARKER' 'an update line of another number is not printed'
not_contains "$O" 'NOT-MINE-NUMBER-TWENTY' '(#20 is not (#2'
not_contains "$O" 'OTHER-NUMBER-UPDATE' '(#3 is not (#2'
[[ $(grep -c 'UPDATE-INSIDE-BLOCK' "$O") == 1 ]] || fail 'an update line inside the block was printed twice'
contains "$O" 'S8-LINE-60' 'section 8 tail: the last line'
contains "$O" 'S8-LINE-21' 'section 8 tail: the 40th line from the end'
not_contains "$O" 'S8-LINE-20' 'section 8 tail: only 40 lines'
not_contains "$O" 'NINE-MARKER' 'section 8 ends at section 9'
contains "$O" '[section 8, last 40 lines]' 'the section 8 slice has its header'
contains "$O" 'B-BODY' 'operator-decisions: the 3rd section from the end'
contains "$O" 'D-BODY' 'operator-decisions: the last section'
not_contains "$O" 'A-BODY' 'operator-decisions: only the last 3 sections'
contains "$O" '[git log --oneline -8 origin/main]' 'the git slice has its header'
contains "$O" 'commit-subject-10' 'git log: the newest commit'
contains "$O" 'commit-subject-3' 'git log: the 8th commit'
not_contains "$O" 'commit-subject-2' 'git log: only 8 commits'
python3 - "$O" <<'PY' || fail 'the worktree count slice is not 2'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
assert re.search(r"\[worktree count \(git worktree list\)\] \(\d+ B, cap 16 B\) ==\n2\n", text), text[-300:]
PY
not_contains "$O" 'STATE-PLATFORM-MARKER' 'main does not print a sub-controller state'
# every header has the documented shape
bad=$(grep '^== ' "$O" | grep -vE '^== .+ \[.+\] (\([0-9]+ B, cap [0-9]+ B\)|MISSING|SKIPPED.*|none) ==$' || true)
[[ -z $bad ]] || fail "a header does not match the documented shape: $bad"
[[ -s $ROOT/err ]] && fail "main wrote to stderr: $(cat "$ROOT/err")"

# --- 2. topic roles --------------------------------------------------------------------------------
rr platform
[[ $RC == 0 ]] || fail "platform exited $RC"
contains "$O" 'STATE-PLATFORM-MARKER' 'platform: STATE.md'
contains "$O" 'PLAT-NEW-BLOCK' 'platform: the newest RESUME block of its HANDOFF.md'
not_contains "$O" 'PLAT-OLD-BLOCK' 'platform: an older RESUME block is not printed'
contains "$O" 'PLAT-UPDATE-LINE' 'platform: its update lines'
contains "$O" 'SUCCESSOR-PLATFORM-MARKER' 'platform: a successor handover newer than STATE.md is printed'
contains "$O" 'PRIME-PLATFORM-MARKER' 'platform: PRIME-platform.md'
contains "$O" '## 1. Binding' 'platform: PROGRAM.md section 1 heading'
contains "$O" '## 3. Topics' 'platform: PROGRAM.md section 3 heading'
not_contains "$O" 'ONE-BODY' 'platform: headings only, no section body'
not_contains "$O" '## 4. Sub-controllers' 'platform: section 4 is not a heading slice'
not_contains "$O" '## 0. Why' 'platform: section 0 is not a heading slice'
contains "$O" 'S8-LINE-60' 'platform: the section 8 tail'
not_contains "$O" 'S8-LINE-20' 'platform: only 40 lines of section 8'
not_contains "$O" 'PRIME-MAIN-MARKER' 'platform does not print the main PRIME'
not_contains "$O" 'D-BODY' 'platform does not print operator-decisions.md'
rr core
contains "$O" 'STATE-CORE-MARKER' 'core: STATE.md'
not_contains "$O" 'CORE-HANDOFF-MARKER' 'core has no HANDOFF slice (only platform and assay do)'
not_contains "$O" 'SUCCESSOR-CORE-MARKER' 'core: a successor older than STATE.md is not printed'
contains "$O" 'vbpub-core-successor.md [successor handover] SKIPPED (not newer than STATE.md)' 'core: the skipped successor says so'
contains "$O" 'PRIME-CORE-MARKER' 'core: PRIME-core.md'
rr assay
[[ $RC == 0 ]] || fail "assay with no files exited $RC"
contains "$O" "$STATE/assay/STATE.md [STATE] MISSING ==" 'assay: a missing STATE.md prints a MISSING header'
contains "$O" "$STATE/assay/HANDOFF.md [newest RESUME HERE block] MISSING ==" 'assay: a missing HANDOFF.md prints a MISSING header'
contains "$O" "vbpub-assay-successor.md [successor handover] MISSING ==" 'assay: a missing successor prints a MISSING header'
contains "$O" "PRIME-assay.md [PRIME] MISSING ==" 'assay: a missing PRIME prints a MISSING header'
contains "$O" 'S8-LINE-60' 'assay: the rest is still read after missing files'
# a successor with no STATE.md at all counts as newer
printf 'SUCCESSOR-EDGE-MARKER\n' > "$STATE/handovers/vbpub-edge-successor.md"
rr edge
contains "$O" 'SUCCESSOR-EDGE-MARKER' 'edge: a successor with no STATE.md is printed'
contains "$O" "edge/STATE.md [STATE] MISSING ==" 'edge: the missing STATE.md is named'
rm -f -- "$STATE/handovers/vbpub-edge-successor.md"

# --- 3. missing everything --------------------------------------------------------------------------
EMPTY=$ROOT/empty-state
mkdir -p "$EMPTY"
env RESUME_READ_STATE="$EMPTY" RESUME_READ_REPO="$ROOT/repo" bash "$SCRIPT" main > "$O" 2> "$ROOT/err" \
  || fail 'main with no state files did not exit 0'
for slice in 'PRIME' 'newest RESUME HERE block' 'section 8 tail' "last 3 sections"; do
  grep -qE "\[$slice.*\] MISSING ==" "$O" || fail "main: no MISSING header for $slice"
done
contains "$O" 'commit-subject-10' 'main: the git slice is still printed when the state files are missing'
# a handover file without any RESUME block is MISSING, not an error
printf 'no block here\n' > "$EMPTY/HANDOFF.md"
env RESUME_READ_STATE="$EMPTY" RESUME_READ_REPO="$ROOT/repo" bash "$SCRIPT" main > "$O" 2> "$ROOT/err"
contains "$O" "no '## RESUME HERE #N' heading)] MISSING ==" 'a HANDOFF.md without a RESUME block says so'

# --- 4. caps and the truncation marker --------------------------------------------------------------
{ printf 'HEADPART-'; printf 'x%.0s' $(seq 1 150); printf '_TAILPART\n'; } > "$PROG/PRIME-main-controller.md"
size=$(wc -c < "$PROG/PRIME-main-controller.md" | tr -d ' ')
rr main RESUME_READ_CAP_PRIME=50
contains "$O" "[PRIME] ($size B, cap 50 B) ==" 'the header carries the real size and the cap'
contains "$O" 'HEADPART' 'a head slice keeps its start'
not_contains "$O" 'TAILPART' 'a head slice is cut at the cap'
contains "$O" "[... TRUNCATED: $((size - 50)) B omitted after the first 50 B (cap)" 'the cut is marked visibly'
rr main RESUME_READ_CAP_SECTION8=60
contains "$O" 'S8-LINE-60' 'a tail slice keeps its newest end'
not_contains "$O" 'S8-LINE-21' 'a tail slice is cut at the cap'
contains "$O" 'TRUNCATED: the first' 'the tail cut is marked visibly'
rr main RESUME_READ_CAP_BLOCK=40 RESUME_READ_CAP_UPDATES=30
contains "$O" "RESUME HERE #2 block" 'a capped block keeps its header'
contains "$O" 'TRUNCATED' 'a capped block is marked'
rr main RESUME_READ_CAP_PRIME=100000
[[ $(grep -c 'TAILPART' "$O") == 1 ]] || fail 'a slice under its cap is not printed whole'

# --- 5. programme directory override, exit 2, read-only ----------------------------------------------
mkdir -p "$STATE/PROGRAM-OTHER"
printf 'OTHER-PRIME\n' > "$STATE/PROGRAM-OTHER/PRIME-main-controller.md"
before=$(tree_sum)   # no fixture changes from here on: everything below must leave the trees as they are
rr main RESUME_READ_PROGRAM=PROGRAM-OTHER
contains "$O" 'OTHER-PRIME' 'RESUME_READ_PROGRAM selects the programme directory'
for role in '' bogus Main platform/../core ../x 'platform core' 'core assay'; do
  env RESUME_READ_STATE="$STATE" RESUME_READ_REPO="$ROOT/repo" bash "$SCRIPT" ${role:+"$role"} > "$O" 2> "$ROOT/err"
  rc=$?
  [[ $rc == 2 ]] || fail "role '$role' exited $rc, not 2"
  [[ ! -s $O ]] || fail "role '$role' printed on stdout"
  contains "$ROOT/err" 'usage: resume-read.sh <main|platform|core|assay|hostobs|edge|rollback>' "role '$role' prints the usage"
done
[[ $(tree_sum) == "$before" ]] || fail 'resume-read.sh changed a file of the state or the repository'
! grep -qE '\b(curl|wget|ssh|nc|git +(fetch|pull|push))\b' "$SCRIPT" || fail 'resume-read.sh must stay offline'

# --- 6. the output budget (M3): the Bash tool persists anything above about 30,000 characters, so the
# default caps of every role must sum within RESUME_READ_TOTAL (24000 B) and a real read must stay
# under it. The numbers are read from the script's own defaults, not from a copy here.
defaults() { # defaults <SLICE>...: the DEFAULT of each cap, parsed from the script's own text
  # (sourcing the script would run a read, and a copy of the numbers here would drift silently)
  local name
  for name in "$@"; do
    awk -v n="$name" 'index($0, n "=${RESUME_READ_" n ":-") == 1 { sub(/^.*:-/, ""); sub(/\}.*/, ""); print; exit }' "$SCRIPT"
  done
}
caps_main="$(defaults CAP_PRIME CAP_BLOCK CAP_UPDATES CAP_SECTION8 CAP_DECISIONS CAP_GIT)"
caps_topic="$(defaults CAP_STATE CAP_BLOCK CAP_UPDATES CAP_SUCCESSOR CAP_PRIME CAP_HEADINGS CAP_SECTION8)"
caps_plain="$(defaults CAP_STATE CAP_SUCCESSOR CAP_PRIME CAP_HEADINGS CAP_SECTION8)"
TOTAL=$(defaults TOTAL)
[[ $TOTAL =~ ^[0-9]+$ ]] || fail "RESUME_READ_TOTAL is not a number: $TOTAL"
(( TOTAL > 0 )) || fail "RESUME_READ_TOTAL is not positive: $TOTAL"
(( TOTAL < 26000 )) || fail "the output budget must stay under the 30,000-character inline limit: $TOTAL"
# (the three role shapes: main, the widest topic, and the plain topic without a HANDOFF slice)
budget_check() { # budget_check <role> <caps>
  local total
  total=$(awk '{ total += $1 } END { print total + 0 }' <<< "$2")
  [[ $total =~ ^[0-9]+$ ]] || fail "the $1 default caps are not numbers: $2"
  (( total <= TOTAL )) || fail "the $1 default caps sum to $total B, over the $TOTAL B budget: $2"
}
budget_check main "$caps_main"
budget_check topic "$caps_topic"
budget_check plain "$caps_plain"
# a budget below the requested caps scales them down (proportionally, in whole bytes)
rr main RESUME_READ_TOTAL=100 RESUME_READ_CAP_PRIME=1000
premain=$(grep -E '^== .*\[PRIME\] ' "$O" | sed -n -E 's/.*cap ([0-9]+) B\) ==$/\1/p')
[[ -n $premain ]] || fail 'no PRIME cap header in the scaled read'
(( premain <= 100 )) || fail "RESUME_READ_TOTAL did not scale the PRIME cap: $premain > 100"
# RESUME_READ_TOTAL=0 keeps the caps as written (no scaling)
rr main RESUME_READ_TOTAL=0 RESUME_READ_CAP_PRIME=50
contains "$O" 'cap 50 B' 'RESUME_READ_TOTAL=0 disables the scaling'
# a full read with the defaults stays under the budget, for main and for the widest topic role
rr main
[[ $RC == 0 ]] || fail 'main failed'
bytes=$(wc -c < "$O")
[[ ${bytes//[[:space:]]/} -le 26000 ]] || fail "the main read is $bytes B, over the output budget"
{ printf '## Now\n'; printf 'state-line-%s-aaaaaaaaaaaaaaaa\n' $(seq 1 1200); } > "$STATE/platform/STATE.md"
rr platform
[[ $RC == 0 ]] || fail 'platform failed on a large STATE.md'
bytes=$(wc -c < "$O")
[[ ${bytes//[[:space:]]/} -le 26000 ]] || fail "the platform read is $bytes B, over the output budget"
# the one-line STATE.md warning: it names the real size and the cap the owner has to fit
[[ $(grep -c 'WARN STATE.md is' "$O") == 1 ]] || fail 'an oversized STATE.md does not get exactly one WARN line'
grep -E '^WARN STATE\.md is [0-9]+ B, cap [0-9]+ B: shorten it' "$O" > /dev/null \
  || fail "the STATE.md WARN line does not carry the size, the cap and the instruction: $(grep 'WARN STATE' "$O")"
{ printf 'short\n'; } > "$STATE/platform/STATE.md"
rr platform
not_contains "$O" 'WARN STATE.md is' 'a STATE.md under its cap is not warned about'

echo 'PASS resume-read: slices per role, caps, MISSING, successor-newer rule, exit 2, read-only'
