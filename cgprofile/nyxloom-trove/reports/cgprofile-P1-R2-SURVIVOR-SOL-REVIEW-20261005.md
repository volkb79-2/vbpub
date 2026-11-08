# P1 R2 survivor-oracle supplemental review — 2026-10-05

Controller-maintained evidence record of a fresh Sol/xhigh review and its
same-reviewer fix verification. The caller selected the route; no reviewer
self-attestation was requested. This is scoped to the P1 survivor-oracle
delta, not a new full daemon review, release approval, or daemon deployment
authorization.

## Initial review — conditional

- Candidate: `c24b0d2b882df96ee6549cf357a713943398a564`.
- Base/current main at the initial review: `67c4c27328a76ded1cb0b8ac192382386d65e796`.
- Worktree was clean before and after inspection. Direct delta: the controller
  log, `lib/serve.py`, P1 daemon report, and `tests/test_access.py`,
  `tests/test_damon.py`, `tests/test_serve.py`.
- The reviewer independently read the old exact-tree R2 verdict/progress and
  confirmed 114 candidates, 89 killed, 25 survived. It checked all 25
  dispositions against source and test behavior. The P1 report's survivor
  table is the disposition matrix: 19 behavioral oracle gaps and six
  contract-equivalent mutants after correction.
- Verdict: `ACCEPT-CONDITIONAL` for provisional integration only, pending the
  corrections below. No tests, gates, containers, or probes were run by the
  reviewer.

### Required corrections

1. Survivor `ffbf084aaf44dc23` (`damon.py:495`, `True` to `False`) had been
   described as a behavioral gap, but its test asserted only private
   `_foreign_growth` state. Under the supported DAMON sysfs count-write
   behavior, indexed objects are removed and recreated; identity reconciliation
   quarantines the replaced slots and prevents reuse or shrink even without
   that sticky flag. It is therefore a sixth contract-equivalent, not a
   behaviorally killed mutant. The test now asserts refusal, quarantine, and
   preservation of foreign state without asserting the private flag.
2. `_close_damon_session()` treated missing `cleanup_confirmed` as
   unconfirmed but also claimed an owned slot would not be reused when there
   was no matching pool quarantine. It now makes that claim only when the
   identified slot is actually quarantined; otherwise it states that cleanup
   is unverified and no no-reuse guarantee is established. Tests cover missing
   identity, a matching quarantine, and an unrelated quarantined index.
3. The lock-override regression had spied on a helper call. It now proves
   filesystem behavior: an explicit override retains its pre-existing mode
   and group, while a separate default-path case verifies repair to mode
   `0660` and the parent group.

## Same-reviewer fix verification — accepted for provisional integration

- Reconciled target: branch `rg55-p1-review-repairs-20261005`, HEAD
  `99cffbe09d9fe72d45ee34ad61b4c767b950a1c1`.
- This contains the fixes in `a8a53fb43e71c206312ce6f48cab5b222bda610f`
  and a no-ff merge of current main `faa812f169a540d4ac206d1440751d12ef28be60`.
  The branch merge-base is that main tip; the merge added the then-current
  backlog edit and did not alter P1 source or tests from the repair commit.
- The same reviewer confirmed all three corrections and found no new blocking
  issue. Verdict: `ACCEPT` for provisional integration, not release.
- Controller-run focused regressions on the same source/test content: seven
  passed in 1.95 seconds. The reviewer did not independently rerun them.
- Controller-run `run-gate.py doctor` on `99cffbe`: 9 OK, 2 warnings, 0
  failures, 2 info. Warnings were the linked-worktree host-lane git view and
  the intentionally down profiler daemon. The reviewer did not independently
  verify this doctor receipt.
- Exact-tip registered `r0-r1` and `r3` after this review record is committed
  remain required before provisional integration. The current R2 campaign on
  `c24b0d2b` is tree-specific and does not judge this repaired tip.

## Evidence boundary

The historical P1 R2 at `33cfb150` remains `FAIL/MUTANTS_SURVIVED` (114
candidates, 89 killed, 25 survived). Human equivalence dispositions do not
change Assay's raw verdict. A new exact-tree R2 and final survivor triage are
required for release, along with the registered full gate. P3 still lacks
live DAMON samples and measured DAMON overhead; socket-carrier acceptance also
requires the host daemon/socket to be present. Nothing in this review grants
release, installation, publication, or daemon activation clearance.
