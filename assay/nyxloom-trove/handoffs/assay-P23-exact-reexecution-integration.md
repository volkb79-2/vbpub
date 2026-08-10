---
schema_version: 1
id: assay-P23-exact-reexecution-integration
project: assay
title: "Every higher-rigor execution reuses one plan, committed seed, and lane deadline"
tier: implement-2
input_revision: "9d30b25b96b8ffd8f952c02e8958b923bb8e1d13"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P22-committed-object-snapshot-substrate]
session: fresh
scope:
  touch: ["src/assay/config.py", "src/assay/runner.py", "src/assay/mutation.py", "src/assay/canary.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md", "nyxloom-trove/reports/assay-P23-exact-reexecution-integration-LOG.md"]
  forbid: ["src/assay/isolation.py", "src/assay/git.py", "src/assay/errors.py", "src/assay/schemas", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/attestation.py", "src/assay/adapters", "nyxloom-trove/carve-assets/P22", "nyxloom-trove/carve-assets/P23"]
oracles:
  - id: O1
    observable: "One immutable CommandPlan, including appended argv and captured passthrough values, is used byte-for-byte for snapshot baseline, every R2 mutant, and both R3 halves; only cwd and the positive remaining timeout vary"
    negative: "An appended selector or captured environment value appears in verdict metadata but is absent or different in a repeated subprocess"
    gate: tester-unified
  - id: O2
    observable: "Every higher-rigor unit executes from an independent P22 committed snapshot at the original project prefix, can read tracked repo siblings, starts without ignored/stale output, and leaves the consumer repository byte/status identical"
    negative: "A nested mutant cannot read ../../shared/input, a transform consumes baseline coverage, or a linked/copied consumer file changes"
    gate: tester-unified
  - id: O3
    observable: "Rigor is an R0-led ordered subsequence, uncovered-line R3 requires R1, and invalid declarations are refused at load before Git, snapshot, output, or process side effects"
    negative: "rigor=[R2], R0/R3/R2, or R0+uncovered-line-R3 reaches verdict construction"
    gate: tester-unified
  - id: O4
    observable: "One injected monotonic deadline covers preparation, materialization, reads, discovery, every process, evaluation, and cleanup; max_mutants+1 is observed before executor construction or unit launch"
    negative: "Each unit receives the full lane budget, a unit starts after expiry, or excess candidates are sampled"
    gate: tester-unified
  - id: O5
    observable: "P22 refusal and cleanup terminals survive unchanged, every child context and executor closes before the prepared seed, and the reachable snapshot-limit v4 pair is closed in ordinary conformance"
    negative: "Cleanup RuntimeError masks a real GIT_FAILED/LANE_TIMEOUT, a worker outlives the seed, or the conformance audit still calls SNAPSHOT_LIMIT_EXCEEDED unreachable"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a required behavior needs any forbidden P20/P21/P22/schema/verifier/adapter file"
  - "the landed P22 context cannot provide an independent unit without source-object or inode sharing"
  - "the existing v4 vocabulary cannot truthfully represent a reachable terminal without an owner change"
mutexes: []
---

# P23 — exact reexecution integration

The claim to attack: **all variants of a higher-rigor lane judge one exact
declared command and one exact committed repository state under one honest
lane-wide budget.**

## Dispatch contract

- Contract class: **2c — bounded cross-module integration**.
- Required roles: **Sonnet xhigh implementer → fresh Opus xhigh reviewer**.
- Readiness: **READY only at input revision `9d30b25b…` with the byte-locked
  packet under `nyxloom-trove/carve-assets/P23/` and decisions A-188–A-196.**
- Implementer freedom: private helper decomposition and names not frozen below.
  Public/internal call shapes named here, ordering, namespaces, deadline,
  terminal mapping, snapshot count, and proof observables are fixed.
- Apply `nyxloom-trove/carve-assets/P23/skeleton.patch` before production work.
  It supplies the mechanical data/API grammar and load-time rigor checks. Do
  not rewrite those decisions. The locked suite is red because orchestration is
  deliberately unfinished, not because the packet expects invention.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P23-exact-reexecution-integration`
on branch `feat/assay-P23-exact-reexecution-integration` from the exact input
revision.

## Context to read first

Read these exact surfaces, in order; do not orient over the whole repository:

1. `nyxloom/reference/AUTHORING.md`, `STANDARD.md`, and `DOCTRINE.md`, then
   `assay/nyxloom-trove/{nyxloom.toml,STATE.md,decisions.md}` decisions
   A-160/A-163/A-173–A-196.
2. P22's landed `assay/src/assay/isolation.py` public surface and
   `assay/tests/test_isolation.py` lifecycle/refusal tests. Consume
   `SnapshotSpec`, `DEFAULT_SNAPSHOT_LIMITS`, `prepare_snapshot`,
   `SnapshotRepository.read_regular_file`, `materialize`, and
   `materialize_replacement`; never duplicate their mechanics.
3. P20's final `git.py` and output/coverage reservation contracts, plus
   `runner.py::{CommandPlan,CommandResult,resolve_command_plan,execute_command,
   refuse_lane,run_lane}`. P20 remains the sole Git/process-safety owner.
4. P21's `mutation.py::{MutationSite,collect_mutation_sites,run_mutation,
   build_mutation_claim}` and v4 terminal tests. Site discovery already stops
   at the requested remaining capacity and validates byte spans/hashes/order.
5. `canary.py::{run_python_canary,run_isolated_canary,build_canary_claim}` and
   the R3 direct/integration tests. Preserve cause-sensitive judgment; replace
   only its live-copy/re-resolve orchestration.
6. This packet's README, fixture manifest, process ledger, expected artifacts,
   skeleton, locked acceptance, and tracer. Expected values are carver-owned.

## Product boundary: direct R0 versus committed reexecution

This is a declared two-state policy, never a fallback:

| lane declaration | execution state machine |
|---|---|
| exactly `("R0",)` | retain P20's current direct clean-tree execution; do not call P22 |
| contains any of R1/R2/R3 | use the P23 committed-snapshot state machine below; any P22 refusal is final |

Assay's registered `tester-unified` lane is intentionally R0-only. The vbpub
commit contains three deliberate absolute symlinks under a Topos security
fixture, so P22 truthfully refuses a whole-monorepo snapshot. This exception is
limited to direct R0 self-hosting: no higher-rigor Assay lane, fixture, or
consumer may catch P22's refusal and run the live tree instead. P25 supplies
external Python-project qualification; self-hosting alone is not product
qualification.

## Mechanical API contract

### One immutable command plan

Extend the existing `CommandPlan`, not a parallel plan type:

```python
@dataclass(frozen=True, kw_only=True)
class CommandPlan:
    argv_declared: tuple[str, ...]
    argv_appended: tuple[str, ...]
    argv_effective: tuple[str, ...]
    env_declared: Mapping[str, str]
    env_passthrough: tuple[str, ...]
    env_effective: Mapping[str, str]
    allow_argv_append: bool
    budget_seconds: float
    project_prefix: PurePosixPath | None
```

`resolve_command_plan(lane, *, argv_append, passthrough_source,
project_prefix=None)` remains pure
and is called exactly once by a higher-rigor `run_lane`, before scratch,
snapshot, coverage reservation, or process activity. It copies the immutable
lane facts above and captures each declared passthrough name from the supplied
mapping once. Missing names remain absent. No repeated unit receives `lane`,
`argv_append`, or an ambient environment as a source of command truth.
`None` is legal only for a refusal/direct helper invoked before repository
identity exists; higher-rigor `run_lane` must supply its exact non-`None`
repo-relative prefix, and every process-ledger test asserts it. This is absence,
not an invented `.` default.

Add the exact execution seam:

```python
def execute_plan(
    plan: CommandPlan,
    *,
    cwd: Path,
    timeout: float,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CommandResult: ...
```

It validates a positive finite `timeout`, enforces `plan.allow_argv_append`,
and invokes only `plan.argv_effective`/`plan.env_effective`. Its result carries
that identical `plan` object. `execute_command(..., project_prefix=None)`
remains the direct-R0/helper wrapper: resolve once (passing that prefix), then
call `execute_plan(..., timeout=plan.budget_seconds)`. Direct `run_lane` already
has repo/project identity and must pass its exact prefix; only a pre-repository
refusal or standalone helper may honestly retain `None`. P23's higher-rigor
path calls `execute_plan` directly everywhere. There is no optional plan
parameter whose absence silently re-resolves.

### One injected lane deadline

Add these exact types in `runner.py` so mutation and canary share one owner:

```python
MonotonicClock = Callable[[], float]

@dataclass(frozen=True, kw_only=True)
class LaneDeadline:
    expires_at: float
    monotonic: MonotonicClock

    @classmethod
    def start(cls, *, budget_seconds: float, monotonic: MonotonicClock) -> "LaneDeadline": ...

    def remaining(self) -> float: ...
```

`start` samples `monotonic` exactly once, rejects booleans/non-finite/nonpositive
budget or clock values, and records `expires_at = start + budget_seconds`.
`remaining` samples once; a positive finite difference is returned verbatim.
Zero/negative/non-finite time raises `AssayError(BUDGET_EXCEEDED,
LANE_TIMEOUT)`. The wall/UTC `clock` remains only for artifact timestamps. No
sleep or elapsed-wall-time test is allowed.

Extend `run_lane(..., monotonic: MonotonicClock = time.monotonic)`. The default
is chosen at the boundary and passed into `LaneDeadline.start`; lower layers
receive the concrete `LaneDeadline`, never choose a clock. Immediately before
every P22 entry/read/materialization and process launch, call `remaining()`
once and pass that exact value. Do not subtract estimates or reset after a
unit.

Make scratch ownership equally injectable and singular:

```python
ScratchRootFactory = Callable[[], ContextManager[Path]]

@contextmanager
def default_scratch_root() -> Iterator[Path]: ...
```

The default wraps one `TemporaryDirectory(prefix="assay-p23-")` and yields its
absolute resolved `Path`. Extend `run_lane(...,
scratch_root_factory=default_scratch_root)`. Higher rigor calls the factory once
and hands that root to `SnapshotSpec`; lower layers never create another root or
read `TMPDIR`. The seam exists so locked tests can make context entry or exit
raise without permissions, races, or monkeypatching `tempfile` internals.

### Namespace map

| value | exact namespace | only conversion |
|---|---|---|
| `repo` / `repo_top` | resolved consumer repository | `repo_top = git.repo_top(repo)` once |
| `project_root` | resolved consumer project directory | must be contained by `repo_top` |
| `project_prefix` | normalized `PurePosixPath`, repo-top-relative | `PurePosixPath(project_root.relative_to(repo_top).as_posix())`; `.` for repo root |
| diff/mutation/canary target | repo-top-relative POSIX | canary's normalized project target is prefixed once |
| coverage artifact | project-relative declared POSIX | resolved only under the current snapshot project root |
| process cwd | concrete snapshot project root | `snapshot.root / project_prefix` supplied by P22 as `snapshot.project_root` |

Never call local `Path.resolve()` to validate a path belonging to another
namespace. Never strip or prefix `project_prefix` twice. Snapshot content is
handled per tree entry/path, never by an OID-to-one-path cache: identical blobs
or tree OIDs may occur at several paths and modes.

## Fixed higher-rigor state machine

### 0. Load-time declaration checks

At `_load_lane`, require `rigor` to equal an ordered subsequence of
`("R0","R1","R2","R3")` and begin with `R0`. Thus `R0`, `R0+R2`, and
`R0+R1+R3` are valid; `R2`, `R1+R0`, and `R0+R3+R2` are not. If canary
mechanism is `uncovered-line`, R1 must be declared. `import-break` needs only
R0. Refuse with `LaneConfigError` before returning the lane; tests install
Git/snapshot/process sentinels to prove no later boundary ran.

### 1. Freeze source identity, plan, deadline, and seed

For a higher-rigor lane, in this order:

1. Resolve the one `CommandPlan`.
2. Read `repo_top`, validate containment/project prefix, read full HEAD once,
   and compare it with the caller's full `commit`. A mismatch is
   `NO_MEASUREMENT/HEAD_CHANGED`; dirt is
   `NO_MEASUREMENT/DIRTY_TREE`. Neither starts a process or P22.
3. Create one `LaneDeadline` and one caller-owned `TemporaryDirectory` outside
   `repo_top`; construct one `SnapshotSpec` with the resolved full commit,
   prefix, scratch root, and `DEFAULT_SNAPSHOT_LIMITS`.
4. Enter `prepare_snapshot(spec, timeout=deadline.remaining())` exactly once.
   Keep it open until baseline/R1/R2/R3 and every executor have completed.

Any preparation `AssayError` produces a complete payload-free claim with its
unchanged pair for every declared rigor. In particular snapshot policy limits
remain `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED`, not `LANE_TIMEOUT` or
`GIT_FAILED`. Use a helper that accepts the already-resolved plan when
constructing this refusal; do not call `refuse_lane` in a way that resolves the
plan again.

### 2. Execute one snapshot unit

Use one shared unit helper for baseline and both canary halves. Given a
`Snapshot` plus its expected commit, it:

1. relocates only `judge.source_root_paths` from consumer project root to
   `snapshot.project_root`; all declared strings remain unchanged;
2. for R1-bearing units, creates a new P20 `OutputReservation` under that
   snapshot project root, checks the declared artifact is not tracked, then
   arms it immediately before the command;
3. calls `execute_plan(plan, cwd=snapshot.project_root,
   timeout=deadline.remaining(), ...)`;
4. observes `git.dirty_paths(snapshot.root)` once, and only if clean observes
   `git.head_rev(snapshot.root)` once; expected-child HEAD is unit-specific;
5. if clean/unchanged, consumes that unit's own reservation once and parses its
   own coverage bytes; and
6. closes the reservation and snapshot on every path.

A process-created tracked/support modification is `DIRTY_TREE`; a process
commit is `HEAD_CHANGED`. The consumer stays untouched, but disposability does
not launder a unit that no longer represents its named commit. Preserve P20's
claim precedence: with higher rigor, the real R0 command claim remains and
later claims are payload-free `NO_MEASUREMENT`; an R0-only lane retains P20's
existing behavior.

Each unit starts artifact-absent even when the consumer has a stale ignored
artifact. No profile object or artifact bytes cross unit boundaries.

### 3. Baseline and R1/R2 target discovery

Enter `prepared.materialize(timeout=deadline.remaining())` once for baseline.
Run the shared unit helper. If baseline R0 is not PASS, start no R2/R3 unit.
R2 retains its existing baseline-propagation claim. R3 constructs the existing
complete `CanaryResult` with the baseline outcome as its failed control and no
transformed outcome; it therefore renders
`INCONCLUSIVE/CANARY_INCONCLUSIVE` with its payload and `judgment.r3`, never an
invalid payload-free judged claim. For uncovered-line, apply the same early
inconclusive shape when baseline R0 passed but baseline R1 did not PASS: a
coverage canary has no known-good control. Import-break depends only on R0.
If R1 is declared, evaluate its freshly parsed profile inside this snapshot,
using the relocated lane and original `judge.base`; capture its already-resolved
base/diff through the existing callbacks.

If R2 needs a diff and R1 did not produce one, resolve it once against the
baseline snapshot. For each target path, read source only through
`prepared.read_regular_file(PurePosixPath(path),
timeout=deadline.remaining())`, apply the existing 8 MiB source ceiling and
strict UTF-8 decode, then give text to the landed P21 resolver. Never reopen a
consumer path. Shared blob OIDs do not merge target paths.

### 4. R2 fresh replacement units

Change `run_mutation`'s execution inputs to the already-frozen values:

```python
run_mutation(
    *, baseline: CommandResult, prepared: SnapshotRepository,
    plan: CommandPlan, deadline: LaneDeadline,
    targets: Iterable[MutationTarget], adapter: LanguageAdapter,
    jobs: int, max_mutants: int, operators: tuple[str, ...],
    process_runner: ProcessRunner, clock: Clock,
    executor_factory: ExecutorFactory = _default_executor_factory,
) -> Mutation | Literal["UNSUPPORTED"] | None
```

The P21 collection call remains `limit=max_mutants + 1`. If the result contains
that sentinel, return the exact limit `Mutation(candidate_count=max+1,total=0)`
before project arithmetic, executor construction, futures, snapshot contexts,
or processes. At max-1 and max, execute all sites.

Each worker builds exactly one full replacement blob from its job's immutable
original bytes and site splice, then enters:

```python
prepared.materialize_replacement(
    path=PurePosixPath(job.path),
    expected=job.original_text.encode("utf-8"),
    replacement=replacement,
    timeout=deadline.remaining(),
)
```

Run the frozen plan at the returned project root with a fresh remaining
timeout. Do not hardlink, copy the live project, or mutate the prepared seed.
Every future is joined and every child context exits before the executor exits;
the executor exits before `prepare_snapshot` exits. If a P22/cleanup
`AssayError` escapes a worker, join/close siblings and propagate that original
pair as a payload-free R2 terminal. P22's normal-exit live-child `RuntimeError`
must not mask an earlier real error.

When the deadline expires before an unstarted mutant, catch only that exact
`BUDGET_EXCEEDED/LANE_TIMEOUT` from `deadline.remaining()`, launch no snapshot
or process for it, and put its identity in `budget_exceeded`; completed identities
remain evidence. The R2 claim is `BUDGET_EXCEEDED/LANE_TIMEOUT`; never sample a
partial set into PASS/FAIL. Result buckets remain identity-ordered independent
of completion order. Expiry or failure before discovery has produced a complete
candidate set is payload-free; do not invent identities. A mutant's post-run
`DIRTY_TREE`/`HEAD_CHANGED` is propagated as a payload-free R2 terminal rather
than collapsed into P21's `crashed/EXEC_FAILED` bucket.

### 5. R3 independent control and transform

Delete the `copytree`/`git commit` orchestration from the installed R3 path.
Normalize `judge.canary.target` at config load as today and convert it exactly
once:

```python
canary_repo_path = project_prefix / PurePosixPath(canary.target)
```

Reject a test path before materialization. Read the original bytes once through
`prepared.read_regular_file`, enforce the same source ceiling/UTF-8 rule, and
apply the adapter's pure transform once. A malformed/no-op transform keeps the
existing `CANARY_INCONCLUSIVE` result without transform execution.

For a real transform:

1. materialize a fresh base control, run the frozen plan, and close it;
2. build the complete transformed bytes;
3. materialize one P22 replacement at the repo-relative target using exact
   original/replacement bytes and run the same frozen plan; and
4. close it before returning.

For `uncovered-line`, control R1 compares `judge.base..seed commit`, while the
transform R1 compares `seed commit..deterministic child commit`; each half uses
its own freshly reserved/parsed coverage. For `import-break`, judge the exact
R0 cause. Any deadline/P22 failure before a complete `CanaryResult` becomes a
payload-free R3 claim with the unchanged terminal; it is never mislabeled
`CANARY_SURVIVED` or `CANARY_INCONCLUSIVE`.

### 6. Budget, scratch, and cleanup accounting

Preparation transfers the source closure once. Each attempted execution unit
materializes its own object pack; hardlinks are forbidden. Define:

```text
U = 1 baseline + attempted R2 sites + (2 when a real R3 transform runs else 0)
```

The P21 sentinel performs zero unit attempts. Including preparation, total pack
write I/O is bounded by `(U + 1) *
DEFAULT_SNAPSHOT_LIMITS.max_pack_bytes`; peak pack space is bounded by
`(1 + max(1, mutation.jobs)) * max_pack_bytes` because the prepared seed stays
live, canary halves are sequential, and every mutant context closes in its
worker. A conservative materialized-tree ceiling is
`max_entries * max_blob_bytes` per live child; P22 also separately bounds
unique objects and total path bytes. Record these formulas in the design guide.
Do not perform a racy free-space preflight or invent a scratch fallback; an
actual scratch I/O failure keeps P22's `ERROR/GIT_FAILED`.

Cleanup is bounded by the same deadline where P22 accepts a timeout. Context
managers still close after expiry. Never abandon a process/future/context to
make the outer deadline return early. Failure to create the caller scratch root
is payload-free `ERROR/GIT_FAILED` for all claims. If outer-root cleanup alone
fails after otherwise normal unit results, replace the highest declared
higher-rigor claim with payload-free `ERROR/GIT_FAILED` and remove its matching
judgment; lower completed claims remain. If cleanup runs while unwinding an
earlier raised `AssayError`, that original exception remains primary and cleanup
must not mask it; an ordinary judged FAIL is not such an unwind exception.

## Terminal and side-effect table

| event | exact result | payload / later work |
|---|---|---|
| invalid rigor order/prerequisite | `LaneConfigError` | no Git/scratch/output/process |
| source dirty before prepare | `NO_MEASUREMENT/DIRTY_TREE` all claims | payload-free; no P22/process |
| caller commit differs from resolved HEAD | `NO_MEASUREMENT/HEAD_CHANGED` all claims | payload-free; no P22/process |
| caller scratch root cannot be created | `ERROR/GIT_FAILED` all claims | payload-free; no P22/process |
| prepare policy limit | `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` all claims | payload-free; no baseline |
| prepare/materialize structural failure | unchanged P22 pair | payload-free affected/unstarted claims |
| baseline process non-PASS | existing R0 terminal; R2 propagates; R3 is `INCONCLUSIVE/CANARY_INCONCLUSIVE` | no R2/R3 unit; R3 carries failed-control `CanaryResult` and judgment |
| stale/missing baseline coverage | `NO_MEASUREMENT/EMPTY_COVERAGE` R1 | no stale profile; R2 may use its own diff if baseline PASS |
| uncovered-line baseline R1 non-PASS | `INCONCLUSIVE/CANARY_INCONCLUSIVE` R3 | failed-control payload; no separate control/transform |
| candidate count `max+1` | `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` R2 | exact sentinel payload; zero executor/units |
| deadline before/within R2 process | `BUDGET_EXCEEDED/LANE_TIMEOUT` R2 | ordered completed/budget identities when discovery completed |
| P22 worker failure | unchanged pair R2 | payload-free; join/close all; no R3 |
| malformed/no-op canary | `INCONCLUSIVE/CANARY_INCONCLUSIVE` R3 | existing complete canary payload; no transform unit |
| deadline/P22 failure in canary | unchanged pair R3 | payload-free; never canary judgment |
| unit writes Git-visible support state | `NO_MEASUREMENT/DIRTY_TREE` affected higher claim | consumer remains clean; mutant path is payload-free, not `crashed`; no later unit |
| unit commits | `NO_MEASUREMENT/HEAD_CHANGED` affected higher claim | same |
| outer scratch cleanup alone fails | `ERROR/GIT_FAILED` highest higher-rigor claim | remove that judgment; preserve lower claims; never mask an earlier failure |

Completed earlier claims remain in the verdict. Once an orchestration failure
stops the lane, every declared but unstarted later claim uses the same
payload-free pair, except the explicitly specified R2 discovered-deadline
payload and R3 failed-control result. The v4 rollup decides the overall
outcome; do not add a new schema or reason code.

## Carver-owned proof and traceability

The byte-locked packet is `nyxloom-trove/carve-assets/P23/`. The implementer
must not edit it. It contains:

- a compiling skeleton for `CommandPlan`, `execute_plan`, `LaneDeadline`, the
  `run_lane` monotonic/scratch seams, and mechanical config checks;
- a two-commit nested `apps/p` repository whose command reads tracked
  `shared/input`, with stale ignored coverage and command-created support state;
- a process ledger fixing nonempty appended argv, present and absent
  passthrough names, exact env, nested cwd suffix, and monotonically decreasing
  timeouts across baseline/mutants/control/transform;
- max-1/max/max+1 and injected-clock fixtures proving zero sentinel submission
  and no post-expiry launch;
- scratch-context entry/exit failures and failed R0/R1 canary controls, proving
  complete v4 terminal/payload handling without extra units;
- linked/hostile content plus P22 absolute/escaping-symlink refusal and the
  explicit R0-only self-host disposition;
- control-writes/transform-omits coverage, proving no cross-unit profile reuse;
- a byte-identical copy of P22's complete snapshot-limit v4 artifact and direct
  Schema/raw/merged verification; and
- a tracer proving P22's landed public calls compose into base/replacement
  units while the current live-copy runner violates the ledger.

| work | production owner | oracle | controlled break required |
|---|---|---|---|
| immutable plan / direct wrapper | `runner.py` | O1 | re-resolve env/argv in one repeated unit |
| deadline / terminal propagation | `runner.py` | O4/O5 | reset timeout or mask real P22 error during cleanup |
| seed/baseline/R1 orchestration | `runner.py` | O2/O5 | run live tree, prepare per unit, or reuse stale reservation |
| bounded replacement workers | `mutation.py` | O2/O4/O5 | copy project, submit max+1, or let a future outlive seed |
| control/transform snapshots | `canary.py` | O1/O2/O4 | reread lane, git commit, or reuse control profile |
| rigor grammar | `config.py` | O3 | accept non-R0/canonical order or uncovered-line without R1 |
| reachable terminal audit | ordinary tests only | O5 | retain snapshot-limit exclusion or use generated expected JSON |

The reviewer must add at least one new combined-axis attack not named in this
packet and record which convenient implementation it killed.

## Required work sequence

1. Apply the skeleton and run the locked suite; record the exact controlled-red
   count without editing assets.
2. Make `execute_plan` and the direct `execute_command` wrapper pass their unit
   cases; migrate all in-scope construction sites to the extended plan.
3. Land and test the load-time rigor grammar before orchestration.
4. Implement the higher-rigor seed/baseline unit helper and pass nested plan,
   stale artifact, hostile symlink, and source-hash cases.
5. Convert mutation to prepared replacement contexts and the shared deadline;
   pass max boundaries, ordering, expiry, and cleanup attacks.
6. Convert canary control/transform to independent P22 contexts; remove the
   installed live-copy/commit path and pass coverage-independence attacks.
7. Copy the locked snapshot-limit document byte-for-byte into ordinary
   fixtures and remove only its conformance exclusion; prove Schema, direct raw
   checks, and merged verification.
8. Run all focused tests, then the registered `tester-unified` gate. Record
   locked asset hashes, controlled-break counts, process ledger, and scratch
   accounting in the LOG.

## Test constraints copied from AUTHORING.md §3b

- No wall-clock verdict or sleep. Use injected monotonic sequences and explicit
  synchronization; real timeouts are hang failsafes only.
- Restore process-global environment and use fresh repositories/snapshots.
- Assert exact bytes/artifacts/ledgers/source hashes and absence—not only call
  counts, private fields, or “did not raise”.
- Hand-authored expected artifacts/manifests never come from Assay serializers.
- Do not weaken assertions, change locked assets, or add coverage-evasion
  pragmas. Network remains unavailable in the real gate.

## Scope and forbid

P23 integrates landed contracts. It does not modify P22 snapshot/Git mechanics,
P21 site/verdict/schema/verifier behavior, adapters, attestation, output
ownership, consumer projects, or the registered gate. Existing direct helper
tests may be mechanically migrated only where the frozen function signature
changes. Any wider repair is a new handoff.

## Mechanical BLOCKED rule

If a named contract requires a forbidden path; a P22 context cannot supply a
fresh unit under the one deadline; the locked expected artifact is invalid
without changing schema/verifier; or a landed public signature contradicts the
exact packet, write `BLOCKED: <contract id, path/signature, observed evidence>`
to the P23 LOG, commit only that evidence, and stop. Do not catch P22 refusal,
fall back to the live tree, invent a default, edit locked assets, or route based
on model confidence.
