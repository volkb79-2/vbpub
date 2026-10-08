# Assay B114 independent post-merge review — 2026-10-07

Scope: Assay delta from `ccf667fdf67a4d50611eb20f1d37b3de805aa91f` through
merge `2dd9b784f31d298f68a6e4e762051df6e7e2f3f6`.

## P2 — Receipt reader startup failure escaped without a verdict

**Observed:** `ReceiptCapture.__init__` started its drain thread after the
descriptor-cleanup block. If `Thread.start()` raised, the read, write, and
drain descriptors remained open. The candidate path re-raised the exception
before the post-attempt cgroup process counters were compared, while the
executor's future-result handler accepts only `AssayError`.

**Inference:** A process limit reached after B148's preflight could make reader
thread creation fail, escape as an unhandled `RuntimeError`, and leave the
candidate run without the required infrastructure verdict. The fix must close
the descriptors, raise a typed execution refusal, and prove a thread-start
failure cannot be classified as a kill.

The initial finding was addressed by commit `4ec3b35ea`. Its exact-tip follow-up
review found additional typed-setup and descriptor-ownership cases; those
corrections and their focused tests, review, and registered gates remain
pending and will be recorded in the B114 agent log.

## Follow-up review of `4ec3b35ea`

The independent review found three actionable gaps:

- **P2:** `os.pipe()` and `os.dup()` / `os.set_blocking()` failures remained
  raw `OSError`s. The mutation future handler accepts only `AssayError`, so
  these setup failures could also escape without an infrastructure verdict.
- **P3:** if `Thread.start()` raises after the reader has started, its `finally`
  block owns closing the drain descriptor. The constructor must not close that
  descriptor again after joining; another worker may already have reused its
  number.
- **P3:** this report said the follow-up test, review, and gate evidence “are
  recorded” while they were still pending.

The follow-up now maps the whole pipe and reader setup boundary to
`ERROR/EXEC_FAILED` and coordinates reader use and startup cleanup through a
shared lock and closed flag. The report states that subsequent evidence is
pending. The corrected commit's exact-tip review and registered gates are not
complete yet.

## Follow-up review of `b0d97239c`

The independent review found one remaining **P3** ambiguity. If the OS thread
has started but `Thread.start()` raises before publishing `ident`, the parent
cannot infer descriptor ownership from `thread.ident`; closing the descriptor
can race with the reader's `finally` close. The started-then-raised oracle in
that commit forced the exception only after `start()` returned and did not
cover the unpublished-ident state.

The current correction uses one lock and closed flag shared by the reader and
startup cleanup. The reader holds the lock while using the descriptor; either
the reader or startup cleanup closes it once, and a late reader exits without
touching it. The regression masks `ident` while a real reader thread starts
and asserts exactly one close and a joined thread. The full unit module passed
after this change (**98 passed in 12.80s**); exact-tip review and registered
gates for the corrected commit remain pending.

## Follow-up review of `b3e706adc`

The independent review found one **P3** test gap: its simulated reader started
before the start error was raised, but it did not wait outside `_drain` while
startup cleanup closed and reused the descriptor. The test therefore did not
prove the `_drain_fd_closed` guard prevents late registration or read.

The revised oracle holds the real reader at a barrier before `_drain` acquires
the shared lock. Startup cleanup closes the drain descriptor, reuses its
number for `/dev/null`, then releases the reader. A poll proxy and `os.read`
observer fail if the reader touches that number after reuse. The full
`test_mutation_witness_unit.py` module passed (**98 passed in 12.75s**); the
new exact-tip review and registered gates remain pending.

After the barrier oracle was added, the full unit module passed again
(**98 passed in 12.74s**), Ruff `E4,E7,E9,F` and `git diff --check` passed.
The exact-tip review of the strengthened oracle and registered gates remain
pending.

## Follow-up review of `9ffec7644`

The independent review found two **P3** cleanup gaps in the barrier oracle:

- It checked for late descriptor access before a final reader join, so an
  unusually late reader might access the reused FD after the assertion.
- A failed thread-exit assertion occurred before the reused FD was closed,
  which could leave both a daemon reader and descriptor behind as monkeypatches
  were restored.

The correction joins and confirms reader exit before inspecting access records.
Its `finally` always releases the barrier, joins the reader again, and closes
the reused descriptor even when an assertion fails. The targeted late-reader
case passed. A follow-up abort signal prevents a delayed reader from entering
the drain path during failure cleanup. The full unit module passed (**98
passed in 12.89s**), Ruff `E4,E7,E9,F` and `git diff --check` passed. The
corrected exact-tip review and registered gates remain pending.

## Follow-up review of `8fe585ec3`

The independent review found one **P3** cleanup gap: if a bounded join timed
out, the test could restore its `os.read` and poll instrumentation while the
reader remained alive. Failure cleanup now sets the abort and stop signals,
releases the barrier, and waits for the reader to terminate before restoring
instrumentation. It closes the reused descriptor after termination. The full
`test_mutation_witness_unit.py` module passed (**98 passed in 13.19s**); Ruff
`E4,E7,E9,F` and `git diff --check` passed. A fresh exact-tip review and
registered gates remain pending.

## Final exact-tree review — `d324754c8669296e46555434499c07bbed73e9c1`

The complete committed tree difference from main's B114 integration commit
`2dd9b784f31d298f68a6e4e762051df6e7e2f3f6` received a read-only Sol xhigh
review through `.codex2`. The reviewer reported **no findings**. The branch does
not contain `2dd9b784` as an ancestor, so the review compared the two committed
trees directly; it also inspected the branch history. `HEAD` was
`d324754c8669296e46555434499c07bbed73e9c1` and `git status --short` was empty
both before and after review. The review made no file changes. Merge and
registered gates on the resulting main revision remain pending.

## Post-review registered gate result

The registered `tester-unified` gate on merge
`eabb6a10c07b4b74c957029002e998d9e137be4f` exited 1 after 483.61s with
**7,826 passed, 11 skipped, 1 failed**. The failed real-run receipt-start
oracle injected only when the caller was not `MainThread`, but the seeded
`jobs=1` candidate can execute inline. Since `ReceiptCapture` is created only
for an active R2 candidate, the oracle now fails the named receipt-reader
start independent of caller thread. The focused oracle passed locally (**1
passed in 0.46s**); Ruff and `git diff --check` passed. Exact-tip review and a
registered gate on the correction remain pending.

## Follow-up review of `e2f5109f3`

The Sol xhigh review found one **P1** oracle gap: the revised global thread
hook could fail `runner.py`'s coverage-baseline receipt before candidate
execution, and the test allowed an absent mutation payload. The correction
patches only `mutation.ReceiptCapture`, the alias used at the candidate call
site, and asserts that this factory ran exactly once before accepting the
`ERROR/EXEC_FAILED` verdict. This leaves the baseline reader untouched. The
next registered tester-unified run still stopped before the candidate because
the fixture declared three sites but `max_mutants=1`. Raising the fixture cap
to 3 reaches the candidate and preserves the startup failure injection. The
focused real-run test passed locally (**1 passed in 1.37s**); Ruff and
`git diff --check` passed. A new exact-tip review and registered gate remain
pending.

## Post-review registered gate result — candidate cap

The registered `tester-unified` gate on merge
`b10b861813a991a7d9fd261dd154cab14bb78f2d` exited 1 after 476.54s with
**7,826 passed, 11 skipped, 1 failed**. The only failure was the new
candidate-reach assertion: `max_mutants=1` was below the fixture's three
discovered sites, so Assay correctly refused before submission. The test now
sets `max_mutants=3`. Its focused real-run case passed locally (**1 passed in
1.37s**); Ruff and `git diff --check` passed. The correction needs a fresh
exact-tip review, merge, registered gate, and preflight.

## Follow-up review of `466a94eeb`

The Sol xhigh review of the correction from `e2f5109f3` reported **no
findings**. The candidate-only `mutation.ReceiptCapture` seam leaves the
runner's coverage-baseline capture untouched and fails explicitly if the
candidate call site is not reached. `HEAD` was
`466a94eebb1028023237adf9f2dba09eaef8d811` and status was clean before and
after the `.codex2` read-only review. A corrected-tip registered gate remains
pending.
