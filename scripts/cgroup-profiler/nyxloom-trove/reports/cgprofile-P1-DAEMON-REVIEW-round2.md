# RG-55 P1 daemon adversarial review — round 2

**Verdict: ACCEPT-CONDITIONAL**

- Reviewed tip: `9471a9af77680b969e0867b680ccee299d153583`
- Comparison: full P1 implementation against `main`; the reviewed source
  implementation is the same source tree as `a2c2501f`; the tip change is the
  mutation-evidence addendum only.
- Reviewer: controller's Luna xhigh blind/read-only review.
- Scope: daemon safety and lifecycle, contract/CLI validation, incremental
  arithmetic, subtree discovery, DAMON ownership, persistence/recovery,
  retention, image/stack configuration, docs, and prior review dispositions.

## Result

No new merge-blocking defect was found in the reviewed source tree. The eight
round-1 blockers are repaired and covered by focused tests as documented in the
P1 report. The implementation keeps the required safety boundaries: daemon
writes are guarded to the sessions root or DAMON admin root, the image has no
network and no Docker socket, the daemon's DAMON pool distinguishes owned from
foreign kdamonds, session stop is incremental, no-token starts are independent,
and malformed ctl responses are refused as daemon faults rather than accepted
as successful measurements.

The conditional status is deliberate. This review does not convert the
mutation lane's one reachable-state-equivalent survivor into an assay PASS,
and it does not replace the final quiet gate set or any required independent
release review. Those conditions are:

1. retain the exact R2 verdict and the written equivalence proof for
   `lib/damon.py:325`;
2. complete the final `r0-r1`, `r3`, and release gates on the final release
   tree, with HEAD quiet and verdicts read separately; and
3. complete the required final independent review/live acceptance before
   release if the controller's standing merge policy still requires it.

## Checks performed

- Read the contract and P1 handoff/report, then inspected the full P1 source
  surface rather than relying on the implementer's summary.
- Checked `lib/damon.py` pool baseline/ownership/free-slot teardown and
  `DamonSession` exception/stop cleanup paths.
- Checked `lib/serve.py` write guarding, restart replay, retention, response
  validation, token deduplication, sample-zero persistence, and report path
  handling.
- Checked `lib/summary.py` nearest-rank arithmetic, null propagation,
  scope-specific counter semantics, per-pair CPU elapsed time, and DAMON
  summary handling.
- Checked `lib/subtree.py` discovery fallbacks and the Docker/ciu stack for
  the authored safety properties recorded in the P1 report.
- Verified the worktree was clean and `git diff --check` produced no output.
- Reconciled the current R2 record: 250 candidates, 249 killed, one survived,
  no budget/crash/hung classifications; the survivor is the documented
  `Gt->GtE` equivalent on the pool release guard.
- Relied on the existing P1 live-acceptance record for the real daemon probe;
  this read-only review did not start or mutate the daemon.

## Non-blocking observation

Restart replay pairs `samples.jsonl` and `host.jsonl` by file order. That is
the documented on-disk design and is safe for the normal append sequence, but
a process death between the two independent appends can leave a final sample
without a host row. The current recovery path intentionally preserves only
complete pairs. This is not a contract blocker for P1, but it is a suitable
follow-up if crash-consistent per-tick pairing becomes a requirement; changing
it now would invalidate the active mutation tree.

