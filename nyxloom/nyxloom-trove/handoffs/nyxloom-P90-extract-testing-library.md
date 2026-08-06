---
schema_version: 1
id: nyxloom-P90-extract-testing-library
project: nyxloom
title: "Extract the testing/rigor mechanics into a standalone library consumable without nyxloom"
tier: sonnet5-high
input_revision: "d977d3aa"
source: {kind: roadmap, ref: "nyxloom-trove/4-backlog.md"}
stack: none
depends_on: []
session: "fresh"
scope:
  touch:
    # O2 must edit nyxloom's own call sites to make it CONSUME the library, so
    # src/nyxloom/** is in scope -- with the three redesign-active modules
    # explicitly forbidden below. Forbidding the file an oracle needs is a known
    # authoring failure (it forces hollow improvisation); this keeps the oracle
    # satisfiable while still protecting the surface that must not move.
    - "src/nyxloom"
    - "src/nyxloom/**"
    - "tests/**"
    - "pyproject.toml"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/planning.py"
oracles:
  - id: O1
    observable: "the extracted package imports and runs its full test suite with nyxloom NOT installed (`pip uninstall nyxloom` in a scratch venv, then `python -m <pkg>.coverage_gate --help` and the package's own pytest run both succeed)"
    negative: "the package still imports nyxloom at runtime, so it is a nyxloom sub-module wearing a library's name"
    gate: tester-unified
  - id: O2
    observable: "nyxloom consumes the library rather than duplicating it: `grep -rn 'def evaluate\\|def _git_added_lines' src/nyxloom/` returns nothing, and nyxloom's own gate stays green through the swap"
    negative: "both copies survive and drift again, which is the defect this package exists to remove"
    gate: tester-unified
  - id: O3
    observable: "a LanguageAdapter protocol exists and the Python adapter is one implementation of it; a second adapter (even a deliberately minimal one) can be registered without editing the core, proven by a test that registers a fake adapter and drives canary injection through it"
    negative: "`.py` globs and `ast` imports remain in the core, so a TypeScript consumer cannot be added without forking"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the CORE REDESIGN (CR-07c..CR-14) is still in flight — this package must NOT land mid-programme; see 'Sequencing' below"
  - "extraction exceeds ~400 changed executable lines in one package — split by module and report, per the CR-07a carving lesson"
mutexes: []
---

# P90 — extract the testing/rigor mechanics into a standalone library

> **STATUS: proposed, DO NOT DISPATCH YET.** See *Sequencing*. This document
> exists so the work is specified while the evidence is fresh, not so it can be
> started now.

## Context to read first

Read these, in order, before touching anything — they are the whole argument:

1. `nyxloom-trove/handoffs/CORE-REDESIGN-SESSION-HANDOFF-2026-08-04.md`
   §"Carving principle" — why this package is split, and why it must not land
   mid-programme.
2. `reference/TESTING-METHODOLOGY.md` §"Scope, rigor, and lanes" — the model the
   extracted library serves.
3. `/workspaces/vbpub/ciu/docs/DESIGN-NOTES.md` D7 — the where/what/how split and
   the placement test this extraction is cut along.
4. The four diverged copies themselves (paths in the table below). **Diff them
   before designing anything**; see "Migration is not a rename".

## Environment

**Worktree:** work only inside a dedicated worktree, never the main checkout:

```bash
git worktree add -b feat/nyxloom-p90-extract-testing-library \
    /workspaces/vbpub/.worktrees/nyxloom-p90-extract-testing-library main
```

Branch name: `feat/nyxloom-p90-extract-testing-library`.
Worktree path: `/workspaces/vbpub/.worktrees/nyxloom-p90-extract-testing-library`.

**Out of scope / forbid** — do not touch `daemon.py`, `reconcile.py` or
`planning.py`. They are the redesign's active surface; a conflict there is a
merge problem for a programme that is mid-flight, and none of them is needed to
move a self-contained testing module.

**Gate:** `tester-unified` (see `nyxloom-trove/nyxloom.toml [gates.*]`). Run it
in the FOREGROUND and paste the real output into the LOG — a backgrounded gate
that is still running when you report is the parked-turn failure this programme
has hit repeatedly.

**BLOCKED:** if any oracle cannot be satisfied within `scope.touch` — most
likely O2, which needs to edit nyxloom call sites — stop, write the LOG
explaining exactly which oracle and which path, commit nothing else, and hand
back. Do NOT widen scope silently and do NOT improvise a partial extraction: a
half-moved module is strictly worse than none, because it creates a fifth copy.

## Why this exists (the evidence, not the theory)

`coverage_gate.py` **already exists four times over, and every copy has
diverged**:

| Copy | Lines | Notes |
|---|---|---|
| `nyxloom/src/nyxloom/coverage_gate.py` | 455 | the "original" |
| `dstdns/scripts/coverage_gate.py` | 804 | most elaborated (`--allow-excluded`, NO-MEASUREMENT guard) |
| `topos/tools/coverage_gate.py` | 299 | oldest/thinnest |
| `shared-ramdisk-depot-manager/tools/covergate` | — | a **Go** reimplementation |

`diff -q` reports the Python copies differ. This is not a hypothetical DRY
argument: the duplication happened, silently, and the copies are now three
different answers to the same question. srdm's Go rewrite is the sharpest
signal — a consumer needed the capability badly enough to build it again in
another language rather than adopt a tool it could not consume standalone.

## The constraint that shapes the design

**The estate's tools are stand-alone and must not depend on each other.**
Synergy by design is fine; a hard dependency is not. So the arrow must point
*from* nyxloom *to* the library, never the reverse, and the library must be
usable by a project that has not adopted nyxloom at all — the same way `ciu` is
used today.

The placement test (ciu `docs/DESIGN-NOTES.md` D7): *would a project using only
this tool still get value from it?* A changed-line coverage floor: yes, any repo
wants one. Dispatch/review/merge: no, only meaningful once you run the factory.
That line is where the extraction cuts.

## Why it is smaller than it looks

The testing cluster's ties to nyxloom-proper are thin — three **dataclasses used
as protocol types**, not behaviour:

```
gate_runner.py    -> config.GateDef, types.GateResult, subprocess, Path
gate_canary.py    -> gate_runner, config.{GateDef, ProjectConfig}, ast
mutation_gate.py  -> coverage_gate, gate_runner
coverage_gate.py  -> (standalone already)
```

~1,600 lines whose only coupling is "a gate is an argv + timeout +
environment". Define that as a small protocol and invert the arrow.

## Language-specificity — real, and confined to the leaves

| Concern | Python-bound? | Evidence |
|---|---|---|
| Gate execution + verdict | **No** | `gate_runner.py` imports only `subprocess`/`Path` |
| Changed-line extraction from git | **No** | pure diff parsing |
| Coverage-data parsing | **Format**-bound | coverage.py JSON vs lcov vs cobertura vs `go cover` |
| Canary injection | **Yes** | `rglob("*.py")`, `ast`, appends a `def` |
| Mutation operators | **Yes** | `ast`, derives `python -m pytest`, maps `src/<mod>.py -> tests/test_<mod>.py` |

So: a `LanguageAdapter` protocol (`source_glob`, `parse_coverage`,
`inject_uncovered_line`, `inject_import_break`, `mutation_operators`), with the
Python adapter shipping first **because it is already written**. This is not
speculative future-proofing — dstdns has `webapp-ui-react` (the canary would
refuse to find a `.py` file in that subtree today) and srdm is Go.

## Sequencing — why this is NOT dispatched now

nyxloom is mid **CORE REDESIGN**: CR-06/07a/13a/16 merged, CR-07b awaiting
review, CR-08..CR-14 outstanding, daemon STOPPED. That programme established a
carving principle it learned the hard way (CR-07a landed at 823 changed lines
and its own reviewer said it should have been split, having *sampled* rather
than exhausted it):

> **Put the thing whose CLAIM needs attacking in its own package, and let the
> volume of mechanical consequence follow separately.**

A ~1,600-line extraction dropped into that is exactly the shape the programme
just learned to reject. So this becomes a CR-numbered package in the redesign's
own order, or it lands after CR-14 — **not** freelanced in between.

Suggested split when it is time:
1. **P90a** — define the protocol (GateDef/GateResult/LanguageAdapter) and move
   `gate_runner` + `coverage_gate`. The claim to attack: *is the boundary real?*
2. **P90b** — move `gate_canary` + `mutation_gate` behind the adapter.
3. **P90c** — nyxloom consumes the library; delete its copies.
4. **P90d..n** — one per consumer migration (see below).

## Naming

`assay` (a test of purity/quality — what a rigor gate is) fits the estate's
register (`ciu`, `cmru`, `topos`, `nyxloom`). **Check PyPI availability before
committing to it.**

## Consumer migration — adopt AFTER the library exists

Each consumer keeps its current copy until then; a note has been added in each
repo. No consumer changes anything before P90c lands.

| Consumer | Today | After |
|---|---|---|
| **nyxloom** | owns 455-line original | imports the library (P90c) |
| **dstdns** | `scripts/coverage_gate.py` (804 lines), wired via `testing-exec.sh --coverage-gate` | import; keep the `--coverage-gate` shim as the invocation surface |
| **topos** | `tools/coverage_gate.py` (299 lines) | import; re-verify its floor after the swap |
| **netcup-api-filter** | ciu+cmru consumer, no gate library | adopt directly — the standalone case, and the proof O1 is real |
| **srdm** | Go `tools/covergate` | keep Go until a Go adapter exists; it is the forcing function for O3 |

**Migration is not a rename.** dstdns's copy is the most elaborated of the four
(`--allow-excluded`, the NO-MEASUREMENT guard that refuses a vacuous verdict when
base resolves to HEAD). Those behaviours are *features that the thinner copies
lack* — the extraction must take the union, not the intersection, or migration
is a silent downgrade. Diff all four before deciding what the library does.
