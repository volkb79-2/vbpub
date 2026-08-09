---
schema_version: 1
id: assay-P21-verdict-v4-evidence-contract
project: assay
title: "Verdict v4 carries enough bounded evidence to verify every judgment"
tier: implement-2
input_revision: "618b6f15451ec5f45b5900dc496d794241180467"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P20-repository-artifact-boundary-integrity]
session: fresh
scope:
  touch: ["src/assay/errors.py", "src/assay/vocabulary.py", "src/assay/output.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/config.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/runner.py", "src/assay/cli.py", "src/assay/adapters/base.py", "src/assay/adapters/python.py", "src/assay/adapters/go.py", "src/assay/schemas/**", "tests/**", "README.md", "docs/DESIGN-GUIDE.md", "assay.toml"]
  forbid: ["pyproject.toml", "nyxloom-trove/carve-assets/P21/README.md", "nyxloom-trove/carve-assets/P21/skeleton.patch", "nyxloom-trove/carve-assets/P21/test_acceptance.py", "nyxloom-trove/carve-assets/P21/python-site-manifest.json", "nyxloom-trove/carve-assets/P21/invalid-cases.json", "nyxloom-trove/carve-assets/P21/expected/combined-pass-v4.json", "nyxloom-trove/carve-assets/P21/expected/r1-unavailable-v4.json", "nyxloom-trove/carve-assets/P21/expected/r2-limit-v4.json", "nyxloom-trove/carve-assets/P21/expected/r2-unsupported-v4.json"]
oracles:
  - id: O1
    observable: "Model construction, shipped JSON Schema, and independent raw-document verification accept the same closed v4 vocabulary and reject every v1-v3 artifact with one version-only diagnostic"
    negative: "An unknown mutant operator passes assay verify, or a v3 artifact is coerced/defaulted into v4"
    gate: tester-unified
  - id: O2
    observable: "Every attempted mutant, including killed mutants, carries a stable byte-site identity and is bound to the declared operator policy; Python discovery retains at most max_mutants+1 small site descriptors; Go capability absence remains distinct from valid empty discovery; neither terminal submits a mutant command"
    negative: "Changing both a killed operator and its policy to an unknown name remains verify-clean, full mutated files are retained per candidate, max_mutants+1 submissions run as a silently truncated sample, or Go UNSUPPORTED is rewritten as NO_MUTANTS with a fake empty payload"
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

The claim to attack: **every fact needed to reproduce Assay's judgment is
present and independently checkable in one v4 artifact, while enumerated
mutation evidence has an explicit, truthful cardinality bound.**

## Dispatch contract

- Contract class: **2b — complex solution-bearing execution** (`implement-4`
  when deployed; frontmatter names the live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Opus xhigh implementer → a fresh
  Opus xhigh independent reviewer session**. Do not let one Opus context author
  and adjudicate the migration.
- Readiness: **READY at `618b6f15451ec5f45b5900dc496d794241180467`.**
  The complete canonical documents, two invalid inputs per changed public
  shape, Python site manifest, compiling output skeleton, and locked three-
  layer acceptance are under `nyxloom-trove/carve-assets/P21/`. The exact
  AUTHORING adversarial review and witnessed controlled-red counts are in
  `nyxloom-trove/reports/assay-P21-JIT-CARVE.md`.
- Implementer freedom: private construction only. Names, types, requiredness,
  operator/reason vocabularies, migration behavior, and cross-field invariants
  are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P21-verdict-v4-evidence-contract`
on branch `feat/assay-P21-verdict-v4-evidence-contract`.

## Context to read first

1. `nyxloom-trove/reports/assay-P21-JIT-CARVE.md` in full, then the locked
   packet `nyxloom-trove/carve-assets/P21/README.md`. Apply its skeleton and
   run its acceptance command before editing production code.
2. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings
   F08–F15, and `docs/DESIGN-GUIDE.md` §6 in full; decisions A-008,
   A-027–A-029, A-041, A-050, A-067, A-116–A-117, A-135–A-138, A-148,
   A-152, A-157–A-158, A-163, A-168, A-170–A-171, A-178, and A-180–A-183.
3. `src/assay/verdict.py`, `src/assay/schemas/verdict.schema.json`, and `src/assay/verify.py` side by side. List each cross-field invariant and prove which of the three layers owns it before editing.
4. `src/assay/mutation.py::{Mutant,collect_mutants,run_mutation,judge_mutation}`,
   `src/assay/adapters/{base,python,go}.py`, `src/assay/canary.py`,
   `src/assay/evaluate.py`, and their complete-artifact fixtures. The old
   full-text candidate seam is deliberately replaced here because it cannot
   enforce P21's bound. Go's edit is restricted to the forced import/method
   migration and its truthful pre-P29 `UNSUPPORTED` terminal (A-183).
   Preserve A-158: a normally-started nonzero mutant
   command is killed; crashed means the command boundary could not execute.
5. `src/assay/config.py`'s closed `MutationConfig` parsing and `JudgeConfig.as_declared`. No runtime consumer may invent a missing cap.
6. P16's migration/conformance tests and P19's model/raw-verifier
   correspondence tests. Extend both independent layers; `verify.py` may
   reconstruct the model as its final shape check, but every cross-field rule
   also has a differently worded raw-document check before reconstruction.

## Implementation packet (normative)

### Owned interfaces

`src/assay/vocabulary.py` is a new stdlib-only leaf. Its public value is the
ordered tuple below; every producer/model/config check imports it rather than
copying a set. The shipped schema remains hand-readable and a conformance test
requires exact set equality and this order in canonical output:

```python
MUTATION_OPERATORS: tuple[str, ...] = (
    "compare-swap", "boolop-swap", "bool-const-flip", "falsy-swap",
)
```

P21, not P23, replaces the unbounded full-file candidate seam. The provisional
queue put this in P23, but that would make P21's own cap false for two packages.
`mutation.py` owns these exact descriptors and error boundary; `base.py` owns
the adapter method; `python.py` is the first real implementation:

```python
@dataclass(frozen=True, kw_only=True)
class MutationSite:
    start_byte: int
    end_byte: int
    replacement: bytes
    lineno: int
    operator: str
    description: str

class MutationDiscoveryError(AssayError):
    # always ERROR / MUTATION_DISCOVERY_FAILED
    ...

def generate_mutation_sites(
    self, text: str, lines: set[int], *,
    operators: tuple[str, ...], limit: int,
) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]: ...

@dataclass(frozen=True, kw_only=True)
class MutantJob:
    path: str
    original_text: str       # shared reference, never a copy per site
    site: MutationSite

def collect_mutation_sites(
    targets: Iterable[MutationTarget], *, adapter: LanguageAdapter,
    operators: tuple[str, ...], limit: int,
) -> tuple[MutantJob, ...] | Literal["UNSUPPORTED"]: ...

def run_mutation(
    lane: Lane, *, baseline: CommandResult, project_root: Path,
    repo_top: Path, scratch_root: Path, targets: Iterable[MutationTarget],
    adapter: LanguageAdapter, jobs: int, max_mutants: int,
    operators: tuple[str, ...], process_runner: ProcessRunner | None = None,
    clock: Clock | None = None,
    executor_factory: ExecutorFactory = _default_executor_factory,
) -> Mutation | Literal["UNSUPPORTED"] | None: ...
```

Each site requires non-boolean integers with
`0 <= start_byte < end_byte <= len(text.encode("utf-8"))`; start/end are UTF-8
code-point boundaries; `replacement` is nonempty valid UTF-8; applying it must
change the named span and leave the result valid UTF-8; `lineno` equals
`line_for_offset(source_bytes, start_byte)`; `operator` belongs to both the
closed product vocabulary and the selected ordered policy; and `description`
is nonempty. Per-file order and uniqueness use
`(start_byte,end_byte,sha256(replacement).hexdigest(),operator)`; description
and line are diagnostics and cannot distinguish identities. Python retains the
smallest `limit` keys while walking—use a bounded selection/heap or equivalent,
not an append-all-then-slice list. Parsing/scanning the already 16-MiB-bounded
source is permitted; retaining more than `limit` descriptors or any full
mutated file is not. Invalid Python syntax raises `MutationDiscoveryError`;
valid syntax with zero sites returns `()`.

`"UNSUPPORTED"` remains a third, capability-wide discovery result. It means
the adapter has no mutation implementation at all; it is not a parse failure,
an unsupported individual construct, or a valid empty analysis. Until P29,
`GoAdapter.generate_mutation_sites` returns it unconditionally for every
text/line/operator/limit input. Python never returns it: invalid Python is the
typed discovery error above. This supersedes A-114's old invalid-Python use
while retaining its whole-adapter-call discipline.

`collect_mutation_sites` validates `limit in 1..10_001`, an ordered unique
nonempty operator subset, every adapter result, and the fixed order. It visits
targets by path, passes only remaining capacity, stops calling later files once
the sentinel is full, and stores one reference to each target's original text.
On a first adapter `"UNSUPPORTED"`, it returns that marker immediately with no
jobs. An adapter that first claims supported discovery (even an empty tuple)
and later returns `"UNSUPPORTED"` is inconsistent and raises
`MutationDiscoveryError`; no partial jobs survive. With no targets, collection
returns the supported empty tuple because no language analysis was required.
One replacement file is materialized only inside a submitted worker from
`original_bytes[:start] + replacement + original_bytes[end:]`. The old
`Mutant`, `generate_mutants`, `collect_mutants`, and full-text identity are
deleted, not retained as compatibility surfaces.

The deletion necessarily touches `go.py`: replace its `Mutant` import and old
`generate_mutants` signature with the common `MutationSite` import and exact
`generate_mutation_sites` union above; keep the body as unconditional
`return "UNSUPPORTED"`. Update only mutation-contract prose/tests around that
forced seam—do not add Go syntax discovery, a helper, tool declaration, or R2
registration. P29 replaces this marker with real bounded Go sites; P30 makes
that capability reachable through the registry.

`run_mutation` propagates `"UNSUPPORTED"` before project-prefix calculation,
executor construction, scratch creation, or submission. `judge_mutation` maps
it to `INCONCLUSIVE/MUTATION_UNSUPPORTED`; `build_mutation_claim` emits no
`mutation` payload for that marker. Valid supported zero remains
`INCONCLUSIVE/NO_MUTANTS` with the exact zero/zero `Mutation` payload. Thus v4
does not let capability absence impersonate an observed empty candidate set.

The locked `python-site-manifest.json` is the before/after candidate oracle. In
particular, deriving a “minimal diff” from old/full mutated strings is
forbidden: `<` → `<=` collapses to a zero-width insertion and `True` → `False`
collapses to `Tru` → `Fals`, neither of which is the syntax site's identity.

`src/assay/output.py` is created by the locked compiling skeleton and owns:

```python
def reserve_verdict_output(target: str, *, stdout: TextIO) -> VerdictOutput: ...
class VerdictOutput:
    @property
    def target(self) -> str: ...
    def emit(self, text: str) -> None: ...
    def close(self) -> None: ...
```

For `target == "-"`, reservation checks the supplied stream is writable with a
zero-length write and never closes it. A file target is interpreted exactly in
the CLI process namespace: relative to its captured current working directory,
or from filesystem root if absolute—never relative to `project_root`. Normalize
`.`/`..` lexically once, then descriptor-walk the absolute parent without
following symlinks. The destination must be absent or the same ordinary
non-symlink regular file through emission. Reservation holds the parent and
observed destination identity, proves write access by creating and removing an
exclusive same-parent probe, and leaves no temp file across lane execution.
Emission revalidates the destination, creates a new exclusive sibling temp,
writes all UTF-8 bytes, and `os.replace`s relative to the held parent. Any
expected open/write/flush/replace/race failure is
`AssayError(ERROR, OUTPUT_WRITE_FAILED)`; it cleans only its own temp and never
overwrites an object that appeared or changed. State is
`RESERVED -> EMITTED|CLOSED`; emit twice is `RuntimeError`; close is idempotent.

`cli._cmd_run` loads the lane, then reserves any explicit output before HEAD,
adapter, or lane command work. `None` means A-028's deliberate no-artifact mode
and creates no reservation. It runs the pipeline, calls
`runner.write_verdict(verdict, destination)` exactly once, and prints no success
summary if emission fails. Preflight or late loss prints one stable
`ERROR/OUTPUT_WRITE_FAILED` diagnostic to stderr and exits 2. No fallback file
or stdout artifact is invented. Lane-config failure precedes output preflight;
after a lane loads, output refusal precedes repository/consumer work.

### v4 grammar and owners

The following excerpt highlights the new serialized keys. It is not the
canonical document: the four complete documents under
`carve-assets/P21/expected/` are normative and contain every unchanged required
v3 field as well.

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

`Mutation.candidate_count` is the bounded number of descriptors observed. It
is not an unbounded total disguised as a count: once the sentinel is full,
Assay knows only that at least that many exist and intentionally stops.
Normally it equals `total`, and `total` equals the lengths of all four identity arrays. On
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
replacement bytes only. It is constructed directly from the validated
`MutationSite`; never derive it by diffing two full texts. All four arrays are
individually identity-sorted, no identity may occur twice within or across
buckets, and every identity path is normalized repo-top-relative. This gives
P29 its final wire identity without forcing v5 and prevents two
same-line/same-description mutants from collapsing.

`Coverage.exclusion_capability` is exactly `"reported"` or `"unavailable"`.
`unavailable` requires both exclusion detail fields empty; `reported` permits
empty or populated detail. Derive it from the parsed profile before evaluation:
all measured file records must agree on `excluded is None`; mixed capability is
`ERROR/UNREADABLE_ARTIFACT`, never majority/default. `CanaryResult.target` is
the normalized declared project-relative string and must equal
`judgment.r3.target`. Project-relative and repo-relative wire paths use
forward-slash components, with no empty, `.`, `..`, leading slash, trailing
slash, backslash, or NUL component. Schema enforces local grammar; model and raw
verifier enforce cross-field rules. The exact sixteen invalid complete inputs and
their applicable layers are `carve-assets/P21/invalid-cases.json`. V1–v3 fail
before required-field or foreign-shape inspection with exactly one version-only
diagnostic.

Add exactly these closed reasons in `errors.py`, Schema, model, and verifier:
`ERROR/OUTPUT_WRITE_FAILED`,
`ERROR/MUTATION_DISCOVERY_FAILED`,
`BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`, and
`BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` (reserved here for P22's reachable
snapshot refusal), plus `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL` (reserved here
for P27's first real external-tool preflight), and
`NO_MEASUREMENT/HEAD_CHANGED`, plus
`INCONCLUSIVE/MUTATION_UNSUPPORTED`. The last one is payload-free and belongs
only to the adapter-wide `"UNSUPPORTED"` discovery marker; `NO_MUTANTS`
requires a present supported zero/zero mutation payload. This supersedes
A-011's collapsed reason spelling while retaining its INCONCLUSIVE outcome and
never-green rule. After the command, call `dirty_paths` once. If
it is nonempty, use `DIRTY_TREE` and do not resolve post-HEAD. Only a clean
result calls `head_rev` once and may use `HEAD_CHANGED`; this fixes precedence
without duplicate Git observations. No generic fallback reason is permitted.
P21 makes `MUTATION_DISCOVERY_FAILED` reachable for invalid Python source; P29
uses it for invalid helper request/response, helper nonzero, or another failed
syntax-aware candidate boundary. Pre-P29 Go capability absence is
`INCONCLUSIVE/MUTATION_UNSUPPORTED`; valid discovery with zero sites remains
`INCONCLUSIVE/NO_MUTANTS` with a present zero payload.

`Mutation` construction permits exactly two arithmetic shapes: normal
`candidate_count == total == sum(bucket lengths)`; or a prospective sentinel
with `candidate_count in 1..10_001`, `total == 0`, and four empty arrays.
`Claim`/`Verdict` then make the latter legal only as
`BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` with
`candidate_count == judgment.r2.max_mutants + 1`. Conversely that reason
requires that exact sentinel. A zero/zero normal payload is
`INCONCLUSIVE/NO_MUTANTS`. `JudgmentR2.max_mutants` is required in `1..10_000`;
normal totals cannot exceed it. Payload-free
`INCONCLUSIVE/MUTATION_UNSUPPORTED` is the only legal adapter-capability
absence and is mutually exclusive with every `Mutation` shape; payload-free
`NO_MUTANTS` and payload-bearing `MUTATION_UNSUPPORTED` are invalid in model,
Schema, and raw verification. The model and raw verifier perform the
cross-object relations independently; JSON Schema performs every locally
expressible enum, range, requiredness, and reason/payload conditional but is
not misrepresented as supporting cross-object arithmetic or timestamp
comparison.

Timestamp order compares parsed offset-aware instants, not strings and not
wall-clock duration. The canonical combined document deliberately starts at
`12:00+01:00` and ends at `11:01+00:00`: lexical order points the wrong way,
while UTC order is valid. A malformed calendar value or a parsed end instant
before start is rejected by the model and independently by the raw verifier.

### Required flow and decision table

1. Load and validate the complete lane, including bounded `max_mutants`.
2. If an output was requested, reserve it before HEAD/adapter/consumer work;
   reservation never redirects or invents a path and leaves no persistent
   probe temp across execution.
3. Resolve HEAD/policy, execute, and construct only v4 producer models. Emit
   once through the reservation's fresh sibling temporary and atomic replace.
4. `verify.py` first checks the raw top-level version, then performs its own
   raw shape and cross-field derivation before producer-model reconstruction.
   The separate test-only Draft 2020-12 suite validates the shipped schema;
   runtime verification stays zero-dependency and never imports jsonschema.

| State | Outcome/reason | Payload/side effect |
|---|---|---|
| bad output parent/type/permission before run | `ERROR/OUTPUT_WRITE_FAILED` | no lane command; stable stderr because requested artifact cannot exist |
| destination lost after run | process `ERROR/OUTPUT_WRITE_FAILED` | lane result is not reported as process PASS; no requested artifact and no fallback file |
| mutation discovery/helper protocol fails | `ERROR/MUTATION_DISCOVERY_FAILED` | complete payload-free R2 claim; zero mutant submissions |
| adapter has no mutation implementation | `INCONCLUSIVE/MUTATION_UNSUPPORTED` | complete payload-free R2 claim; zero executor/scratch/submissions; Go only until P29 |
| supported discovery finds zero selected sites | `INCONCLUSIVE/NO_MUTANTS` | exact zero/zero mutation payload; zero submissions |
| candidates = max+1 | `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` | sentinel mutation payload; zero submissions |
| snapshot exceeds P22 bound | `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` | complete artifact; zero command for that snapshot |
| command leaves dirt, whether or not HEAD moved | `NO_MEASUREMENT/DIRTY_TREE` | actual R0 preserved for higher rigor; no higher-rigor work |
| command moves HEAD and leaves clean tree | `NO_MEASUREMENT/HEAD_CHANGED` | actual R0 preserved for higher rigor; no higher-rigor work |
| old schema | verify failure | exactly one version diagnostic |

### Traceability and degrees of freedom

Work 1–4 -> vocabulary/model/schema/verifier/config/site seam -> O1/O2 ->
complete v4 documents, agreeing-unknown operator, UTF-8 site manifest,
distinct Go-unsupported/valid-empty terminals, and max+1 zero-executor
sentinel; work 5–7 -> canary/coverage/time correspondence
-> O3/O4 -> exact field mutations; work 8 -> output reservation -> O4 -> a
real CLI marker proving zero consumer calls; work 10 -> O5 -> real clean-commit
and commit-plus-dirt repositories. The REPORT repeats this mapping with actual
ordinary tests and A-067 controlled-break counts. Private helper names, heap
choice, temp nonce source, and equivalent decomposition may vary; serialized
keys, public signatures, path grammars, ranges, state/side-effect order,
sentinel shape, reasons, and independent raw re-derivation may not.

## Work

1. Bump the verdict artifact to schema v4 in one atomic migration. Convert every hand-written expected artifact and installed-wheel witness deliberately. `assay verify` must return one version-only diagnostic for v1–v3 before reading foreign fields; it never upgrades, defaults, or rewrites them.
2. Put the mutation-operator vocabulary in one cycle-safe module imported by config, mutation construction, verdict model, and raw verifier. Close both `MutantOutcome.operator` and `judgment.r2.operators` in the model and schema. Delete the current model/schema/verifier mismatch rather than maintaining parallel literal sets.
3. Replace killed's count-only representation with ordered `MutantOutcome`
   identities, matching survived/crashed/budget-exceeded, and add the packet's
   `candidate_count`. Identity includes the packet's UTF-8 byte span and
   replacement hash; construct it from the packet's validated `MutationSite`
   and delete the old full-text candidate surface. Verify normal and limit-
   sentinel arithmetic exactly and require every payload operator to belong to
   the recorded policy. Sorting uses the identity tuple, never description or
   completion order.
4. Make `judge.mutation.max_mutants` a required integer in `1..10,000`, record it in `judgment.r2`, implement the exact bounded `MutationSite`/Python adapter seam, and enforce it before any mutant command is submitted. Retain at most `max_mutants + 1` descriptors and never more than 10,001; excess renders the packet's `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` sentinel, with no partial sample and no credit. Invalid Python syntax renders the payload-free discovery terminal; valid zero sites remain `NO_MUTANTS`. `jobs` remains only a concurrency bound and is never derived from machine capacity.
   Migrate Go's forced import/method surface in the same atomic deletion but
   leave it unconditionally `"UNSUPPORTED"`; propagate that marker to the
   payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED` claim. It must never become
   a fake supported-empty payload, discovery error, executor call, helper, or
   R2 registry capability.
5. Add the project-relative canary target to `CanaryResult` and bind it exactly to `judgment.r3.target` in both construction and raw verification. The description remains explanation, never a parseable identity channel.
6. Preserve A-008 in the artifact with a closed R1 exclusion-capability field (`reported` versus `unavailable`). `unavailable` may not carry excluded lines; `reported` may truthfully carry an empty mapping. Re-derive the same rule in `verify.py`; do not infer capability from a particular format name.
7. Add construction/schema/raw-verifier checks that `ended >= started`. Use injected/fixed clocks in tests and exact timestamp values; no elapsed-time assertion.
8. Apply the locked `output.py` skeleton and close A-O14 with `ERROR/OUTPUT_WRITE_FAILED`. Wire its exact reservation before HEAD/adapter/command work; a bad/missing/unwritable parent must not allow the lane to run. Do not redirect to an invented fallback path. If a destination becomes unusable after reservation, preserve the changed object, emit the stable error to stderr, clean only internal temporary state, and never claim the requested file was written.
9. Hand-author valid and adversarial v4 artifacts for all levels. Break killed identity, operator vocabulary, max-mutant enforcement, canary target, exclusion capability, interval ordering, version handling, and output preflight independently; record exact A-067 failure counts.
10. Replace P20's schema-v3-compatible collapse of a clean post-command HEAD
    move into `DIRTY_TREE` with `NO_MEASUREMENT/HEAD_CHANGED`. Resolve HEAD once
    before execution; after execution check dirt once, then resolve HEAD once
    only on the clean branch. Prove the
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

This package is the one pre-adoption v4 migration and the bounded common/Python
site seam required to make its cap true immediately. Its only Go change is the
forced old-to-new mutation import/signature migration and unchanged truthful
`UNSUPPORTED` behavior. It must not add Go syntax discovery, an external
helper/tool declaration, Go R2 registration, TypeScript behavior, distribution
changes, or isolation redesign. Python's candidate set must match the locked
manifest. P22 consumes the snapshot terminal; P23 consumes the already-landed
site/cap interface while making repeated R0/R2/R3 execution faithful.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
