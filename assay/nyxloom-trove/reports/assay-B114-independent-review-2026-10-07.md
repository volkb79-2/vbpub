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

The finding was addressed in the follow-up under review. Its focused tests,
exact-tip review, and registered gates are recorded in the B114 agent log.
