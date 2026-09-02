# assay Wave D (v10) — implementer LOG

One entry per commit, in order. Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`, from `main` at `a4a865da`.

Wave prompt: `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`.
Report: `assay-WAVE-D-v10-REPORT.md`. Briefs: `assay-WAVE-D-v10-BRIEF-<n>.md`.

## Generation 1

**Id allocation, checked against `main` before filing** (the wave prompt's own
rule): `git show main:assay/nyxloom-trove/decisions.md | grep -c '^| A-'` and
the `4-backlog.md` id sweep on `main` at `a4a865da` both agree with this
branch — the last decision on main is **A-407** and the last backlog entry is
**B061**, so this generation allocates from **A-408** and, if it files
anything, from **B062**. No collision with a concurrent branch was found.

### 1. `fix(assay): a replaced output directory is named, not read as EMPTY_COVERAGE (B049, A-408)`

- Item: **B049**, ruling **DA-D1** (option 4).
- Changed: `src/assay/safeio.py` (the guard, in `OutputReservation.consume`
  via the new `_refuse_if_parent_was_replaced`), `tests/test_safeio_replaced_output_directory.py`
  (new, 8 tests), `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`,
  `CHANGES.md`, `nyxloom-trove/decisions.md` (A-408),
  `nyxloom-trove/4-backlog.md` (B049 acceptance boxes + RESOLVED).
- Red-first: with `src/assay/safeio.py` stashed to the pre-fix state,
  `pytest tests/test_safeio_replaced_output_directory.py -q` →
  `4 failed, 4 passed` (the 4 that fail are the four new-behaviour
  assertions; the 4 that pass are the legitimate-state controls, which must
  pass on both sides). With the fix restored: `8 passed`, and
  `tests/test_safeio.py` (45 tests) unchanged green.
- No `verdict.py` / `verify.py` / schema / drift-guard file was touched —
  phase 1 stays releasable on v9.
