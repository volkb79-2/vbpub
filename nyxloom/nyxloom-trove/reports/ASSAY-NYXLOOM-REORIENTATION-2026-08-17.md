> **Historical planning record, imported 2026-09-02 by nyxloom-P98.** This
> document lived only on the unmerged `feat/nyxloom-redesign-continuation`
> branch (never landed on main) and is preserved here for its analysis, not
> as a live plan. Its sequenced packages P91-P96 were never dispatched from
> this branch; nyxloom-P48 independently reached P96's functional outcome
> (self gate consumes an Assay verdict) via a fresh carve. **P97 (delete
> `coverage_gate.py`/`mutation_gate.py`/`gate_canary.py`) is executed by
> nyxloom-P98**, which also retires GA1/GA4 (the `nyxloom gate verify`
> cadence built on `gate_canary.py`) per this doc's own "Deletion inventory
> and Assay transfer check" section below. Read that section as the
> authoritative rationale; do not resume P91-P95 as written.

# Assay / nyxloom reorientation — 2026-08-17

Input revision: `c499f4fb0d34c5f71e477e82a527047bad6c249c` (`main` at
orientation). This is a planning record, not an implementation handoff.

## Facts verified at the boundary

- Assay 1.0.0 is the released standalone judgment product and its own
  `tester-unified` lane invokes `assay run tester-unified`.
- nyxloom has not yet adopted it: its declared self gate still invokes
  `nyxloom.coverage_gate`, and `mutation_gate = true` still selects
  nyxloom's in-process mutation machinery.
- The duplicated nyxloom surface is `coverage_gate.py`, `mutation_gate.py`,
  `gate_canary.py`, and the judgment-specific portions of `gate_runner.py` and
  their callers/tests. Process launch, worktree lifecycle, event recording, and
  policy translation remain nyxloom responsibilities.
- CIU S16 (`ciu worktree add|rm|list`, `d977d3aa`) now owns checkout-local
  instance identity. CIU 6.0.3 is released locally and additionally provides
  machine-readable provenance, in-container revision, namespaced data,
  shared-infrastructure joins, and a worktree deployment budget. The ambient
  executable is only 4.11.2.dev12: it has the basic S16 worktree verb but lacks
  those later capabilities, so executable presence is not a sufficient preflight.
- CIU does not yet drop into nyxloom's lifecycle unchanged. `ciu worktree add`
  always creates a new branch whose name equals one single-component worktree
  name. Nyxloom creates and resumes branches such as `feat/<task-id>` at nested
  `.worktrees/feat/<task-id>` paths. Neither tool currently exposes the complete
  other-side contract, and raw Git fallback would conceal rather than solve it.

## CIU capability audit

The first supported path needs these CIU contracts, verified in the released
6.0.3 source rather than inferred from the command name:

| Capability | Why nyxloom needs it | 4.11.2.dev12 | 6.0.3 |
| --- | --- | --- | --- |
| `worktree add|rm|list` + checkout-local `ciu.env` identity | create, enumerate, and clean an isolated task instance | yes | yes |
| clean-before-remove | do not strand volumes/config after task retirement | yes | yes |
| `up` / `health` / `clean` with explicit target root/profile | prepare and retire the live environment without ambient-root inference | yes | yes |
| `provenance --json` | machine-check running images against the commit under test | no | yes |
| `CIU_IMAGE_REVISION` exposure | bind an in-container lane to the running image identity | no | yes |
| namespaced data isolation | stateful real lanes do not share a database/schema | no | yes, when declared |
| shared-infrastructure join | reuse a named heavy reference tier without collapsing instance identity | no | yes, when declared |
| cross-worktree deployment budget | bound parallel stacks on the shared host | no | yes, when configured |
| governance/cgroup validation | never launch test/build containers unconfined beside production | partial | yes |

Nyxloom must call CIU with the target worktree/root explicitly and load that
worktree's `ciu.env`; the current shell demonstrates why ambient discovery is
unsafe (`ciu worktree list` resolves the unrelated dstdns root from inherited
environment). CIU has no general `exec` verb. After CIU prepares the WHERE,
nyxloom launches the product's public `assay run` itself with only the lane's
declared/required environment available to Assay.

## Product boundary

Assay answers whether a specific commit satisfies a declared lane and produces
the versioned verdict artifact. nyxloom decides what that fact means for a task:
whether to merge, requeue, escalate review depth, route a later attempt, or show
the result. nyxloom does not reinterpret evidence algorithms or create a second
PASS condition.

For the first migration wave, every automatically orchestrated lane is
CIU-backed. Its CIU worktree
identity is resolved once, carried through launch and verdict consumption, and
bound to the commit/lane. Missing CIU identity is a refusal, not a fallback to
main. Existing non-CIU consumers are not silently broken or claimed supported by
the new integration; their future contract is TBD.

## One product adoption, two invocation paths

A product adopts Assay independently of nyxloom:

1. commit one `assay.toml` containing its named lanes;
2. pin and verify an immutable Assay release artifact rather than inheriting an
   ambient version from tester-unified;
3. make the lane's declared argv and required test tools available in its gate
   environment; and
4. for CIU-backed live lanes, commit CIU configuration and require the safe
   identity inputs (`CIU_INSTANCE_ID`, `CIU_IMAGE_REVISION`) explicitly in the
   lane rather than relying on ambient inheritance.

An operator or AI CLI can then run the product directly:

```
assay lanes --file assay.toml
assay run <lane> --file assay.toml --verdict-json <reserved-path>
assay verify <reserved-path>
```

For a live lane, the caller first uses that worktree's qualified CIU lifecycle
to prepare/start the instance and obtains `ciu provenance --json`; the same
Assay lane still performs the judgment. Nyxloom is an orchestrating caller of
these same public contracts: it chooses when and where, reserves a unique
artifact destination, loads only the target worktree's declared CIU environment,
launches the same `assay run`, separately verifies the artifact, records its
digest, and translates the verified fact into workflow policy. There is no
nyxloom-only Assay configuration and no hidden import API.

Direct Assay remains available. Per D-073, nyxloom additionally exposes
`nyxloom gate run <project> <lane>` as the workflow facade for CIU preparation,
Assay run/verify, receipt retention, and nyxloom-owned policy. The current
`nyxloom gate verify` semantics retire; Assay owns both R3 qualification and
artifact verification.

## Deletion inventory and Assay transfer check

The union was compared before deletion:

- Coverage behavior is present and stronger in Assay, including dirty/base/head
  measurability, missing and unclassified locations, exclusion policy, multiple
  coverage formats, span attribution, and v6 branch/whole-target coverage.
- Mutation behavior is present and stronger in Assay, including the same four
  Python operators (including `falsy-swap`), declared operator/budget/job bounds,
  deterministic identities, baseline gating, and isolated execution. Nyxloom's
  sibling-test-name derivation and in-place fallback are deliberate non-ports:
  they invent a project convention and fail open respectively.
- Canary behavior is present and stronger in Assay: both `import-break` and
  `uncovered-line` are cause-sensitive R3 mechanisms with structured payloads.
  Nyxloom's only unmatched convenience is automatic ranking and trying of up to
  four Python files. Assay deliberately requires a declared target. If broader
  qualification is wanted, file an Assay backlog assessment for an explicit
  ordered multi-target canary set; do not port nyxloom's heuristic discovery as
  a hidden default. This is not a blocker to deleting `gate_canary.py`.
- Post-merge first-parent selection is nyxloom workflow policy for choosing the
  comparison commit, not an Assay judging capability. It remains on the nyxloom
  side and must be made explicit rather than copied into Assay.

## Required sequence

1. **Carve the bridge contract (Sol).** Inventory every nyxloom gate execution
   path, existing Assay verdict fields, CIU S16 lifecycle call, and retained
   policy decision. Freeze the nyxloom-owned receipt/identity schema and the
   exact refusal vocabulary before implementation. This is design-bearing work,
   not a mechanical replacement.
2. **Enforce handoff premise freshness.** Make `input_revision` an admission
   fact before every fresh or resumed implementation launch, closing the known
   ACTIVE-resume gap while preserving re-carve as the repair path. Drift must
   produce a visible revalidation/re-carve route, never a silent permanent
   reconcile refusal. Keep the per-issue grammar and importer out of M3.
3. **Land the Assay v6 artifact consumer.** Independently verify, parse and bind
   an already-produced v6 verdict before taking on process/environment effects.
   This package can proceed once the v6 release is immutable and does not depend
   on resolving CIU's worktree-creation mismatch.
4. **Resolve and introduce the CIU execution bridge.** Decide D-072 after
   weighing nyxloom's durable task identity against a branch migration. CIU-28
   independently asks CIU to separate logical instance name, branch, and target
   path and to adopt/resume existing branches because that is useful to any
   automation consumer. One owner then introduces the exact-target environment
   bridge with no ambient-root or raw-Git fallback.
5. **Run one declared lane and consume one verified fact.** The owner launches the
   declared CIU-backed lane, receives one verdict artifact, validates its version, lane,
   commit, and digest, then returns a typed result. No raw-Git identity inference
   or live-checkout fallback is legal on this path.
6. **Migrate consumers of gate results.** Replace separate pre-merge,
   post-merge, periodic-verification, CLI, routing, review-depth, and dashboard
   interpretations with the typed bridge result. Prove each refusal remains
   fail-closed.
7. **Migrate nyxloom's own gate, then delete duplicate judgment code.** Make the
   self-hosted `tester-unified` gate consume Assay first. Only after an end-to-end
   committed verdict proof lands may the legacy evaluator modules, config
   selectors, and their tests be removed. Do not retain a compatibility default
   that can silently choose the old evaluator.
8. **Advance to M4.** After the M3 branch merges, implement D-068's issue
   grammar/importer/index and the gap engine as the next programme.

## Superseded and deferred work

`nyxloom-P90-extract-testing-library` is superseded: it would recreate the
product Assay now provides. B30 is historical evidence, not a language-support
implementation target for nyxloom. The CR-00--CR-16 deferred slices retain their
named forcing functions and are not reopened by this migration.
