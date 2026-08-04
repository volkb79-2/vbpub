# CR-07 prerequisite — kernel/compiler inventory of the frozen transition graph

Date: 2026-08-03
Parent: [`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md`](CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md)
Status: carving artifact, produced by the controller before CR-07 is dispatched

## Why this exists

The amendment's §9 answers the parent's own external-review question — *does
lifecycle-plus-node preserve every safety property of `TASK_TRANSITIONS`?* —
with **"not automatically"**, and names the missing artifact:

> The safety properties currently enforced by the transition graph are of two
> kinds: lifecycle legality (terminal tasks cannot re-enter; attempts cannot
> regress) and workflow ordering (review precedes merge). The first must stay
> in the kernel; the second moves into the compiled plan and is only as strong
> as compile-time rejection condition 6. **CR-07 needs an explicit inventory
> mapping each current transition rule to *kernel* or *compiler*, with a
> negative test per rule. Without that inventory the migration is a
> best-effort translation.**

This is that inventory. It is derived mechanically from
`types.TASK_TRANSITIONS` (FROZEN CORE), so it is a fact about the shipped
graph rather than a reading of it.

**Amended 2026-08-04 (CR-07d):** `DRAFT` removed as a constructible,
executable state -- pinned inert since 2026-07-17 (no code ever assigned a
task to it; `daemon.py`'s `CreateTask` hardcodes `CARVED`). It contributed
exactly four of the counts below: one compiler edge (`DRAFT → READY_TO_CARVE`)
and three kernel edges (`DRAFT → NEEDS_DECISION` K-decision,
`DRAFT → SUPERSEDED`/`DRAFT → CANCELLED` K-escape). `types.TaskState._missing_`
now maps a legacy-persisted `"DRAFT"` value to `NEEDS_DECISION` on read; see
`tests/test_workflow_ir.py` (`test_partition_totals_match_the_inventory_document`,
`test_kernel_class_counts_match_the_inventory_headings`), which check this
document's own stated numbers against the derived graph.

**The graph has 51 edges across 15 states: 35 kernel, 16 compiler.**

## The classification principle

An edge is **kernel** if no workflow manifest may be allowed to remove, add or
redirect it — the safety property survives independently of which workflow is
composed. An edge is **compiler** if it expresses *which stage follows which*,
which is exactly what a manifest is for.

The test for kernel-ness is adversarial, and it is the one that matters:
**could a hostile or merely careless manifest use this edge to reach an
outcome the product forbids?** If yes, the edge is kernel and the compiler
must be unable to express it. `stages.validate_pipeline` already applies this
reasoning to stage composition (P43's closure invariant); CR-07 generalizes it
from stages to nodes.

## Kernel — 35 edges in five classes

### K-escape (20 edges) — supersede and cancel

`→ SUPERSEDED` and `→ CANCELLED` from all 10 non-terminal states that have
them. These are operator and kernel authority: a re-carve supersedes an
origin task, an operator cancels. A manifest must not be able to withdraw the
ability to abandon work.

*Negative test obligation:* a compiled workflow that omits every escape edge
still leaves both reachable from each non-terminal node; a manifest that
attempts to define its own `SUPERSEDED` predecessor set fails compilation.

### K-merge-spine (4 edges) — the merge authority chain

    AWAITING_REVIEW → MERGE_READY → MERGED → VALIDATING → COMPLETED

This is the most safety-critical structure in the product, and the graph makes
it a **strict chain**: each of `MERGE_READY`, `MERGED` and `COMPLETED` has
exactly one non-escape entry. Nothing reaches `MERGED` except through
`MERGE_READY`; nothing reaches `MERGE_READY` except through `AWAITING_REVIEW`.

The amendment warns this is "only as strong as compile-time rejection
condition 6" once it moves. **It does not move.** What moves to the compiler is
*which stage produces the approval*; the predecessor sets themselves stay in
the kernel. CR-03 already removed prose from the merge decision and CR-05b
made every merge refusal leave the task at `MERGE_READY` — a compiler able to
redirect these edges would undo both.

*Negative test obligation:* a manifest declaring any other predecessor of
`MERGE_READY` or `MERGED` fails compilation, with a message naming the merge
authority chain. This is the negative test for the parent's CR-07 acceptance
"removing review/gate prerequisites from a merge path fails compilation".

### K-block (5 edges) — the typed dead end

`→ BLOCKED` from `QUEUED`, `ACTIVE`, `SELF_REVIEWING`, `AWAITING_REVIEW`,
`VALIDATING`. Surfacing a dead end is a kernel guarantee: a workflow may not
remove the ability to say "there is no path forward, a human is needed".
Contract items 4 and 11 both depend on it, and P14's silent-dead-end fix
exists because the absence of this transition left tasks `ACTIVE` forever with
zero events.

*Negative test obligation:* every non-terminal executable node retains a
`BLOCKED` exit after compilation, whatever the manifest says.

### K-decision (5 edges) — human decision authority

`→ NEEDS_DECISION` from `READY_TO_CARVE`, `CARVED`, `QUEUED`,
`REVIEW_REJECTED`, `BLOCKED`. The north star's "human owns direction"
invariant in graph form. B4a/D-060 relies on it for the carve-less pipelines:
`gated` and `lean` terminate their reject loops here, and that is precisely
what makes a pipeline with no carve stage safe.

*Negative test obligation:* a manifest cannot compile a workflow in which a
rejected task has no route to a human.

### K-operator-retry (1 edge) — `BLOCKED → VALIDATING`

A pre-existing frozen edge with no code path that plans it: an operator who
fixes the underlying cause by hand re-queues a blocked post-merge task to
retry the gate. Kernel because it is *operator* authority, and it is recorded
here specifically so CR-07 does not delete it as dead — it is reachable only
by human action, which is the reason it looks unreachable to a static reader.

## Compiler — 16 edges

These are the stage-to-stage edges. `stages.py` already expresses most of them
as declarative `exit_map`s, so CR-07 is generalizing a mechanism that exists
rather than inventing one.

    ACTIVE          → AWAITING_REVIEW | SELF_REVIEWING | QUEUED
    SELF_REVIEWING  → AWAITING_REVIEW | QUEUED
    AWAITING_REVIEW → REVIEW_REJECTED
    REVIEW_REJECTED → QUEUED | READY_TO_CARVE
    MERGE_READY     → REVIEW_REJECTED
    QUEUED          → ACTIVE
    CARVED          → QUEUED
    READY_TO_CARVE  → CARVED
    NEEDS_DECISION  → QUEUED | READY_TO_CARVE
    BLOCKED         → QUEUED | READY_TO_CARVE

*Negative test obligation, per edge:* a manifest that omits the edge produces
a workflow in which the corresponding outcome is unreachable **and
compilation reports it as a dead end** — never a workflow that silently
strands a task. This is the generalization of
`test_invariants.py::test_no_dead_end_ready_to_carve`, which is exactly the
bug P45 closed when `READY_TO_CARVE` had no handler at all.

## Two structural facts worth stating before they are rediscovered

**1. The post-merge spine is irreversible.** `MERGED` and `VALIDATING` are the
only non-terminal states with *no* escape edges — a task cannot be superseded
or cancelled once its merge commit exists. This is correct (history is fixed
once it is in the branch) and it is nowhere written down. A CR-07 node model
that grants every node a uniform escape set would silently add four edges to
the frozen graph and make merged work cancellable.

**2. `MERGED → VALIDATING` is unconditional, but `VALIDATING → COMPLETED` is
already pipeline-composed.** Contract item 11 auto-advances `VALIDATING →
COMPLETED` when the pipeline omits `post_merge_gate`, and routes through
`RunPostMergeGate` when it does not. So one half of this pair is kernel and
the other is already compiler — the boundary CR-07 must draw runs *through*
this state, not around it. `MERGED → COMPLETED` must remain absent: every
merged task transits `VALIDATING`, whether or not a gate runs there.

## What this implies for the CR-07 split

The amendment's §9 names CR-07 as still too broad to carve and asks for it to
be split "at minimum into compiler-and-IR versus lifecycle-migration". This
inventory sharpens that cut:

- **CR-07a — compiler and IR.** Schema, parser, normalized IR, validation,
  canonical serialization, digest, the negative corpus, and shadow
  compilation. **The 38 kernel edges become compile-time rejection conditions
  in this package**, each with the negative test named above — so the safety
  properties are enforced before any workflow is migrated onto the mechanism.
- **CR-07b — lifecycle migration.** Move the 17 compiler edges onto compiled
  nodes; land the lifecycle/node schema through a CR-04 upcaster; keep read
  compatibility for the removed `DRAFT` value through enum `_missing_` rather
  than as an executable workflow state.

  *(As actually carved: this "CR-07b" umbrella split further once CR-07a
  landed. The shipped `CR-07b` became the `GuardFacts` derivation alone; the
  `stages.py`/`effects_exit.py` repair became `CR-07c`; `DRAFT` removal (this
  paragraph's read-compat item) became `CR-07d`; the compiler-IR-to-dispatch
  wiring plus the upcaster/schema became `CR-07e`. See the implementation
  plan amendment's "CR-07c split" ledger row for why.)*

Landing the rejection conditions first is what makes the migration checkable:
CR-07b's manifests are validated by machinery CR-07a already proved rejects
the unsafe shapes, instead of by machinery written alongside the thing it
validates.

## Stop-loss

The amendment's §5.4 declares: *if CR-07's compiler cannot express the current
flow without a per-node escape hatch into imperative code, stop* — that is the
signal the workflow language is the wrong abstraction, and the fallback is
CR-05/CR-06's decomposition plus a hand-written flow, which already delivers
most of the maintainability gain.

This inventory makes that trigger measurable rather than a judgement call: the
16 compiler edges (17 at the time this inventory was written, before CR-07d
removed `DRAFT`) are the entire set the language must express. **If any one
of them needs an escape hatch, the trigger has fired.**
