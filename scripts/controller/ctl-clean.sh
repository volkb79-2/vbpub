#!/usr/bin/env bash
# ctl-clean.sh - remove scratch remnants that finished runs leave in the repo checkout.
#
# Dry run by default: prints the eligible count, apparent size, and oldest/newest
# examples per class. `--apply` deletes. Only classes with no value after a run are
# considered: run-gate-gitcfg-*, run-gate-test-bin-*, run-gate-test-invoke* and
# assay-liveness-*.
#
# An entry is eligible only when its NEWEST mtime -- including every file and
# directory inside it -- is older than --age-hours. The default is 48h, on the
# assumption that a gate never runs that long. This deliberately does not trust a
# directory's own mtime: creating a child updates it, but a long-running tool may
# keep modifying an existing nested file while the directory itself stays quiet.
#
# `git worktree prune` is opt-in. `--prune-worktrees` must only be used from the
# shared checkout when no session is creating or moving worktrees; in a shared
# multi-session checkout, the default path never prunes worktree admin entries.
#
# Evidence, history.json, artifacts/ and .worktrees/ are REPORTED, never deleted
# here (worktree-sprawl rule: those need a human or controller decision).
#
# Usage: ctl-clean.sh [--apply] [--age-hours N] [--repo DIR] [--prune-worktrees]
set -u -o pipefail

usage() {
  printf '%s\n' \
    'Usage: ctl-clean.sh [--apply] [--age-hours N] [--repo DIR] [--prune-worktrees]' \
    '' \
    'Dry run by default. Eligible scratch entries are older than --age-hours based' \
    'on the newest mtime of the entry and everything inside it. Default: 48h.' \
    '' \
    '  --apply              delete eligible scratch entries' \
    '  --age-hours N        integer from 1 to 8760 (default 48)' \
    '  --repo DIR           worktree root to inspect (default: current worktree root)' \
    '  --prune-worktrees    opt in to git worktree prune; use only from the shared' \
    '                       checkout when no session is creating or moving worktrees'
}

die() {
  printf 'ctl-clean: %s\n' "$*" >&2
  exit 2
}

parse_age_hours() {
  local raw=$1 zeros digits
  [[ -n $raw ]] || die '--age-hours requires a non-empty integer from 1 to 8760'
  case $raw in
    *[!0-9]*) die "--age-hours must be an integer from 1 to 8760 (got '$raw')" ;;
  esac

  # Strip leading zeroes before arithmetic so 08 is decimal 8 and a very long
  # digit string cannot overflow Bash's signed integer expression.
  zeros=${raw%%[!0]*}
  digits=${raw#"$zeros"}
  [[ -n $digits ]] || digits=0
  ((${#digits} <= 4)) ||
    die "--age-hours must be an integer from 1 to 8760 (got '$raw')"

  AGE_H=$((10#$digits))
  ((AGE_H > 0 && AGE_H <= 8760)) ||
    die "--age-hours must be an integer from 1 to 8760 (got '$raw')"
}

APPLY=0
PRUNE_WORKTREES=0
AGE_H=48
REPO=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
while (($# > 0)); do
  case $1 in
    --apply)
      APPLY=1
      ;;
    --age-hours)
      (($# >= 2)) || die '--age-hours requires an integer from 1 to 8760'
      parse_age_hours "$2"
      shift
      ;;
    --repo)
      (($# >= 2)) || die '--repo requires a directory'
      [[ -n $2 ]] || die '--repo requires a non-empty directory'
      case $2 in
        -*) die "--repo must not start with '-' (got '$2')" ;;
      esac
      REPO=$2
      shift
      ;;
    --prune-worktrees)
      PRUNE_WORKTREES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument $1"
      ;;
  esac
  shift
done

requested=$(cd -- "$REPO" 2>/dev/null && pwd -P) ||
  die "--repo is not a directory: $REPO"
# `git -C` does not accept `--` between the option and its path. The leading-dash
# refusal above is the guard that makes passing REPO directly safe here.
worktree_root=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) ||
  die "--repo is not inside a Git worktree: $REPO"
[[ -n $worktree_root ]] || die "--repo is not inside a Git worktree: $REPO"
worktree_root=$(cd -- "$worktree_root" 2>/dev/null && pwd -P) ||
  die "cannot resolve the Git worktree root for: $REPO"
[[ $worktree_root == "$requested" ]] ||
  die "--repo must name the worktree root, not a subdirectory or unrelated path (got '$requested', root is '$worktree_root')"

REPO=$requested
cd -- "$REPO" || die "cannot enter repo: $REPO"
RG=./.run-gate
CLASSES=('run-gate-gitcfg-*' 'run-gate-test-bin-*' 'run-gate-test-invoke*' 'assay-liveness-*')

printf 'ctl-clean: target=%s age=%sh mode=%s prune-worktrees=%s\n' \
  "$REPO" "$AGE_H" "$([[ $APPLY == 1 ]] && echo APPLY || echo dry-run)" \
  "$([[ $PRUNE_WORKTREES == 1 ]] && echo opt-in || echo off)"

newest_mtime() {
  local entry=$1 newest
  newest=$(find "$entry" -printf '%T@\n' 2>/dev/null | LC_ALL=C sort -nr | sed -n '1p')
  [[ $newest =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  printf '%s\n' "$newest"
}

show_examples() {
  local -a oldest=() newest=()
  local i epoch path encoded
  mapfile -t oldest < <(
    for i in "${!EX_EPOCH[@]}"; do
      printf '%s\t%s\n' "${EX_EPOCH[i]}" "${EX_PATH[i]}"
    done | LC_ALL=C sort -n
  )
  mapfile -t newest < <(
    for i in "${!EX_EPOCH[@]}"; do
      printf '%s\t%s\n' "${EX_EPOCH[i]}" "${EX_PATH[i]}"
    done | LC_ALL=C sort -nr
  )
  for i in 0 1 2; do
    ((i < ${#oldest[@]})) || break
    encoded=${oldest[i]}
    epoch=${encoded%%$'\t'*}
    path=${encoded#*$'\t'}
    printf '    oldest: %s  %s\n' \
      "$(date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '%s' "$epoch")" \
      "$path"
  done
  for i in 0 1 2; do
    ((i < ${#newest[@]})) || break
    encoded=${newest[i]}
    epoch=${encoded%%$'\t'*}
    path=${encoded#*$'\t'}
    printf '    newest: %s  %s\n' \
      "$(date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '%s' "$epoch")" \
      "$path"
  done
}

class_size() {
  local -a batch
  local i out rc bytes path total=0
  for ((i = 0; i < $#; i += 128)); do
    batch=("${@:i+1:128}")
    if out=$(timeout 60 du -sb -- "${batch[@]}" 2>/dev/null); then
      while IFS=$'\t' read -r bytes path; do
        [[ $bytes =~ ^[0-9]+$ ]] || {
          SIZE_TEXT='(size not computed: invalid du output)'
          return 1
        }
        total=$((total + bytes))
      done <<< "$out"
    else
      rc=$?
      if ((rc == 124)); then
        SIZE_TEXT='(size not computed: timed out)'
      else
        SIZE_TEXT='(size not computed)'
      fi
      return 1
    fi
  done
  SIZE_TEXT="$total bytes"
}

delete_entry() {
  local entry=$1
  if ((USE_PRIORITY)); then
    printf '%s\0' "$entry" | nice -n 19 ionice -c3 xargs -0 -r rm -rf --
  else
    printf '%s\0' "$entry" | xargs -0 -r rm -rf --
  fi
}

removed=0
failed=0
selected=0
inspect_failed=0
prune_failed=0
USE_PRIORITY=0
if ((APPLY)) && command -v nice >/dev/null 2>&1 && command -v ionice >/dev/null 2>&1; then
  USE_PRIORITY=1
elif ((APPLY)); then
  printf 'ctl-clean: INFO nice/ionice unavailable; using plain xargs rm\n' >&2
fi

if [[ -d $RG ]]; then
  for pattern in "${CLASSES[@]}"; do
    ENTRIES=()
    mapfile -d '' -t ENTRIES < <(
      find "$RG" -mindepth 1 -maxdepth 1 -name "$pattern" -print0 2>/dev/null
    )
    candidate_count=${#ENTRIES[@]}
    ((candidate_count > 0)) || continue

    EX_EPOCH=()
    EX_PATH=()
    for entry in "${ENTRIES[@]}"; do
      if ! recent=$(find "$entry" -newermt "-${AGE_H} hours" -print -quit 2>/dev/null); then
        printf 'ctl-clean: WARNING cannot inspect activity; keeping %s\n' "$entry" >&2
        inspect_failed=$((inspect_failed + 1))
        continue
      fi
      [[ -z $recent ]] || continue
      if ! epoch=$(newest_mtime "$entry"); then
        printf 'ctl-clean: WARNING cannot determine newest mtime; keeping %s\n' "$entry" >&2
        inspect_failed=$((inspect_failed + 1))
        continue
      fi
      EX_EPOCH+=("$epoch")
      EX_PATH+=("$entry")
    done

    eligible=${#EX_PATH[@]}
    selected=$((selected + eligible))
    if ((APPLY)); then
      printf '  %-26s eligible %d of %d; older than %sh (newest file inside included)\n' \
        "$pattern" "$eligible" "$candidate_count" "$AGE_H"
      for entry in "${EX_PATH[@]}"; do
        if delete_entry "$entry"; then
          removed=$((removed + 1))
        else
          failed=$((failed + 1))
          printf 'ctl-clean: FAILED to remove %s\n' "$entry" >&2
        fi
      done
    else
      SIZE_TEXT='(size not computed)'
      if ((eligible > 0)); then
        class_size "${EX_PATH[@]}" >/dev/null 2>&1
      else
        SIZE_TEXT='0 bytes'
      fi
      printf '  %-26s eligible %d of %d; older than %sh (newest file inside included); %s\n' \
        "$pattern" "$eligible" "$candidate_count" "$AGE_H" "$SIZE_TEXT"
      ((eligible > 0)) && show_examples
    fi
  done

  keep=0
  while IFS= read -r -d '' entry; do
    keep=$((keep + 1))
  done < <(find "$RG" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
  printf '  .run-gate entries now: %d (history.json and unlisted classes are kept)\n' "$keep"
else
  printf '  no .run-gate here\n'
fi

if ((PRUNE_WORKTREES)); then
  if ((APPLY)); then
    cmd=(git worktree prune -v)
    label='prune'
  else
    cmd=(git worktree prune -n -v)
    label='would prune'
  fi
  if prune_output=$("${cmd[@]}" 2>&1); then
    :
  else
    prune_failed=1
  fi
  if [[ -n $prune_output ]]; then
    while IFS= read -r line; do
      printf '  %s: %s\n' "$label" "$line"
    done <<< "$prune_output"
  fi
  ((prune_failed == 0)) || printf 'ctl-clean: git worktree prune failed\n' >&2
fi

printf 'report (never deleted by this script):\n'
mapfile -t wt_lines < <(git worktree list 2>/dev/null || true)
mapfile -t merged_lines < <(git branch --merged origin/main 2>/dev/null || true)
printf '  worktrees: %d   local branches merged into origin/main: %d   (see the worktree-sprawl recipe)\n' \
  "${#wt_lines[@]}" "${#merged_lines[@]}"

report_dir() {
  local dir=$1 size rc output
  [[ -d $dir ]] || return 0
  if output=$(timeout 60 du -sh -- "$dir" 2>/dev/null); then
    size=${output%%$'\t'*}
  else
    rc=$?
    if ((rc == 124)); then
      size='(size not computed: timed out)'
    else
      size='(size not computed)'
    fi
  fi
  printf '  %-12s %s (use dust -d 1 %s to drill down)\n' "$(basename -- "$dir")" "$size" "$dir"
}
report_dir "$REPO/artifacts"
report_dir "$REPO/.worktrees"

if ((APPLY)); then
  printf 'ctl-clean: removed %d, failed %d scratch entries\n' "$removed" "$failed"
else
  printf 'ctl-clean: selected %d scratch entries; dry-run made no deletions\n' "$selected"
fi

if ((failed > 0 || inspect_failed > 0 || prune_failed > 0)); then
  exit 1
fi
