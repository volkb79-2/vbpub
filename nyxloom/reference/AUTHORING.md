# Writing a nyxloom handoff — the authoring guide

> **Canonical doctrine — ships with the nyxloom product** (`reference/AUTHORING.md`).
> This file is **not** copied into project troves. **Project-specific additions or
> overrides live in the same-named sibling `nyxloom-trove/AUTHORING.md`** — when that
> sibling exists, read it *after* this file; it refines (never replaces) the rules
> here. One canonical source, one optional project delta.


> **Revision:** 2026-08-08-r5 · (bump on every substantive change. Consumers no
> longer hold copies to go stale — this file is read from the running nyxloom
> product, so its revision is simply the product's doctrine version.)

Point an implementation agent (or yourself) here when a feature/fix comes out of
a discussion and needs to become a **handoff** — a self-contained work package.
A good handoff is the single biggest lever on whether a cheap agent finishes
reliably or produces subtle garbage. This guide has two levels:

- **Level 1 — a good handoff** (the contract + context + oracles + escalation).
- **Level 2 — a nyxloom-*compatible* handoff** (the YAML frontmatter the daemon
  parses + `nyxloom lint` validates).

Distilled from the pre-nyxloom controller-workflow (`legacy-workflow-origin/`)
and the lessons this project learned the hard way.

---

## The one idea behind all of it

The reader is a **fresh agent with no memory of your discussion** and a limited
token budget. Every sentence either (a) tells it exactly what to do, (b) tells
it exactly what to read to get context cheaply, or (c) is noise that costs
tokens and invites drift. Write only (a) and (b).

## Anatomy

```
---
<YAML frontmatter — machine-readable, parsed WITHOUT reading the body>
---

# P<NN> — <title>

## Context to read first        <- token efficiency: exact files+sections
## Work                          <- the contract: numbered, imperative
## Oracles                        <- how "done" is proven
## Scope / forbid                 <- what NOT to touch
## BLOCKED rule                   <- the mechanical escape hatch
```

## Level 1 — what makes a handoff good

### 1. Keep it SMALL and SPECIFIC
Clear files, clear tests, explicit out-of-scope. Big/vague packages fail; small
ones with a named contract finish. If it needs >2 files outside its stated
scope, that's an escalation, not a stretch.

### 2. "Context to read first" — the token lever
List the EXACT files and sections the agent must read (and nothing else) to get
full context: the code it will edit, the one test file to mirror, the spec
section that defines the contract. This is the difference between an agent that
spends its budget re-deriving the codebase and one that spends it implementing.
State it explicitly — never assume the agent will find it.

### The 2a-2e implementation-contract ladder

A handoff is not an invitation to rediscover the carver's design. If correctness
depends on a schema, protocol, state machine, path translation, snapshot,
concurrency boundary, or external integration, the carver transfers the
load-bearing solution into the package. The implementer should be choosing only
the decisions deliberately assigned to its contract class, not reconstructing
unstated product semantics or the architecture that makes the oracles possible.

Classify the **irreducible reasoning left after carving**, not the number of
files, estimated tokens, or apparent prestige of the feature. Carve toward the
lowest class that is honest. A large mechanical migration can be `2e`; a small
concurrency fix can be `2a`. The class orders hardest to easiest and maps to the
planned implementer bands, whose numeric order runs in the opposite direction:

| contract class | planned tier | work left to the implementer |
|---|---|---|
| `2a` | `implement-5` | bounded design choices remain; frontier reasoning is part of the task |
| `2b` | `implement-4` | the solution and public behavior are fixed; difficult private construction remains |
| `2c` | `implement-3` | bounded multi-component integration against fixed contracts |
| `2d` | `implement-2` | constrained implementation from examples, skeletons, and prepared proof |
| `2e` | `implement-1` | mechanical completion of a locked edit map and acceptance suite |

The table above names the PLANNED tier each contract class is intended to route
through once the remaining implementer bands exist — it is not itself a claim
about what `routes.toml` declares today. `contract_class` (2a-2e) is an
authoring/review classification recorded in the body; frontmatter `tier` is a
different thing entirely: it must always be a literal key that exists in the
CURRENT live `routes.toml`, chosen for the capability the assigned contract
class needs, regardless of what name a future band will eventually carry. Do
not put a nonexistent tier in frontmatter. Routing a `2a`-`2c` package through
a live tier whose capability is below what the class needs requires an explicit
human/controller override and a frontier-capable route; preferably carve it
down first.

#### 2a. Design-bearing implementation (`implement-5`, hardest)

Use only when a product-approved outcome is fixed but some architecture,
algorithm, or failure-model choice cannot economically be resolved before
implementation. Enumerate every open choice, its admissible options, invariants,
and the evidence that decides it. State which choices require a `D-<NNN>` product
decision. Give a tracer-bullet prototype or probe for the dangerous seam. `2a`
does **not** mean “figure it out”: unspecified externally visible behavior is an
authoring defect, not implementer discretion.

#### 2b. Complex solution-bearing execution (`implement-4`)

All public interfaces, serialized forms, state transitions, error vocabulary,
ownership, bounds, and side-effect ordering are fixed. The implementer may make
difficult choices about private data structures, algorithms, and decomposition,
but none may change observable behavior. Supply full examples and a proved
construction path for the highest-risk seam.

#### 2c. Bounded integration (`implement-3`)

Component contracts and control flow are fixed. Name every call site and owner,
provide the adapter/conversion table, prescribe error translation, and include
fixtures for both ends of each seam. The implementer selects only local glue and
equivalent internal decomposition. Cross-component ambiguity triggers BLOCKED.

#### 2d. Constrained implementation (`implement-2`)

Provide exact signatures and shapes, a numbered construction recipe, a compiling
skeleton for non-obvious code, prepared acceptance fixtures, and controlled
negative tests. Limit work to one subsystem or one already-specified integration.
The remaining judgment should be ordinary code construction and local debugging,
not contract discovery.

#### 2e. Mechanical execution (`implement-1`, easiest)

Provide an exact edit map, fixed replacement/creation shapes, a compiling or
schema-valid skeleton, locked acceptance tests, and one unambiguous command for
the gate. No externally visible decision, novel algorithm, interface invention,
or multi-owner reconciliation may remain. If the implementer must infer what a
field means, choose an error, or design a seam, the package is not `2e`.

#### Implementation packet (normative for `2a`-`2d`)

Add an `## Implementation packet (normative)` before `## Work`. A `2e` package
may use the compact equivalent (`edit map + skeleton + locked proof`) but must not
omit information needed for mechanical execution. The packet contains the
smallest useful version of each item below; omit an item only when genuinely
irrelevant:

1. **Owned interfaces.** Name the module owner and give exact public type,
   function, or wire signatures. Pin field names, types, ordering, error
   vocabulary, and which caller constructs/consumes each value. A schema or
   protocol gets one valid example and at least two invalid examples.
2. **Construction and state flow.** Give numbered pseudocode from input to
   terminal artifact. Identify the single owner for identity, freshness,
   policy, bounds, and error translation. Say which operations must happen
   before side effects and which values are resolved once and carried forward.
3. **Topology and namespaces.** Draw the relevant path/identity map: repository
   top versus project root, consumer versus snapshot path, producer artifact
   versus verdict destination, host versus container. State each translation
   exactly once and forbid local validation of a foreign namespace.
4. **Decision table.** For every important input/state combination, name the
   output, stable reason, payload presence, and whether a side effect is legal.
   This is especially important where `PASS`, refusal, failure, and budget
   exhaustion meet.
5. **Bounds and provenance.** Name the authoritative source for every value.
   Give concrete fixed bounds or the exact declaration that supplies them.
   Required facts are derived, read, or refused; the implementer may not invent
   a fallback.
6. **Prepared proof material.** Name the fixture topology, exact example
   artifacts, and a wrong implementation each negative distinguishes. When an
   interface or algorithm is non-obvious, provide a compiling skeleton with
   TODO bodies and already-failing acceptance tests. The carver must run the
   skeleton and witness each acceptance negative fail before dispatch.
7. **Traceability.** Include a table mapping every work item to its owner,
   behavioral oracle, test/fixture, and controlled break. The implementation
   REPORT repeats the table with actual file/test names and failure counts.
8. **Degrees of freedom.** State what is intentionally left to the implementer
   (normally private helper names and equivalent local decomposition). Anything
   capable of changing externally visible behavior is not a degree of freedom.

The detail must transfer decisions, not dictate incidental syntax. An unprobed
code sketch merely gives a bad assumption more authority. Before freezing the
packet, the carver runs a tracer bullet through the proposed construction and
one deliberately hostile case. Record those commands/results in the carve log
or source review. If that cannot be done, carve a design/probe package first.

Tests written by the implementer from the same handoff are not independent
evidence: specification, implementation, and test can share one misconception.
For a high-integrity package, the acceptance material must include at least one
carver-authored expected artifact and the reviewer must add at least one new
combined-axis attack that was not named by the implementer's tests. Varying
`repo != project`, appended argv, and passthrough environment only in separate
happy fixtures is insufficient; exercise the meaningful combination too.

A compact packet template:

```markdown
## Implementation packet (normative)

### Interfaces and grammar
- Owner: `src/pkg/module.py`
- `resolve(input: Declared) -> Resolved`; exact fields: ...
- Valid: `...`; invalid: `...` -> `REASON`, `...` -> `REASON`.

### Required flow
1. Read/derive ... before any side effect.
2. Construct ... once; all consumers receive that value.
3. On ... emit ...; never fall through to ...

### Topology and bounds
`repo_top/project_prefix/... -> snapshot_root/project_prefix/...`
Bound/source table: `field | source | limit | refusal`.

### Decision table
`state | outcome/reason | payload | side effects`.

### Prepared proof and traceability
`work | owner | oracle | fixture | controlled break`.

### Degrees of freedom
Private helper names and equivalent decomposition only; serialized and public
shapes above are fixed.
```

### Environment setup is a RECIPE, not a pre-built artifact
If the package's oracles need a live stack or any non-default environment,
the handoff carries a mechanical `## Environment setup` section: the exact
command sequence the **implementing agent executes at dispatch time from
fresh main** (worktree add, env generation, config selection via the
render-input layer, bring-up, teardown). The carver PROBES the recipe once
at carve time but never pre-builds the environment — a pre-built checkout
is stale by the time the task goes ACTIVE. Prefer pointing at the
project's `nyxloom-trove/GUIDE.md` section over restating the recipe
per-handoff (single source; recipes rot fast).

### 3. Oracles that assert the BEHAVIORAL CONTRACT
Each oracle is a checkable claim with an **observable** (what proves it) and a
**negative** (what a broken version does), plus the **gate** that checks it.
The classic failure is a *hollow test*: it passes but asserts implementation
trivia, not the contract. Name the behavior, not the line.

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

### 4. The gate is the project's REAL gate — never the cockpit
State the exact gate command. It runs in the project's declared gate
environment (for the vbpub family: the `tester-unified` container; for dstdns:
`testing-exec.sh` → test-runner), **never** the devcontainer. "Green in the
cockpit venv" is not a ship signal — the pins differ. (And the gate container
must give the run-uid a full identity — passwd+group+HOME+XDG — or suites fail
with errors that look like breakage but are pure environment.)

### 5. Escalation is MECHANICAL, not introspective — BLOCKED is first-class
This is the load-bearing lesson. Models are **demonstrably poor at knowing what
they missed** (four models, identical omissions, zero flagged uncertainty). So
"reflect on whether this suits your expertise" yields false confidence or
performative hedging — it does NOT work. What works: a **trigger-based** escape
hatch. Every handoff ends with:

> BLOCKED rule: if a named contract cannot be met as specified, or scope
> requires a forbidden file, STOP — write `BLOCKED: <reason>` to the LOG,
> commit, and exit. Do NOT improvise a workaround.

A BLOCKED exit is a *cheap, clean signal* (the controller re-routes to a higher
tier); a silently improvised workaround is the *expensive* failure (merged
subtle garbage). **BLOCKED is a success mode, not a failure** — it is exactly
what makes cheap-model-first dispatch safe.

### 6. Product decisions are DECISIONS, not BLOCKED
If the gap is a *product* call (a name, a contract, a user-facing choice), it's
not a mechanical BLOCKED — file a `D-<NNN>` in `decisions.md` and add
`depends_on: [D-NNN]` to the handoff. The agent keeps working around it.

### 7. Trust git state, not receipts
The reviewer verifies against actual `git log/status/diff` of the branch — a
receipt claiming `head_commit`/`files_touched`/`oracles` is *evidence to check*,
not truth (a receipt has lied "null commit" over a real commit). Uncommitted
worktree changes are reviewed too, not discarded.

### The author's pre-flight checklist
- [ ] Frontmatter present + valid (`nyxloom lint` passes).
- [ ] "Context to read first" names exact files/sections — nothing to re-derive.
- [ ] Complex work has a probed implementation packet: interfaces/examples,
      flow, topology, decisions, bounds, proof material, traceability, and
      explicit degrees of freedom.
- [ ] Work steps are numbered, imperative, and scoped to named files.
- [ ] Every oracle has observable + negative + gate; none is hollow.
- [ ] **If the handoff asks for tests: §3b's anti-pattern list is pasted into it**
      (no wall-clock deadlines, no global-state leaks, no hollow tests, no
      no-cover pragmas, no live network/clock). An agent cannot infer these.
- [ ] Gate command is the project's real gate (never the cockpit).
- [ ] `scope.touch` / `forbid` are explicit; out-of-scope is a BLOCKED trigger.
- [ ] BLOCKED rule present (mechanical); product gaps routed to a `D-` decision.
- [ ] Small enough to finish in one focused pass.

### Pre-dispatch adversarial handoff review

Yes, **adversarial review** applies before implementation. Call this a
*pre-dispatch adversarial specification review* so it is not confused with a
later code-diff review. Use this prompt with the proposed handoff and its named
context:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

## Level 2 — making it nyxloom-compatible (the frontmatter)

The daemon parses the frontmatter WITHOUT reading the body (header fields beat
prose — cheaper + unambiguous), and `nyxloom lint` rejects a handoff whose
frontmatter is missing/invalid. Validate against
`nyxloom/src/nyxloom/schemas/handoff-frontmatter.schema.json`. Core fields:

```yaml
---
schema_version: 1
id: <project>-P<NN>-<kebab-slug>      # unique per project
project: <project id>
title: "<one line>"
tier: <a live key from routes.toml>    # live capability band, not a model name
input_revision: "<base commit short sha>"
depends_on: []                         # [P52, D-006] — merged handoffs / open decisions
session: fresh                         # or: resume:<area>  (cache-reuse hint)
source: {kind: product-goal|roadmap, ref: <trove path>}
scope:
  touch:  ["src/<pkg>/<file>.py", "tests/<file>.py"]
  forbid: ["<paths that would break isolation>"]
oracles:
  - id: O1
    observable: "<what, run in the gate, proves the behavior>"
    negative:  "<what a broken version does>"
    gate: <gate id from nyxloom.toml [gates.*]>
gates: [<gate id>]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
---
```

- `tier` drives the routing matrix (cheap model first; BLOCKED re-routes up).
- `session: resume:<area>` reuses a warm cache for a related package; `fresh`
  builds a focused cache for an independent one.
- `depends_on` mixes merged handoffs (`P52`) and open decisions (`D-006`) — the
  daemon holds the task until they resolve.
- `gate` on each oracle + top-level `gates` must reference a `[gates.*]` id
  declared in the project's `nyxloom.toml`.

Naming + lifecycle live in `STANDARD.md`; this file goes to
`nyxloom-trove/handoffs/<id>.md` — the filename stem MUST equal the frontmatter
`id` (lint L1), i.e. `<project>-P<NN>-<slug>.md`. A short `P<NN>-<slug>.md`
filename paired with a project-prefixed `id` fails L1.
