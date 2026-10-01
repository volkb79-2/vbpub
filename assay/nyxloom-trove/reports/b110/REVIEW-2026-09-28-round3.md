# B110 documents: pre-dispatch review, round 3, final (2026-09-28)

## Who reviewed and what

Three fresh-session, read-only reviewers checked `210d6130`. None was a fork, none edited files, and none ran tests or gates.

Round 3 covered:
- the five briefs that were still NOT READY after round 2: P3b, P5, P8, P9 and P10b (with P10a and P10c read alongside);
- a cross-document consistency check of the plan, decisions, backlog and README.

The verbatim outputs are in [`review-round3/`](review-round3/). This was the last round under the 3-round doctrine cap.

## How findings were handled

Every round-3 finding is accepted as the reviewer proposed. The fixes are in the commit that adds this file. The plan, decisions and README fixes were made by the carver; the brief fixes came from one fixer pass. **The round-3 fixes themselves have not been re-reviewed**, because the round cap was reached. The implementer's own review and the per-merge adversarial review cover them.

## Final status

| Document | Round-3 verdict | Round-3 items (fixed in this commit) |
|---|---|---|
| Plan / decisions / backlog / README | READY-WITH-FIXES | D7 wording, A-471 lock wording, P3b/P9/P10b/P8 dependency rows, §6 audit-check wording, §9.2 REUSED / `--resume` |
| P0, P1, P2, P3a, P3c, P3d, P4, P6, P7, P11 | READY-WITH-FIXES after round 2 (not in round-3 scope; round-2 fixes applied) | P7 template renumbered in round 3 |
| P3b | READY-WITH-FIXES | P3B3-1 (snapshot-timer `LANE_TIMEOUT` also routed to `R2BaselineTimeoutError`); P3B3-2 (a C21 oracle that can actually be built); C22 residual documented |
| P5 | READY-WITH-FIXES | P5R3-1 (C-quoted `--stdin-paths`, R5g); P5R3-2 (deletion, type-change and mode-change reporting); minor fixes |
| P8 | READY | minor fixes |
| P9 | READY-WITH-FIXES | P9R3-1 (`discard-source`, `audit-evidence-missing`); P9R3-2 (multi-invocation audit-check); P9R3-3 (write order and rollback) |
| P10a / P10b / P10c | READY-WITH-FIXES / READY-WITH-FIXES / READY | P10R3-1 (judge identity computed unconditionally under `--equivalence-audit`); minor fixes |

Verified end to end in round 3:
- **C21:** a whole-lane `BUDGET_EXCEEDED/LANE_TIMEOUT` verdict built by `_refuse_lane_with_plan` on the real B105 lane verifies `[]`. An R2-only `LANE_TIMEOUT` still fails verification.
- **C22:** it is sound under pytest-cov 7.1.0, whose `pytest_runtestloop(wrapper=True)` preserves per-item logstart order.
- **The plan's merge order** is acyclic. P10b is off the pilot path, and P9 is not blocked on P10c.

## Remaining before dispatch (controller)

These are not document defects. They are preconditions the documents name:

1. Reconcile the integration line into a new canonical branch (plan §11.1, C18).
2. Get the operator's answers on:
   - D7 (plan §11.2; the default NO is in force);
   - OC4, OC7, OC10, OC15 and OC17, plus the ledger's narrowing of the 8 h ceiling (§11.7). These are needed only before P10b.
3. Carver residuals:
   - build and witness the P3a/P3b v14 expected artifacts (§11.8);
   - write P9's carve log.
4. The implementer runs the tracer-bullet and probe steps named in each brief as its first step, for example P5's C4 probe and P3b's step 1a, and stops with BLOCKED if a probe contradicts the design.
