# Assay 10.2.0 CW9 continuation

CW9 is implemented on branch `assay-cw9`, based on `main` at
`9170dc585eec5b29c55080a138e650cce2e2fe16`. The red-first acceptance tests
were committed before the implementation. Verdict schema v16, waiver windows,
RS1/RS2, PR1 and WP8 are unchanged.

## Implementation

When the active cowtree workspace advertises `cheap_clone`, Assay builds one
verified baseline tree, seals it, and keeps it inert for the lifetime of the
prepared snapshot. Every baseline or replacement materialization gets its own
`clone_tree`. `_apply_replacement` is shared by the old materializer and the
clone path; on clones it rewrites the changed file before verification, then
plants that unit's declared links. Candidate clones and the sealed template
are removed on normal close and error/interruption cleanup. Without
`cheap_clone`, the existing materialization route is retained.

Fake workspace tests check the read-only seal-before-clone ordering, that lane
processes only receive per-unit clones, cleanup across success/failure/
`KeyboardInterrupt`/leak/seal failure, and identical plain/CoW mutation bucket
signatures for killed, survived, crashed and budget-exceeded candidates.

## Controller 20-candidate bucket-parity pilot

Run this from the exact pushed CW9 commit, with a native R2-capable
`self-qualification` lane and an existing user-owned btrfs area exported as
`MDT_COW_ROOT`. Keep the checkout at the same commit for both runs. The
selector report and both summaries/logs are retained under a fresh ignored
`.assay/cw9-<commit>` directory. Run the same selected candidates once with
plain storage and once with Level B enabled; use separate state and progress
paths. Pilot exit 6 means the selected run completed, not that the lane passed.

```bash
set -euo pipefail
: "${MDT_COW_ROOT:?export the existing user-owned btrfs area}"
source_commit="$(git rev-parse HEAD)"
pilot_root=".assay/cw9-$(git rev-parse --short=12 HEAD)"
mkdir -p .assay
mkdir "$pilot_root"  # Refuse to overwrite an earlier pilot's evidence.
plan="$pilot_root/plan.json"
candidates="$pilot_root/candidates.json"
selection="$pilot_root/selection.json"

assay plan self-qualification --json --file assay.toml > "$plan"
python tools/b110_pilot_select.py \
  --plan "$plan" \
  --repo-root "$(git rev-parse --show-toplevel)" \
  --out "$candidates" \
  --report "$selection" \
  --seed assay-cw9-10.2.0 \
  --size 20

run_completed_pilot() {
  local label="$1"
  shift
  local status
  if "$@" > "$pilot_root/$label.summary.json" 2> "$pilot_root/$label.log"; then
    status=0
  else
    status="$?"
  fi
  printf '%s exit: %s\n' "$label" "$status"
  if [[ "$status" != 6 ]]; then
    printf '%s pilot did not complete with the expected selection exit 6\n' "$label" >&2
    return 1
  fi
}

run_completed_pilot plain env COWTREE_MODE=off assay run self-qualification \
  --file assay.toml --candidates-file "$candidates" --pilot-jobs 1 \
  --cold-witness --resume \
  --state-dir "$pilot_root/state-plain" \
  --progress "$pilot_root/progress-plain.jsonl"
run_completed_pilot cow env COWTREE_MODE=auto MDT_COW_ROOT="$MDT_COW_ROOT" \
  assay run self-qualification --file assay.toml \
  --candidates-file "$candidates" --pilot-jobs 1 --cold-witness --resume \
  --state-dir "$pilot_root/state-cow" \
  --progress "$pilot_root/progress-cow.jsonl"

test "$(git rev-parse HEAD)" = "$source_commit"
```

Compare the two JSON summaries' candidate bucket counts and candidate IDs;
they must match. Retain the selection report, summaries, progress streams and
logs with the controller's pilot record. Do not interpret the pilot as
qualification or as a live btrfs acceptance test for any other host.

## Local verification

- Focused isolation/mutation/adapter/workspace/runner/B105-boundary selection
  with `PYTHONPATH=../libraries/cowtree/src`: 212 passed.
- Focused isolation/mutation/adapter/workspace/B105-boundary selection with
  line and branch coverage: 160 passed; every CW9-added `isolation.py` line
  and branch exit was exercised.
- Documentation, vocabulary, cross-document anchors, and self-lane selection:
  230 passed. The README's three links across the public docs resolve.
- No new source modules were introduced, so the three existing B105 target
  inventories are unchanged; all three remain equal and sorted.
- `python -m pyflakes src tests gate` from the estate venv: clean.
- No real btrfs mount, `run-gate`, or Docker acceptance was run here. The
  controller pilot and registered `self-qualification-preflight` line/branch
  coverage check remain separate follow-up evidence.
