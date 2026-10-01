# P6 controller checkpoint — final short gates and provisional-review round

This file supersedes stale process/gate instructions in earlier P6 briefs.
Continue only from the exact branch state below; do not infer current status
from the 2026-09-12 dispatch table.

## Current state at checkpoint

- Branch: `rg55-followups-cgprofile-final`.
- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`.
- Before this checkpoint's documentation edits, HEAD was `1c2ca22b`, a clean
  reconciliation of main `4d32bcfe`; main's current base at the time of this
  checkpoint is `4d32bcfe`.
- The P6 source implementation and two controller reviews are present. P6
  review rounds 1 and 2 are not the required independent GPT-6-Sol xhigh final
  review. Round 3 remains to be run by a genuinely fresh Sol session.
- Prior registered R0/R1 and R3 passed on earlier tree `41c6fba6`, but those
  receipts predate subsequent records/docs commits. R3's old container was not
  inspected live for its CPU cap. These receipts do not qualify the final
  candidate.
- There is **no qualifying current-tree R2**. The retained R2 at
  `aae66356bf3a65ef8b3ba7fa04a8042f2feee55c` ended
  `BUDGET_EXCEEDED/LANE_TIMEOUT` (362 candidates; 312 killed, 12 survived, 38
  budget-exceeded, 0 crashed). Its survivor triage is documented in the
  REPORT, but it is not evidence for this reconciled candidate.
- P1 R2 is running independently in the isolated CIU clone/worktree described
  by RW-331/RW-332. Its last observed progress was at 17:18:19Z on
  2026-09-25; the scheduled next check is no earlier than 17:43:19Z. Never
  edit its HEAD or poll before the user's 25-minute interval absent an expected
  early error/completion.
- Main is `4d32bcfe` in the shared checkout; do not include any unrelated main
  worktree state. P6 branch includes main through that hash.

## Work to complete, in order

1. Finish the review-contract/status documentation already started in this
   checkpoint: this brief, P6 review handoff, P6 LOG/REPORT, the P55 Sol packet,
   and controller log ruling RW-333. Update the dispatch P6 status to name the
   final branch/worktree and current provisional-only stage. Commit only these
   intended paths in this private branch and record the resulting exact HEAD.
2. Keep the P6 candidate quiet. Verify the root and mirrored interface
   contracts are byte-identical. Confirm host memory PSI `full avg10 <= 5`,
   loaded `dev-gates.slice`, and exact active gate/mutation containers before
   launching anything. Respect the estate-wide three-mutation-lane limit.
3. Run the registered `./run-gate.py r0-r1` on the committed exact tip. Read
   the job exit and gate verdict/history in a separate step. Require 100% line
   AND branch coverage, clean tree, and loaded `dev-gates.slice` / 3-CPU cap
   evidence for the exact gate container.
4. Run `./run-gate.py r3` on the same quiet tip. Capture the exact container
   name and inspect it immediately while live; record `dev-gates.slice` and
   `NanoCpus=3000000000`. Read the R3 verdict separately; all canaries must be
   rejected, none survive. If it is too short to inspect, use a non-mutating
   event-triggered observer for the exact printed name and rerun the short
   gate if cap evidence is unavailable.
5. Start one genuinely fresh GPT-6-Sol xhigh final review, target P6. Follow
   `run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md`
   and this package's `cgprofile-P6-FOLLOWUPS-REVIEW-HANDOFF.md` exactly. Set
   `CODEX_HOME=/home/vscode/.codex2`, select `--model gpt-6-sol` and
   `model_reasoning_effort="xhigh"`, and verify the saved session's actual
   model/effort metadata before accepting its review. Required output is
   `cgprofile-P6-FOLLOWUPS-REVIEW-round3.md`. It may make scoped repairs; if it
   does, resume that same Sol session for verification, then rerun affected
   short gates on the repair tip. Sol may not merge, tag, release, install, or
   start/stop the singleton daemon.
6. On Sol ACCEPT and green final short gates, the controller may provisionally
   merge `--no-ff` to unblock P5/RG-55 work. This is not shipment. Do not
   release cgprofile 1.1.0 or `ciu up` until a fresh exact-tree P6 R2 has a
   complete acceptable verdict and survivor disposition, the registered full
   gate passes, and all other RG-55 release conditions are satisfied. Run the
   R2 campaign in a separate full local clone + CIU worktree, not a shared
   `--shared` clone; pin that clone's `origin/main` to the exact pre-P6 main
   baseline and prove the R2 judged base/tree from the verdict. Keep campaign
   HEAD quiet and resume only at the same tree.
7. Preserve the P3 live DAMON-series/overhead measurement as an independent
   release/close-out requirement. The prior host rejected DAMON
   `kdamond_commit` with `EINVAL`; report it honestly if still unavailable.

## Fixed constraints

- Do not touch `/workspaces/dstdns`. Do not mutate any other agent-owned
  container, CIU identity, network, or campaign tree. Never remove by image,
  ancestor, label, or prune.
- No `--cgroupns=host`, `--pid=host`, or `--net=host`. The daemon uses its
  designed private namespaces and explicit host bind mounts.
- Before any new container/image/gate launch, check host memory PSI; do not
  launch when `full avg10 > 5`. Gate containers belong under the loaded
  `$CGROUP_PARENT_DEV_GATES` and get an exact 3-CPU cap. The only reviewer-owned
  containers are uniquely named; inspect/remove only exact names created by
  this run.
- For long campaigns: verify progress once 90 seconds after kickoff, estimate
  duration from the plan/history, then inspect no more often than every 25
  minutes unless an earlier error or expected completion is likely. Use a
  mechanical watcher that records one terminal marker; do not poll clocks or
  logs repeatedly while waiting.
- Use `apply_patch` for file edits, absolute paths, no `cd`, and read gate
  verdicts separately (never pipe a command into `tail` and mistake that
  process's status for the job's status).
