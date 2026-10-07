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

The corrections are in progress. The constructor now maps the whole pipe and
reader setup boundary to `ERROR/EXEC_FAILED`, leaves a started reader's drain
descriptor under reader ownership, and has focused failure-path oracles. The
report now states that subsequent evidence is pending. The corrected commit's
exact-tip review and registered gates are not complete yet.
