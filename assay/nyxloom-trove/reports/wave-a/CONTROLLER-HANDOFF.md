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
4. Stage 3: W7 (after W4), then W8 (after W2 and W7, CD42), then W9 (after W8).
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
| 2026-09-29 ~06:00Z | W5 round 2 applied (`9e34f7f9`). W8/W9 review applied (`189ec19a`, CD39–CD42). **All briefs are final.** W3 implementer done: `be0357de`, `f2c249fe`, `ea990a53`; O1–O6 green; the gate was not run because the host was busy with another session's cmru mutation gate, which is due to finish around 08:00Z. Fresh W3 review dispatched (`scratchpad/REVIEW-W3.md`). Next: apply the review fixes, run the W3 gate (controller), merge, then W1 and W2 in parallel. |
| 2026-09-29 ~06:30Z | W3 review: MERGE-WITH-FIXES. **W3R-1 blocker:** the vbpub root `.gitignore` `core` pattern hid the new contract test, so the gate would have been hollow-green. Fixed with W3R-2..5 on the W3 branch; HEAD `7a9c8948`, fresh-checkout collect 5975. CD43–CD45 recorded (`1fc887a3`). W3 gate watcher queued (log `scratchpad/gate-w3-7a9c8948.log`; it refuses if HEAD moved). The host is held by another session's cmru mutation gate (778/1070), so a start around 10:00–11:00Z is expected. **W1 and W2 dispatched in parallel** from W3's head plus landing: `.worktrees/wave-a-w1-retire`, `.worktrees/wave-a-w2-analysis`. Neither runs a gate (CD44). Merge order: W3 (after a green gate), then W1 and W2 serially, each after its own gate and review. |
| 2026-09-29 ~07:00Z | **W1 done:** HEAD `fb4b0cc7`, READY-FOR-GATE `bb696f53`, collect 5975→5882. **W2 done** (after a checkpoint and a successor): HEAD `e81dce9c`, READY-FOR-GATE `4f2fd892`, tests 5783 + analysis/tests 221. Fresh reviews running: `scratchpad/REVIEW-W1.md`, `REVIEW-W2.md`. **W6 dispatched** in `.worktrees/wave-a-w6-guards` (base W3 plus landing, `20846716`). W4 waits for W1 and W2 to merge. The only local failure (`test_git_boundary`) comes from a stray empty `/tmp/.git`, created 2026-09-28 22:17, not by us; the operator was told. |
| 2026-09-29 ~07:40Z | W1 review: MERGE-WITH-FIXES (0 blocker, 1 major: v13 schema freeze lost with the W9 phase, restored by a sha256 pin). All 7 fixes applied, READY-FOR-GATE `241e32f6`. The W3-only watcher was stopped in favour of a **stage-1 gate**. Merged into landing: W3 (`eb0acea6`), W1 (`83f099e5`); collect 5885; contract test green. W2 is waiting for its review and fixes, then merges, then the stage-1 gate runs on the landing tip. The W3 and W1 worktrees are kept until the stage gate is green. |
| 2026-09-29 07:20Z | W2 review: MERGE-WITH-FIXES (1 major, W2R-1: the conftest `__getattr__` proxy served an unregistered copy of the judge conftest; replaced by a package-based `assay_judge_conftest` loader, CD31 amended; CD46). Fixes applied, READY-FOR-GATE `194cf74a`. **W2 merged** (`42dd7285`; CHANGES and `test_distribution_gate.py` conflicts resolved by hand, adding `analysis-lane-passed` to the phase list and to the inner order). Collect 5917; 299 focused tests green. W6 review: MERGE-WITH-FIXES (1 major, W6R-1: the O4b drain test idles about 120 s under two mutants, which would be hung in R2); Sonnet fixer running. B134 filed (host-dependent git-marker test); W5 new ids from B135. **W4 dispatched** in `.worktrees/wave-a-w4-test-split` from landing `11ace522`. Next: merge W6 after its fixes, then start the stage-1 gate on the landing tip (W3+W1+W2+W6). |

## Per-package loop (controller)
**Gate ownership (from 2026-09-29, after W3 polled for 3 hours):** implementers do **not** run the registered gate. They finish their local oracles, commit, write `READY-FOR-GATE` in their LOG and return. The controller runs every package gate serially from one watcher, so two of this session's agents never race for the single gate slot. Gate failures go back to a Sonnet fixer in the same worktree.

**Stage gates (from 2026-09-29 ~07:40Z).** The single gate slot is the bottleneck: other sessions' multi-hour campaigns hold it. So a stage's reviewed and fixed packages are merged `--no-ff` into landing, and **one** registered `tester-unified` gate runs on the landing tip. That is the exact content bound for main. While the stage gate runs, the controller commits nothing to landing. A red stage gate is attributed by its failing tests, fixed in the package worktree, merged again and re-gated. Package worktrees and branches are deleted only after their stage gate is green.

1. Create the worktree and dispatch a Sonnet implementer with the brief, CARVER-DECISIONS, host rule, edit rule, BLOCKED rule, LOG file and checkpoint clause, and the instruction not to run the registered gate.
2. On return, read the LOG and the gate markers yourself.
3. Dispatch a fresh reviewer (default model) on the package diff against the brief's oracles and negatives, including a combined-axis attack.
4. Send fixes back to a Sonnet fixer in the same worktree, and re-gate.
5. Merge `--no-ff` into landing, delete the worktree and branch, and append to the status log above.
