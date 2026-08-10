# Contract ladder v2 — axes, wave carving, and the cost model behind them

> **Status: PROPOSAL. Not adopted, not implemented.** Written 2026-08-10 while
> nyxloom is mid-restructure (test/gate logic decomposing out to `assay`) and
> therefore unusable for dispatch. **Revisit after the open nyxloom handoffs
> complete.** Nothing here changes `reference/AUTHORING.md`,
> `schemas/handoff-frontmatter.schema.json`, or any live route until it is
> explicitly adopted.
>
> **Supersedes nothing yet.** It proposes a successor to the `2a`-`2e`
> implementation-contract ladder introduced in `reference/AUTHORING.md`
> revision `2026-08-08-r5` (commits `f521bfaa`, `2f2167f5`).
>
> **Backlog:** B38-B41 in `nyxloom-trove/4-backlog.md`.
> **Pilot candidate:** dstdns CW2 / P85 — see §10 and
> `dstdns:docs/proposals/cw2-p85-wave/`.

---

## 1. Why this document exists

The `2a`-`2e` ladder landed on 2026-08-08 and was immediately exercised across
assay packages P20-P25 under a new operating regime: JIT carving per package by
`gpt-5.6-sol` at xhigh, implementation by Sonnet 5 at xhigh, adversarial review
by Opus 5 at xhigh, review/merge disposition back to sol.

The regime *felt* extremely expensive — roughly four to five hours per package,
with a single post-P24/P25 carve session consuming close to a fifth of a weekly
quota. The natural hypothesis was that the ladder itself, and specifically the
normative implementation packet it demands, had made handoffs so heavy that
authoring them cost more than it saved.

That hypothesis is **measurably wrong**, and the measurement matters more than
the conclusion, because it points every future optimisation at a different
target. This document records the measurement, the quality evidence on the other
side of the ledger, six specific defects in the ladder as written, and a
proposed redesign that replaces one scalar class with four independent axes.

---

## 2. The measured baseline

### 2.1 Method

Per-request provider telemetry, deduplicated by `message.id` (usage repeats per
content block), using the recipe already recorded in
`assay/nyxloom-trove/MEASUREMENTS.md`:

```sh
jq -s '[.[] | select(.message.usage) | {id: .message.id, u: .message.usage}]
  | unique_by(.id)
  | {requests: length,
     cache_read:  (map(.u.cache_read_input_tokens // 0) | add),
     cache_write: (map(.u.cache_creation_input_tokens // 0) | add),
     out:         (map(.u.output_tokens // 0) | add)}' SESSION.jsonl
```

For the codex controller sessions, the cumulative `total_token_usage` carried on
the last `token_count` event of the rollout.

Two windows, same project, same roles, same models:

- **pre-ladder** — Claude sessions ending 2026-08-07 02:10 through 2026-08-08
  18:19, covering roughly assay P15-P19.
- **post-ladder** — Claude sessions ending 2026-08-09 15:19 through 2026-08-10
  15:30, covering roughly assay P20-P25.

Package attribution is by wall-clock window, not by an explicit label, so
per-package figures are approximate. The aggregate ratios are not.

### 2.2 Claude implementation + review legs

| | packages | requests | cache-read input | output | **context / request** |
|---|---|---|---|---|---|
| pre-ladder (P15-P19) | ~5 | 2,686 | 1.114 **G** | 2.57 M | **415 k** |
| post-ladder (P20-P25) | ~5.5 | 3,649 | 1.461 **G** | 3.42 M | **400 k** |
| per-package delta | | +23 % | +19 % | +21 % | **−4 %** |

### 2.3 Wall clock, from the commit timeline

| package | regime | carve → merged (approx.) |
|---|---|---|
| P18 | pre | **4 h 45** |
| P19 | pre | **3 h 15** |
| P20 | post | 3 h 43 |
| P21 | post | 4 h 03 (includes a BLOCKED and a re-carve) |
| **P22** | post | **1 h 51** |
| P23 | post | 4 h 13 |
| **P24** | post | **~1 h 04** |

The two fastest packages in the entire record are post-ladder. Pre-ladder was
already three to five hours per package. Within the post-ladder set, the
implementation leg alone ranges from 46 minutes (P24) to 2 h 21 (P23) — a
threefold spread under an identical regime. **Scope variance dominates regime
variance.**

### 2.4 The correction that matters: the two windows are not like-for-like

The comparison above understates the pre-ladder regime's true cost, in a
direction that flatters the *old* way of working. Pre-ladder was not "the same
loop without a packet". It was a different loop:

- one carve and review pass from sol at the **head of a series**;
- Sonnet implementation and Opus review/merge running **serially across several
  packages** without re-carving between them;
- a **terminal sol review of the whole series** at the end.

That terminal review is
`assay/nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`
(2026-08-08). Its disposition on five already-implemented, already-Opus-reviewed,
already-merged packages was **NOT READY FOR EXTERNAL ADOPTION**, on four
reproduced critical integrity failures:

1. R2 mutants and both R3 halves silently dropped caller-appended argv and
   resolved environment passthrough — judging a different command from the one
   the artifact records.
2. R2 copied only the project directory, so in a monorepo a mutant could fail
   for an absent tracked sibling and be counted as a **killed mutant**, awarding
   a false R2 PASS.
3. R3 copied a pre-existing coverage profile into scratch without a freshness
   requirement, so a half producing no profile could be judged from stale
   baseline evidence.
4. The Git boundary inherited ambient repository selectors, so a hostile or
   accidental `GIT_DIR` could make a run rooted at repository A record
   repository B's HEAD.

Consequences for the accounting:

- The pre-ladder window **excludes** that terminal review's own cost. The sol
  sessions in the pre-ladder period total roughly **363 M input tokens**
  (2.1 M + 44.6 M + 69.5 M + 246.7 M across 2026-08-06/07), against roughly
  **142 M** for the two post-ladder controller sessions. One further session
  (`rollout-2026-08-08T18-19-20`) spans both regimes and is still being
  appended, so it is deliberately left unattributed.
- The pre-ladder window also **excludes the rework it caused**, which lands
  *inside* the post-ladder window: `ebbe208c` "review P15-P19 and recarve
  successor wave" (08-08 19:21) followed by `257f2e7e` "recarve pre-adoption
  wave P20-P32" (08-08 22:25) — a thirteen-package recarve, ratified as A-167.

So the honest statement is not "the ladder costs +20 %". It is:

> **The ladder plus JIT carving moved defect discovery from a terminal
> series-wide review to per-package, at approximately constant total cost per
> package, and the +20 % measured on the Claude legs is an upper bound that
> still charges the successor wave for its predecessor's deferred debt.**

### 2.5 The controllers

| session | span | requests | input tokens | output | ratio |
|---|---|---|---|---|---|
| P20 controller | 3 h 37 | 762 | **102.3 M** (99.1 % cached) | 86.7 k | 1180 : 1 |
| P21-P25 controller | ~20 h | 2,418 | **39.7 M** (98.3 % cached) | 37.4 k | 1060 : 1 |

Twenty hours of frontier-model time produced **37,424 output tokens** — about
twenty-five pages. Each `git log`, each head-commit verification, each "is this
finding in scope" cost a full 130-220 k context re-send.

### 2.6 The cost model

Two numbers explain nearly all of it, and they are stable across both regimes:

- **~400,000 tokens of context per request**
- **~660 requests per package**

`660 × 400 k ≈ 266 M input tokens per package`. A 535-line handoff is ≈ 8 k
tokens: **0.003 %** of that. Even re-read twenty times it is noise.

> **The cost function is `Σ over requests of (context size)` — turns multiplied
> by accumulated context. Specification depth enters only through its effect on
> turn count, and a good packet should *reduce* turns by removing exploration
> and rework.**

This inverts the intuition that produced the original worry. It also reprices
every proposed optimisation: shortening handoffs saves ~0.003 % per package;
halving the context a session carries, or halving its turns, saves ~50 %.

---

## 3. What the regime buys

The other half of the ledger, and the half most easily skipped.

**Per-package review found real defects in every single post-ladder package**,
visible in the commit subjects alone: P20 (commit identity, bounded reads, gated
hostile-Git), P22 (four bounded repairs, cleanup contract, dead-code removal),
P23 (two bounded repairs, target-selection repair), P24 (an undecodable
`METADATA` member crashing instead of returning the required refusal receipt).

**The escape hatch fired cheaply and correctly.** `71b1b961`
`blocked(assay): P21 mutation seam needs forbidden go.py` — twenty-two minutes
after the carve. A carving defect (the handoff forbade the file the correct
implementation needed) cost twenty-two minutes instead of a bad merge.

**The most expensive check has repeatedly been the most productive.**
`MEASUREMENTS.md` already records the external adversarial pass finding 23
confirmed defects where two in-house readiness passes found 3, and notes that
this "is the opposite of what the cost table alone would suggest".

**Every phase that ran before code was written found defects in the
specification, not in the implementation.** That is `MEASUREMENTS.md`'s own
summary of the pre-flight and orientation phases, and it is the single most
load-bearing empirical claim in the whole programme.

And §2.4 supplies the complement, which is new: **per-package review catches
per-package defects; it structurally cannot catch cross-package integrity
defects.** All four critical findings in the P15-P19 terminal review were
cross-package (argv/env dropped consistently across R2 and both R3 halves;
monorepo sibling absence; stale profile reuse; ambient Git selectors). Five
individually-green Opus reviews did not see them, because none of them was
looking at the seam between packages. This is the reason a cross-cutting
qualification pass must survive the redesign as a **first-class package**, not
as an optional extra.

---

## 4. Six defects in the ladder as written

### 4.1 The class is unenforceable

`reference/AUTHORING.md` states that `contract_class` "is an authoring/review
classification recorded in the body". The frontmatter schema is
`additionalProperties: false` and has no such field, so the classification
cannot even be *expressed* to a machine. `nyxloom lint` returns `clean` on a
package whose contracts are entirely unfixed. Nothing — router, daemon, gate
selector, reviewer checklist — can cross-check it.

**Fix:** make it a schema field with a lint rule that cross-checks it against
`tier` through a declared mapping. See §7.

### 4.2 The tier column is fiction on the live host

The ladder maps `2a`→`implement-5` … `2e`→`implement-1`. Only `implement-1` and
`implement-2` exist upstream, and the **live** matrix at
`~/.local/state/nyxloom/routes.toml` (revision `2026-07-23`) has no
`implement-*` tier at all — it is still the pre-B16 model-named ladder
(`haiku-low`, `flash-high`, `sonnet5-high`, `frontier-review`). A carver
following the table literally writes a tier that does not resolve; a carver
following the live matrix writes one the ladder cannot interpret.

**Fix:** the axis→tier mapping must be *declared data* read from the routing
matrix, not prose in a doctrine file. See §7.3.

### 4.3 There is no rung for "not yet carveable"

`2a` is described as design-bearing implementation and is given a route
(`implement-5`). But a package whose externally visible contracts are simply
**not yet fixed** — no DDL, no signatures, no config vocabulary — is not a
harder implementation package. It is **not an implementation package at all**,
and dispatching it to any tier produces invented contracts. The ladder has no
way to say that, so such packages get stamped `2a` and dispatched.

dstdns P85 is exactly this case, which is why it was so hard to classify.

**Fix:** the contract axis gets a bottom value that is explicitly
non-dispatchable and whose only legal successor is a contract-freeze package.

### 4.4 No calibration

Five prose definitions with adjacent boundaries ("difficult private
construction" versus "bounded multi-component integration") and zero worked
examples will not classify consistently across carvers, sessions, or models.

**Fix:** two worked examples per value, drawn from merged packages.

### 4.5 It implies a one-way ratchet, and the economics are not one-way

The guide says to "carve toward the lowest class that is honest", which reads as
*always carve down*. But a fully-packeted `2b` means the carver writes the
interfaces, the skeleton, and the already-failing acceptance tests — a large
fraction of the implementation, **at frontier prices** — so that a cheap band
can fill in bodies.

That trade pays only when it actually pays. State the inequality out loud:

> Carve down from contract state `A` to `A'` only when
>
> `carve(A → A') + implement(tier(A')) + review(D)  <  implement(tier(A)) + review(D)`
>
> i.e. only when the **carve delta is cheaper than the tier delta**. When it is
> not, **route up instead** — that is a correct outcome, not a failure of
> carving discipline.

Two important boundary conditions:

- When the package is at the bottom of the contract axis (§4.3), the carve is
  **not** an optimisation and the inequality does not apply: dispatch is unsafe
  at *every* tier, so the contract package is mandatory.
- The measured numbers make the comparison tractable: a carve leg has been
  running 20-45 minutes of frontier session; an implementation leg 46 minutes to
  2 h 21. Carving down one band must save more than it costs, and for a
  well-bounded package it frequently does not.

### 4.6 A prescriptive, wrong packet is worse than a vague one

This is the failure mode the packet introduces and does not handle. Given a
normative packet, the implementer faithfully builds the carver's mistake — now
carrying the carver's authority. The guide warns that "an unprobed code sketch
merely gives a bad assumption more authority", which is the right diagnosis, but
it adds no matching escalation trigger.

**Fix:** every packeted handoff carries a standing escalation line:

> `escalate_if: "the normative packet is internally inconsistent or contradicted
> by the code it names"`

This must be a *standing* entry emitted with the packet, not something a carver
remembers to add — a carver who could reliably foresee the contradiction would
not have written it.

### 4.7 The pre-flight checklist under-tests the packet

One checklist bullet covers all eight packet items, and the *falsifiable* parts
get no checkbox of their own. "Was the tracer bullet actually run?" and "did each
acceptance negative actually fail?" are the two claims that separate a probed
packet from a plausible one, and they are exactly the two that vanish into
"Complex work has a probed implementation packet".

**Fix:** promote the falsifiable subset to individual checkboxes, each requiring
a recorded command and result:

- [ ] Tracer bullet run through the proposed construction — command + result
      recorded in the carve log.
- [ ] One deliberately hostile case run — command + result recorded.
- [ ] Skeleton compiles / validates — command + result recorded.
- [ ] **Each** acceptance negative witnessed failing before dispatch — count
      recorded, and it equals the number of negatives specified.
- [ ] At least one expected artifact is carver-authored, not implementer-derived.
- [ ] Standing packet-contradiction escalation line present (§4.6).

---

## 5. The redesign: four axes, not one class

### 5.1 Why a scalar fails

`2a`-`2e` is a single scalar carrying four independent variables. A package can
be extreme on any subset of them, and the remedies differ per variable. dstdns
P85 is extreme on all four at once, which is why no single letter fits it and
why "route it to a smarter model" — the only remedy a scalar can express — does
not address any of its actual problems.

### 5.2 The axes

| axis | question it answers | what it drives | why separate |
|---|---|---|---|
| **A — contract fixedness** | are the externally visible interfaces pinned: schema/DDL, signatures, serialized forms, error vocabulary, config keys? | **implementer tier**, and packet depth | the only axis a cheap model's *safety* depends on |
| **B — integration breadth** | how many owners must agree for the change to be correct? | **sequencing and parallelism** | breadth makes work *wide*, not *hard*; with A high it parallelises, with A low it cannot |
| **C — proof cost** | what does the evidence require: unit, conformance, live stack, scale/fault? | **gate lane**, wall clock, and whether the proof is its own package | proof cost is independent of implementation difficulty and is the main driver of elapsed time |
| **D — blast radius** | how reversible is it: additive, behavioural, schema/protocol, consumer-visible? | **review tier and depth** | reversibility decides how much you should pay to be sure, independent of how hard it was to write |

### 5.3 Scales

**A — contract fixedness** (higher = more fixed = cheaper implementer)

| value | state | dispatchable? |
|---|---|---|
| `A1` | externally visible contracts **not fixed** — the implementer would have to invent schema, signatures, error vocabulary, or config names | **NO.** Only legal successor is a contract-freeze package. |
| `A2` | product outcome fixed; some architecture/algorithm/failure-model choice genuinely unresolvable before implementation; every open choice enumerated with admissible options and deciding evidence | frontier only |
| `A3` | all public interfaces, serialized forms, state transitions, error vocabulary, ownership, bounds and side-effect ordering fixed; difficult **private** construction remains | upper-mid |
| `A4` | exact signatures and shapes, numbered construction recipe, compiling skeleton for non-obvious code, prepared acceptance fixtures | mid |
| `A5` | exact edit map, fixed replacement shapes, locked acceptance tests, one unambiguous gate command | cheap |

`A5`≈old `2e`, `A4`≈`2d`, `A3`≈`2c`/`2b`, `A2`≈`2a`. `A1` is new (§4.3).

**B — integration breadth** (drives sequencing)

`B1` one owner/module · `B2` two owners, one seam · `B3` several owners against
**fixed** contracts (parallelisable **iff** A ≥ 4) · `B4` cross-cutting where
contracts co-evolve (must serialise, or must be preceded by a contract package).

**C — proof cost** (drives gate lane)

`C1` unit/mock in the standard lane · `C2` adds a contract/conformance lane ·
`C3` requires a live stack · `C4` live plus scale/fault matrix plus repeated
runs for pollution and races.

**Rule:** a `C4` obligation is its own package. Attaching a scale/fault
qualification to an implementation package makes the implementation's wall clock
hostage to infrastructure, and makes the qualification's failures
indistinguishable from the implementation's.

**D — blast radius** (drives review)

`D1` additive/reversible · `D2` modifies existing behaviour, revertible ·
`D3` schema, serialized format, or protocol change · `D4` irreversible, or
visible to another repo/consumer.

**Rule:** review tier is `f(D)`, never `f(A)`. A mechanically-simple `A5`
package that rewrites fresh-init DDL is `D3` and gets a deep review; a hard `A2`
package adding an isolated module is `D1` and does not.

### 5.4 What the axes replace

| decision | old input | new input |
|---|---|---|
| implementer tier | the class | **A** |
| packet depth | the class (`2a`-`2d` all get the full eight items) | **A** (see §5.5) |
| gate id / lane | prose in the body | **C** |
| review tier | convention ("Opus reviews") | **D** |
| parallel or serial | carver's judgement | **B**, gated on **A** |
| "is it too big" | "small enough to finish in one focused pass" | any axis at its top value is a split signal; two or more is mandatory |

### 5.5 Packet normativity keys on A alone

The current guide demands the full eight-item packet for everything `2a`-`2d`.
That is where the carve overhead comes from, and most of it is not doing work.

| A | required packet |
|---|---|
| `A1` | none — the package is a **contract-freeze** package and its deliverable *is* the packet (§6) |
| `A2` | full eight items, **including** carver-run skeleton and witnessed-failing negatives |
| `A3` | full eight items; carver-run skeleton required only for the single highest-risk seam |
| `A4` | interfaces + examples, construction flow, decision table, bounds/provenance, degrees of freedom. **No** carver-run skeleton required; prepared fixtures suffice |
| `A5` | edit map + fixed shapes + locked acceptance tests |

Items 3 (topology/namespaces) and 5 (bounds and provenance) are required at
**every** value of A, including `A5`. They are cheap to write and they are the
two that map directly onto this estate's recurring live defects (path-namespace
confusion; invented fallbacks for facts that have an authoritative source).

### 5.6 Standing escalation for packeted packages

Emitted automatically with any packet (§4.6):

```yaml
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
  - "the normative packet is internally inconsistent or contradicted by the code it names"
```

---

## 6. Carving redesign: the wave contract

### 6.1 Shape

For work that is `A1` and `B3`/`B4` — which is what most substantial features
actually are — the correct decomposition is not "one big package at a high
tier", nor "five packages each re-deriving the same interfaces". It is:

```
  W0  contract-freeze package        (frontier carver; deliverable = executable contract)
       │
       ├── W1 ─┐
       ├── W2 ─┤   slices, A4/A5, B1, C1-C2, parallel
       ├── W3 ─┤
       └── W4 ─┘
       │
       Wq  qualification package     (C3/C4, serial, terminal)
```

**W0's deliverable is the contract, and it must be executable**, not prose:

- the actual DDL that creates on a fresh init;
- the actual type/function/wire signatures that import and type-check;
- the config key table — `key | file | type | default | validation | consumer` —
  with the keys actually declared and validated;
- the error vocabulary as real constants;
- **a conformance suite that fails red for every unimplemented slice.**

This amortises the single most expensive component of the current regime. Under
JIT carving, five packages produced five JIT-CARVE reports of 22-24 KB each,
every one of them re-deriving overlapping interface decisions in a separate
frontier session. One contract, carved once, replaces that.

**Each slice's gate then becomes "my slice of the frozen conformance suite goes
green"** — runnable on day one, in isolation, in parallel, with no integration
required and no cross-slice negotiation.

**Wq is where the cross-package integrity attack lives**, and §3's evidence says
it must exist: five individually-green packages hid four critical cross-package
false-PASS defects. Wq is also where every `C3`/`C4` obligation is discharged.

### 6.1a W0 splits along its own A axis — and that is what makes it deployable

Refinement found while piloting this against dstdns CW2. The contract package
contains two different kinds of work, and they sit at opposite ends of the
contract axis:

| phase | work | axis | role |
|---|---|---|---|
| **decide + probe** | choose and *prove* the schema, signatures, cursor codec, config vocabulary, error constants, decision table | `A1` — frontier, **no committed product code** | the existing carve authority |
| **land** | write exactly what the frozen contract dictates: DDL, stubs, config keys, gate declarations, red conformance suite | `A4` — mechanical against a locked contract | the existing implementer lane |

This matters more than it first appears. Treating W0 as one frontier package
that both decides *and* writes code breaks the role separation most controller
loops already depend on — dstdns's, for instance, states plainly that the
review/carve authority does not write product code and the implementer does not
decide architecture. Splitting W0 at its own A boundary means **the wave needs
no new role and no new lane**: "decide + probe" is simply what the carve step
produces this cycle (a contract document plus a probe log, committed with the
successor handoff), and "land" is an ordinary implementation package.

It is also cheaper. The frontier session spends its budget on decisions instead
of on typing DDL, which is precisely the substitution §4.5's inequality is
about.

**Corollary for the axes:** a package whose A value is not uniform across its
own work is a split signal, exactly like a package extreme on two axes. Ask
where inside the package A changes, and cut there.

### 6.2 What may and may not be deferred

A natural instinct, once slices run in parallel, is to defer the gate until
everything converges. **Split that instinct in two:**

| | verdict |
|---|---|
| Defer the **C3/C4 proof** — live stack, scale, fault matrix | **Yes.** It is integration-dependent by nature and cannot run per-slice. That is what Wq is for. |
| Defer **contract conformance** | **No.** |

The reason is not principle, it is the recorded natural experiment. The
pre-ladder regime *was* the deferred variant: implement several packages
serially, review each locally, and run the cross-cutting review at the end. The
result (§2.4) was five merged packages, each individually green, and a terminal
verdict of NOT READY on four critical integrity defects, followed by a
thirteen-package recarve.

Deferring conformance also destroys the highest-yield signal the programme has
measured — that every phase running *before* code found defects in the
specification — and replaces it with a convergence event where several slices'
incompatible assumptions surface simultaneously and cannot be attributed.

W0's red conformance suite is what makes parallelism safe **and** early. It is
strictly better than deferral on both cost and safety.

### 6.3 Wave-level scope closure

nyxloom's backlog already records that a per-handoff reviewer would have missed
assay's carving defect because it needed the cross-handoff view. Under a wave,
this becomes mechanical: **the union of the slices' `scope.touch` must cover
every file the contract change makes stale**, and no slice may `forbid` a path
another slice or the contract requires. That check belongs to W0's own
pre-dispatch review and is cheap once the contract is explicit.

---

## 7. Proposed frontmatter schema (v2)

Additive to `schemas/handoff-frontmatter.schema.json`. Nothing below is
implemented; `additionalProperties: false` means these keys **fail lint today**,
which is why the pilot in §10 keeps them out of live handoff files.

### 7.1 New properties

```json
{
  "axes": {
    "type": "object",
    "additionalProperties": false,
    "required": ["contract", "integration", "proof", "blast"],
    "description": "Carver-stamped contract axes. Replaces the prose 2a-2e class. Cross-checked against tier/gates/review by lint.",
    "properties": {
      "contract":    {"type": "integer", "minimum": 1, "maximum": 5,
                      "description": "A1 unfixed (NOT dispatchable) .. A5 locked edit map"},
      "integration": {"type": "integer", "minimum": 1, "maximum": 4,
                      "description": "B1 single owner .. B4 cross-cutting co-evolving contracts"},
      "proof":       {"type": "integer", "minimum": 1, "maximum": 4,
                      "description": "C1 unit/mock .. C4 live + scale/fault + repeated runs"},
      "blast":       {"type": "integer", "minimum": 1, "maximum": 4,
                      "description": "D1 additive/reversible .. D4 irreversible or consumer-visible"}
    }
  },

  "wave": {
    "type": "object",
    "additionalProperties": false,
    "required": ["id", "role"],
    "description": "Membership in a contract-first wave (see design-contract-ladder-v2.md §6).",
    "properties": {
      "id":       {"type": "string", "pattern": "^[a-z][a-z0-9-]{1,62}$"},
      "role":     {"enum": ["contract", "slice", "qualification"]},
      "contract": {"type": "string",
                   "description": "Task id of the wave's contract package; required for role=slice|qualification"},
      "conformance_gate": {"type": "string",
                   "description": "Gate id running the frozen conformance suite; required for role=slice"}
    }
  },

  "packet": {
    "type": "object",
    "additionalProperties": false,
    "description": "Falsifiable evidence that the normative packet was probed, not merely written.",
    "properties": {
      "probed":               {"type": "boolean"},
      "probe_ref":            {"type": "string", "description": "carve-log path holding the tracer-bullet commands and results"},
      "skeleton_ref":         {"type": "string"},
      "negatives_specified":  {"type": "integer", "minimum": 0},
      "negatives_witnessed":  {"type": "integer", "minimum": 0,
                               "description": "count actually observed failing before dispatch; lint requires == negatives_specified"},
      "carver_authored_artifacts": {"type": "array", "items": {"type": "string"}}
    }
  },

  "review_tier": {
    "type": "string",
    "pattern": "^[a-z][a-z0-9-]{1,62}$",
    "description": "Ladder key for the review route. Derived from axes.blast; explicit so an override is visible."
  }
}
```

### 7.2 Lint rules (new)

| id | rule | rationale |
|---|---|---|
| `L-A1` | `axes.contract == 1` ⇒ `wave.role == "contract"`. Any other role is an error. | §4.3 — an unfixed-contract package is not an implementation package |
| `L-A2` | `tier` must equal the tier declared for `axes.contract` in the routing matrix's `[contract_axis]` map, unless `tier_override_reason` is present | §4.1, §4.2 — the mapping is data, and an override is visible |
| `L-A3` | packet required for `axes.contract <= 4`; for `<= 3`, `packet.probed == true` and `packet.negatives_witnessed == packet.negatives_specified` | §4.7 — the falsifiable subset is machine-checked |
| `L-A4` | `escalate_if` must contain the standing packet-contradiction line whenever a packet is required | §4.6 |
| `L-A5` | every oracle's `gate` must name a gate whose declared argv can **collect** the oracle's evidence; a gate whose lane cannot see the named test paths is an error | the dstdns P85 failure — eight oracles bound to a unit-only lane |
| `L-A6` | `axes.proof >= 3` ⇒ role is `qualification`, or the package declares a gate at that lane | §5.3 — C4 is its own package |
| `L-A7` | `review_tier` must be ≥ the tier declared for `axes.blast` | §5.3 — review depth follows reversibility |
| `L-A8` | wave-level: union of slices' `scope.touch` covers every path the contract package marks stale; no slice `forbid`s a path another slice or the contract needs | §6.3, and the P21/P85 forbid-what-is-needed defect |

`L-A5` and `L-A8` are the two that would have caught real, already-observed
defects, and are the highest-value rules in the table.

### 7.3 Routing-matrix addition

The axis→tier mapping is **declared data**, not doctrine prose:

```toml
# routes.toml
[contract_axis]          # axes.contract -> implementation ladder key
5 = "implement-1"
4 = "implement-2"
3 = "implement-3"
2 = "implement-5"
# 1 has no route by construction: carve a contract package

[blast_axis]             # axes.blast -> review ladder key
1 = "review-1"
2 = "review-2"
3 = "review-3"
4 = "review-3"
```

This removes §4.2 permanently: a host whose matrix has not been migrated
declares its own mapping, and the doctrine file stops asserting tier names it
cannot guarantee exist.

---

## 8. Cost levers that are independent of the ladder

From §2.6, these dominate anything the ladder can do. They should be sized
against `assay/nyxloom-trove/MEASUREMENTS.md` and are listed in expected-value
order.

1. **Stop running orchestration inside a frontier chat session.** 2,418 requests
   at ~140 k context for 37 k of output over twenty hours. Control flow is a
   script; the model should be called for judgement, not for `git status`.
2. **Cut the standing prefix.** `assay/nyxloom-trove/decisions.md` is 179 KB
   (≈ 44 k tokens) and grows monotonically; `STATE.md` is 41.6 KB (≈ 10 k). A
   handoff that says "read `decisions.md` decisions A-160/A-163/A-173-A-196"
   still costs the agent the whole file to find twenty rulings. **Inline the
   cited decisions into the packet** — ≈ 50 k off every session's prefix, for
   free, with no doctrine change.
3. **Cap turns.** 660 requests/package at 400 k each *is* the bill. Batching
   reads, one shell command per verification, and pushing exploration into an
   isolated sub-agent attack the multiplicand directly.
4. **Frozen-orientation forks** (already piloted). Note the ordering: at 400 k
   context per request, orientation saving is second-order and turn count is
   first-order. Do 1-3 before optimising 4.

---

## 9. Adoption

Sequenced so that nothing lands while nyxloom is mid-restructure:

1. **Now (paper only):** this document, the backlog entries, and the dstdns
   P85 pilot expressed under the proposed model but kept out of live handoff
   directories.
2. **After the open nyxloom handoffs complete:** schema additions (§7.1) and
   lint rules `L-A5`, `L-A8` first — they are the two with observed defects
   behind them and they do not require the routing migration.
3. **With the routing migration:** `[contract_axis]` / `[blast_axis]` (§7.3),
   then `L-A1`, `L-A2`, `L-A7`.
4. **Then:** rewrite `reference/AUTHORING.md` §"2a-2e" as §"axes", fold in
   §4.5's inequality, §4.6's standing escalation, and §4.7's checklist, and add
   two worked examples per axis value from merged packages.

Open questions this should answer, to be recorded as measurements rather than
argued:

- Does the wave contract actually reduce total frontier carve spend versus
  per-package JIT carving, or only redistribute it? (Measure W0 spend against
  the sum of the JIT-CARVE sessions it replaces.)
- Does a red conformance suite reduce slice rework turns measurably?
- Does packet depth keyed on A (§5.5) reduce carve spend without increasing
  review findings — i.e. is the `A4` relaxation free?
- Is `L-A5` (gate can actually collect the oracle's evidence) sufficient, or do
  oracles need a declared evidence path per oracle?

---

## 10. Pilot: dstdns CW2 / P85

`dstdns:nyxloom-trove/handoffs/dstdns-P85-bounded-corpus-dns-admission.md` is
the natural test candidate. Under the current ladder it has no honest
classification: it would be stamped `2a`, routed to a nonexistent
`implement-5`, and in practice was stamped `tier: sonnet5-high` — two to three
bands optimistic. It lints `clean`.

Under the axes it classifies immediately as **`A1 / B4 / C4 / D3`**, and every
axis prescribes a concrete remedy rather than a model upgrade:

- `A1` — the DDL, the source protocol signature, and the config vocabulary are
  unfixed ⇒ **not dispatchable**; carve a contract package.
- `B4` — four services plus DDL plus docs ⇒ parallelisable only *after* A rises.
- `C4` — live Mode B plus a million-domain scale proof plus repeated runs ⇒ its
  own qualification package.
- `D3` — fresh-init schema rewrite ⇒ deep review regardless of how mechanical
  the slices become.

The pilot decomposition, the per-package axis stamps, and the proposed v2
frontmatter for each are in `dstdns:docs/proposals/cw2-p85-wave/`. It also
carries three defects found in the existing P85 that are independent of this
proposal and would block it under any regime: a forbidden file the work
requires, two out-of-scope test files asserting the doomed schema, and eight
oracles bound to a gate lane that cannot collect their evidence.
