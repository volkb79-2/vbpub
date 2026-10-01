---
schema_version: 1
id: ciu-P04-structured-worktree-control
project: ciu
component: worktree
title: "Add exact worktree inspection and capability discovery"
tier: implement-2
input_revision: "71f5ec79"
source: {kind: roadmap, ref: "nyxloom-trove/roadmap.md#package-c--machine-control-and-execution-ciu-29"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "tests/tests/test_ciu_documentation_contract.py"
    - "README.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/FEATURES.md"
    - "docs/ARCHITECTURE.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P04-structured-worktree-control-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/workspace_env.py"
    - "nyxloom-trove/decisions.md"
oracles:
  - id: O1
    observable: "`ciu worktree inspect LOGICAL --json` and `ciu worktree list --json` emit one versioned, closed schema containing the persisted instance record plus freshly derived exact Git registration, branch, HEAD, dirty/detached/primary facts; prose and JSON never claim a Git fact that was inferred from a name or stale record."
    negative: "A stale-record-only result, an unknown/detached state collapsed into a benign state, an ambiguous logical identity, or extra prose on stdout in JSON mode fails deterministically before any side effect."
    gate: tester-unified
  - id: O2
    observable: "Lifecycle and removal JSON use the same envelope and closed operation/status/recovery vocabulary; partial failure identifies retained resources without claiming successful removal."
    negative: "A wrapper exit status, missing retained-resource fact, unversioned shape, or success document after failed clean/remove is rejected by contract fixtures."
    gate: tester-unified
  - id: O3
    observable: "`ciu capabilities --json` returns a separately versioned closed allowlist that includes only shipped machine contracts; README, DESIGN-GUIDE, CONSUMERS, SPEC and parser-backed documentation tests agree on every public value and anchor."
    negative: "SemVer inference, an undocumented capability, a stale config example, or a broken cross-document anchor makes the gate red."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "Any public field, closed value, error category, or Git-fact meaning is not fixed by this handoff and D-009/CIU-29."
  - "Implementation requires changing worktree allocation semantics or files outside scope."
---

# P04 — Structured worktree control

Contract class: **2c (bounded integration)**. Use an implementation-capable
model; `tier: implement-2` is the currently deployed route and requires an
explicit capable-model override.

## Context to read first

1. `nyxloom/reference/AUTHORING.md` §§2c, implementation packet, 3/3b, 4-6.
2. `nyxloom-trove/decisions.md` D-003, D-004, D-006, D-009, D-010.
3. `nyxloom-trove/roadmap.md` Package B and Package C.
4. `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-28 and CIU-29.
5. `src/ciu/worktree.py`: `WorktreeInstanceRecord`, record reading/listing,
   `create`, `ensure`, `adopt`, `remove`, and `list_worktrees`.
6. `src/ciu/cli.py`: `_worktree` and top-level dispatch.
7. `tests/tests/test_ciu_cli_worktree.py` and the record corruption/collision
   fixtures in `tests/tests/test_ciu_worktree.py`.

## Implementation packet (normative)

### Interfaces and grammar

- Owner: `src/ciu/worktree.py`.
- Add pure document builders for `inspect`, managed `list`, lifecycle, and
  removal. Every document has integer `schema_version = 1`, closed `operation`,
  and closed `status`. Persisted identity remains nested under `instance` using
  `WorktreeInstanceRecord.to_dict()`; current Git facts live under `git`.
- Git facts are freshly read from Git: registered path, registered branch or
  detached state, current HEAD, dirty boolean, and primary boolean. A mismatch
  between record and Git is a refusal, not a repaired or guessed value.
- JSON stdout contains exactly one JSON document. Diagnostics go to stderr.
- `ciu capabilities --json` owns its own integer schema version and a sorted
  array of closed identifiers. Initial identifiers are exactly:
  `worktree.identity.v1`, `worktree.inspect.v1`, and `worktree.lifecycle-json.v1`.
  Do not advertise `up`, local exec, or target exec before P05/P06 ships.

### Required flow

1. Resolve the primary Git family and exact logical record with existing code.
2. Validate record/Git correspondence, then derive current Git facts once.
3. Build a document from validated facts; only the CLI renders JSON or prose.
4. For removal, capture validated pre-state, run existing clean/remove, and emit
   success only after both complete. Existing failure exceptions remain failures.
5. Add capability identifiers only for code paths present in this commit.

### Decision table

| state | result | side effect |
|---|---|---|
| exact ready/recovery record + matching Git | inspect/list document | none |
| no logical record | refusal | none |
| duplicate, moved path, branch/registration mismatch | refusal | none |
| remove clean or Git removal fails | nonzero error, no success JSON | existing partial state only |
| JSON requested | one JSON stdout document | operation-specific only |

### Documentation contract

Create `docs/DESIGN-GUIDE.md` (WHY) and `docs/CONSUMERS.md` (HOW). Update
`README.md` (WHAT). Add a documentation test that parses every TOML example in
all three with the shipped loader/current schema, checks every closed public
value appears, and resolves every cross-document anchor.

### Prepared proof and traceability

| work | oracle | controlled break |
|---|---|---|
| current Git facts | O1 | mutate record branch after creation |
| JSON envelope/removal | O2 | make clean fail after pre-state capture |
| capabilities/docs | O3 | advertise an unshipped identifier; break one anchor |

Degrees of freedom: private helper names and equivalent decomposition only.

## Work

1. Implement the exact document builders and CLI surfaces.
2. Add deterministic unit/integration fixtures for every row above.
3. Create/synchronize the three user-facing documents and their failing tests.
4. Update SPEC/CONFIG/FEATURES/ARCHITECTURE/CHANGES without changing policy.
5. Write the report with actual commands, controlled breaks, and commit SHA.

## Test constraints (mandatory)

- No wall-clock deadlines or sleeps; synchronize on real events or pure steps.
- No leaked process-global state, order/worker dependence, or destructive teardown.
- No hollow tests, implementation-trivia-only assertions, weakened assertions,
  or tests that merely assert nothing raised.
- No coverage-exclusion pragmas on changed code.
- No live network, registry, model endpoint, or uncontrolled clock.
- At least one real CLI integration fixture; do not mock the component under test.
- Demonstrate each regression test fails under its controlled break before fix.

## Gate

Run the `tester-unified` gate from `nyxloom.toml`. Capture the job's exit status,
not a pipe/wrapper status. A cockpit-only pytest run is diagnostic, not evidence.

## Out of scope / forbid

Do not alter allocation/recovery semantics, Assay, nyxloom, engine/deploy, or
introduce `up`/`exec` before their dependent packages.

## BLOCKED rule

**BLOCKED:** emit only when an `escalate_if` trigger is observed, naming the exact
field/file/conflict and the smallest decision required. Do not invent a public
field or capability. Product choices become a new `D-NNN`; keep working around
unrelated findings.
