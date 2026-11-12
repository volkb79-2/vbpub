# cgprofile P6 follow-ups — controller review round 1

**Reviewer:** Luna xhigh controller review (not the independent Sol review)
**Reviewed tip:** `54e0a364` (`bb1042a6` reconciliation plus the adopter-doc
version correction)
**Date:** 2026-09-23 14:25:13Z
**Verdict:** ACCEPT-CONDITIONAL

## Scope and evidence

I reviewed the P6 implementation and its reconciliation with the current P1
daemon, including `lib/serve.py`, `lib/placement.py`, `lib/liveness.py`, the
DAMON/store/summary/analyze paths, CLI wire validation, ciu/infra/configuration,
the contract and all changed user-facing documentation, tests, goldens and
the frozen cross-package fixture copy. I read the P6 LOG/REPORT/BRIEF-10 and
the standing P6 review handoff, and checked the current implementation against
contract §8 and the D-15/D-20/D-25/D-27..D-30 rulings.

The focused daemon suite on the reconciled tree passed **591 tests, 6
skipped**. The three documentation tests passed after the version example
repair. The contract mirror comparison returned zero: the two
`RG55-INTERFACE-CONTRACT.md` copies are byte-identical.

## Findings

No new merge-blocking code defect was found.

* The P1 sample-zero integration at `lib/serve.py:865-904` records one
  synchronous sample and feeds the identical snapshot into liveness. This
  keeps summary, status and liveness baselines aligned and was covered by the
  reconciled golden tests.
* The placement boundary remains a whitelist. `lib/placement.py:408-439`
  delegates only the three allowed controllers, while the guard rejects
  non-leaf paths, symlink escapes and negative subtree-control values. Cap
  values are read back after writes (`:416-429`), and survivor migration reads
  the leaf's own `cgroup.procs` before release (`:516-555`).
* The liveness kill path distinguishes a placed leaf from an unplaced shared
  scope and refuses the latter rather than risking the devcontainer. The
  CP-12 finalization repair is present; the session is finalized after an
  enforced kill and the stream can emit its terminal `end`.
* Socket peer credentials, wire-shape validation, response validation, DAMON
  sample pairing, event persistence, manifest limits, run-id widening,
  host-PSI seam, daemon slice reporting, and fixture identity were all
  inspected against their tests and the prior report evidence.
* README, CONSUMERS.md and DESIGN-GUIDE.md had stale adopter-facing `1.0.0`
  examples. They were corrected to `1.1.0` in `54e0a364`; the frozen wire
  contract examples remain unchanged and the contract copies remain
  byte-identical.

## Conditions before final merge/release

This is not the independent final release review and does not waive it. The
following remain required:

1. Fresh registered `r0-r1` and `r3` gates on the exact quiet post-review tip.
2. P6 R2 mutation evidence on the exact judged tree, with every survivor
   disposition recorded. The existing P6 report's earlier R2 was not treated
   as evidence for this reconciled tree.
3. The required fresh Sol xhigh adversarial review before the final merge and
   release, with live probes or an explicit host-capacity disclosure.
4. Final live daemon probes for both carriers, placement and watch kill/end;
   the earlier probe evidence predates this reconciliation and is not silently
   substituted for a current-tip probe.

The current P6 worktree is clean. No product decision was reopened and no
operator-owned path was changed.

Co-Authored-By: Luna xhigh <noreply@anthropic.com>
