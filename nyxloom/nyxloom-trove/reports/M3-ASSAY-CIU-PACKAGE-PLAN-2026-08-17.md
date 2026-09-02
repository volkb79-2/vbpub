> **Historical planning record, imported 2026-09-02 by nyxloom-P98 — superseded.**
> Operator direction 2026-09-02: nyxloom has no live consumers today, so the
> staged P91-P95 migration bridge this plan builds (to protect in-flight
> consumers during the cutover) is unnecessary. nyxloom-P98 deletes the
> toolkit modules directly instead of executing P91-P97 as sequenced here.
> Kept for the P92/P94 design reasoning (how an Assay verdict gets consumed,
> if a future package needs it) — not a live queue.

# M3 package plan — Assay v6 + CIU execution substrate

Input revision: `33c694571a90b9881df2f83c006e15a4ee096a67`.

This is the package queue behind F020/B47 and F019/B46. It is not itself a
dispatch handoff. An item becomes a handoff only when its named admission
trigger is true and its contract can be satisfied without a silent fallback.

## P91 — refuse stale handoff premises before effects

**Readiness:** design/probe first, then carveable without waiting for Assay or
CIU.

Claim to attack: `input_revision` is authoritative admission data rather than a
decorative header, and a package whose carve premise has moved cannot launch a
fresh or resumed implementation effect. Corrective re-carve/revalidation remains
legal; otherwise the guard would forbid its own recovery path.

Resolve the configured target branch and its current revision once per planning
pass; do not infer `main` when the project declares another target. Resolve an
abbreviated input to its full Git object before comparison. A missing,
malformed, unreachable, or unequal `input_revision` produces a typed,
operator-visible stale-premise result and routes to an explicit revalidation or
re-carve path before any implementation effect. It must not be implemented as a
silent effect-boundary refusal: premise drift does not heal by itself, so silently
re-emitting and refusing the same action would create a permanent reconcile
loop. Explicit revalidation records the new revision durably; there is no
boolean waiver that leaves stale metadata in place.

The existing QUEUED fresh-dispatch guard is retained, not reinvented. The design
probe closes its known ACTIVE-resume gap, settles the existing `NEEDS_DECISION`
release interaction, and proves two consecutive reconcile passes cannot
auto-release a drifted task. Behavioral negatives cover drift before fresh
dispatch and before resume, unchanged input admission, a non-default declared
target branch, unreadable Git state, and zero agent/process/worktree side
effects on refusal. This package does not introduce the per-issue ledger,
importer, or derived index, and it does not replace the separate commit/base
guards at review, gate, or merge.

## P92 — consume one exact Assay v6 artifact

**Readiness:** carveable now; dispatch only after an immutable Assay release
emitting verdict schema v6 is available.

Claim to attack: nyxloom can turn an externally produced artifact into a typed,
commit/lane-bound fact without importing Assay or implementing its schema again.

The consumer reads the artifact bytes once through a bounded, no-symlink input
boundary, computes their digest, passes those same bytes to the pinned public
`assay verify -` CLI, and parses those same bytes only after verification exits
zero. This avoids a verify-then-reread race. It requires schema version 6 and
exact expected `commit` and `lane`. Missing, unsafe, oversized, unreadable,
verifier-exec, verifier-timeout, verifier-rejected, malformed, wrong-schema,
wrong-commit, and wrong-lane are distinct typed refusals. A valid non-PASS
outcome is a successfully consumed fact, not a parser error; workflow policy
owns its consequence.

P92 does not launch a lane, touch CIU, choose a review depth, or grant merge
permission. Its public data retains the complete verified document plus stable
top-level facts and the artifact digest, so later consumers never need to read
the file again or scrape prose.

## P93 — align nyxloom worktree lifecycle with CIU

**Readiness:** not carveable until D-072 chooses the owner of the missing API.

Claim to attack: every automatically dispatched task worktree is a real CIU
instance whose creation, resumption, environment identity, optional live data,
and retirement are owned by CIU rather than parallel raw-Git mechanics.

Verified mismatch:

- nyxloom creates or resumes `feat/<task-id>` and other slash-bearing branches,
  with paths such as `.worktrees/feat/<task-id>`;
- CIU 6.0.3 `worktree add NAME` rejects slash-bearing NAME, always creates a
  new branch named NAME, and cannot attach/register an already-existing branch
  or checkout.

The recommended resolution is an upstream CIU extension: separate logical
instance name, branch, and target path; support both create-new and attach an
existing branch while retaining CIU's own `ciu.env` generation and
clean-before-remove contract. Changing nyxloom's durable branch identity would
touch task/review/report recovery logic throughout the product and would move a
CIU lifecycle limitation into nyxloom's domain.

P93 must also reject an ambient/stale CIU executable. Initial support is an
explicitly qualified release allowlist starting at 6.0.3, plus capability
probes/fixture contracts; semver comparison alone must not infer a CLI feature.
Every call supplies the intended root and target-worktree environment
explicitly. Conditional data isolation and shared-infrastructure declarations
are passed through to CIU; secrets such as `CIU_DATA_ISOLATION_DSN` are never
copied into Assay's recorded passthrough environment.

## P94 — run one declared lane and return one verified fact

**Depends on:** P91, P92, P93.

One effect owner prepares/starts the CIU instance when the lane requires it,
obtains and stores `ciu provenance --json`, reserves a unique Assay artifact
destination, launches the consumer's pinned `assay run`, waits for completion,
then invokes P92. It never treats the run exit code, stdout, CIU prose, or a
verdict from another commit/lane as authority. Cleanup/retention is explicit and
evented; a launch failure cannot fall back to the primary checkout.

The product owns the single `assay.toml`. Direct operator/AI use and P94 invoke
the same lane. Nyxloom configuration may select a lane and qualified executable
artifact, but may not restate rigor, thresholds, coverage paths, mutation
operators, or canary mechanics.

## P95 — migrate policy consumers in bounded slices

**Depends on:** P94.

Split by independent claims, not file count:

- **P95a — merge boundary:** pre-merge and post-merge execution, explicit
  comparison-commit selection, and no publish on any absent/unverified fact.
- **P95b — recovery boundary:** failed/inconclusive/error/budget outcomes route
  through the decided workflow matrix without generic process-code branches.
- **P95c — observation boundary:** dashboard, receipts, manual command, and
  periodic verification render the same stored fact and digest.
- **P95d — routing/review boundary:** rigor/claim facts can increase required
  review depth or alter a later route, but can never lower a project minimum or
  rewrite Assay's outcome.

The initial outcome-to-policy matrix and operator-facing command are fixed by
D-073 and D-074. Later optimization remains explicitly revisitable.

## P96 — self-host nyxloom through Assay v6

**Depends on:** P95a-P95d and the released v6 consumer artifact.

Add nyxloom's own committed `assay.toml`, pin the immutable Assay artifact in
the consumer-owned tester-unified setup, and make the authoritative self gate
produce and independently verify one v6 verdict. This package must preserve the
dedicated tester-unified container and host cgroup placement; a cockpit pass is
still diagnostic only. It removes the self gate's invocation of
`nyxloom.coverage_gate` but does not delete the module yet.

## P97 — delete displaced judgment code

**Depends on:** P96 plus a complete call-site census proving P95 migrated every
consumer.

Delete `coverage_gate.py`, `mutation_gate.py`, `gate_canary.py`, legacy
selectors/config fields, raw gate-verdict interpretations, and tests that assert
their implementation. Preserve or migrate tests of nyxloom-owned behavior:
comparison-commit selection, process isolation, receipt/event durability,
workflow routing, review/merge policy, and failure recovery. The removal oracle
is both literal (no displaced definitions/config remain) and behavioral (the
Assay-v6 self gate and all policy paths remain green).

## After M3

Only after P97 should the retained orchestration be re-censused for further
decomposition. Refactoring the old gate cluster before deletion would improve
code whose responsibility no longer belongs to nyxloom. The M4 issue-ledger and
gap-engine programme then starts from the smaller product.
