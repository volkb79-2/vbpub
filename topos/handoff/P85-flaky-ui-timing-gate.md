# P85 - The UI timing tests are flaky, which makes the gate unreadable

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** none
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** the flake turns out to be a real race in the snapshot/status code rather than a test-timing artifact. That is a product bug, not a test bug - say so in the REPORT and do not paper over it with a retry or a longer sleep.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P79 pass #2). Found by
re-running the full gates from main, which is exactly what that rule exists to catch.
Not a child of P79's diff: P79 touches the recording reader and never loads the UI.
-->

## Goal

Two tests fail intermittently on **unmodified `main`**, independent of any package:

- `tests/test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately`
  - fails roughly 1 run in 3 when run in isolation on `main`.
- `tests/test_record.py::test_record_cli_runs_ui_and_writes_frames`
  - passes in isolation, fails under full-suite load.

A flaky gate costs the same thing a permanently-red gate costs, which is why P82 was
worth a package: reviewers learn to skim past failures, and the next genuine
regression hides behind the familiar noise. In P79's review both failures had to be
hand-characterised (isolated re-runs, then re-runs on `main`) before the package's
own results could be trusted. That is a recurring tax on every future review.

## Required Contracts

1. **Diagnose before you fix.** Say in the REPORT *why* each test is flaky. A
   `sleep`, a retry decorator, or a bumped timeout that makes the symptom go away
   without naming the race will be rejected - that converts a flaky test into a slow
   test that still cannot fail honestly.
2. **Distinguish test-timing artifact from product race.** `..._appears_immediately`
   asserts on a status that is set asynchronously. If the status genuinely can be
   missed by a real user, that is a product bug and the test is right. Decide which,
   and defend it.
3. **The tests must still be able to fail.** Whatever the repair, breaking the
   behavior under test must turn them red. Prove it in the REPORT (mutate, show the
   red).
4. **No sweeping.** Repair these two, audited. If other tests are flaky the same
   way, *name* them in the REPORT; do not fix them silently in the same diff.

## Acceptance Oracles (numbered, adversarial)

1. Each repaired test run **20x in a row** from `main` is green 20/20. Quote the
   command and the count - a single green run proves nothing about a 1-in-3 flake.
2. Each repaired test is green **under full-suite load**, not just in isolation
   (`test_record_cli_runs_ui_and_writes_frames` only fails under load, so an
   isolated green is not evidence).
3. Breaking the behavior each test covers turns it red (contract 3).
4. The full suite from `main` is green across 3 consecutive runs.

## Out Of Scope

- Any non-UI test.
- The optional-extra environment pinning (P84 owns that).
- Rewriting the Textual test harness.

## Docs

`docs/STATUS.md` if the flake was a product race.

## Gates

```bash
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q
python3 -m py_compile <changed files>
git diff --check
```
