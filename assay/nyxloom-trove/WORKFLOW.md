# The implementation loop — a controller that holds the design, a stream of implementers that hold one package each

> Written 2026-08-06, before the first dispatch, from a pattern the owner
> described. It is recorded here because the *reasons* are what make it work;
> the steps alone would survive being copied and quietly degraded.

## The shape

One long-lived **controller** (this session) holds the whole design: eleven
packages of rationale, every decision and its reason, and the running state of
what has landed. A stream of short-lived **implementers** each hold exactly one
package and then die.

Three invariants, each doing real work:

**1. Design authority never leaves the controller.**
An implementer receives a handoff and a pointer to the specification. It may
*read* `docs/DESIGN-GUIDE.md` and `nyxloom-trove/decisions.md`; it may never
edit them — they are in every handoff's `scope.forbid`. A discrepancy comes back
as a **report**, not an edit. This is what stops eleven agents each making a
locally-reasonable amendment that collectively dissolves the design.

**2. An implementer context is disposable and never reused across packages.**
One package, one agent, one death. A context that has been through an
implementation carries its dead ends, its wrong turns, and its own justifications
for them. Reusing it for the next package imports all of that as if it were
established fact.

**3. Fixes flow up, not back.**
When review finds a defect, the **controller** repairs it. The implementer is not
re-primed. Two reasons: the controller has the design intent the implementer
never had, and re-prompting an agent that has already argued itself into a
position is the most expensive way to change its mind.

There is a threshold on invariant 3, and it matters. Controller-repairs is right
for *local* defects. It is wrong when the **handoff** was wrong rather than the
implementation, or when the repair would touch a large fraction of the package.
In those cases the correct move is: fix the handoff, discard the branch,
re-dispatch. Rewriting a structurally-wrong package by hand costs more than
re-running it and produces work no oracle was written against.

## The economics — a stable prefix, not a forked snapshot

The original description was: let an agent orient itself, stop it there, and
fork that warm state once per package. **That mechanism does not exist for
sub-agents.** A fork inherits the *controller's* context (which is the opposite
of what is wanted — it would carry the whole review history into an implementer),
and there is no way to snapshot a sub-agent and branch it N ways. `SendMessage`
continues *one* agent serially, which is context reuse, not duplication.

The good news is that the mechanism was never necessary, because prompt caching
keys on the **prefix of the request**, not on any explicit snapshot. Two agents
whose opening bytes are identical share a cache hit up to the point where they
diverge. So the requirement is not "fork a snapshot"; it is:

> **Invariant bytes first, volatile bytes last.**

Concretely, every implementer prompt is assembled as:

| | | changes between packages? |
|---|---|---|
| 1 | the frozen preamble — role, invariants, where the spec lives, how to run the gate, how to report | **no** |
| 2 | the reading order — the same files, in the same order, every time | **no** |
| 3 | the package tail — which handoff, what landed last, what to read of the previous commit | **yes** |

The first implementer pays full price. Every one after it starts warm, provided
the frozen part is *byte*-identical and the model is the same.

**Therefore the refresh trigger is not a count.** "After five packages" is a
proxy for the real condition, which is:

> **Refresh when the frozen part is no longer frozen.**

That happens when the preamble is edited, when `DESIGN-GUIDE.md` or
`decisions.md` are revised (which the between-package review step deliberately
does), or when the reading order changes. A count is a decent backstop, not the
rule — and knowing the real rule means the controller can *choose* to batch
spec revisions rather than dribbling them out and invalidating the cache eleven
times.

Model choice fragments the cache the same way: two models mean two warm
prefixes. That is a bounded cost — one miss per model, not a per-package
penalty — so it is worth paying where a package genuinely needs the stronger
model, and not worth paying for variety's sake.

## The loop, per package

1. **Pre-flight** *(selective, not every package)*. Dispatch an agent to read
   the handoff and its context and report only: is this implementable exactly as
   written? It stops there.
   Its value is **not** cache warming — that was the part that does not work.
   Its value is catching an ambiguous or stale handoff for the price of one
   small turn, before an implementation, a review and a rework are paid for.
   So it is worth it where a defect would propagate — the first package, and any
   handoff the controller has since revised — and not otherwise.
2. **Implement.** A fresh agent, frozen preamble + package tail. It implements,
   runs the gate in the foreground, and then runs a **self-review** pass against
   its own diff before reporting.
3. **Review and merge.** The controller reads the diff and the gate output —
   *not the tree*. It checks what is **missing** from the commit as hard as it
   checks what is in it: an oracle satisfied by a test that asserts implementation
   trivia is the failure mode that survives every green gate.
4. **Repair.** Controller-side, subject to the threshold above.
5. **Propagate.** Ask what this package's result changes about the *remaining*
   handoffs. This is the step most loops skip and the one that makes a carved
   series adaptive rather than a plan that ages. Batch the resulting edits where
   possible (see the refresh trigger).

## The self-review step

The implementer reviews its own work before reporting. This is worth its cost
for a specific reason: it is the cheapest possible reviewer of the code *while
the context that wrote it is still live*. It catches the mechanical class —
a forgotten branch, an oracle with no test, a leftover stub — at the moment
that costs least.

It is explicitly **not** a substitute for controller review, and must not be
described as one. An author reviewing their own work shares every assumption
that produced the defect. Its blind spot is exactly the class the controller is
positioned to catch: *did this implement the thing the design meant?*

## Honest assessment

**Where it earns its cost.** The controller holds a design context that would be
ruinous to re-establish eleven times; the implementers hold almost nothing.
Serial execution is not a limitation here but a requirement — `/workspaces/vbpub`
has a concurrent committer, so parallel implementers would be actively
dangerous. And the propagate step is the highest-value part: it converts a
carved plan into one that learns.

**Where it costs and does not pay.**

- **Controller context growth is the real risk.** Eleven review cycles. The
  discipline that prevents it: review diffs and gate output, never whole trees;
  delegate broad searches rather than reading files in; let the gate be the
  evidence. If the controller starts reading source to form opinions, the
  pattern's central economy is gone.
- **Wall-clock is the honest price.** Eleven serial packages, each with a build
  and a gate. Nothing here makes that faster; the pattern buys correctness and
  adaptability, not speed.
- **It generalises badly.** For independent, well-specified, parallelisable
  work this is strictly worse than fanning out. It fits *this* shape — many
  packages, one large shared design, correctness over throughput, a plan that
  may need to change as evidence arrives.

## Model assignment for this series

Chosen per package by where a mistake propagates, not by size.

| Model | Packages | Why |
|---|---|---|
| **Opus** | P01, P04, P06, P09, P10 | P01's schema shapes all ten others; P04 and P06 carry the two structural oracles that define the adapter boundary; P09 has no reference implementation to work from; P10 is intricate AST work whose containment oracle is the whole point |
| **Sonnet** | P02, P03, P05, P07, P08, P11 | tightly specified, with a reference implementation to work from, and an escalation hatch when the spec runs out |

Revisable as evidence arrives — which is what step 5 is for.
