# P6 controller checkpoint — reconciled after provisional B107 integration

This brief supersedes the branch/base and process-state portions of
BRIEF-12. BRIEF-12 remains the implementation history for the P6 repair;
Session 19 below is still the code-change description.

## Exact integration state

- Branch/worktree: `rg55-followups-cgprofile-final`,
  `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`.
- P6 repair commit: `738bf1f5cb8ea52b751078052d52a551feee485e`.
- P6 reconciliation merge: `b53c5ffa1c55415a93f36beac63d320e478ebbfb`.
- Merge parents: P6 `14fae51e3b030f281dad79995ffd7f73b5c76a05` and current
  `main` `3a8bbe54068d46f34652b2ed52d19a7cddb53dd6`.
- Current-main includes B107's provisional integration. The merge conflict
  resolution retained P6's stop-time systemd restoration contract and the
  newer main controller rulings through RW-362. The root interface contract
  and `scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md` are
  byte-identical. Do not rewrite historical records or touch the shared dirty
  root `AGENTS.md`.
- Before running P6 final gates, commit the small controller checkpoint
  updates made with this brief; the exact candidate is the resulting clean
  HEAD, which the controller must record below and keep quiet during each
  judgment.

## B107 gate running independently

The registered `assay/run-gate.py tester-unified` gate is running from its
separate CIU worktree at the exact B107-integrated tree
`3a8bbe54068d46f34652b2ed52d19a7cddb53dd6`. It is not P6 gate evidence and
must not be disturbed.

- Kickoff: 2026-09-28 23:36:14Z.
- Worktree: `.worktrees/rg55-b107-ciu-anchor-20260928/.worktrees/rg55-b107-full-gate-20260928`.
- Container: `run-gate-assay-selfhosted-3634378-21136-1790638575`.
- At 23:38:31Z, 2m17s after kickoff, the 3-CPU cap was read back as
  `NanoCpus=3000000000`, `CgroupParent=dev-gates.slice`; wheel install and
  multiple Assay verdict-schema phases were progressing. No final verdict is
  available yet. Comparable registered tester-unified gates took about
  19–21 minutes, so the expected completion window is roughly 23:55–23:57Z.
  Do not poll routinely before that window; preserve the separate verdict and
  wrapper exit marker when it completes.

## P6 evidence status and next actions

The 122 focused placement tests and `lib/placement.py` coverage of 398/398
statements and 166/166 branches predate the main reconciliation and are not
registered current-tip gate evidence. The prior P6 mutation verdict at
`6540f87761a66ff933c8bb45f81d8ac9117f407b` remains
`BUDGET_EXCEEDED/CANDIDATE_HUNG`; it does not qualify this tree.

1. Run exact-tip registered `r0-r1` and `r3` on the clean P6 candidate after
   this checkpoint commit. Record each gate's verdict, exit status, exact
   container name, loaded `dev-gates.slice`, and 3-CPU readback.
2. Judge 100% changed executable lines and branches on the candidate after
   main reconciliation; preserve the coverage JSON and branch-aware judge
   output. Re-run any affected exact-tree gates after fixes.
3. Perform the P6 review handoff's own-instance live probes before review,
   especially stop-time survivor restoration to its original systemd scope,
   D-25 event emission, leaf-removal ordering, and the fail-closed retention
   case. Never use the main singleton or host namespaces.
4. Once short gates, coverage, and live probes are green, run the fresh
   caller-configured GPT-6-Sol xhigh review series starting at round 5. The
   caller verifies the route; do not ask the reviewer to self-attest. Rounds
   1–4 remain the prior review record; maximum three rounds for this series.
5. After ACCEPT, provisionally merge only under the current wave policy. Run a
   fresh exact-tree R2 and registered full gate in a separate CIU worktree
   asynchronously. Release, install, and `ciu up` remain blocked until R2 is
   complete and acceptable, every survivor has a behavioral disposition, the
   full gate is green, and all RG-55 release/close-out conditions are met.

P3's real DAMON series/overhead measurement remains open independently of P6.
