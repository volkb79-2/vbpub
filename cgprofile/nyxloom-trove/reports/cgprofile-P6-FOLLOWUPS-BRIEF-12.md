# P6 controller checkpoint — survivor restoration and fresh review series

This brief supersedes earlier process, review-round, and gate-status
instructions. Continue only from the current branch and reconcile it with
shared main before final evidence.

## Current branch state

- Branch/worktree: `rg55-followups-cgprofile-final` at
  `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`.
- Code commit: `738bf1f5cb8ea52b751078052d52a551feee485e`
  (`fix(cgprofile): restore placed survivors across private PID namespaces`).
  This brief and the Session 19 records are to be committed on this branch
  next.
- At checkpoint creation, merge-base with `main` was
  `4d32bcfea566ea36ae8835dcdf4e820b3183a501`; shared main was
  `87c13eff5b03c65f07733a282c5b1dac24609e54`. The branch is stale and must
  merge current main before final gates.
- The root and `scripts/cgroup-profiler/docs/` interface contracts must remain
  byte-identical.

## What changed and what was verified

The placement daemon uses private PID/cgroup namespaces. Its host-systemd
bridge can move host-visible PIDs into the lane leaf, but teardown previously
read namespace-local `cgroup.procs` and swallowed failures returning survivors
to their original scope. The `738bf1f5` repair now:

- enumerates host-visible leaf tasks only through a verified host-proc view;
- rechecks each task's cgroup before moving it;
- writes the exact origin `cgroup.procs` first and uses
  `AttachProcessesToUnit` only on `ESRCH`, with a safe nearest unit/subgroup
  derived from the absolute original cgroup path;
- verifies origin membership through host `/proc` and records successful
  systemd-mediated writes in the D-25 event stream;
- refuses to mark placement released or remove the leaf while any survivor
  cannot be safely restored.

Pre-commit focused result: `tests/test_serve_placement.py`, **122 passed**.
Branch-aware coverage of `lib/placement.py`: **398/398 statements and
166/166 branches**. The broader cockpit suite could not collect because
optional `pandas` and `matplotlib` packages are absent; this is not a code
gate. Registered gate evidence has not been produced for this repair.

## Review and mutation status

- P6 prior review artifacts rounds 1–4 are preserved. Rounds 1–2 were
  conditional, round 3 was environmentally blocked, and round 4 rejected
  B1–B3. The latest code repairs the live timestamp and kill findings and
  supplies the private-PID placement/move-back bridge. A genuinely fresh
  GPT-6-Sol xhigh reviewer is required after final short gates and live probes;
  this is a new three-round series at rounds 5–7. Caller selects/verifies the
  Sol route; do not ask the reviewer to self-attest model/effort.
- Latest prior P6 R2 is exact tree `6540f87761a66ff933c8bb45f81d8ac9117f407b`,
  terminal `BUDGET_EXCEEDED/CANDIDATE_HUNG` (312 accounted; 301 killed, 10
  survived, 1 hung). It is diagnostic only; the changed tree needs a fresh
  exact-tree R2.
- The current-tree R2 and registered full gate remain release blockers.
  Provisional integration after accepted review and green short gates does
  not authorize cgprofile 1.1.0, install, or `ciu up`.

## Required continuation

1. Commit this checkpoint and the Session 19 log/report/handoff updates.
2. Wait for the separately managed B107 exact-tree evidence and merge; then
   reconcile the resulting current `main` into this P6 branch. Do not touch
   another worktree's dirty files or running tree.
3. Verify host memory PSI `full avg10 <= 5`, loaded
   `$CGROUP_PARENT_DEV_GATES`, and exact active containers before each gate.
   Keep the candidate HEAD quiet during all judgments. Run registered exact-tip
   short gates (`r0-r1` and `r3`), capturing each exact container's loaded
   parent and 3-CPU cap. Require 100% changed executable lines and branches.
4. Build a review-owned daemon using private PID/cgroup namespaces and perform
   the live stop-time survivor probe: verify return to the original systemd
   scope through the explicit host-proc view, D-25 event emission, and leaf
   removal only after verified restoration. Exercise a fail-closed restoration
   refusal and prove it retains the leaf/unreleased state. Do not touch the
   singleton or any other agent's containers/networks.
5. Run the fresh caller-configured Sol review series starting at round 5, with
   rounds 1–4 and this brief supplied as prior evidence. If Sol repairs code,
   resume that same reviewer while alive and rerun affected exact-tree gates.
6. On ACCEPT and green short gates, provisionally merge `--no-ff` only if
   authorized by the current wave policy. Run the replacement R2 and
   registered full gate in an isolated CIU worktree, asynchronously; release
   only after both pass and survivor disposition is complete.
7. Keep P3's real DAMON series/overhead measurement as an independent
   close-out requirement; state any host limitation honestly.

## Resource note

At 2026-09-28 22:56:51Z, process inventory showed three mutation slots occupied
by other work: PIDs `1573821`, `3330133`, and `3351574` (containers
`run-gate-vbpub-mutation-1573821-1790475805`,
`run-gate-vbpub-mutation-3330133-1790628741`, and
`run-gate-vbpub-session-extract-3351574-1790629480`). Do not disturb them.
The next status observation must respect the operator's 25-minute cadence
unless a concrete earlier completion or error is expected.
