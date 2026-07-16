# nyxloom-P34-resume-safety-guarded — INDEPENDENT FRONTIER REVIEW

- Reviewer: independent frontier reviewer (merge gate), 2026-07-16
- Reviewed commit: `4ea996c` (implementer) on `feat/nyxloom-P34-resume-safety-guarded`
- Review-fix commit: mine, this branch (tests only; no source change)
- Gate: `tester-unified`, re-run by me from the branch's own worktree

VERDICT: APPROVED

(As with P33, the single machine-readable verdict line above is deliberately the
only line-anchored verdict token in this file. `_parse_review_verdict` collects
`^\s*VERDICT:\s*(APPROVED|REJECTED)` matches into a set and fail-safes to
rejected on a conflicting pair, so every other mention here is inline-code
quoted — a leading backtick is not `\s`, so quoted mentions cannot match.)

## Summary

**This re-cut does what P26 could not, and I am approving it.** The decision
table is implemented exactly as specified, the six-state guard matrix genuinely
refuses the dispatch P26 was rejected for, and the sequence provably terminates.
Scope was respected precisely: five files, all in `scope.touch`, none of the
four forbidden files touched, no new Action / Attempt state / event type. The
gate is green and I re-ran it myself rather than trusting the report: **521
passed** (520 before my fixes, +1 from a test I added).

I did not take the passing tests on faith. I mutation-tested the branch — four
independent mutations, each reverting one load-bearing rule — to check whether
the suite actually discriminates. Three of the four were caught cleanly. The
fourth exposed a real, fixable weakness in the *tests* (never in the source),
which I fixed on the branch:

| Mutation (rule reverted) | Caught by | Verdict |
| --- | --- | --- |
| A — poison detection off (pre-P34 behaviour) | 8 of 13 | **O6 passed → hollow** (F1) |
| B — `fresh_start_eligible` bypassed (**P26's exact defect**) | O4 c3–c6 | solid |
| C — record budget blind to receiptless records (**P26's unbounded bug**) | O6 | solid |
| D — latest-only rule removed | O4 c1, O5 | solid |

Mutations B and C are the two defects that got P26 reverted. Both are caught by
real tests. That is the core of this package and it holds.

The receipt at `att-ad088ac5ddd9/receipt.json` is empty, so per the role contract
I verified against git directly (`git log main..feat/…`, `git status`): exactly
one commit, worktree clean, diff stat matching the packet. Nothing was hidden.

## Findings

### F1 (moderate, fixed by me) — O6's test was hollow: it passed with P34 disabled

The strongest finding of this review. `test_o6_record_budget_gone_types_blocked`
passed with poison detection **entirely disabled** (mutation A: `poisoned =
False`), so as committed it asserted nothing about P34.

Root cause: the fixture's attempt had no `session_handle`. O6 asserts
`Transition(to=BLOCKED)` with an `ENVIRONMENT` blocker — but P14's *pre-existing*
no-handle dead-end emits a byte-identical transition. Both paths converge on the
same assertion target, so a handle-less fixture cannot tell the record-budget
dead-end (the thing O6 exists to prove) from the no-handle dead-end that has
shipped since P14.

The fix is one line of fixture, and it is exactly the state the oracle describes:
give the poisoned attempt a `session_handle`. With a handle, the pre-P34 planner
takes the `ResumeAttempt` branch instead (`attempts_count` is 0 — a receiptless
record is invisible to it), so the test now genuinely discriminates. I also added
an explicit "never resumes, even on the dead-end path" assertion.

Verified: O6 now **fails** under mutation A and still passes against real source.

### F2 (minor, fixed by me) — O1's "zero ResumeAttempt" assertion was vacuous

Same root cause. `test_o1_…_fresh_dispatch` omitted `session_handle`, and
`ResumeAttempt` is unreachable for a handle-less attempt whether or not it is
poisoned — so `assert len(resumes) == 0` held trivially. O1's negative is
specifically *"the planner keeps emitting ResumeAttempt for the same poisoned
session_handle every pass"*, which the fixture therefore never modelled.

The `dispatches == 1` half was always meaningful (O1 did fail under mutation A),
so this was a weak assertion rather than a hollow test. Fixed by setting a
handle, which makes both halves load-bearing and matches the live scenario: a
handle that exists and keeps being resumed forever.

### F3 (minor, fixed by me) — O5's exhaustion clause was asserted but never driven

O5 requires: *"Repeated to exhaustion, the sequence terminates: after
`max_attempts_per_task` distinct IMPLEMENTER records the planner emits the typed
BLOCKED of O6, never another dispatch."* The committed test drives two passes,
stops at `max_attempts_per_task=3`, and never reaches the budget — the second
pass parks only because the poisoned record is no longer latest. Termination was
therefore argued (O5's park + O6's single-record block) but never demonstrated.

That matters more here than it usually would: P26's defect was *non-termination*,
and a single-pass assertion cannot exclude a loop. I added
`test_o5_fresh_start_sequence_terminates_at_record_budget`, which drives real
plan/apply cycles to a fixed point in the worst case the oracle is about — every
fresh start dies poisoned the same way — and asserts the planner blocks, emits
exactly one fresh start per unused record slot, and never dispatches and blocks
in the same pass.

## Verified, no action needed

- **The fresh-start correctly bypasses the `dispatch_capacity` cap.** P26 was
  faulted for passing neither `dispatch_eligible` nor the capacity cap, and
  `fresh_start_eligible` deliberately reuses only checks 1/6/7/8 — so I checked
  whether the omitted cap is a hole. It is not, and applying it would be a bug:
  `dispatch_capacity = max_active_tasks - active_count`, and `active_count`
  already counts ACTIVE tasks. A fresh-start target is ACTIVE by construction, so
  it has *already consumed its own slot* — the identical reasoning the handoff
  gives for excluding the wip-cap. Concurrency stays bounded by
  `max_active_tasks`, and the latest-only rule bounds it to at most one dispatch
  per task per pass.
- **Detection is wired to the real naming contract.** `_resume_failures` globs
  `attempt.resume-*.log` in `paths.attempt_dir(project, attempt_id)` — the exact
  directory and naming the `ResumeAttempt` handler writes via `_next_resume_n`
  (daemon.py:1402, :1492). The grace window is a strict `now - mtime >
  grace_seconds`, matching the spec. The P26 `size <= 200 bytes` progress rule
  that scored a noisily-dying session as healthy is gone, as instructed.
- **Guard ordering matches the decision table.** Record-budget is tested before
  the transient guards, so a spent budget yields BLOCKED rather than a silent
  park. O4 c3–c6 pass only because the budget check does *not* fire first
  (`max_attempts_per_task=3` vs 1 record), which pins the ordering.
- **The drain-agents branch still precedes the poisoned branch**, so a P34
  fresh-start — a new agent process — is correctly suppressed during drain.
- **O2's healthy path is genuinely unchanged**: it passes under mutation A, as it
  must, since it does not depend on the poisoned branch.

## Residual risk (out of scope, flagged not fixed)

`daemon._resume_failures` — the detection function the whole package keys on —
has **no test**. This is a contract consequence, not implementer negligence:
`scope.touch` lists `tests/test_reconcile.py` only, so a `tests/test_daemon.py`
case was not available to the implementer, and I held to the same boundary rather
than improvising past it (the P26 failure mode). The oracles O1–O6 are all
planner/config/schema-level and are met. The mtime/glob logic is simple and I
read it against the real `_next_resume_n` naming contract by hand, but it is
carried by inspection, not by the gate. Worth a follow-up package.

## What I fixed

All fixes are in `tests/test_reconcile.py` (in `scope.touch`). **No source file
was modified** — the implementation needed no correction. I restored
`reconcile.py` after each mutation and confirmed the branch is clean apart from
my test changes.

1. F1 — `session_handle` on O6's fixture + a no-resume assertion, so the test
   discriminates the record-budget dead-end from P14's no-handle dead-end.
2. F2 — `session_handle` on O1's fixture, making "zero ResumeAttempt" real.
3. F3 — new `test_o5_fresh_start_sequence_terminates_at_record_budget`, driving
   the sequence to a fixed point.
4. `session_handle` on O5's first-pass fixture, for the same reason as F2.

## Gate evidence (re-run by me, not quoted from the report)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/feat/nyxloom-P34-resume-safety-guarded/nyxloom \
    && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
=> 521 passed in 73.31s   (PYTEST_EXIT=0)
```

P34 oracle subset: 14 passed (13 committed + 1 added by me).

## Reasoning for the verdict

The package meets every named oracle, honours the scope and forbid lists exactly,
and resolves the contradiction that made P26 unimplementable — it never marks the
poisoned attempt terminal at all. The two defects that got P26 reverted (guard
bypass, unbounded re-dispatch) are each pinned by tests that I confirmed fail
when the corresponding rule is removed.

The three defects I found were confined to test strength, all shared one root
cause (fixtures omitting the `session_handle` that defines the live bug), and
none indicated a fault in the implementation. They were small and inside the
declared scope, so per the role contract I fixed them on the branch rather than
rejecting. Approving.
