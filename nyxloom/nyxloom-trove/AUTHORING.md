# Writing a nyxloom handoff — the authoring guide

> **Revision:** 2026-07-16-r2 · (bump on every substantive change; `nyxloom init`
> copies carry this line so a project can detect a stale copy against the
> canonical `nyxloom/nyxloom-trove/AUTHORING.md`.)

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

### 3. Oracles that assert the BEHAVIORAL CONTRACT
Each oracle is a checkable claim with an **observable** (what proves it) and a
**negative** (what a broken version does), plus the **gate** that checks it.
The classic failure is a *hollow test*: it passes but asserts implementation
trivia, not the contract. Name the behavior, not the line.

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
- [ ] Work steps are numbered, imperative, and scoped to named files.
- [ ] Every oracle has observable + negative + gate; none is hollow.
- [ ] Gate command is the project's real gate (never the cockpit).
- [ ] `scope.touch` / `forbid` are explicit; out-of-scope is a BLOCKED trigger.
- [ ] BLOCKED rule present (mechanical); product gaps routed to a `D-` decision.
- [ ] Small enough to finish in one focused pass.

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
tier: sonnet5-high                     # routing: which model/effort implements
input_revision: "<base commit short sha>"
depends_on: []                         # [P52, D-006] — merged handoffs / open decisions
session: fresh                         # or: resume <area>  (cache-reuse hint)
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
- `session: resume <area>` reuses a warm cache for a related package; `fresh`
  builds a focused cache for an independent one.
- `depends_on` mixes merged handoffs (`P52`) and open decisions (`D-006`) — the
  daemon holds the task until they resolve.
- `gate` on each oracle + top-level `gates` must reference a `[gates.*]` id
  declared in the project's `nyxloom.toml`.

Naming + lifecycle live in `STANDARD.md`; this file goes to
`nyxloom-trove/handoffs/<id>.md` — the filename stem MUST equal the frontmatter
`id` (lint L1), i.e. `<project>-P<NN>-<slug>.md`. A short `P<NN>-<slug>.md`
filename paired with a project-prefixed `id` fails L1.
