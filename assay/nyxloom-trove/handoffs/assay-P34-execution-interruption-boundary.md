---
schema_version: 1
id: assay-P34-execution-interruption-boundary
project: assay
title: "An orchestrator interruption cannot become a functional assay result"
tier: luna-high
input_revision: "a1050e58d573b2e97f9003be239885b39c0ce4ae"
source: {kind: product-goal, ref: "nyxloom-trove/4-backlog.md#b099"}
stack: none
depends_on: []
session: fresh
scope:
  touch: ["src/assay/cli.py", "src/assay/errors.py", "src/assay/runner.py", "src/assay/safeio.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/schemas/verdict.schema.json", "src/assay/schemas/interruption-receipt.schema.json", "src/assay/result_reports/**", "tests/**", "gate/**", "README.md", "docs/DESIGN-GUIDE.md", "docs/CONSUMERS.md", "CHANGES.md"]
  forbid: ["nyxloom-trove/decisions.md", "nyxloom-trove/4-backlog.md", "src/assay/adapters", "src/assay/coverage_parsers", "src/assay/mutation.py"]
oracles:
  - id: O1
    observable: "A run-gate-supplied, schema-valid interruption receipt plus an absent, malformed, or incomplete structured report produces INCONCLUSIVE/INFRASTRUCTURE_INTERRUPTED for every unfinished claim, for both exit 0 and nonzero; no candidate bucket or functional FAIL is emitted from the interrupted portion"
    negative: "A nonzero kill is called COMMAND_FAILED, a zero exit is called PASS, a partial mutation payload earns credit, or a liveness/budget event is inferred from stderr, CPU, elapsed time, or the final resource profile"
    gate: tester-unified
  - id: O2
    observable: "A verified-complete framework report remains the sole R0 truth and determines PASS/FAIL from its own failure count in both exit-code directions, even when an interruption receipt is present; no receipt preserves B078's legacy fallback"
    negative: "Receipt presence overrides a complete report, an absent receipt is silently treated as interruption, or a generic nonzero exit is converted to PASS"
    gate: tester-unified
  - id: O3
    observable: "The no-test reconciliation path consumes only an atomically written execution journal and a verified receipt, preserves complete prior claim boundaries, marks unfinished levels payload-free, writes one schema-valid verdict, and never reruns the declared command"
    negative: "Reconciliation reruns tests, trusts a stale journal, manufactures coverage/mutation/canary payloads, or reports a killed run as a product failure"
    gate: tester-unified
  - id: O4
    observable: "Receipt identity, bounded safe reading, atomic replacement, observer/kind vocabulary, and receipt-write failure are fail-closed across the assay/run-gate boundary; a final RG-55 profile summary alone cannot trigger the terminal"
    negative: "A stale/symlinked/malformed receipt is accepted, a receipt write is lost before termination, a post-hoc host stall is treated as causal, or an unreadable sidecar falls back to COMMAND_FAILED"
    gate: tester-unified
  - id: O5
    observable: "The shipped README, DESIGN-GUIDE, CONSUMERS, CHANGES, schema examples, closed-vocabulary checks, and anchors describe the new input and outcome consistently, with every loadable example carrying the current lane schema version"
    negative: "A new flag, receipt value, schema version, or reconcile command is only present in code, a config example does not parse, or a cross-document link dangles"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "run-gate cannot atomically publish a fresh receipt before every liveness or outer-budget termination, or cannot bind the same receipt and execution-journal paths into the assay namespace"
  - "the only host-pressure input is RG-55's final resources.host.memory_full_stall_seconds summary, with no live causal cgprofile observation and explicit threshold owner"
  - "the new interruption terminal cannot be represented without reusing LANE_TIMEOUT, CANDIDATE_HUNG, CANARY_INCONCLUSIVE, or another narrower reason code"
  - "the red-first matrix would need sleep, wall-clock scheduling, host load, network, or a parser invented inside assay"
mutexes: [merge-lane]
---

# P34 — execution interruption boundary

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P34-execution-interruption-boundary`
on branch `feat/assay-P34-execution-interruption-boundary`. The companion
run-gate work is a separate package and must not be implemented by editing
`/workspaces/vbpub/run-gate-project` from this worktree.

## Context to read first

1. `nyxloom-trove/4-backlog.md` B099, this handoff, and decision A-448.
2. `nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md` and B078's merged review
   report; preserve the verified-complete report precedence and all B078
   fallback behavior.
3. `src/assay/result_reports/`, `src/assay/runner.py`, `src/assay/verdict.py`,
   `src/assay/verify.py`, `src/assay/errors.py`, and their focused tests.
4. The run-gate owner must read its live `ProgressWatch`, `LogStreamWatch`,
   inflight, and RG-55 profiling contracts before implementing the companion
   receipt producer. The final profile summary is not a causal interruption
   fact.

## Claim boundary

An execution that the orchestrator interrupted for liveness or a safety
ceiling is incomplete infrastructure evidence. It is never silently turned
into a framework PASS or a product FAIL. A framework report that assay has
verified complete remains stronger evidence than the interruption receipt and
continues to determine R0 truth. This package is intentionally narrower than
"host pressure": assay does not read host PSI, and run-gate's final resource
summary cannot establish causality for one earlier process.

## Why this is design-only at the current revision

Three independently required facts are outside assay's current interfaces:

1. `assay` has no host `/proc/pressure` reader and no input for run-gate's
   `ProgressWatch`/`LogStreamWatch` decision. Its candidate-only `LivenessRunner`
   is deliberately not a baseline/R0 oracle.
2. run-gate's watcher and outer budget live outside the assay process. A kill
   can arrive before assay writes its final verdict, while RG-55 finalizes the
   profile in the outer `finally` after the child has already stopped. An
   assay-only `incomplete report => INCONCLUSIVE` rule would misclassify a
   genuine crash or product failure; an assay-only `profile summary =>
   interruption` rule would invent causality.
3. The closed reason-code vocabulary has no truthful cross-tier interruption
   member. `LANE_TIMEOUT` names assay's elapsed lane deadline,
   `CANDIDATE_HUNG` is native R2 candidate evidence, and
   `CANARY_INCONCLUSIVE` is R3-specific. Adding the required
   `INFRASTRUCTURE_INTERRUPTED` member changes the hand-written verdict schema
   and therefore requires the A-170 schema-v12 migration decision recorded as
   A-448.

Implementing only the assay half now would therefore be a false boundary. The
next agent must land the run-gate receipt producer/transport contract and the
assay consumer/reconcile contract together, with the cross-project acceptance
matrix below red before implementation.

## Normative contract

### 1. Receipt lifecycle and namespace

run-gate creates a fresh per-run `run_id` as 32 lowercase hexadecimal
characters and a fresh sidecar file outside the judged tree. The sidecar is
owned by the run-gate state directory, created with the existing private-store
permissions, and is mounted into the assay container at an explicit path. It
is never placed in the judged Git tree and is never discovered by globbing.

Before starting the assay command, run-gate atomically writes this valid
initial document:

```json
{
  "schema_version": 1,
  "state": "running",
  "run_id": "0123456789abcdef0123456789abcdef",
  "lane": "ui_unit",
  "commit": "0123456789abcdef0123456789abcdef0123456789"
}
```

On a liveness or outer-budget interruption, the owning watcher atomically
replaces it **before** termination with:

```json
{
  "schema_version": 1,
  "state": "interrupted",
  "run_id": "0123456789abcdef0123456789abcdef",
  "lane": "ui_unit",
  "commit": "0123456789abcdef0123456789abcdef0123456789",
  "kind": "liveness",
  "observer": "run-gate.progress"
}
```

The closed v1 pairs are:

| `kind` | accepted `observer` | required fact already available to the producer |
|---|---|---|
| `liveness` | `run-gate.progress` | `ProgressWatch.poll()` returned a stall while the container was still running |
| `liveness` | `run-gate.log-stream` | `LogStreamWatch.poll()` returned a stall while the container was still running |
| `budget_expired` | `run-gate.budget` | run-gate's own fixed safety ceiling expired and the owner terminated the lane |

No stderr signature, exit code, CPU sample, elapsed-time guess, or final
`resources.host.memory_full_stall_seconds` summary is a valid producer. A
host-pressure observer is intentionally **not** a v1 value: RG-56/cgprofile
must first provide a live, causally scoped sample and an explicitly owned
threshold. The operator's PSI launch limit is an operational guard, not this
product fact.

The receipt is read through a bounded no-follow safe reader. Assay verifies
the exact schema, `run_id`, lane, commit, and `(kind, observer)` pair against
the invocation. A missing receipt is not evidence of interruption. A supplied
receipt that is malformed, stale, symlinked, oversized, or mismatched is a
named infrastructure refusal; it must not fall through to a functional
result. The initial `running` state is not interruption evidence and keeps the
normal no-interruption behavior for a naturally completed invocation. If
run-gate cannot replace it before a forced termination, run-gate itself must
refuse the gate rather than accept assay's fallback result.

### 2. Verdict and report precedence

The future verdict schema v12 adds the closed reason code
`INFRASTRUCTURE_INTERRUPTED` to `INCONCLUSIVE` and an optional, validated
`execution_interruption` summary containing the receipt's `run_id`, `kind`,
`observer`, and SHA-256 digest. It is present iff the verdict contains the
new interruption reason. The raw receipt remains the durable external proof;
the verdict summary makes the status explicit to consumers that only retain
the verdict artifact.

For a normal `assay run`, the adjudication table is:

| report | verified receipt | process exit | result |
|---|---|---|---|
| complete, zero failures | any | any | `PASS` |
| complete, one or more failures | any | any | `FAIL` with the report-derived functional reason |
| absent/unparseable/incomplete | interrupted | 0 or nonzero | `INCONCLUSIVE/INFRASTRUCTURE_INTERRUPTED` |
| absent/unparseable/incomplete | valid `running` or no receipt | 0 | existing A-073 `PASS` fallback |
| absent/unparseable/incomplete | valid `running` or no receipt | nonzero | existing A-073 `FAIL/COMMAND_FAILED` fallback |
| receipt malformed/mismatched when supplied | any | any | explicit infrastructure refusal; never fallback |

The complete-report rows have priority over the receipt because verified
framework truth is stronger than an interruption that happened after the
framework had already finished. The interrupted row has priority over the
raw exit code because a killed process can return 0 or nonzero without saying
whether tests finished. A generic nonzero exit still never becomes PASS.

For R1–R4, the affected claim is payload-free. No incomplete coverage,
mutation, canary, or red-first payload is synthesized. If a prior rigor
boundary was atomically completed and independently valid, it may remain; the
unfinished suffix is inconclusive. A functional FAIL that was already backed
by a verified complete claim remains a functional FAIL, because that claim is
independent evidence rather than the interruption's exit status.

### 3. Execution journal and reconciliation

Assay writes a private, atomic execution journal before launching the wrapped
command. It contains the exact `schema_version`, `run_id`, lane, commit, and
resolved lane metadata needed to construct a verdict, followed by one
record for each rigor boundary only after that boundary's claim is complete
and its payload has passed the same model/verifier checks used for the final
artifact. A torn or missing journal never grants a claim.

The CLI additions are exact:

```text
assay run <lane> ... --run-id <32-lower-hex> --interruption-receipt <path>
assay reconcile <lane> ... --run-id <32-lower-hex> \
  --execution-journal <path> --interruption-receipt <path>
```

`assay reconcile` is a no-test operation. It requires an interrupted receipt,
reads the journal and the already-declared report paths, verifies lane/commit/
run identity, retains only complete journal boundaries, emits one schema-valid
verdict, and returns the verdict's exit code. It never invokes the lane's
argv, a framework parser, or a framework-specific completion heuristic. A
receipt-write failure, missing journal, or lost container is handled by
run-gate as infrastructure refusal; it is not converted into
`COMMAND_FAILED`.

For native R2, journal boundaries and the existing `.assay/progress-*` plus
state store remain resumable. An interrupted attempt contributes no partial
`killed`, `survived`, `crashed`, `budget_exceeded`, `hung`, or `equivalent`
candidate result. The next invocation uses the existing mandatory
`--resume --progress` path and begins at the next uncommitted candidate.

### 4. Cross-project run-gate obligations

The assay package cannot satisfy these alone. A companion run-gate change
must:

1. allocate the receipt and journal paths before `docker run -d`, bind the same
   path into the assay namespace, pass `--run-id` and
   `--interruption-receipt`, and disclose the paths;
2. replace the receipt before every `ProgressWatch`, `LogStreamWatch`, or
   outer-budget termination, then terminate the container;
3. fail closed if that replacement fails, preserving the inflight record and
   reporting an infrastructure result rather than a product result;
4. after a forced termination, invoke `assay reconcile` before clearing the
   inflight record, while allowing a later re-attach to finish that same
   reconciliation; and
5. keep RG-55's final resources/profile disclosure observational. It must not
   synthesize an interruption receipt from a post-run summary.

That companion work belongs to run-gate/cgprofile and must not be improvised
inside this assay package or by editing an operator-owned run-gate backlog.

## Red-first acceptance matrix

The following tests are required before implementation is accepted. Each
listed negative must fail against the pre-change implementation or the
corresponding test is not an oracle.

| test | red condition | green assertion |
|---|---|---|
| `test_incomplete_report_interruption_is_inconclusive_for_exit_0_and_nonzero` | B078 currently uses A-073 exit mapping | both exits yield the new pair and no functional reason |
| `test_complete_report_wins_over_interruption_in_both_directions` | a naive receipt override changes a complete report result | report clean/failure counts remain the only R0 truth |
| `test_no_receipt_keeps_b078_fallback` | an unconditional incomplete-report override changes legacy lanes | no receipt is byte-compatible; nonzero is never PASS |
| `test_receipt_identity_and_shape_fail_closed` | no current receipt reader exists | wrong run/lane/commit, unknown state/pair, symlink, oversized, torn, and malformed documents are explicit infrastructure refusals; valid `running` is not interruption evidence |
| `test_reconcile_never_runs_declared_argv` | there is no reconcile command | a spy/fixture proves no subprocess test command is launched and one verdict is written |
| `test_reconcile_preserves_only_complete_boundaries` | there is no journal | kill at each phase boundary preserves complete claims and marks the suffix payload-free |
| `test_interrupted_r2_resumes_without_partial_credit` | current mutation state cannot be tied to a forced outer kill | next `--resume --progress` run starts at the next uncommitted identity and no abandoned identity enters a result bucket |
| `test_run_gate_receipt_precedes_each_termination` | current watcher has no receipt sidecar | progress, log-stream, and budget kill probes observe the receipt before termination; write failure refuses the gate |
| `test_final_profile_summary_cannot_create_causality` | RG-55 has a host stall summary but no interruption input | post-run resources alone leaves the functional result unchanged |
| `test_docs_schema_examples_vocabularies_and_anchors_are_executable` | no v12 input/vocabulary/docs exist | shipped loader parses examples, every receipt/observer/reason value is documented, and all links resolve |

No test may use host load, sleep, wall-clock timing, network, an invented
framework parser, or a mutable consumer checkout as its oracle. Use injected
clocks and fake watcher decisions for the assay unit tests; reserve the
cross-project gate probe for the real run-gate/container boundary.

## Exact implementation file list

### Assay package

- `src/assay/errors.py`: add `ReasonCode.INFRASTRUCTURE_INTERRUPTED` and bind
  it only to `Outcome.INCONCLUSIVE`.
- `src/assay/schemas/verdict.schema.json`, `src/assay/verdict.py`, and
  `src/assay/verify.py`: cut verdict schema v11 → v12, add the receipt summary
  and all presence/identity/closed-vocabulary rules; do not accept v11 in the
  released verifier.
- `src/assay/schemas/interruption-receipt.schema.json`, `src/assay/safeio.py`,
  and a focused receipt module: bounded no-follow read, strict schema, digest,
  and invocation identity checks; no host PSI reader.
- `src/assay/runner.py`: thread the optional receipt/journal through every
  report-enabled baseline path and the no-test reconciliation path. Complete
  report precedence and legacy no-receipt fallback must remain centralized.
- `src/assay/cli.py`: add the exact flags/subcommand, require the run id when
  receipt mode is requested, and return the emitted verdict's exit code.
- `src/assay/result_reports/**`: preserve the existing complete/incomplete
  distinction; do not add a framework-specific parser or trust `success`.
- `tests/**`: add the red-first matrix, hand-written schema-v12 fixtures,
  digest/identity attacks, journal boundary cases, and the no-rerun probe.
- `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, and `CHANGES.md`:
  document the optional run-gate input, exact receipt vocabulary, precedence,
  resume semantics, schema version, and the fact that host PSI is not inferred.
  Add executable checks for loadable config examples, closed values, and
  anchors in the same change.

### Companion owner (separate handoff required)

The run-gate/cgprofile package owns `run-gate-project/run_gate.py`, its tests,
and its own user-facing docs. It must not be folded into this assay package or
implemented by editing operator-owned backlog/WIP files. Its contract is the
five obligations in §4 plus the host-pressure decision gate in
`escalate_if`. Until that companion packet is accepted, this assay packet is
not dispatchable.

## Scope / forbid

This package does not alter RG-55 D-1..D-30, the existing `Outcome` exit-code
numbers, B078's verified-complete report semantics, assay adapters, coverage
parsers, or the mutation engine's current candidate buckets. It does not add a
performance lane or infer host pressure. It does not touch dstdns,
run-gate-project files, or operator-owned dirty files.

## BLOCKED rule

If the companion receipt producer cannot meet the exact lifecycle, namespace,
and fail-closed write contract, or if the v12 reason/summary cannot be
represented without a forbidden scope expansion, write
`BLOCKED: <reason>` to the implementation LOG, commit it, and exit. Do not
replace a missing observer fact with exit-code, stderr, CPU, elapsed-time, or
final-profile inference.
