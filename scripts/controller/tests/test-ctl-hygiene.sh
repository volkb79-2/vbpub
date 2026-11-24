#!/usr/bin/env bash
# Tests for ctl-hygiene.sh. Every tree is a fake one under a temporary root; "today" is pinned, so no result depends on the
# clock, the host or the real controller state.
set -uo pipefail
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
HYG=${CTL_HYGIENE_SCRIPT:-$SCRIPT_DIR/../ctl-hygiene.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-hygiene-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
TODAY=2026-11-24

new_tree() {
  # $1 name; sets T, M (memory dir), S (state dir), R (a git repository holding the tracked backlogs)
  T=$ROOT/$1; M=$T/memory; S=$T/state; R=$T/repo
  mkdir -p "$M" "$S/handovers" "$R" "$T/home"
  git -C "$R" -c init.defaultBranch=main init -q || fail 'git init'
}
hyg() {
  # hyg; sets RC, the report is $ROOT/out, stderr $ROOT/err
  env -i HOME="$T/home" PATH="$PATH" HYGIENE_MEMORY_DIR="$M" HYGIENE_STATE_DIR="$S" HYGIENE_REPO="$R" HYGIENE_TODAY="$TODAY" \
    bash "$HYG" > "$ROOT/out" 2> "$ROOT/err"
  RC=$?
  [[ $RC == 0 ]] || { cat "$ROOT/out" "$ROOT/err" >&2; fail "ctl-hygiene.sh must always exit 0, got $RC"; }
  [[ ! -s $ROOT/err ]] || { cat "$ROOT/err" >&2; fail 'ctl-hygiene.sh wrote to stderr'; }
}
has() { grep -qE -- "^HYGIENE $1" "$ROOT/out" || { cat "$ROOT/out" >&2; fail "$2 (missing /$1/)"; }; }
lacks() { ! grep -qE -- "^HYGIENE $1" "$ROOT/out" || { cat "$ROOT/out" >&2; fail "$2 (unexpected /$1/)"; }; }
silent() { [[ ! -s $ROOT/out ]] || { cat "$ROOT/out" >&2; fail "$1: wanted no findings"; }; }

# n_bytes N: N ASCII bytes ending in a newline (one line).
n_bytes() { printf '%*s' "$(( $1 - 1 ))" '' | tr ' ' a; printf '\n'; }
# n_lines N: N short lines.
n_lines() { local i; for (( i = 1; i <= $1; i++ )); do printf 'line %s\n' "$i"; done; }

# --- a quiet tree and a missing tree are both silent
new_tree quiet
{ n_lines 3; printf -- '- [x](x.md) fine, 2026-11-24 TEMPORARY\n'; } > "$M/MEMORY.md"; : > "$M/x.md"
printf '## RESUME HERE #1\n## RESUME HERE #2\n' > "$S/HANDOFF.md"
hyg; silent 'a tree inside every threshold'
new_tree missing; rmdir "$M" "$S/handovers"
hyg; silent 'absent files are skipped'

# --- M1 size and lines, at the threshold and one above
new_tree m1
n_bytes 12288 > "$M/MEMORY.md"; hyg; lacks 'M1' '12288 B is at the threshold'
n_bytes 12289 > "$M/MEMORY.md"; hyg; has "M1 $M/MEMORY.md: 12289 B \\(threshold 12288 B WARN\\)" '12289 B is one above'
n_lines 70 > "$M/MEMORY.md"; hyg; lacks 'M1' '70 lines is at the threshold'
n_lines 71 > "$M/MEMORY.md"; hyg; has "M1 $M/MEMORY.md: 71 lines \\(threshold 70 lines WARN\\)" '71 lines is one above'
n_lines 150 > "$M/MEMORY.md"; hyg; has 'M1 .*: 150 lines \(threshold 70 lines WARN\)' '150 lines is still only a WARN'; lacks 'M1 .*ALERT' '150 lines is no ALERT'
n_lines 151 > "$M/MEMORY.md"; hyg; has 'M1 .*: 151 lines \(threshold 150 lines ALERT' '151 lines is an ALERT'; lacks 'M1 .*70 lines WARN' 'an ALERT replaces the WARN line count'
printf 'one\ntwo' > "$M/MEMORY.md"; hyg; silent 'a last line without a newline counts as a line, not as a finding'

# --- M2 stale marker: 3 days is fine, 4 is not; undated; each keyword; first date wins; other casing and a future date are quiet
new_tree m2
cat > "$M/MEMORY.md" <<'MD'
- IN PROGRESS 2026-11-21 three days old
- IN PROGRESS 2026-11-20 four days old
- CURRENT 2026-11-05 old
- TEMPORARY until 2026-11-01 old, then 2026-11-24 fresh
- TEMPORARY with no date at all
- Current state heading in a normal casing 2026-01-01
- IN PROGRESS 2026-12-30 in the future
- CURRENT 2026-13-31 not a calendar date
MD
hyg
lacks 'M2 .*:1:' 'exactly 3 days old is not stale'
has "M2 $M/MEMORY.md:2: 2026-11-20 is 4 days old \\(threshold 3 days\\)" '4 days old is stale'
has "M2 $M/MEMORY.md:3: 2026-11-05 is 19 days old" 'CURRENT marker'
has "M2 $M/MEMORY.md:4: 2026-11-01 is 23 days old" 'the FIRST date on the line decides'
has "M2 $M/MEMORY.md:5: undated marker line" 'a marker line with no date is reported as undated'
lacks 'M2 .*:6:' 'a line in another casing is not a marker'
lacks 'M2 .*:7:' 'a future date is not stale'
has "M2 $M/MEMORY.md:8: undated marker line" 'an impossible date is undated, not a crash'

# --- M3 dead link
new_tree m3
printf -- '- [ok](here.md) [gone](gone.md) [site](https://example.invalid/page.md) [sub](dir/x.md)\n' > "$M/MEMORY.md"; : > "$M/here.md"
hyg
has "M3 $M/MEMORY.md:1: dead link gone.md" 'a missing target is reported'
lacks 'M3 .*here.md' 'an existing target is fine'
[[ $(grep -c '^HYGIENE M3' "$ROOT/out") == 1 ]] || { cat "$ROOT/out" >&2; fail 'only the bare-name .md target is checked'; }

# --- H1 blocks: 2 is fine, 3 is not; UPDATE blocks (edge) count and their (#N ...) lines do not; every file in the list
new_tree h1
blocks() { local i; for (( i = 1; i <= $1; i++ )); do printf '## RESUME HERE #%s (2026-11-24)\nbody (#%s later)\n' "$i" "$i"; done; }
blocks 2 > "$S/HANDOFF.md"; hyg; lacks 'H1' 'two blocks are at the threshold'
blocks 3 > "$S/HANDOFF.md"; hyg
has "H1 state/HANDOFF.md: 3 blocks \\(threshold 2; archive the older ones to state/archive/main-$TODAY/\\)" 'three blocks in the root handoff'
mkdir -p "$S/platform" "$S/edge" "$S/assay" "$S/core" "$S/hostobs" "$S/rollback"
blocks 2 > "$S/platform/HANDOFF.md"; printf '## UPDATE 2026-11-24 a\n(#1 x)\n## UPDATE 2026-11-24 b\n(#2 x)\n## UPDATE 2026-11-24 c\n' > "$S/edge/HANDOVER.md"
blocks 5 > "$S/assay/HANDOFF.md"
printf '## RESUME HERE\nno number\n## RESUME HERE #1\n## UPDATE x\n' > "$S/core/STATE.md"
blocks 3 > "$S/hostobs/STATE.md"; blocks 4 > "$S/platform/STATE.md"; blocks 3 > "$S/rollback/STATE.md"; blocks 3 > "$S/edge/STATE.md"; blocks 3 > "$S/assay/STATE.md"
hyg
lacks 'H1 state/platform/HANDOFF.md' 'platform handoff with two blocks'
has 'H1 state/edge/HANDOVER.md: 3 blocks \(threshold 2; archive the older ones to state/archive/edge-' 'three UPDATE blocks in edge/HANDOVER.md'
has 'H1 state/assay/HANDOFF.md: 5 blocks' 'assay handoff'
lacks 'H1 state/core/STATE.md' 'a RESUME HERE line with no #N does not start a block'
for f in hostobs/STATE.md platform/STATE.md rollback/STATE.md edge/STATE.md assay/STATE.md; do has "H1 state/$f: " "STATE file $f"; done

# --- H2 size
new_tree h2
n_bytes 24576 > "$S/HANDOFF.md"; hyg; lacks 'H2' '24576 B is at the threshold'
n_bytes 24577 > "$S/HANDOFF.md"; hyg; has 'H2 state/HANDOFF.md: 24577 B \(threshold 24576 B\)' '24577 B is one above'
n_bytes 24577 > "$S/stray.md"; rm "$S/HANDOFF.md"; hyg; silent 'only the listed files are measured'

# --- S1 stale successor
new_tree s1
mkdir -p "$S/platform" "$S/assay"
: > "$S/platform/STATE.md"; : > "$S/handovers/vbpub-platform-successor.md"
touch -d @1700000000 "$S/handovers/vbpub-platform-successor.md"; touch -d @$((1700000000 + 86400)) "$S/platform/STATE.md"
hyg; lacks 'S1' 'a successor exactly 24 h behind STATE.md is at the threshold'
touch -d @$((1700000000 + 86401)) "$S/platform/STATE.md"
hyg; has 'S1 state/handovers/vbpub-platform-successor.md: 24.0 h older than state/platform/STATE.md \(threshold 24 h\)' '24 h plus 1 s is stale'
touch -d @$((1700000000 + 3 * 86400)) "$S/platform/STATE.md"; touch -d @$((1700000000 + 3 * 86400)) "$S/handovers/vbpub-platform-successor.md"
hyg; lacks 'S1' 'a refreshed successor is quiet'
: > "$S/handovers/vbpub-assay-successor.md"; hyg; lacks 'S1' 'a successor with no STATE.md is skipped'
: > "$S/handovers/vbpub-03-successor.md"; : > "$S/handovers/notes.md"; mkdir "$S/handovers/sub"
hyg
has 'S1 state/handovers/vbpub-03-successor.md: not-a-programme-controller' 'a numbered successor is not a programme controller'
has 'S1 state/handovers/notes.md: not-a-programme-controller' 'any other file is reported'
[[ $(grep -c '^HYGIENE S1' "$ROOT/out") == 2 ]] || { cat "$ROOT/out" >&2; fail 'a directory and the programme names are not reported'; }

# --- B1 closed backlog. entry <heading> <status line or ''> <total bytes>: an entry of exactly that size.
entry() {
  local LC_ALL=C body pad   # byte lengths, whatever the locale
  body=$1$'\n'
  [[ -z $2 ]] || body+=$2$'\n'
  pad=$(( $3 - ${#body} - 1 ))
  (( pad >= 0 )) || fail "entry too small for its text: $1"
  printf '%s' "$body"; printf '%*s' "$pad" '' | tr ' ' x; printf '\n'
}
track() { git -C "$R" add -- "$1" || fail "git add $1"; }
new_tree b1
mkdir -p "$R/tool"; BL=tool/KNOWN_ISSUES_TODO_BACKLOG.md
{ entry '## CTL-01 — closed' 'Status: DONE' 300; entry '## CTL-02 — open' 'Status: OPEN' 700; } > "$R/$BL"; track "$BL"
hyg; lacks 'B1' '30% closed is at the threshold'
{ entry '## CTL-01 — closed' 'Status: DONE' 301; entry '## CTL-02 — open' 'Status: OPEN' 699; } > "$R/$BL"
hyg; has "B1 $BL: 30% of 1000 B in 1 closed of 2 entries \\(threshold 30%\\)" '30.1% closed is one byte above'
# every closed word, in the Status shapes of the real backlogs; the first Status line decides; no Status line is open
{ i=0; for s in DONE FIXED CLOSED RESOLVED IMPLEMENTED SUPERSEDED WONTFIX; do i=$((i + 1)); entry "## CTL-$i — x" "**Status:** $s by CTL-37 (2026-11-24)" 100; done
  entry '## CTL-20 — first status wins' $'Status: OPEN\nStatus: FIXED' 600; entry '## CTL-21 — no status line' '' 600; } > "$R/$BL"
hyg; has "B1 $BL: 36% of 1900 B in 7 closed of 9 entries" 'all seven closed words count, an OPEN-first and a Status-less entry do not'
{ entry '## CTL-01 — a' '**Status: FIXED (v1)**' 100; entry '## CTL-02 — b' '**Status:** **WONTFIX**' 100; entry '## CTL-03 — c' '  Status OPEN' 100; entry '## CTL-04 — d' 'Status: SUPERSEDED-BY-X' 100; } > "$R/$BL"
hyg; has "B1 $BL: 50% of 400 B in 2 closed of 4 entries" 'bold Status shapes close; a colon-less OPEN and a value that is not exactly a closed word stay open'
# an entry runs to the next entry heading: sub-headings and a table row inside it are not entries
{ entry '## CTL-01 — closed' 'Status: DONE' 300; printf '### Why this\ntext\n## Open items at a glance (audited 2026-09-23)\n  - {id: B119, title: "x", type: feature}\n'; entry '## CTL-02 — open' '' 100; } > "$R/$BL"
hyg; has "B1 $BL: .* in 1 closed of 2 entries" 'sub-headings without an ID stay inside the entry they follow'
# a file that is not tracked, or not a backlog name, is not looked at
new_tree b1-untracked; mkdir -p "$R/tool"
entry '## CTL-01 — closed' 'Status: DONE' 300 > "$R/tool/KNOWN_ISSUES_TODO_BACKLOG.md"
entry '## CTL-01 — closed' 'Status: DONE' 300 > "$R/tool/NOTES.md"; track tool/NOTES.md
hyg; silent 'an untracked backlog and a tracked non-backlog are skipped'

# --- B1, R2-4: the heading shapes of the real assay and iobench backlogs
new_tree b1-real; mkdir -p "$R/assay/nyxloom-trove" "$R/libraries/iobench"
{ printf '# Assay backlog\n\n## Open items at a glance (audited 2026-09-23)\n\n  - {id: B119, title: "B110 P9: mutation evidence", type: feature, component: mutation}\n\n'
  entry '## B001 — a SQL/DDL adapter, and why PostgreSQL projects are the cheapest place to prove R2/R3' '**Status: DONE (v2.1.0, 2026-08-18) — merge ccf9ca55 "wave 3 -- P34/B001 source-oriented SQL/DDL adapter", on main.**' 2000
  printf '### Assay-owner disposition — authoritative over the original proposal below\n\n'
  entry '## B002 — still being carved' '**Status:** **CARVED and REVIEWED, wave 3, 2026-08-18 — implementation in' 1000; } > "$R/assay/nyxloom-trove/4-backlog.md"
{ entry '## IOB-BL-40 — 2026-11-08 fill-rate figures are invalid (FIXED, IB-FILL)' '' 1000
  entry '## IOB-BL-41 — Live acceptance of the IBX raw-partition safety boundary' '' 500; } > "$R/libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md"
track assay/nyxloom-trove/4-backlog.md; track libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md
hyg
has 'B1 assay/nyxloom-trove/4-backlog.md: [0-9]+% of [0-9]+ B in 1 closed of 2 entries' 'a B001-style heading is an entry (the old pattern saw none)'
has 'B1 libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md: 66% of 1500 B in 1 closed of 2 entries' 'a status word in the heading parentheses closes an IOB-BL-style entry'
# a heading with no digit in its ID is not an entry; a heading with an ID and only a lowercase parenthesis is not closed
new_tree b1-ids; mkdir -p "$R/t"
{ entry '## README — what this is' 'Status: DONE' 500; entry '## CIU-9 — open (see fixed notes)' '' 500; entry '## CTL-5 — DONE claim was premature' 'Status: OPEN' 500; } > "$R/t/KNOWN_ISSUES_TODO_BACKLOG.md"; track t/KNOWN_ISSUES_TODO_BACKLOG.md
hyg; silent 'only an ID with a digit makes an entry; only a closed word INSIDE the heading parentheses closes one'
# a file with Status lines and no parsable entry is reported as unparsed; one with neither is silent
new_tree b1-unparsed; mkdir -p "$R/u" "$R/v"
printf '## Intro\nStatus: OPEN\nStatus: DONE\n' > "$R/u/KNOWN_ISSUES_TODO_BACKLOG.md"; printf '## Intro\nnothing\n' > "$R/v/KNOWN_ISSUES_TODO_BACKLOG.md"
track u/KNOWN_ISSUES_TODO_BACKLOG.md; track v/KNOWN_ISSUES_TODO_BACKLOG.md
hyg
has 'B1 u/KNOWN_ISSUES_TODO_BACKLOG.md: unparsed: 0 entries but 2 Status lines' 'unparsed file'
lacks 'B1 v/' 'a file with no entries and no Status lines is silent'
# no git repository at all: B1 is skipped, the other rules still run
new_tree b1-norepo; rm -rf "$R"; n_lines 71 > "$M/MEMORY.md"
hyg; has 'M1' 'M1 still runs without a repository'; lacks 'B1' 'B1 needs a repository'

# --- F5: a failing rule group becomes an `error` finding and the later groups still run; a bad date does not stop anything
new_tree rule-errors; n_lines 71 > "$M/MEMORY.md"; blocks 3 > "$S/HANDOFF.md"
TODAY=not-a-date hyg
has 'error HYGIENE_TODAY: ValueError' 'a bad HYGIENE_TODAY is reported'; has 'M1 ' 'the rules still run after a bad date'; has 'H1 ' 'every rule group still runs'
if [[ $(id -u) != 0 ]]; then   # root reads a mode-000 directory anyway
  chmod 000 "$S/handovers"; hyg; chmod 755 "$S/handovers"
  has 'error S1: PermissionError \(threshold no exception\)' 'an unreadable handovers directory is an error finding'
  has 'H1 ' 'the groups before it ran'; lacks 'error B1' 'the group after it ran'
fi

# --- F1: every production default (state dir of the MAIN checkout from a linked worktree, memory dir under HOME)
new_tree defaults
git -C "$R" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --allow-empty -m x || fail 'git commit'
git -C "$R" worktree add -q --detach "$T/wt" || fail 'git worktree add'
mkdir -p "$R/.controller-state" "$T/home/.claude/projects/-workspaces-vbpub/memory"
blocks 3 > "$R/.controller-state/HANDOFF.md"; n_lines 71 > "$T/home/.claude/projects/-workspaces-vbpub/memory/MEMORY.md"
env -i HOME="$T/home" PATH="$PATH" HYGIENE_REPO="$T/wt" HYGIENE_TODAY="$TODAY" bash "$HYG" > "$ROOT/out" 2> "$ROOT/err"
has 'H1 .controller-state/HANDOFF.md: 3 blocks' 'run from a linked worktree it reads the main checkout state'
has "M1 $T/home/.claude/projects/-workspaces-vbpub/memory/MEMORY.md: 71 lines" 'the memory directory defaults to the one under HOME'

# --- the script changes nothing it reads
new_tree readonly; mkdir -p "$S/platform"
n_lines 80 > "$M/MEMORY.md"; blocks 5 > "$S/HANDOFF.md"; blocks 3 > "$S/platform/STATE.md"; : > "$S/handovers/x.md"
before=$(find "$M" "$S" -printf '%p %T@ %s\n' | sort)
hyg
[[ -s $ROOT/out ]] || fail 'the read-only fixture must produce findings'
[[ $(find "$M" "$S" -printf '%p %T@ %s\n' | sort) == "$before" ]] || fail 'ctl-hygiene.sh must not change, move or create anything it reads'

echo 'PASS ctl-hygiene'
