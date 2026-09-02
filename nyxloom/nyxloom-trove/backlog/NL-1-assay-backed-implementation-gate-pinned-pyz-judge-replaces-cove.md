---
kind: backlog-entry
schema_version: 1
id: NL-1
title: "assay-backed implementation gate: pinned pyz judge replaces coverage_gate/mutation_gate self-judgment"
status: fixed
type: "feature"
severity: "medium"
component: "gates"
provenance: "controller session 2026-08-21; evidence: nyxloom-trove/reports/backlog-entries-test-evidence-2026-08-21.md"
priority: 2
filed_date: "2026-08-21"
carved_handoff: nyxloom-P48-assay-gate
closed_date: "2026-09-02"
closed_reason: "Merged 2a8f5278 -- nyxloom-P48-assay-gate: nyxloom's own tester-unified gate now judged by Assay's R0/R1 changed-line judge, replacing the retired coverage_gate self-judgment. R2 mutation + gate_canary.py/R3 reconciliation deferred to NL-5. ('merged' is auto-tick-only via the daemon's merge hook, which this hand-driven package never went through; 'fixed' records the actual resolution.)"
---

## Observed mechanism and reproduction

nyxloom's own implementation gate self-judges: `coverage_gate.py` (changed-line
floor, D-064-L2) and `mutation_gate.py` run in-process against the project's
own diff, with no durable verdict artifact and no judge-sensitivity proof.
The estate has already moved past this shape: ciu's implementation gate runs a
vendored, sha256-pinned Assay zipapp (`tools/assay/assay-2.1.0.pyz`) whose lane
judges the same changed-line floor and emits `.assay/verdict-ciu.json` — and
ciu's `assay.toml` explicitly names nyxloom's judge "the retired
`nyxloom.coverage_gate`". nyxloom is the last consumer of its own retired
judge. Meanwhile Assay ships two engines no estate gate uses yet:
`assay.mutation` (bounded `run_mutation`, R2 claims with a true work-bounded
`max_mutants`) and `assay.canary` (cause-sensitive judge-sensitivity proofs).
Reproduction: read `nyxloom-trove/nyxloom.toml [gates.tester-unified]` — the
argv pipes pytest coverage into `nyxloom.coverage_gate`; compare with
`ciu/nyxloom-trove/nyxloom.toml`'s gate + `ciu/assay.toml`.

Evidence for the current manual-mutation workflow this entry replaces:
`nyxloom-trove/reports/backlog-entries-test-evidence-2026-08-21.md`
(86/86 mutants killed by hand-launched campaign; the campaign found a real
defect that 100% line coverage had passed).

## Why nyxloom owns it

The gate declaration surface (`[gates.*]`, the `asserts` closed vocabulary
`tests-pass|changed-line-coverage|mutation|canary-verified`) is nyxloom's
product contract. The estate convergence decision D-110/D-111 already split
orchestration (`run-gate.py`) from judgment (Assay); nyxloom's own trove is
the place that split lands last.

## Proposed contract

1. Vendor + pin the Assay zipapp at `nyxloom/tools/assay/assay-<ver>.pyz`
   (+ `.sha256`), verified in the gate argv before any run (ciu precedent).
2. Add `assay.toml`: lane `tester-unified` with `rigor = ["R0", "R1"]`,
   `judge.language = "python"`, `fail_under = 100`, `require_branch = true`,
   `base = "origin/main"`, `judge.coverage.format = "coverage-py-json"` —
   replacing what `coverage_gate.py` does today; declare
   `snapshot_selection = "repository-minus-unsafe-symlinks"` with THIS
   repo's unsafe symlinks declared verbatim (the monorepo tracks them under
   topos/tests/fixtures — ciu declares three today).
3. Add `judge.mutation` (bounded) so mutation becomes an assay-judged R2
   claim in the verdict instead of a hand-launched `mutation_gate` campaign;
   retire or demote `mutation_gate.py` accordingly.
4. Gate asserts become `["tests-pass", "changed-line-coverage",
   "canary-verified", "assay-verdict"]`; verdict retained at
   `.assay/verdict-nyxloom.json`.
5. A canary proves the judge rejects a controlled wrong implementation
   (import-break / assert-canary mechanisms already exist in
   `gate_canary.py` — reconcile with assay's `canary.py` rather than
   duplicating).

## Oracles

- Green run leaves a schema-valid verdict JSON naming the lane, the R1
  changed-line result, the mutation claim, and the canary outcome.
- Corrupting the pinned zipapp (byte flip) fails the gate at the sha256
  step before any test runs (controlled wrong implementation #1).
- Dropping the coverage artifact makes the JUDGE report an error verdict,
  not a silent pass (controlled wrong implementation #2 — the
  absence-for-pass trap).
- A mutant that survives below the configured bound fails the gate with the
  surviving site named.

## SPEC ownership

SPEC.md gates/asserts sections + CONFIG documentation of `[backlog_entries]`
is unaffected; the new surfaces are `assay.toml` (project root) and the gate
argv in `nyxloom-trove/nyxloom.toml`. STANDARD.md gains nothing until the
estate promotes the pattern; ciu's `run-gate-project/HANDOFF-P01` (CIU-40)
remains the orchestration-side companion.
