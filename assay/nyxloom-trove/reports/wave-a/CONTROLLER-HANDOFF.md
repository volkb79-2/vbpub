# Wave A controller handoff (living file)

**Updated:** 2026-09-29. The controller is a Claude Opus 5.5 session working with the operator. Read this first when resuming Wave A.

## Where everything is
- **Integration branch and worktree:** `assay-b110-landing` in `/workspaces/vbpub/.worktrees/assay-b110-landing`. This is canonical vbpub; the old side clones are superseded. The branch is main (incl. B107) + B105 + the B110 docs + Wave A docs. It merges to main only after all of Wave A (operator).
- **Plan:** `../assay-WAVE-A-PLAN-2026-09-29.md`.
- **Decisions:** A-475..A-480, plus carver decisions CD1–CD31 in `CARVER-DECISIONS.md`. The CDs override the briefs.
- **Briefs:**
  - W1 retire, W2 analysis package, W3 component boundaries, W4 test split, W5 SQL, W10 DRY;
  - W8 measurement and W9 campaign analysis (re-carved per CD26);
  - W6 = `../b110/P2-loop-guards.md` + the P2 half of `REBASE-P0-P2.md`;
  - W7 = plan §3.
- **Pre-dispatch review round 1:** `review-predispatch/`. All findings were applied.

## Operator rules (all binding)
- **Implementers are Sonnet only** (`model: "sonnet"`). Reviewers and researchers are fresh default-model sessions, never forks.
- **Edits via the Edit/Write tools only**, never sed or python scripts.
- **Host-load rule:** nice/ionice everything. At most one gate container on the host, and none while any `run-gate-*` container runs. Never run the full `self-qualification` lane. Remove containers by exact name.
- **One worktree per package:** `wave-a-<wN>-<slug>` from `assay-b110-landing`. Merge back serially with `--no-ff`. Delete the worktree and branch right after the merge. No branch sprawl.
- **Every merge into landing needs** a fresh adversarial review plus a green `./run-gate.py tester-unified` on the package branch, with the verdict read in a separate step. Every gate failure is real (CD29).

## Order (plan §2 + CD ordering)
1. Stage 0: W3.
2. Stage 1: W1, W2.
3. Stage 2: W6, then W4, then W5. W5 branches only after W4 has merged.
4. Stage 3: W7 (after W4), W8 (after W2), W9 (after W8).
5. Stage 4: W10.
6. Then:
   - re-merge main, gate green, merge to main;
   - merge `docs/testability-cleanup-20260928`;
   - `cmru release --project assay` (7.2.0 per CD30);
   - deploy to the devcontainer; notify dstdns.

## Status log (append)
| When | Event |
|---|---|
| 2026-09-29 | Briefs carved, reviewed and fixed (`3daf62a7`). W3 implementer dispatched in `.worktrees/wave-a-w3-boundaries`. W5 and W8/W9 fixers were still running. A landing checkpoint gate was queued behind other sessions' gates (log `scratchpad/gate-landing-1e3c8a49.log`). |
| 2026-09-29 ~03:00Z | W5 brief fixed (`43abf9e8`); CD21 amended, plus CD32 (gate-entry host check, W4 O7a) and CD33. W8/W9 self-contained briefs committed (`ec427945`), with CD34–CD38. The landing checkpoint gate was **cancelled, never started**: W3's gate covers the same commit. Fresh reviews dispatched: W8+W9 round 1 (`scratchpad/REVIEW-waveA-W8-W9.md`) and W5 round 2 (`scratchpad/REVIEW-waveA-W5-round2.md`). W3 has committed Parts 1 and 2 on its branch and is now in its before/after suite comparison and gate. |

## Per-package loop (controller)
1. Create the worktree and dispatch a Sonnet implementer with the brief, CARVER-DECISIONS, host rule, edit rule, BLOCKED rule, LOG file, gate command and checkpoint clause.
2. On return, read the LOG and the gate markers yourself.
3. Dispatch a fresh reviewer (default model) on the package diff against the brief's oracles and negatives, including a combined-axis attack.
4. Send fixes back to a Sonnet fixer in the same worktree, and re-gate.
5. Merge `--no-ff` into landing, delete the worktree and branch, and append to the status log above.
