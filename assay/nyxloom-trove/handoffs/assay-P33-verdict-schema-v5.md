---
schema_version: 1
id: assay-P33-verdict-schema-v5
project: assay
title: "The verdict artifact expresses a coverage-less mutation language without inventing one"
tier: implement-2
input_revision: "@LANDING_COMMIT@"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P26-attested-evidence-cli-hardening]
session: fresh
scope:
  touch: ["src/assay/schemas/verdict.schema.json", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/vocabulary.py", "src/assay/config.py", "src/assay/mutation.py", "src/assay/errors.py", "src/assay/cli.py", "src/assay/adapters/python.py", "src/assay/runner.py", "gate/python/**", "tools/tester-unified-gate.sh", "tests/**"]
  forbid: ["src/assay/coverage_parsers/**", "src/assay/adapters/go.py", "src/assay/canary.py", "src/assay/git.py", "src/assay/safeio.py", "nyxloom-trove/carve-assets/**", "nyxloom-trove/decisions.md", "docs/DESIGN-GUIDE.md", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "The installed CLI's own verifier accepts a schema-v5 R0,R2 artifact recording language and source_roots in judgment.resolved with no judgment.r1 present, and accepts an R0,R3 artifact whose judgment.resolved carries NO base, while refusing an R0,R2 artifact that omits base"
    negative: "judgment.resolved is absent, or carries no base on an R1/R2 lane, or an R0,R3 lane is forced to invent a base it never resolved"
    gate: tester-unified
  - id: O2
    observable: "Each language's operator vocabulary is closed: a lane whose resolved language is python and which declares a sql: or go: operator is refused at config load, and every mutant_outcome.operator prefix equals judgment.resolved.language"
    negative: "A cross-language operator is accepted, so a Python lane reports SQL mutation operators it cannot possibly have applied"
    gate: tester-unified
  - id: O3
    observable: "A run whose mutants are all provably equivalent renders INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT, and the equivalent bucket is paired both-present-or-both-absent with judgment.r2.equivalence_artifact"
    negative: "An all-equivalent run renders PASS, so a run that proved nothing about the tests reads as green; or equivalent entries appear with no declared equivalence_artifact"
    gate: tester-unified
  - id: O4
    observable: "kill_attribution is derived from judge.mutation.kill_signal_artifact and self-consistent: declared requires a kill_signal on every killed entry, unattributed forbids one anywhere, and no bucket other than killed may carry a kill_signal under either value"
    negative: "A survived or equivalent entry carries a kill_signal, so a mutant nothing killed reports the mechanism that supposedly killed it"
    gate: tester-unified
  - id: O5
    observable: "The whole registered gate is green after v5: P33's locked suite runs and passes, P26's module still runs with exactly FOUR tests deselected (20 of 24), and both P25 qualification consumers compare against the carver-supplied v5 siblings"
    negative: "Any gate step still compares a live v5 artifact against a locked v4 expectation -- P26's three template-coupled tests, gate/python/qualify_topos.py, tests/test_python_qualification.py -- or a non-v4-coupled security oracle is deselected along with them"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a v5 field cannot be populated without reaching a forbidden owner"
  - "editing a locked carve asset appears necessary despite A-222/A-224/A-226"
  - "sweep_v4_consumers.py reports a consumer of a locked v4 expectation that no work item addresses"
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
  cross-object invariants, and because 92 files migrate behind it.
- Readiness: **re-carved three times, after three NOT READY pre-dispatch reviews.**
  `nyxloom-trove/SCHEMA-V5-DESIGN.md` and decisions A-220/A-221/A-222 plus the
  repair sets A-223/A-224/A-225, A-226/A-227/A-228 and **A-229** fix every
  externally visible choice. Committed under `nyxloom-trove/carve-assets/P33/`: the locked v5
  schema, a committed v4 snapshot, **six** expected templates (four v5 shapes plus
  two P25 v5 siblings), the controlled-red probe, the migration manifest, the
  consumer sweep, and the locked v5 acceptance suite. **Three review rounds are
  answered** in `reports/assay-P33-JIT-CARVE.md` §§ "Answering the pre-dispatch
  review", "Answering round 2" and "Answering round 3".
- Implementer freedom: internal decomposition of model and verifier code only.
  The schema bytes, the operator vocabulary, the field names, the ownership split
  in the migration manifest, the six expected templates, and the locked v5
  acceptance suite are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P33-verdict-schema-v5` on
branch `feat/assay-P33-verdict-schema-v5`.

## Context to read first

1. `nyxloom-trove/SCHEMA-V5-DESIGN.md` in full — it is the specification, and it
   records what v5 deliberately does *not* do and why.
2. Decisions **A-220** (v5's shape), **A-221** (the `go:*` vocabulary and its
   deliberate exclusions), **A-222** (locked v4 evidence is not rewritten),
   **A-223** (the repair set: conditional `base`, derived `kill_attribution`,
   observable-direction helper correspondence, the `ALL_MUTANTS_EQUIVALENT`
   terminal, `kill_signal` killed-only, base/source-roots provenance, the
   restored at-least-one-tier guarantee), **A-224** (scope corrections),
   **A-225** (A-221's corrected reasoning — P33 does not make a Go or SQL R2 lane
   reachable), **A-226** (the consumer inventory, the P25 v5 siblings, and the
   amendment that P26's module is deselected rather than retired), **A-227**
   (`kill_signal_artifact` reserved but not declarable; all three `helpers` roles
   ruled; the base rule's only-if half), **A-228** (the new terminal inherits every
   sibling constraint), **A-229** (the sweep's own five closure gaps, the
   over-inclusive deselect, and CA8 taken rather than deferred), plus
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
against the committed asset both before and after your work. It can, because the
transform reads the committed `verdict.schema.v4-snapshot.json` rather than the
live shipped schema you are about to overwrite — the first carve read the live
path, which made this requirement unsatisfiable the moment work item 1 ran.

The five changes are V5-1 through V5-5 in the design document, as amended by
A-223. Their rationale is not repeated here; read it there, because three are
counter-intuitive: V5-1 is a v4 bug fix rather than an SQL feature, V5-4
deliberately does not close the gap it describes, and `base` inside
`judgment.resolved` is conditional rather than always-required because
`JUDGE_FIELDS_BY_RIGOR` carries it for R1/R2 and not for R3.

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
3. **The all-inert terminal, not a score.** There is no `score` field in v5 and
   none is being added, so "excluded from the score" would have been an
   unobservable requirement. What is observable is the terminal: `killed +
   survived == 0` with a non-empty `equivalent` bucket renders
   `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT`, ranked after `survived` in A-117's
   precedence. That makes the exclusion falsifiable — an implementation that
   ignores `equivalent` renders `PASS` and fails `test_ca4_*`. The
   `candidate_count`/`total`/bucket-length relation extends to include
   `equivalent`.
4. **Attribution consistency.** `kill_attribution: declared` requires
   `kill_signal_artifact` and a `kill_signal` on every `killed` entry;
   `unattributed` forbids both, on every bucket.
5. **Helper correspondence, observable direction only (A-223c).** Every
   `helpers` entry requires a correspondingly-judged claim: `mutation-sites`
   requires an R2 claim carrying a `mutation` payload, `statement-positions` an R1
   claim carrying `coverage`. The **converse is deliberately not implemented
   here** — nothing in the artifact bytes says a claim used a helper, so "a claim
   produced with a helper requires an entry" has no readable antecedent and any
   implementation of it would be vacuous. P34 owns it, with the adapter that makes
   the state reachable (A-142/A-144's precedent).

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
   unreachable**, and **prove the refusal behaviour unchanged** (A-225). P33 does
   **not** make a Go or SQL R2 lane runnable: `cli._built_in_registry` registers
   Python only, and `cli._resolve_declared_adapters` already refuses any declared
   level above R0 for an unregistered language with `ERROR/BAD_LANE_CONFIG`
   before anything executes (A-139). Add a test pinning exactly that, for both
   `go` and `sql`, so a later package cannot quietly change it. Do not register a
   language, do not touch `adapters/go.py`, and do not create `adapters/sql.py` —
   that is P34's.
6. Derive `kill_attribution` (A-223b), and **refuse the declaration until P34
   (A-227).** `judgment.r2.kill_attribution` is `declared` when
   `judge.mutation.kill_signal_artifact` is present and `unattributed` otherwise —
   derived, never stored, for A-036's reason. But P33 ships no producer for
   `kill_signal`, so a lane declaring that field could not emit a consistent
   artifact: `config` must refuse `judge.mutation.kill_signal_artifact` at load
   with a **typed error whose message names it as reserved for P34**. Every real
   P33 lane therefore derives `unattributed`; both values stay expressible as
   documents and the locked templates exercise both.

   **The observable is the message, not the refusal.** `judge.mutation` is already
   a closed sub-table, so an unknown key is *already* rejected today — meaning
   "the declaration is refused" cannot distinguish a correct implementation of
   this work item from doing nothing at all. The oracle is that the refusal names
   `kill_signal_artifact` as **reserved and deferred to P34**, not as an unknown
   key. The locked suite pins that string, and pins the exception type to
   `LaneConfigError` specifically (A-230c) — a test accepting any exception whose
   message carries the right words is satisfied by the wrong class with an
   accidentally-right message.

6b. **`equivalence_artifact` gets the identical disposition (A-230b):** reserved
   in the artifact contract, refused at config load with the same typed
   `LaneConfigError` naming P34. P33 ships a producer for neither field, so
   allowing one and refusing the other was an inconsistency with nothing behind
   it.

6c. **`helpers` is OMITTED when no helper ran (A-230a)** — never serialized as
   `helpers: []`. The same rule the artifact already follows for `judgment`,
   which is absent rather than empty when nothing was judged.

6d. **P33 does not claim to witness the `kill_attribution` derivation
   (A-230d).** Because `kill_signal_artifact` is P33-refused, every P33 lane
   derives `unattributed`, and no P33 fixture can distinguish a correct
   derivation from a hardcoded constant. Specify the rule, enforce it for
   documents, leave the producer-side proof to P34 — and do **not** build a
   construction seam whose only purpose is to make the claim testable.
7. Add the `ALL_MUTANTS_EQUIVALENT` terminal (A-223d) to `errors.py` and rank it
   in `judge_mutation` after `survived`: `killed + survived == 0` with a
   non-empty `equivalent` bucket renders `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT`.
   Extend the `candidate_count`/`total`/bucket-length arithmetic to include
   `equivalent`. **Propagate it to every layer that constrains its three siblings
   (A-228), which is the full list:** the schema branch (already in the locked
   asset), `verdict.py`'s `_MUTATION_ONLY_REASON_CODES`, the reasoning around
   `verify.py`'s `_INDEPENDENT_R2_TERMINALS`, and `verdict.py`'s "four empty
   buckets" error text, which is now five. Ranking after `survived` is
   order-insensitive given the guard; keep it there for readability, not because
   it is load-bearing.
8. **Keep P26's module, deselect exactly FOUR tests, and add P33's suite
   (A-226 as amended by A-229).** In `tools/tester-unified-gate.sh`, keep the P26
   invocation and append `--deselect` for each of:
   `test_cli_emits_the_complete_hand_authored_v4_artifact`,
   `test_cli_preserves_independent_malformed_missing_and_current_evidence`,
   `test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command`, and
   `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`.
   Then add a second invocation of `carve-assets/P33/test_acceptance_v5.py` and
   emit `ASSAY_GATE_PHASE=verdict-v5-accepted`. Do not edit P26's suite or its
   templates. **20 of P26's 24 tests keep running.**

   `test_all_structural_and_aggregate_bounds_precede_every_git_call` is **NOT**
   deselected. It tests A-210's aggregate-bounds-before-Git ordering and touches
   no artifact shape. An earlier version of this work item deselected it because
   a summary table and its own appendix disagreed and the wrong one was adopted —
   a security oracle dropped by a transcription slip.
   **Retiring the module outright is explicitly wrong**: it drops 18
   shape-independent oracles, several of them boundaries earned from witnessed
   incidents.
8b. **Repoint the other two consumers of a locked v4 expectation (A-226).**
   `gate/python/qualify_topos.py` and `tests/test_python_qualification.py:259`
   both compare against P25's locked v4 templates. **`qualify_topos.py` has SIX
   v4-coupled sites, not two** — an earlier version named only the first two, so
   round 3's R3-D2 was only partially closed. All six, verified by direct read:
   `:51` `_EXPECTED_ROOT`; `:715` `normalized["judgment"]["r1"]["base"]`, which
   v5 moves to `judgment.resolved.base`; `:928` reads `missing-v4-template.json`;
   `:962` passes it as `template=`; `:1009` iterates both template names; `:1018`
   passes `template=_EXPECTED_ROOT / template_name`. Every one must point at the
   v5 siblings. Point both at `carve-assets/P33/expected/p25-pass-v5-template.json`
   and `p25-missing-v5-template.json`, which the carver supplies. P25's originals
   stay frozen and unedited. Do **not** write a v4→v5 transform inside a gate
   harness: an unfrozen proof source is not an expectation.
   The inventory is `carve-assets/P33/sweep_v4_consumers.py`; re-run it and
   confirm it reports no consumer you have not addressed. It now also reports
   `gate/distribution/release_wheel.py` as `indirect-path-from-caller`: it
   compares a frozen release manifest whose path arrives on its command line, so
   v5 does not break it, but it is in the inventory because the closure claim has
   to be true regardless of whether a given instance happens to be harmless.

8c. **Take CA8: make declared and resolved `base` genuinely differ (A-143/A-229).**
   In `gate/python/qualify_topos.py`, add one qualification scenario declaring
   `judge.base` as a **tag on the base commit** — not HEAD, which
   `_check_base_is_head` already occupies — and assert `judgment.resolved.base`
   equals the resolved 40-hex and is **not** the declared string. The machinery
   exists: `:404` has `base_override`, `:424` uses it as `declared_base`, `:836`
   already passes it. Without this, an implementation that records the declared
   string passes every other oracle, because every locked template substitutes a
   full 40-hex and `resolve_base` returns a full SHA unchanged. A-143: *the
   fixture must make resolved and declared genuinely different, or it proves
   nothing.* This carve deferred it twice on the premise that it needs a real
   repository and therefore belongs with the producer proof — a premise that does
   not survive checking, since the harness is already in `scope.touch` and
   already edited by 8b.
9. Migrate every path in `migration-manifest.json`'s `implementer_owned` bucket
   — see `migration-manifest.json`'s `CANONICAL_COUNTS`, which is the single
   source of truth for every count in this package. Do not touch
   `locked_carver_owned` or any path in `carver_owned_prose_excluded`. Round 4
   found four different count pairs across the handoff, README, manifest and
   suite docstring; no document restates a number independently any more.
10. Make the locked suite green: `PYTHONPATH=src python3 -m pytest
    nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -q -p no:randomly` is
    the pre-implementation state recorded in `migration-manifest.json`'s
    `CANONICAL_COUNTS` and must reach all green. Every negative in it is differential — it asserts a clean control
    verifies *and* the injected defect does not — so none of them can pass on a
    version mismatch.
11. Run the full gate and record exact A-067 controlled-break counts.

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
permitted exactly one of three product options.

**This handoff's first version repeated the defect it named.** It claimed every
load-bearing file had a status while leaving `src/assay/cli.py` and
`tools/tester-unified-gate.sh` with none, forbidding `src/assay/mutation.py`
that in-scope `verify.py` already imports, and omitting
`gate/python/qualify_topos.py` from scope while work item 9 required migrating
it. All four are corrected above (A-224). The lesson is not "add statuses" but
that a claim of completeness is itself a thing to verify: the scope list is now
checked mechanically against `migration-manifest.json`, and that check is a test
in the locked suite rather than a sentence here.

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
fixture would catch. All three are frozen in the locked suite rather than
delegated.

**Every negative must be differential.** Assert that the unmodified control
verifies clean in the same test that asserts the injected defect does not. A bare
"this is refused" passes on a pre-implementation tree for the wrong reason — the
v4 verifier refuses any v5 document on its version alone — and that exact
short-circuit is why three blocking defects survived the first carve. The locked
suite's `refuses_only_the_defect` helper is the required shape.

## Scope / forbid

P33 owns the artifact contract and its consumers. It owns no adapter capability,
no coverage parser, and no locked carve asset. `merge-lane` is required because
the artifact contract changes.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not
improvise a workaround.
