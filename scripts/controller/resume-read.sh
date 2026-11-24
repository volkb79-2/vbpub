#!/usr/bin/env bash
set -u
# resume-read.sh <role>
# ONE call after a restart or compaction: prints exactly the slices of the state files a restarted
# controller session needs (not whole files), each under a header
#   == <path> [<slice>] (<bytes> B, cap <cap> B) ==
# and truncated at its cap with a visible marker. Read-only: no network (git log reads the local
# origin/main ref, there is no fetch), nothing is written. Exit 0 whenever a role was read (a
# missing file prints a MISSING header and the read goes on), 2 for an unknown or missing role.
#   main                 PRIME-main-controller.md; the newest `## RESUME HERE #N` block of
#                        .controller-state/HANDOFF.md and every `(#N ...)` update line; the last 40
#                        lines of PROGRAM.md section 8; the last 3 sections of operator-decisions.md;
#                        `git log --oneline -8 origin/main`; the worktree count.
#   platform|core|assay|hostobs|edge|rollback
#                        <topic>/STATE.md; for platform and assay the newest RESUME block of
#                        <topic>/HANDOFF.md and its update lines; handovers/vbpub-<topic>-successor.md
#                        only when newer than STATE.md; PRIME-<topic>.md; the PROGRAM.md section 1-3
#                        headings and the last 40 lines of section 8.
# Settings (environment; defaults in brackets): RESUME_READ_STATE [nearest .controller-state above
# the current directory, else the one of the checkout holding this script], RESUME_READ_REPO [the
# checkout holding this script; where git runs], RESUME_READ_PROGRAM [PROGRAM-2026-11-24: the
# programme directory under the state directory], RESUME_READ_CAP_<SLICE> overrides a cap below,
# RESUME_READ_TOTAL [24000 B] the budget the caps are scaled to fit.
# OUTPUT BUDGET: the Bash tool turns output above about 30,000 characters into a persisted file with
# a 2 KB preview, so ONE call must stay under it. RESUME_READ_TOTAL is that budget: each cap below is
# scaled down, proportionally and in whole bytes, when the number of slices a role prints times their
# caps would exceed it (a slice that is printed under its cap keeps its bytes, so the output is at
# most the budget plus the headers). Setting it to 0 disables the scaling (the caps apply as written).
# Truncation keeps the start of a slice, except for the tail slices (section 8, update lines), which
# keep the newest end. A cap cuts bytes, so a multi-byte character at the cut can be split.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO=${RESUME_READ_REPO:-$(cd -- "$SCRIPT_DIR/../.." && pwd -P)}
PROGRAM=${RESUME_READ_PROGRAM:-PROGRAM-2026-11-24}
TOPICS="platform core assay hostobs edge rollback"
TOPICS_WITH_HANDOFF="platform assay"
TAIL_LINES=40
# The output budget: the Bash tool inlines output up to about 30,000 characters and persists anything
# larger (2 KB preview), which turns the "ONE call" orientation into a second Read. 24 kB leaves room
# for the headers and the truncation markers (measured 2026-11-24: the real main read was 22.5 kB).
TOTAL=${RESUME_READ_TOTAL:-24000}
# Caps in bytes per slice (about 4 bytes per token: the whole main read stays near 15k tokens).
# STATE.md is the living state of a topic, so it keeps the largest share; the worst-case role
# (platform, which also prints a HANDOFF block and update lines) sums to 23500 B, under the budget.
CAP_PRIME=${RESUME_READ_CAP_PRIME:-3000}
CAP_BLOCK=${RESUME_READ_CAP_BLOCK:-3000}
CAP_UPDATES=${RESUME_READ_CAP_UPDATES:-1200}
CAP_SECTION8=${RESUME_READ_CAP_SECTION8:-2500}
CAP_DECISIONS=${RESUME_READ_CAP_DECISIONS:-4000}
CAP_GIT=${RESUME_READ_CAP_GIT:-1500}
CAP_STATE=${RESUME_READ_CAP_STATE:-12000}
CAP_SUCCESSOR=${RESUME_READ_CAP_SUCCESSOR:-1500}
CAP_HEADINGS=${RESUME_READ_CAP_HEADINGS:-300}

# The caps a role's slices are printed under, scaled to fit the output budget: main prints PRIME,
# block, update lines, section 8, the last decision sections and the git log; a topic prints STATE,
# [its HANDOFF block and update lines], the successor, PRIME, the section 1-3 headings and section 8.
fit_caps() {
  local total=0 cap scale=0
  for cap in "$@"; do total=$((total + cap)); done
  if [[ -z ${TOTAL//[0-9]/} && $TOTAL -gt 0 && $total -gt $TOTAL ]]; then
    scale=$((total - TOTAL))
  fi
  for cap in "$@"; do
    printf '%s\n' "$((cap - (cap * scale + total - 1) / total))"
  done
}

usage() { echo "usage: ${0##*/} <main|${TOPICS// /|}>" >&2; }

state_dir() {
  if [[ -n ${RESUME_READ_STATE:-} ]]; then printf '%s\n' "$RESUME_READ_STATE"; return; fi
  local dir=$PWD
  while [[ -n $dir && $dir != / ]]; do
    if [[ -d $dir/.controller-state ]]; then printf '%s\n' "$dir/.controller-state"; return; fi
    dir=$(dirname -- "$dir")
  done
  printf '%s\n' "$REPO/.controller-state"
}

TMP=$(mktemp "${TMPDIR:-/tmp}/resume-read.XXXXXX") || exit 1
trap 'rm -f -- "$TMP"' EXIT

# emit <path> <slice> <cap> <head|tail>: the slice text is in $TMP; print header, text, truncation marker.
emit() {
  local path=$1 slice=$2 cap=$3 mode=${4:-head} bytes
  bytes=$(wc -c < "$TMP")
  bytes=${bytes//[[:space:]]/}
  printf '== %s [%s] (%s B, cap %s B) ==\n' "$path" "$slice" "$bytes" "$cap"
  if (( bytes <= cap )); then
    cat -- "$TMP"
  elif [[ $mode == tail ]]; then
    printf '[... TRUNCATED: the first %s B of this slice are omitted (cap %s B); read the file for them ...]\n' "$((bytes - cap))" "$cap"
    tail -c "$cap" -- "$TMP"
    echo
  else
    head -c "$cap" -- "$TMP"
    printf '\n[... TRUNCATED: %s B omitted after the first %s B (cap); read the file for the rest ...]\n' "$((bytes - cap))" "$cap"
  fi
}

missing() { printf '== %s [%s] MISSING ==\n' "$1" "$2"; }

# whole_file <path> <slice> <cap>
whole_file() {
  if [[ -r $1 ]]; then cat -- "$1" > "$TMP"; emit "$1" "$2" "$3"; else missing "$1" "$2"; fi
}

# resume_block <handoff> <label>: the newest `## RESUME HERE #N` block, then the `(#N ...)` update lines.
resume_block() {
  local file=$1 start end number
  if [[ ! -r $file ]]; then missing "$file" "newest RESUME HERE block"; return; fi
  start=$(grep -n '^## RESUME HERE #[0-9]' -- "$file" | tail -1 | cut -d: -f1)
  if [[ -z $start ]]; then missing "$file" "newest RESUME HERE block (no '## RESUME HERE #N' heading)"; return; fi
  number=$(sed -n "${start}p" -- "$file" | sed -E 's/^## RESUME HERE #([0-9]+).*/\1/')
  end=$(awk -v s="$start" 'NR > s && /^## / { print NR - 1; found = 1; exit } END { if (!found) print NR }' "$file")
  sed -n "${start},${end}p" -- "$file" > "$TMP"
  emit "$file" "newest RESUME HERE #$number block, lines $start-$end" "$CAP_BLOCK"
  awk -v s="$start" -v e="$end" -v n="$number" \
    'NR >= s && NR <= e { next } $0 ~ ("^\\(#" n "[ )]") { print }' "$file" > "$TMP"
  if [[ -s $TMP ]]; then emit "$file" "update lines (#$number ...)" "$CAP_UPDATES" tail
  else printf '== %s [update lines (#%s ...)] none ==\n' "$file" "$number"; fi
}

# section8_tail <program.md>: the last TAIL_LINES lines of `## 8.`
section8_tail() {
  local file=$1
  if [[ ! -r $file ]]; then missing "$file" "section 8 tail"; return; fi
  awk '/^## 8[. ]/ { p = 1 } p && /^## / && !/^## 8[. ]/ { exit } p { print }' "$file" | tail -n "$TAIL_LINES" > "$TMP"
  if [[ -s $TMP ]]; then emit "$file" "section 8, last $TAIL_LINES lines" "$CAP_SECTION8" tail
  else missing "$file" "section 8 (no '## 8.' heading)"; fi
}

last_sections() { # last_sections <file> <count>
  local file=$1 count=$2
  if [[ ! -r $file ]]; then missing "$file" "last $count sections"; return; fi
  awk -v c="$count" '{ line[NR] = $0 } /^## / { heads[++h] = NR }
    END { s = (h >= c) ? heads[h - c + 1] : 1; for (i = s; i <= NR; i++) print line[i] }' "$file" > "$TMP"
  emit "$file" "last $count '## ' sections" "$CAP_DECISIONS" tail
}

git_slices() {
  git -C "$REPO" log --oneline -8 origin/main > "$TMP" 2>&1
  emit "$REPO" "git log --oneline -8 origin/main" "$CAP_GIT"
  printf '%s\n' "$(git -C "$REPO" worktree list 2>/dev/null | wc -l | tr -d '[:space:]')" > "$TMP"
  emit "$REPO" "worktree count (git worktree list)" 16
}

# state_oversize <state.md> <cap>: one line when the file is bigger than its share, so its owner keeps
# STATE.md current-only instead of relying on the truncation marker (review M3).
state_oversize() {
  local file=$1 cap=$2 bytes
  [[ -r $file ]] || return 0
  bytes=$(wc -c < "$file")
  bytes=${bytes//[[:space:]]/}
  if (( bytes > cap )); then
    printf 'WARN STATE.md is %s B, cap %s B: shorten it to what the next session needs\n' "$bytes" "$cap"
  fi
}

read_main() {
  local state=$1
  # Every slice main prints, scaled to the output budget in one step.
  mapfile -t caps < <(fit_caps "$CAP_PRIME" "$CAP_BLOCK" "$CAP_UPDATES" "$CAP_SECTION8" "$CAP_DECISIONS" "$CAP_GIT")
  CAP_PRIME=${caps[0]} CAP_BLOCK=${caps[1]} CAP_UPDATES=${caps[2]} CAP_SECTION8=${caps[3]}
  CAP_DECISIONS=${caps[4]} CAP_GIT=${caps[5]}
  whole_file "$state/$PROGRAM/PRIME-main-controller.md" "PRIME" "$CAP_PRIME"
  resume_block "$state/HANDOFF.md"
  section8_tail "$state/$PROGRAM/PROGRAM.md"
  last_sections "$state/operator-decisions.md" 3
  git_slices
}

read_topic() {
  local state=$1 topic=$2 successor program=$1/$PROGRAM/PROGRAM.md with_handoff=0
  [[ " $TOPICS_WITH_HANDOFF " == *" $topic "* ]] && with_handoff=1
  if (( with_handoff )); then
    mapfile -t caps < <(fit_caps "$CAP_STATE" "$CAP_BLOCK" "$CAP_UPDATES" "$CAP_SUCCESSOR" "$CAP_PRIME" "$CAP_HEADINGS" "$CAP_SECTION8")
    CAP_STATE=${caps[0]} CAP_BLOCK=${caps[1]} CAP_UPDATES=${caps[2]} CAP_SUCCESSOR=${caps[3]}
    CAP_PRIME=${caps[4]} CAP_HEADINGS=${caps[5]} CAP_SECTION8=${caps[6]}
  else
    mapfile -t caps < <(fit_caps "$CAP_STATE" "$CAP_SUCCESSOR" "$CAP_PRIME" "$CAP_HEADINGS" "$CAP_SECTION8")
    CAP_STATE=${caps[0]} CAP_SUCCESSOR=${caps[1]} CAP_PRIME=${caps[2]}
    CAP_HEADINGS=${caps[3]} CAP_SECTION8=${caps[4]}
  fi
  state_oversize "$state/$topic/STATE.md" "$CAP_STATE"
  whole_file "$state/$topic/STATE.md" "STATE" "$CAP_STATE"
  if (( with_handoff )); then resume_block "$state/$topic/HANDOFF.md"; fi
  successor=$state/handovers/vbpub-$topic-successor.md
  if [[ ! -e $successor ]]; then missing "$successor" "successor handover"
  elif [[ ! -e $state/$topic/STATE.md || $successor -nt $state/$topic/STATE.md ]]; then
    whole_file "$successor" "successor handover (newer than STATE.md)" "$CAP_SUCCESSOR"
  else printf '== %s [successor handover] SKIPPED (not newer than STATE.md) ==\n' "$successor"; fi
  whole_file "$state/$PROGRAM/PRIME-$topic.md" "PRIME" "$CAP_PRIME"
  if [[ -r $program ]]; then
    grep -E '^## [123]\.' -- "$program" > "$TMP"
    emit "$program" "section 1-3 headings" "$CAP_HEADINGS"
  else missing "$program" "section 1-3 headings"; fi
  section8_tail "$program"
}

role=${1:-}
if [[ $role == main && $# -eq 1 ]]; then read_main "$(state_dir)"; exit 0; fi
if [[ $# -eq 1 ]]; then
  for topic in $TOPICS; do
    if [[ $role == "$topic" ]]; then read_topic "$(state_dir)" "$role"; exit 0; fi
  done
fi
usage
exit 2
