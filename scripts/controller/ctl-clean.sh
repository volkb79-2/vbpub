#!/usr/bin/env bash
# ctl-clean.sh - remove scratch remnants that finished runs leave in the repo checkout.
#
# Dry run by default: prints what WOULD go (count + size per class). `--apply` deletes.
# Only classes with no value after the run are removed, and only entries older than --age-hours (default 24)
# so a live gate's private dirs are never touched. Evidence, history.json, artifacts/ and .worktrees/ are
# REPORTED, never deleted here (worktree-sprawl rule: those need a human or controller decision).
#
# Usage: ctl-clean.sh [--apply] [--age-hours N] [--repo DIR]
set -u
APPLY=0; AGE_H=24; REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --age-hours) AGE_H="${2:?--age-hours needs a number}"; shift ;;
    --repo) REPO="${2:?--repo needs a directory}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "ctl-clean: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done
case "$AGE_H" in ''|*[!0-9]*) echo "ctl-clean: --age-hours must be a number" >&2; exit 2 ;; esac
cd "$REPO" || exit 2
# The shared checkout root holds the scratch; a linked worktree has its own (usually empty) .run-gate.
MIN=$((AGE_H * 60))
RG=".run-gate"
# Scratch name classes inside .run-gate (all created by run-gate/assay/tests, none holds evidence).
CLASSES=('run-gate-gitcfg-*' 'run-gate-test-bin-*' 'run-gate-test-invoke*' 'assay-liveness-*')
tot=0
echo "ctl-clean: repo=$REPO age>=${AGE_H}h mode=$([ $APPLY = 1 ] && echo APPLY || echo dry-run)"
if [ -d "$RG" ]; then
  for pat in "${CLASSES[@]}"; do
    n=$(find "$RG" -maxdepth 1 -name "$pat" -mmin +"$MIN" 2>/dev/null | wc -l)
    [ "$n" -gt 0 ] || continue
    printf '  %-26s %8d entries older than %sh\n' "$pat" "$n" "$AGE_H"
    tot=$((tot + n))
    if [ "$APPLY" = 1 ]; then
      # -delete cannot remove non-empty dirs; rm -rf in batches. nice/ionice: this can be 80k+ entries.
      find "$RG" -maxdepth 1 -name "$pat" -mmin +"$MIN" -print0 2>/dev/null \
        | nice -n 19 ionice -c3 xargs -0 -r rm -rf --
    fi
  done
  keep=$(find "$RG" -maxdepth 1 -mindepth 1 2>/dev/null | wc -l)
  echo "  .run-gate entries now: $keep (history.json and unlisted classes are kept)"
else
  echo "  no $RG here"
fi
# Dangling linked-worktree admin entries (the VS Code Git log shows ENOENT HEAD for removed remote-gate worktrees).
if [ "$APPLY" = 1 ]; then git worktree prune -v 2>&1 | sed 's/^/  prune: /'; else git worktree prune -n -v 2>&1 | sed 's/^/  would prune: /'; fi
# Report only.
echo "report (never deleted by this script):"
wt=$(git worktree list 2>/dev/null | wc -l)
merged=$(git branch --merged origin/main 2>/dev/null | wc -l)
echo "  worktrees: $wt   local branches merged into origin/main: $merged   (see the worktree-sprawl recipe)"
for d in artifacts .worktrees; do
  [ -d "$d" ] && printf '  %-12s %s\n' "$d" "$(timeout 60 du -sh "$d" 2>/dev/null | cut -f1) (use dust -d 1 $d to drill down)"
done
echo "ctl-clean: $([ $APPLY = 1 ] && echo removed || echo would remove) $tot scratch entries"
