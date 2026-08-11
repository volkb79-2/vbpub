---
schema_version: 1
id: assay-P33-verdict-schema-v5
project: assay
title: "The verdict artifact expresses a coverage-less mutation language without inventing one"
tier: implement-2
input_revision: "b03555d79227ef7eb76eaf7f851c2896968fa455"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P26-attested-evidence-cli-hardening]
session: fresh
scope:
  touch: ["src/assay/schemas/verdict.schema.json", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/vocabulary.py", "src/assay/config.py", "src/assay/adapters/python.py", "src/assay/runner.py", "tests/**"]
  forbid: ["src/assay/coverage_parsers/**", "src/assay/adapters/go.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/git.py", "src/assay/safeio.py", "nyxloom-trove/carve-assets/**", "nyxloom-trove/decisions.md", "docs/DESIGN-GUIDE.md", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "The installed CLI's own verifier accepts a schema-v5 R0,R2 artifact that records language, source_roots and base in judgment.resolved with no judgment.r1 present, and records the helper that produced its mutation sites"
    negative: "An R0,R2 artifact omitting judgment.resolved still validates, so a mutation verdict exists whose language and comparison base are unrecorded"
    gate: tester-unified
  - id: O2
    observable: "Each language's operator vocabulary is closed: a lane whose resolved language is python and which declares a sql: or go: operator is refused at config load, and every mutant_outcome.operator prefix equals judgment.resolved.language"
    negative: "A cross-language operator is accepted, so a Python lane reports SQL mutation operators it cannot possibly have applied"
    gate: tester-unified
  - id: O3
    observable: "The equivalent bucket is paired both-present-or-both-absent with judgment.r2.equivalence_artifact, and equivalent mutants are excluded from the mutation score's numerator and denominator"
    negative: "An equivalent mutant is counted as survived, or equivalent entries appear with no declared equivalence_artifact, so a provable no-op reads as untested behaviour"
    gate: tester-unified
  - id: O4
    observable: "kill_attribution is required and self-consistent: declared demands kill_signal_artifact plus a kill_signal on every killed entry, unattributed forbids both"
    negative: "A killed entry carries a kill_signal while kill_attribution is unattributed, so an unverifiable mechanism claim rides in an artifact that says it has none"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a v5 field cannot be populated without reaching a forbidden owner"
  - "migrating a locked carve asset appears necessary despite A-222"
mutexes: [merge-lane]
---

# P33 — verdict schema v5

The claim to attack: **the artifact can describe a language that has mutation
but no coverage, and can distinguish a provably-inert mutant from an untested
one, without inventing capability for any language that lacks it.**

## Dispatch contract

- Contract class: **2b — contract migration**.
- Required roles: **Opus xhigh implementer → fresh Opus xhigh independent
  reviewer**. Opus because the change is a closed-vocabulary schema plus two
  cross-object invariants, and because 90 files migrate behind it.
- Readiness: **the design is frozen.** `nyxloom-trove/SCHEMA-V5-DESIGN.md` and
  decisions A-220/A-221/A-222 fix every externally visible choice. The locked v5
  schema, both expected templates, the controlled-red probe and the migration
  manifest are committed under `nyxloom-trove/carve-assets/P33/`.
- Implementer freedom: internal decomposition of model and verifier code only.
  The schema bytes, the operator vocabulary, the field names, the ownership split
  in the migration manifest, and the expected templates are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P33-verdict-schema-v5` on
branch `feat/assay-P33-verdict-schema-v5`.

## Context to read first

1. `nyxloom-trove/SCHEMA-V5-DESIGN.md` in full — it is the specification, and it
   records what v5 deliberately does *not* do and why.
2. Decisions **A-220** (v5's shape), **A-221** (the `go:*` vocabulary and its
   deliberate exclusions), **A-222** (locked v4 evidence is not rewritten), plus
   A-138/A-170 (a schema version is a consumer migration), A-192 (rigor grammar —
   why `R0,R2` is legal and therefore why V5-1 is a bug fix), A-183
   (`UNSUPPORTED` versus `NO_MUTANTS`), A-209 (the both-present-or-both-absent
   pattern V5-3 copies), A-197 (only the carver corrects a locked asset).
3. `nyxloom-trove/carve-assets/P33/` — every asset, especially
   `migrate_v4_to_v5.py`, which is the auditable delta, and
   `migration-manifest.json`, which is the ownership boundary.
4. `src/assay/verdict.py`, `verify.py`, `vocabulary.py`, `config.py` and their
   tests; identify every place a bare operator name or a `judgment.r1` key is
   read, not only where one is written.

## Environment setup

From fresh main, the ordinary `tester-unified` gate. No Go and no PostgreSQL are
needed: P33 changes the contract only. `gate/go/` does not exist and P33 does not
create it.

## Implementation packet (normative)

### The schema is supplied, not authored

`carve-assets/P33/verdict.schema.v5.json` **is** the new
`src/assay/schemas/verdict.schema.json`. Install it byte-for-byte. Do not
hand-edit it, and do not regenerate it from a modified transform:
`python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check` must exit 0
against the committed asset both before and after your work.

The five changes are V5-1 through V5-5 in the design document. Their rationale
is not repeated here; read it there, because two of them are counter-intuitive
(V5-1 is a v4 bug fix rather than an SQL feature, and V5-4 deliberately does not
close the gap it describes).

### The cross-object invariants the schema cannot express

JSON Schema cannot relate two objects, so these belong in the model and the raw
verifier, following the convention the existing `mutant_outcome` and `mutation`
descriptions already use for byte-order and bucket arithmetic:

1. **Operator prefix equals resolved language.** Every entry of
   `judgment.r2.operators` and every `mutant_outcome.operator` must have prefix
   `judgment.resolved.language`. Checked in `config` at load for the declared
   set, and in the verifier for the artifact.
2. **Equivalence pairing.** `equivalent` nonempty requires
   `judgment.r2.equivalence_artifact`; absent artifact requires an empty bucket.
3. **Score arithmetic.** The mutation score is `killed / (killed + survived)`.
   `equivalent`, `crashed` and `budget_exceeded` are excluded from both. The
   existing `candidate_count`/`total`/bucket-length relation extends to include
   `equivalent`.
4. **Attribution consistency.** `kill_attribution: declared` requires
   `kill_signal_artifact` and a `kill_signal` on every `killed` entry;
   `unattributed` forbids both, on every bucket.
5. **Helper correspondence.** A claim whose payload was produced with an external
   helper requires the matching `helpers` entry. P33 has no helper-producing
   adapter, so the *check* lands here while its first real exercise is P34's.

### Operator renaming is not an alias

`python:compare-swap` replaces `compare-swap`. There is no accepted bare
spelling, in config, in the artifact, or in `vocabulary.py`. A v4 artifact is
refused by version, as it already is — `verify_text` on a v5 tree must reject
`schema_version: 4` exactly as today's rejects 5.

## Work

1. Install the locked v5 schema verbatim; confirm `--check` exits 0.
2. Migrate the model: `Judgment` gains `resolved` (required); `JudgmentR1` drops
   `language`/`source_roots`/`base`; `JudgmentR2` gains `equivalence_artifact`,
   `kill_attribution`, `kill_signal_artifact`; `Mutation` gains `equivalent`;
   `MutantOutcome` gains `kill_signal`; `Verdict` gains `helpers`.
3. Migrate the raw verifier, adding the five cross-object invariants above. Each
   gets its own controlled break — **per clause, not per function** (the one-hop
   item carried from P26's review, which applies directly to a closed vocabulary
   and an "anything else is inconsistent" rule).
4. Rename the Python operator vocabulary throughout `vocabulary.py`,
   `adapters/python.py`, and `config.py`'s validation.
5. Populate `go:*` and `sql:*` in the vocabulary as **declarable but
   unproducible**: no adapter generates them in P33. A Go or SQL R2 lane must
   load and then render payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED` per
   A-183, never a load-time refusal and never green.
6. Migrate every path in `migration-manifest.json`'s `implementer_owned` bucket
   — 90 files. Do not touch `locked_carver_owned` (17 files) or
   `excluded_build_artifact` (7).
7. Prove both `carve-assets/P33/expected/*-v5-template.json` validate through the
   installed wheel's own verifier, and that
   `probe_v5_controlled_red.py`'s expectations 3, 4 and 5 now **invert** — the v5
   templates that the pre-implementation tree rejected are accepted, and the v4
   artifact that v5 rejected is still rejected.
8. Run the full gate and record exact A-067 controlled-break counts.

## Carried in from P21, merged (read before work items 2 and 3)

P21 owns the v4 artifact and its vocabulary; **P33 is the package that supersedes
it**, which is the one legitimate route to changing these files. A-157 already
declared v4 "the next pre-adoption artifact contract" and batched
A-O14/A-O16/A-O18 into it. P33 does not reopen those: they were answered in v4
and their answers carry forward unchanged. If a v4 answer appears to need
revisiting, that is `escalate_if`, not an in-scope repair.

## Carried in from P27, blocked (read before work item 5)

A-217 ruled that Go's line attribution needs a real statement-position oracle,
and `src/assay/coverage_parsers/go_cover.py` is **forbidden** here. P33 gives
Go's mutation operators a spelling; it gives Go no coverage capability and no
adapter. The `helpers` array exists partly for that future oracle, and P33 only
has to *validate* it, never populate it.

Note the defect that produced this explicit status: P27's own handoff left
`go_cover.py` in neither `touch` nor `forbid`, and its scope therefore silently
permitted exactly one of three product options. Every load-bearing file in this
handoff has a status for that reason.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** No
timing assertions; a timeout is a failsafe, never an oracle.

**B. No order/worker dependence.** Fresh `tmp_path`; no mutation of
process-global state without restoration. Fixture migration must not leave a
half-migrated fixture visible to a sibling test.

**C. No hollow tests.** Assert the contract, not a call count. A cross-object
invariant needs a test per clause; a vocabulary needs a rejected value per
language, not one rejection standing for all three.

**D. No coverage evasion.** No `no-cover` pragma on changed lines.

**E. Network, clock and filesystem are inputs.** Offline; timestamps injected.

## Package-specific test emphasis

The failure mode this package invites is a **migration that passes because every
fixture moved together.** Ninety files change; a test suite that only ever sees
migrated fixtures cannot tell a correct migration from a consistent one. So:
at least one test must assert that a *v4* artifact is refused for its version,
at least one that a v5 artifact missing `judgment.resolved` is refused, and at
least one that a cross-language operator is refused — none of which any migrated
fixture would catch.

## Scope / forbid

P33 owns the artifact contract and its consumers. It owns no adapter capability,
no coverage parser, and no locked carve asset. `merge-lane` is required because
the artifact contract changes.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not
improvise a workaround.
