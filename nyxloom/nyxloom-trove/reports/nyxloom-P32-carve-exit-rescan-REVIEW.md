# nyxloom-P32-carve-exit-rescan — FRONTIER REVIEW

- **Reviewer:** independent frontier reviewer (merge gate)
- **Date:** 2026-07-16
- **Branch:** `feat/nyxloom-P32-carve-exit-rescan` @ `992826d` (implementer),
  + `<this commit>` (reviewer test-hardening)
- **Verdict:** ✅ **APPROVED** — after reviewer fixes to the O2 test coverage.
  The shipped behaviour was correct as written; its regression guard was not.

## Gate

Re-run by the reviewer, not trusted from the report:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P32-carve-exit-rescan/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

**473 passed** at `992826d` (exit 0). **475 passed** after the two tests this
review adds (exit 0). No failures, no errors.

## Git state (verified, not from the receipt)

- `git log main..feat/nyxloom-P32-carve-exit-rescan` → exactly one commit,
  `992826d`.
- `git status` in the branch worktree
  (`.worktrees/feat/nyxloom-P32-carve-exit-rescan`) → clean; no uncommitted
  work. The packet's "clean" claim is accurate. (The modified files visible in
  the *main* checkout — `legacy-workflow-origin/*`, the P31 handoff,
  `src/nyxloom/cli.py` — are pre-existing and unrelated to P32.)
- Files touched: `src/nyxloom/reconcile.py` + `tests/test_reconcile.py` —
  matches `scope.touch` exactly. Neither forbidden file (`daemon.py`,
  `config.py`) was modified. `escalate_if` did not apply: the fix genuinely
  needed only the trigger.

## What is correct

**O1 is fully met, and its test is genuinely load-bearing.** The added branch
(reconcile.py:395-404) fires `EmitAttemptExit` for an EXITED CARVER attempt of
an ACTIVE task with a receipt. I verified the downstream claim rather than
assuming it: `daemon.py:1583` routes `EmitAttemptExit` with `role == CARVER`
into `_consume_carve_exit` (daemon.py:1588), which is exactly the handler the
handoff says already works. The trigger really does reach it, so the stranded
carve is finalized to SUPERSEDED and the wip slot is freed.

**Keying on `role == Role.CARVER` rather than a `^carve-.*-\d+$` task_id regex
is sound** — and better than the oracle's literal wording. `Role.CARVER` is
constructed in exactly one place (daemon.py:1253, the carve dispatch), on a
synthetic task whose id is `f'carve-{project}-{seq}'` (daemon.py:184). The role
is therefore a strictly stronger invariant than a string match on the id. O1's
regex describes the *fixture*, not a required predicate. Not a defect.

## Finding 1 (fixed by reviewer): the O2 test was hollow

`test_carver_exited_superseded_task_no_refire` **passes with the entire CARVER
branch deleted.** It cannot fail for the reason it claims to test.

Cause: `SUPERSEDED ∈ TERMINAL_TASK_STATES` (types.py:63-65), and the attempt
loop skips terminal tasks before any attempt is examined:

```python
# reconcile.py:368
if tsf.state in TERMINAL_TASK_STATES:
    continue
```

So the SUPERSEDED fixture returns at line 368 and never reaches the branch under
test. The test asserts a true fact about the system, but the terminal-skip — not
the new code — is what makes it green.

The consequence is not cosmetic. O2 requires the branch be *"bounded exactly
like the existing IMPLEMENTER/FRONTIER_REVIEW ones"*. I mutation-tested that
claim: **stripping `tsf.state == TaskState.ACTIVE` from the branch entirely
left all 473 tests passing.** The guard is load-bearing — without it, a carve
task in any non-terminal, non-ACTIVE state (QUEUED, AWAITING_REVIEW, CARVED,
MERGE_READY…) with an EXITED carver attempt would emit `EmitAttemptExit` and be
force-fed to `_consume_carve_exit` — yet nothing in the suite noticed.

**Fix (this commit):** added `test_carver_exited_non_active_task_no_exit`, which
asserts no `EmitAttemptExit` for an EXITED CARVER attempt on a carve task in
QUEUED / AWAITING_REVIEW. Both are *non-terminal*, so the terminal-skip cannot
be what passes it — it fails iff the `ACTIVE` bound is dropped. I also corrected
the SUPERSEDED test's docstring to state honestly what it does and does not
guard, and kept it: the end-state contract it asserts is still worth pinning.

## Finding 2 (fixed by reviewer): O2's negative was untested

O2's negative names two failure modes — re-firing once finalized, **"or fires
for a non-carve task"**. Nothing in the diff covered the second.

**Fix (this commit):** added `test_non_carver_exited_active_task_no_exit` — an
EXITED `SELF_REVIEW` attempt with a receipt on an ACTIVE task must not emit.
It fails iff the `role == Role.CARVER` check is dropped.

## Mutation matrix (how each finding was proven)

Each mutation was applied to `992826d`, the full suite run, then reverted.

| Mutation | Before this review | After |
|---|---|---|
| CARVER branch deleted entirely | `test_carver_exited_active_task_emits_exit` fails ✅ | fails ✅ |
| `tsf.state == ACTIVE` guard stripped | **473 passed — undetected** ❌ | `test_carver_exited_non_active_task_no_exit` fails ✅ |
| `role == Role.CARVER` check stripped | **undetected** ❌ | `test_non_carver_exited_active_task_no_exit` fails ✅ |

O1's coverage was real from the start. O2's was not: the branch could be
unbounded in either dimension and the gate would stay green.

## Why APPROVED rather than REJECTED

The role contract says fix small defects, reject large/architectural ones. The
**implementation is correct as committed** — I did not change a line of
`reconcile.py`; the source diff ships exactly as the implementer wrote it. Both
defects are missing regression coverage on a correct 3-line branch, which is
squarely "small, fix it yourself". There is no design decision to improvise
here, and nothing about the fix's shape is in question.

## Oracle assessment

| Oracle | Test passes | Contract actually met |
|---|---|---|
| O1 | ✅ | ✅ — branch fires; verified it reaches `_consume_carve_exit` |
| O2 | ✅ | ✅ behaviour was already correct; ⚠️ *as committed* the tests proved neither bound — now pinned by two added tests |

## Note for the controller (not a P32 defect)

The packet instructed writing this report to `topos/handoff/reports/`. That path
does not exist in this repo; every prior nyxloom review lives in
`nyxloom/nyxloom-trove/reports/` (P23, P25, P26, P27). I followed the
established convention. The packet's report-path template looks stale and is
worth correcting at the source, since each reviewer will otherwise re-derive it.
