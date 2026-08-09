---
schema_version: 1
id: assay-P21-verdict-v4-evidence-contract
project: assay
title: "Verdict v4 carries enough bounded evidence to verify every judgment"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P20-repository-artifact-boundary-integrity]
session: fresh
scope:
  touch: ["src/assay/errors.py", "src/assay/vocabulary.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/config.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/runner.py", "src/assay/cli.py", "src/assay/schemas/**", "tests/**", "README.md", "docs/DESIGN-GUIDE.md", "assay.toml"]
  forbid: ["src/assay/adapters/python.py", "src/assay/adapters/go.py", "pyproject.toml"]
oracles:
  - id: O1
    observable: "Model construction, shipped JSON Schema, and independent raw-document verification accept the same closed v4 vocabulary and reject every v1-v3 artifact with one version-only diagnostic"
    negative: "An unknown mutant operator passes assay verify, or a v3 artifact is coerced/defaulted into v4"
    gate: tester-unified
  - id: O2
    observable: "Every attempted mutant, including killed mutants, carries a stable identity and is bound to the declared operator policy; a required positive max_mutants is recorded and excess candidates stop before any mutant command"
    negative: "Changing a killed identity/operator remains schema-valid and verify-clean, or max_mutants+1 submissions run as a silently truncated sample"
    gate: tester-unified
  - id: O3
    observable: "A canary payload records the exact project-relative target and coverage records whether exclusion data was reported or unavailable; both correspond to the resolved judgment policy"
    negative: "Changing judgment.r3.target or rewriting exclusion-unavailable as known-empty passes independent verification"
    gate: tester-unified
  - id: O4
    observable: "Verdict intervals satisfy ended >= started and an unavailable verdict destination is detected before the lane command, exits ERROR with OUTPUT_WRITE_FAILED, and causes no consumer-side effects"
    negative: "A reversed interval validates, or an unwritable output path runs the command before failing with a traceback/generic exit"
    gate: tester-unified
  - id: O5
    observable: "A command that moves HEAD but leaves a clean tree produces HEAD_CHANGED, while any remaining staged, unstaged, or nonignored untracked dirt retains DIRTY_TREE precedence"
    negative: "A clean commit is mislabeled DIRTY_TREE or higher-rigor evaluation runs against the moved HEAD"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "one of the named facts cannot be represented without a second schema bump"
  - "output destination readiness cannot be established before execution without writing outside the declared path"
mutexes: []
---

# P21 — verdict v4 evidence contract

The claim to attack: **every fact needed to reproduce Assay's judgment is present, bounded, and independently checkable in one v4 artifact.**

## Dispatch contract

- Contract class: **2b — complex solution-bearing execution** (`implement-4`
  when deployed; frontmatter names the live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Opus xhigh implementer → a fresh
  Opus xhigh independent reviewer session**. Do not let one Opus context author
  and adjudicate the migration.
- Readiness: **PROVISIONAL until P20 merges, then JIT-FREEZE REQUIRED.** Sol must
  replace every abbreviated object below with a complete canonical v4 example,
  two invalid examples per public shape, and carver-authored model/schema/raw-
  verifier acceptance inputs before dispatch.
- Implementer freedom: private construction only. Names, types, requiredness,
  operator/reason vocabularies, migration behavior, and cross-field invariants
  are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P21-verdict-v4-evidence-contract`
on branch `feat/assay-P21-verdict-v4-evidence-contract`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings F08–F12 and the schema-v4 recommendation; reproduce the unknown-operator verifier acceptance before implementation.
2. `docs/DESIGN-GUIDE.md` §6 in full; decisions A-008, A-027–A-029, A-041, A-050, A-067, A-116–A-117, A-135–A-138, A-148, A-152 and A-157–A-158.
3. `src/assay/verdict.py`, `src/assay/schemas/verdict.schema.json`, and `src/assay/verify.py` side by side. List each cross-field invariant and prove which of the three layers owns it before editing.
4. `src/assay/mutation.py::{MutantOutcome,run_mutation,judge_mutation}`, `src/assay/canary.py`, `src/assay/evaluate.py`, and their complete-artifact fixtures. Preserve A-158: a normally-started nonzero mutant command is killed; crashed means the command boundary could not execute.
5. `src/assay/config.py`'s closed `MutationConfig` parsing and `JudgeConfig.as_declared`. No runtime consumer may invent a missing cap.
6. P16's migration/conformance tests and P19's model/raw-verifier correspondence tests. Extend both independent layers; do not make `assay verify` import the producer model as its oracle.

## Implementation packet (normative)

### v4 grammar and owners

`src/assay/vocabulary.py` is a new stdlib-only leaf owning
`MUTATION_OPERATORS`. Config, mutant construction, verdict models, JSON Schema,
and the raw verifier use that one ordered tuple; the Schema enum may be emitted
from the same reviewed literal at authoring time but the shipped schema remains
hand-readable and its conformance test compares the two sets.

The following excerpt fixes the new serialized shapes (unchanged v3 fields are
omitted here, not optional in the real document):

```json
{
  "schema_version": 4,
  "judgment": {
    "r2": {"jobs": 2, "max_mutants": 50,
           "operators": ["compare-swap"]},
    "r3": {"mechanism": "uncovered-line", "target": "src/p.py"}
  },
  "claims": [{"rigor": "R1", "coverage": {
    "exclusion_capability": "reported"
  }}, {
    "rigor": "R2",
    "mutation": {
      "candidate_count": 1,
      "total": 1,
      "killed": [{"path": "src/p.py", "lineno": 7,
                  "start_byte": 83, "end_byte": 84,
                  "replacement_sha256": "<64 lowercase hex>",
                  "operator": "compare-swap", "description": "< to <="}],
      "survived": [], "crashed": [], "budget_exceeded": []
    }
  }, {
    "rigor": "R3",
    "canary": {"mechanism": "uncovered-line", "target": "src/p.py",
               "description": "...", "control_outcome": "PASS",
               "transformed_outcome": "FAIL",
               "expected_reason_code": "UNCOVERED_LINES",
               "observed_reason_code": "UNCOVERED_LINES"}
  }]
}
```

`Mutation.candidate_count` is the bounded number discovered. Normally it
equals `total`, and `total` equals the lengths of all four identity arrays. On
the pre-submission limit terminal it is exactly `max_mutants + 1`, `total` is
zero, all four arrays are empty, and the R2 claim is
`BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`; no mutant command ran. This sentinel
shape is the independent evidence for the refusal, not a silently truncated
list. Candidate discovery itself has a hard product ceiling of **10,001** so a
malicious declared cap cannot create unbounded memory/work; `max_mutants` must
be in `1..10,000`.

Every `MutantOutcome` identity is exactly
`(path,start_byte,end_byte,replacement_sha256,operator)`. `lineno` and
`description` remain diagnosis, not uniqueness. Byte offsets are zero-based
half-open UTF-8 byte offsets into the exact source file at the recorded commit;
`0 <= start_byte < end_byte`; `replacement_sha256` is lowercase SHA-256 of the
replacement bytes only. P21 derives the unique minimal changed byte span from
the existing original/mutated-text pair when constructing the outcome; if a
candidate has no change or cannot be represented as one contiguous replacement,
it is refused before execution. This gives P29's site protocol its final wire
identity without forcing v5 and prevents two same-line/same-description mutants
from collapsing.

`Coverage.exclusion_capability` is exactly `"reported"` or `"unavailable"`.
`unavailable` requires both exclusion detail fields empty; `reported` permits
empty or populated detail. `CanaryResult.target` is the normalized declared
project-relative string and must equal `judgment.r3.target`. Invalid v4 examples
that all three validation layers must reject: `"killed": 1`; operator
`"unknown"`; `unavailable` with a nonempty excluded line; reversed timestamps;
or different canary/policy targets. V1–v3 fail before any of these fields are
visited with one version-only diagnostic.

Add exactly these closed reasons in `errors.py`, Schema, model, and verifier:
`ERROR/OUTPUT_WRITE_FAILED`,
`ERROR/MUTATION_DISCOVERY_FAILED`,
`BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`, and
`BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` (reserved here for P22's reachable
snapshot refusal), plus `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL` (reserved here
for P27's first real external-tool preflight), and
`NO_MEASUREMENT/HEAD_CHANGED`. `HEAD_CHANGED` is used only when the post-command
index/worktree is clean and resolved HEAD differs from the pre-run full OID;
any dirt takes `DIRTY_TREE` precedence. No generic fallback reason is permitted.
P29/P30 use `MUTATION_DISCOVERY_FAILED` for invalid source, invalid
helper request/response, helper nonzero, or an otherwise failed syntax-aware
candidate boundary; valid discovery with zero sites remains
`INCONCLUSIVE/NO_MUTANTS`.

### Required flow and decision table

1. Load and validate the complete lane, including bounded `max_mutants`.
2. Resolve HEAD and policy. Preflight/reserve the verdict destination before
   any consumer command; reservation never redirects or invents a path.
3. Construct only v4 producer models. Serialize atomically to the reserved
   sibling temporary file and replace the destination.
4. `verify.py` first checks the raw top-level version, then JSON Schema, then
   re-derives cross-field rules without trusting producer constructors.

| State | Outcome/reason | Payload/side effect |
|---|---|---|
| bad output parent/type/permission before run | `ERROR/OUTPUT_WRITE_FAILED` | no lane command; stable stderr because requested artifact cannot exist |
| destination lost after run | `ERROR/OUTPUT_WRITE_FAILED` | no PASS claim and no fallback file |
| mutation discovery/helper protocol fails | `ERROR/MUTATION_DISCOVERY_FAILED` | complete R2 claim; zero mutant submissions |
| candidates = max+1 | `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` | sentinel mutation payload; zero submissions |
| snapshot exceeds P22 bound | `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` | complete artifact; zero command for that snapshot |
| command leaves dirt, whether or not HEAD moved | `NO_MEASUREMENT/DIRTY_TREE` | actual R0 preserved for higher rigor; no higher-rigor work |
| command moves HEAD and leaves clean tree | `NO_MEASUREMENT/HEAD_CHANGED` | actual R0 preserved for higher rigor; no higher-rigor work |
| old schema | verify failure | exactly one version diagnostic |

### Traceability and degrees of freedom

Work 1–3 -> vocabulary/model/schema/verifier -> O1/O2 -> hand-authored v4 plus
unknown/killed/same-line-identity/limit mutations; work 5–7 -> canary/coverage/time correspondence
-> O3/O4 -> exact field mutations; work 8 -> output reservation -> O4 -> a
sentinel command proving zero calls. The REPORT repeats this mapping with real
tests and controlled-break counts. Private helper names and dataclass field
order may vary; serialized keys, ranges, sentinel shape, reasons, validation
order, and independent raw re-derivation may not.

## Work

1. Bump the verdict artifact to schema v4 in one atomic migration. Convert every hand-written expected artifact and installed-wheel witness deliberately. `assay verify` must return one version-only diagnostic for v1–v3 before reading foreign fields; it never upgrades, defaults, or rewrites them.
2. Put the mutation-operator vocabulary in one cycle-safe module imported by config, mutation construction, verdict model, and raw verifier. Close both `MutationOutcome.operator` and `judgment.r2.operators` in the model and schema. Delete the current model/schema/verifier mismatch rather than maintaining parallel literal sets.
3. Replace killed's count-only representation with ordered `MutantOutcome`
   identities, matching survived/crashed/budget-exceeded, and add the packet's
   `candidate_count`. Identity includes the packet's UTF-8 byte span and
   replacement hash; derive it from the original/mutated pair and reject a
   no-op or non-contiguous edit before execution. Verify normal and limit-
   sentinel arithmetic exactly and require every payload operator to belong to
   the recorded policy. Sorting uses the identity tuple, never description or
   completion order.
4. Make `judge.mutation.max_mutants` a required integer in `1..10,000`, record it in `judgment.r2`, and enforce it after bounded candidate discovery but before any mutant command is submitted. Discover at most `max_mutants + 1` and never more than 10,001; excess renders the packet's `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` sentinel, with no partial sample and no credit. `jobs` remains only a concurrency bound and is never derived from machine capacity.
5. Add the project-relative canary target to `CanaryResult` and bind it exactly to `judgment.r3.target` in both construction and raw verification. The description remains explanation, never a parseable identity channel.
6. Preserve A-008 in the artifact with a closed R1 exclusion-capability field (`reported` versus `unavailable`). `unavailable` may not carry excluded lines; `reported` may truthfully carry an empty mapping. Re-derive the same rule in `verify.py`; do not infer capability from a particular format name.
7. Add construction/schema/raw-verifier checks that `ended >= started`. Use injected/fixed clocks in tests and exact timestamp values; no elapsed-time assertion.
8. Close A-O14 with `ERROR/OUTPUT_WRITE_FAILED`. Validate and reserve the declared output destination before the command executes; a bad/missing/unwritable parent must not allow the lane to run. Do not redirect to an invented fallback path. If a destination becomes unusable after reservation, emit the stable error to stderr, clean internal temporary state where safe, and never claim the requested file was written.
9. Hand-author valid and adversarial v4 artifacts for all levels. Break killed identity, operator vocabulary, max-mutant enforcement, canary target, exclusion capability, interval ordering, version handling, and output preflight independently; record exact A-067 failure counts.
10. Replace P20's schema-v3-compatible collapse of a clean post-command HEAD
    move into `DIRTY_TREE` with `NO_MEASUREMENT/HEAD_CHANGED`. Resolve HEAD once
    before execution and once immediately after; check dirt first. Prove the
    clean-commit, dirty-only, and commit-plus-dirt cases independently and prove
    no R1/R2/R3 work begins after either refusal.

## Test constraints copied from AUTHORING.md §3b

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

## Package-specific test emphasis

**A. No speed-dependent verdicts.** Cardinality and timestamps use exact injected values; output readiness is an I/O fact, never a timing guess.

**B. No order/worker dependence.** Artifact fixtures are immutable per test and mutation arrays are identity-sorted, never completion-sorted.

**C. No hollow tests.** Every new schema field has an independently malformed artifact and a real producer witness; model-only rejection is insufficient.

**D. No coverage evasion.** Maintain 100% statement/branch coverage and mutation-check each model/schema/verifier parity guard.

**E. Control inputs.** Clocks, candidate manifests, output paths, and raw JSON are explicit local inputs; no network or ambient metadata.

## Scope / forbid

This package is the one pre-adoption v4 migration. It must not add Go/TypeScript
behavior, change distribution identity, or redesign isolation. P22 consumes the
snapshot terminal; P23 and later R2/R3 packages consume the cap and evidence
fields to make repeated execution faithful.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
