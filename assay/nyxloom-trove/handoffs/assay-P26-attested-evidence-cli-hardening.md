---
schema_version: 1
id: assay-P26-attested-evidence-cli-hardening
project: assay
title: "Declared attested evidence is bounded, contained, path-current, and lane-budgeted"
tier: implement-2
input_revision: "233926cedd26a6e34512806e267b7141377913b2"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P25-real-python-project-qualification]
session: resume:assay-v11-attestation
scope:
  touch: ["src/assay/cli.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/measurability.py", "src/assay/git.py", "src/assay/attestation.py", "src/assay/safeio.py", "src/assay/canary.py", "src/assay/mutation.py", "tests/**", "tools/tester-unified-gate.sh", "docs/DESIGN-GUIDE.md", "nyxloom-trove/reports/assay-P26-attested-evidence-cli-hardening-LOG.md"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas", "src/assay/isolation.py", "src/assay/adapters", "gate", "nyxloom-trove/carve-assets/P26"]
oracles:
  - id: O1
    observable: "R0 and higher-rigor lane configuration round-trips the exact ordered attested declarations, and assay run emits one exactly covering sibling evidence entry per identity on normal and refused paths"
    negative: "A declared identity is omitted, reordered, placed in claims, or erased by adapter refusal while complete hand-authored v4 comparison still passes"
    gate: tester-unified
  - id: O2
    observable: "Every declaration and record is closed and bounded before Git; safe descriptor-relative input distinguishes true absence from traversal, symlink, special-file, malformed, and aggregate-limit attacks"
    negative: "A ../ key reads the seeded outside record, a missing parent becomes unreadable, or an oversized/over-aggregate batch launches Git"
    gate: tester-unified
  - id: O3
    observable: "A full exact ancestor OID is current only when every exact blob/tree path exists there and literal Git reports no change beneath it, including directory, metacharacter, and newline names"
    negative: "Changing a child beneath a reviewed directory or a literal hostile filename leaves evidence PASS"
    gate: tester-unified
  - id: O4
    observable: "One CLI-started LaneDeadline reaches HEAD, attestation, direct R0, higher-rigor measurability, mutation/canary integrity checks, and every generic Git bootstrap/substantive child; expiry kills the process group, preserves LANE_TIMEOUT, and launches no successor"
    negative: "A Git child gets a fresh duration, a deadline/overflow leaves a descendant holding a pipe, direct R0 attestation is unbounded, or substantive Git starts after bootstrap timeout"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "meeting the complete evidence coverage contract requires changing verdict.py or the v4 schema"
  - "the required remaining callable cannot reach a lane-owned Git call without touching isolation.py or changing a computed R2/R3 terminal"
  - "the descriptor-safe input contract requires a runtime dependency or a platform fallback"
  - "the locked packet is internally inconsistent, edited, or cannot produce exactly the stated controlled-red baseline"
mutexes: []
---

# P26 — attested evidence CLI hardening

The claim to attack is:

> Every declared attestation is resolved exactly once from bounded contained
> input, is current for every path it claims to cover, and consumes the same
> finite lane budget as the computed work beside it.

## Dispatch contract

- Contract class: **2c — bounded cross-module integration**.
- Roles: **Sonnet xhigh implementer → fresh Opus xhigh independent reviewer**.
- Frontmatter `implement-2` is the currently deployed live-route override for
  this 2c package, not a claim that the work is 2d; the controller must retain
  the named frontier-capable Sonnet xhigh route.
- Readiness: **READY**, frozen by Sol xhigh after P25 merge. Do not dispatch
  from the provisional pre-P25 packet or any revision other than the commit
  that contains this handoff and its locked P26 assets.
- Implementer freedom: private helper decomposition, diagnostics, and test
  file grouping only. Grammar, constants, public seams, query order/argv,
  lifecycle, terminal mapping, exact evidence order, and gate marker are fixed.
- The reviewer must add at least one materially new combined-axis attack and
  may repair/enhance only within this scope. A product/schema/owner change
  routes to Sol.

Work only in
`/workspaces/vbpub/.worktrees/assay-P26-attested-evidence-cli-hardening` on
branch `feat/assay-P26-attested-evidence-cli-hardening`.

## Context to read first

Read these exact sources; do not re-orient across the whole repository:

1. `nyxloom-trove/carve-assets/P26/README.md`, then every file named by its
   `fixture-manifest.json`. The manifest digest at freeze is
   `4cb702ddad368becd8aca55c0d5ef6ac2c55a086bb88751ff7a450d3b05352f8`.
   The packet is read-only.
2. `docs/DESIGN-GUIDE.md` §§3, 6 (external axes/transparency), 7, and 12;
   decisions A-033–A-034, A-074–A-078, A-085, A-110–A-111, A-160–A-161,
   A-173–A-175, A-189, A-193, A-201, and A-209–A-214.
3. `src/assay/attestation.py`, `src/assay/safeio.py`, and exactly
   `tests/test_attestation_record.py`, `tests/test_attestation_evaluate.py`,
   `tests/test_attestation_load_declared.py`, and
   `tests/test_attestation_pipeline_integration.py`. Run the locked premise probe;
   it must reproduce the old false PASSes before production edits.
4. `src/assay/config.py` around `JudgeConfig`, `_load_judge`, and
   `as_declared`; `src/assay/cli.py::_run_reserved`; and the evidence parameters
   already present in `runner.assemble_verdict`.
5. `src/assay/git.py`'s P20 generic boundary and separate P22 boundary;
   `runner.LaneDeadline`, `_run_higher_rigor_lane`, `_run_prepared_lane`, and
   `_execute_snapshot_unit`; every Git call in `measurability.py`,
   `mutation.py`, and `canary.py`.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` findings 7–8
   and `reports/assay-v2-post-series-review-sol-P15-P19.md` A-O15/F8 context.

## Implementation packet (normative)

Copy the shapes in `carve-assets/P26/interface-contract.json` and
`skeleton.patch`; adapt the compiling `git_boundary_skeleton.py` for the hard
process loop without replacing `git.py`'s existing identity/env owners. Do not
rename or reinterpret these shapes. The locked quick suite is
the independent executable contract:

```text
PYTHONPATH=src python -m pytest \
  nyxloom-trove/carve-assets/P26/test_acceptance.py -q -p no:randomly
```

At the frozen input it is the controlled red **9 passed, 32 failed**. The
failures are confined to the absent P26 config/API/behavior/gate seams. After
implementation all **41** tests pass and all eleven locked hashes remain exact.

### Topology, namespaces, and owners

```text
lane file parent (resolved) = project_root
project_root / attestation_dir / <key>.json
    -> safeio descriptor walk -> one bounded producer record
git.repo_top(project_root) = repo_top
record.reviewed_paths[] (repo-top-relative)
    -> sanitized Git objects at attested_commit and HEAD
reserved --verdict-json destination
    <- runner assembles computed claims + exact sibling evidence once
tester-unified worktree assets + run-venv interpreter
    -> locked test imports installed wheel, never worktree src/
```

`config.py` owns declaration grammar, `safeio.py` owns contained bytes,
`attestation.py` owns record grammar/staging/terminal translation, `git.py`
owns argv/raw output/process groups, CLI owns lifecycle and atomic batch
timeout, and runner owns lane-source binding plus artifact coverage. No layer
revalidates a repo-top path with project-root filesystem calls, and no test
uses a container path to validate a host namespace.

### Terminal and side-effect table

| state | evidence result/payload | computed work | artifact |
|---|---|---|---|
| exact current record | `PASS`, attested payload | runs | complete |
| exact ancestor with any changed reviewed path | `NO_MEASUREMENT/STALE_ATTESTATION`, attested payload | runs | complete |
| absent parent/final record | `NO_MEASUREMENT/MISSING_ATTESTATION`, no attested payload | runs | complete |
| malformed/unsafe/invalid Git identity or path | `ERROR/UNREADABLE_ARTIFACT`, no attested payload | runs | complete |
| aggregate path-query excess | every otherwise-valid identity unreadable; prior missing/malformed retained | no Git for batch; computed work runs | complete |
| adapter refusal after resolved evidence | resolved evidence unchanged | command does not run; all claims carry adapter refusal | complete |
| expiry during attestation batch | every evidence and claim `BUDGET_EXCEEDED/LANE_TIMEOUT`, no payload | no adapter or command | complete |
| expiry after evidence | resolved evidence unchanged; affected claims time out | no post-expiry successor | complete |
| expiry before HEAD identity | n/a | none | existing typed no-artifact terminal |

### Prepared proof and traceability

| construction | owner | oracle | locked witness | convenient break caught |
|---|---|---|---|---|
| declaration + lane binding | config/runner | O1 | R0 round-trip, binding guard, complete v4 | omit/reorder declarations or erase on refusal |
| descriptor-contained records | safeio/attestation | O2 | missing/symlink/outside/malformed batch | precheck+reopen or call every absence unreadable |
| closed grammar + aggregate ceiling | attestation | O2 | duplicate/surrogate/3×700/no-Git spies | parser last-wins or per-record-only bound |
| immutable exact Git identity | git/attestation | O3 | annotated tag object and missing-after-stale | peel non-commit or short-circuit before existence |
| literal file/tree currentness | git/attestation | O3 | changed directory, newline/metachar decoy | display parser, flat membership, pathspec expansion |
| generic process boundary | git | O4 | compiling skeleton, exited-parent pidfd, overflow | kill direct child only or wait forever on held pipe |
| singular deadline | CLI/runner/all Git callers | O4 | missing-only expiry, bootstrap ledger, 17s R0 | start late, omit no-Git sample, or reset duration |
| installed artifact | gate driver | O1–O4 | exact run-venv/PYTHONPATH-cleared block | marker/comment passes while source bytes are tested |

Only private helper names, diagnostics, test grouping, and behaviorally
equivalent local decomposition remain free. No grammar, public signature,
bound, argv meaning, ordering, terminal, payload, owner, or gate command is an
implementer decision.

### 1. Closed lane grammar

The only declaration shape is:

```toml
[lanes.<name>.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source = "attested", key = "security-review"},
  {source = "attested", key = "api-review.v2"},
]
```

Add `config.EvidenceConfig(source: str, key: str)` with exact
`as_declared() -> {"source":...,"key":...}` and append these optional fields
to `JudgeConfig`:

```python
attestation_dir: str | None = None
evidence: tuple[EvidenceConfig, ...] | None = None
```

They are both absent or both present. Present `evidence` has 1..64 entries,
preserves input order, accepts exactly the inline keys `source` and `key`,
supports only `source="attested"`, and rejects duplicate `(source,key)` pairs.
No attestation directory is derived.

These two HOW fields are legal on any canonical R0-led rigor sequence. An
R0-only lane may have a `[judge]` containing exactly this pair; every computed
judge field remains forbidden there. A higher-rigor lane treats the pair as
additional to its exact required computed fields, never as satisfying one.
`Lane.as_declared()` and `JudgeConfig.as_declared()` reproduce the parsed TOML
exactly. An R0 lane with neither field retains `judge=None`.

`attestation_dir` is canonical nonempty project-relative POSIX spelling:

- UTF-8 byte length 1..4,096 and at most 128 nonempty components;
- not absolute; no `.`/`..`, repeated slash, trailing slash, NUL, or control
  character; and `PurePosixPath(value).as_posix() == value`;
- existence is not required at config load. Runtime descriptor traversal owns
  absence and symlink/type facts.

Here “control character” is the closed ASCII set U+0000..U+001F plus U+007F;
do not substitute a locale or Unicode-category policy. Backslash is an ordinary
POSIX filename character, not a separator.

An evidence key matches ASCII
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. It cannot be option-like, contain a path
separator/control byte, or escape. Its only filename is `<key>.json`.

### 2. Closed record grammar and fixed bounds

The only JSON object is:

```json
{"producer":"human:alice","attested_commit":"<40 lowercase hex>",
 "reviewed_paths":["src/api.py","docs/contracts"]}
```

It has exactly those three keys. `producer` is a nonempty string of at most
256 UTF-8 bytes. `attested_commit` matches `[0-9a-f]{40}` before Git. The path
array contains 1..1,000 unique strings, each 1..4,096 UTF-8 bytes, canonical
repo-top-relative POSIX spelling with no NUL/absolute/`.`/`..`/empty component.
Newline, U+2028, backslash, leading `-`, and Git pathspec metacharacters remain
legal filename bytes/characters; they are not structure in JSON or Git argv.
Reject duplicate JSON member names rather than accepting a parser's first/last
value, and map any string that cannot be encoded as UTF-8 (including a lone
surrogate) to unreadable. These checks occur before Git.

Fixed constants and terminals:

| input/work | exact bound | terminal |
|---|---:|---|
| declarations | 64 | `ERROR/BAD_LANE_CONFIG` |
| attestation directory bytes/components | 4,096 / 128 | `ERROR/BAD_LANE_CONFIG` |
| one file | 1,048,576 bytes | evidence `ERROR/UNREADABLE_ARTIFACT` |
| producer | 256 UTF-8 bytes | evidence `ERROR/UNREADABLE_ARTIFACT` |
| paths per record | 1,000 | evidence `ERROR/UNREADABLE_ARTIFACT` |
| one path | 4,096 UTF-8 bytes | evidence `ERROR/UNREADABLE_ARTIFACT` |
| per-lane path-query commands | 4,096 | evidence `ERROR/UNREADABLE_ARTIFACT` |

Each valid reviewed path costs exactly two path-query commands (`ls-tree` and
`diff`), so preflight requires
`2 * sum(len(record.reviewed_paths) for every structurally valid record) <= 4096`.
Read/parse all declarations first. If the aggregate exceeds the bound, launch
no Git: every otherwise-valid record becomes unreadable, while identities
already known missing or malformed keep that own result. This is an atomic
preflight, never an order-dependent partial query.

The loader samples `remaining()` at batch entry, after every bounded file
read/parse, and immediately before returning, including an all-missing batch;
each Git helper samples it at its own boundary. Thus expiry during bounded JSON
work is observed as an atomic attestation timeout even when no valid record
would otherwise launch Git.

### 3. Descriptor-safe input

Add exactly:

```python
safeio.read_bounded_input(
    project_root: Path, relative_path: str, *, limit: int
) -> bytes | None
```

Reuse the P20 descriptor machinery. Open `project_root`, walk each directory
with `dir_fd` plus `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`, open the final file with
`O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC`, `fstat` that same descriptor as regular, and
read at most `limit+1`. Never `resolve()`/`is_file()` and reopen.

- `ENOENT` for any parent or the final name returns `None`: the declared
  producer supplied no record, so the evidence is
  `NO_MEASUREMENT/MISSING_ATTESTATION`.
- Symlink, non-directory parent, non-regular final object, permissions/race,
  overflow, invalid UTF-8, or malformed/over-bound JSON is
  `ERROR/UNREADABLE_ARTIFACT` for that identity.
- Decode UTF-8 exactly once after the byte limit. Resolve later identities
  despite one missing/malformed record.

`load_attested_evidence` repeats the closed `attestation_dir`, source/key, and
duplicate-declaration grammar at its public boundary and maps misuse to
`ERROR/BAD_LANE_CONFIG` before safe I/O. It does not assume every caller came
through `config.py`; neither an unsafe directory nor key reaches `safeio`.

Replace the old path-taking loader with the skeleton's contained API. No
public P26 path accepts a caller-composed attestation filename.

### 4. Exact Git semantics

Add `git.Remaining = Callable[[], float]` and these exact required-deadline
attestation helpers:

```python
verify_exact_commit(repo, oid, *, remaining) -> None
is_ancestor(repo, ancestor, descendant, *, remaining) -> bool
tree_entry_kind(repo, commit, path, *, remaining) -> str | None
path_is_current(repo, before, after, path, *, remaining) -> bool
```

They alone own argv/exit/raw-output interpretation:

1. `verify_exact_commit` runs sanitized
   `rev-parse --verify --end-of-options <oid>^{commit}` and requires the sole
   stripped output to be byte-for-byte the same lowercase full OID.
2. `is_ancestor` runs
   `merge-base --is-ancestor <ancestor> <descendant>`; exit 0 is true, 1 false,
   anything else is typed Git failure.
3. `tree_entry_kind` runs literal `ls-tree -z <oid> -- <path>`, parses raw bytes,
   and accepts exactly one exact UTF-8 path whose object type is `blob` or
   `tree`. Empty is `None`; extra/malformed/other-type output is Git failure.
4. `path_is_current` runs literal
   `diff --quiet --exit-code --no-ext-diff --no-textconv <old> <new> -- <path>`;
   exit 0 is true, 1 false, anything else typed Git failure.

“Literal” is supplied by P20's existing global `--literal-pathspecs` option in
`git._run_bytes`; both helpers must use that generic boundary and must not
launch a bespoke Git child without it. The locked decoy fixture makes a path
containing `*?[x]` match another filename under ordinary pathspec semantics,
then changes only the decoy; the exact reviewed identity remains current.

For each record: verify its exact commit once; require it ancestor-or-equal;
run every existence query before any staleness query; then require every path
current. A directory therefore covers all descendants and a file only itself.
No newline-delimited name list, display decoder, `cat-file <oid>:<path>`, ref
shorthand, or path-membership set remains.

Record-supplied invalid/unresolvable/unrelated/missing-path Git facts map to
that evidence identity's `ERROR/UNREADABLE_ARTIFACT`. One `diff` exit 1 maps to
payload-preserving `NO_MEASUREMENT/STALE_ATTESTATION`. Do not catch/remap an
`AssayError` whose exact pair is `BUDGET_EXCEEDED/LANE_TIMEOUT`.

### 5. One deadline, including R0 and nested integrity calls

Generic `git._run_bounded` gains the keyword-only
`remaining: Remaining | None = None`; `_resolve_repo`, `_run_bytes`, `run`,
`repo_top`, `head_rev`, `resolve_base`, and `dirty_paths` forward the same
value to both repository bootstrap and substantive children. Preserve `None`
only for a genuine non-lane/legacy helper call. Every lane-owned call passes a
callable.

With a callable, `_run_bounded`:

1. samples it before `Popen` and before every selector wait;
2. starts the child with `start_new_session=True`;
3. uses the observed positive remainder as the selector timeout;
4. retains the existing stdout/stderr byte ceilings;
5. on deadline, stdout/stderr overflow, pump/read failure, or another abnormal
   exit, sends `SIGKILL` to the whole process group, closes/drains only within
   the existing byte bounds, and waits/reaps the direct child; a remaining-call
   `AssayError` is re-raised as that exact exception object; and
6. never starts another bootstrap/substantive child after expiry. Output
   readiness does not refresh the absolute deadline.

The cleanup attempt is group-owned, not child-state-owned: never skip
`killpg()` merely because `proc.poll()` says the boundary child has exited. A
forked descendant can still hold stdout/stderr open after that exit.

Do not rewrite P22's `_P22Deadline`/isolation protocol. Its public operations
already receive freshly sampled `deadline.remaining()` and own their groups.
P26 closes generic Git calls made inside those snapshots.

CLI lifecycle is exact:

1. load lane and reserve requested output as today;
2. start one `LaneDeadline`;
3. resolve HEAD with `remaining=deadline.remaining`;
4. convert config declarations to verdict `EvidenceDeclaration` objects and
   resolve the complete attestation batch with that same callable;
5. resolve the adapter;
6. call `run_lane(..., evidence=..., declared_evidence=...,
   deadline=deadline)`; and
7. emit once.

`run_lane(deadline=None)` may preserve source/library callers by deriving and
starting one object immediately from `lane.budget_seconds` and the injected
monotonic clock. CLI never uses `None`. Direct R0 resolves one plan and calls
`execute_plan(... timeout=deadline.remaining())`; it no longer gives its child
the original full duration after Git/evidence work.

Thread the callable through every lane-owned generic Git call, including:

- runner repo identity, pre/post dirt/HEAD, base resolution, tracked-artifact
  check, R1 diff, and R2 diff fallback;
- both measurability guards;
- mutation snapshot dirt/HEAD checks (pass `deadline.remaining` into the
  private helper; change no bucket semantics); and
- isolated canary `_judge_unit`/`evaluate_r1` calls. Legacy standalone canary
  helpers may explicitly retain `remaining=None`; the P23 isolated path may
  not.

This limited forwarding is why `canary.py` and `mutation.py` are in touch
scope. Do not alter their computed policy, mutation/canary payload, or process
count.

### 6. Evidence lifecycle and complete artifacts

Resolve all declarations once and in order before adapter/command work.
Missing/malformed/stale evidence is a sibling result, not permission to skip
computed work. Pass the already-resolved tuples through `run_lane`,
`refuse_lane`, `_refuse_lane_with_plan`, and every `assemble_verdict` call.
Adapter refusal preserves them.

`run_lane` and `refuse_lane` derive the authoritative ordered declarations
from `lane.judge.evidence` at entry and require exact ordered identity equality
with both supplied tuples before Git, plan, adapter, or command work.
`assemble_verdict` repeats the same binding before output so its direct public
callers cannot bypass the source. Their empty tuple defaults remain compatible
with existing no-evidence lanes because the derived source is then empty;
omission against a nonempty declaration fails `ERROR/BAD_LANE_CONFIG`. This is
a checked derived default, never a substitute for a fact that exists on the
lane.

If the deadline expires during the atomic attestation batch, launch neither
adapter nor command. Build a complete refusal: every declared rigor claim and
every declared evidence identity is payload-free
`BUDGET_EXCEEDED/LANE_TIMEOUT`. If it expires later, preserve the resolved
evidence and apply existing runner timeout precedence to computed claims. If it
expires before HEAD, commit identity is unavailable and the existing pre-HEAD
typed/no-artifact behavior remains.

Add ordinary v4 conformance fixtures for evidence-level LANE_TIMEOUT and all
new CLI states. Do not edit verdict/schema owners: the landed v4 envelope
already accepts the pair and independently checks coverage.

## Required implementation work

1. Copy the frozen config types/grammar and make exact round-trip/rejection
   tests for R0 and higher-rigor lanes.
2. Add the safe bounded input seam and migrate attestation loading off every
   pathname precheck/reopen.
3. Close record grammar and enforce all structural/aggregate bounds before
   Git; preserve independent later identities.
4. Add the four narrow Git helpers and replace display-name/current-membership
   logic with exact raw/path-status queries.
5. Extend generic Git with callable deadline/process-group ownership without
   weakening byte/env/repository hardening.
6. Start/forward one lane deadline across CLI, direct R0, higher rigor,
   measurability, mutation, canary, and attestation.
7. Thread declarations/evidence through every normal/refusal artifact and add
   the exact timeout-batch behavior.
8. Add installed-wheel complete-artifact fixtures for current, stale file,
   stale directory, absent parent/final, malformed/unknown field, symlink,
   unrelated/descendant/non-commit OID, missing reviewed path, literal hostile
   name, per-record/aggregate limit, adapter refusal, and attestation timeout.
9. Make all 41 locked acceptance tests green without editing their packet; run
   focused tests, full ordinary tests with statement+branch coverage, mutation
   breaks per A-067, and static checks.
10. Extend the installed-wheel registered gate to run locked P26 acceptance
    after `wheel-installed` and before `run_self_hosted_lane`, then emit exactly
    `ASSAY_GATE_PHASE=attestation-hardened`. Preserve every earlier phase and
    outer receipt. Run it with the gate's installed interpreter, point
    `ASSAY_P26_PROJECT_ROOT` at the reviewed worktree's `assay/`, and pass
    `--override-ini=pythonpath=` so `pyproject.toml` cannot shadow the wheel
    with `src/`. Clear ambient `PYTHONPATH` for the same reason; use the exact
    block below.
11. Write the package LOG with commands, exact counts, break matrix, locked
    hashes, new combined-axis attacks, and successor-only facts not already in
    code/doctrine.

### Exact registered-gate insertion

```bash
PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \
    "$scratch/run-venv/bin/python" -m pytest \
      "$worktree/assay/nyxloom-trove/carve-assets/P26/test_acceptance.py" \
      -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=attestation-hardened'
```

## Required negative and combined-axis fixtures

The implementation/review matrix includes at least:

| axes combined | expected result |
|---|---|
| R0-only + valid attestation + `/bin/true` | complete v4 PASS, one evidence sibling |
| changed directory descendant + literal pathspec metachar/newline sibling | each stale independently; unrelated exact file stays PASS |
| missing attestation directory + later valid declaration | first missing, later PASS; absence is not unreadable |
| malformed first + missing second + current third | exact hand-authored ordered artifact; overall ERROR, command still ran |
| symlink-swapped parent/final + seeded outside PASS record | outside bytes never consumed; unreadable |
| three valid 700-path records | aggregate unreadable before any Git argv |
| uppercase/short/symbolic/descendant/unrelated OID | unreadable, never resolution into apparent immutable identity |
| adapter refusal + current attestation | computed BAD_LANE_CONFIG plus preserved current evidence |
| attestation Git timeout + later declaration + command side-effect sentinel | every axis LANE_TIMEOUT; no adapter/command/successor argv |
| generic Git byte followed by descendant-held pipe + synchronized expiry | original timeout object; descendant exited/reaped; no hang |
| generic Git output overflow + descendant-held pipe | `ERROR/GIT_FAILED`; complete group exited; no hang |
| direct R0 after evidence consumed part of budget | command receives remaining duration, not `lane.budget_seconds` |

Every test obeys AUTHORING §3b: synchronization events/FIFOs/pidfds decide
process outcomes; wall-clock timeouts are 60-second hang failsafes only. Each
repository and attestation root is fresh. Expected evidence/artifacts are
literal carver/test data, never producer serialization. No network, ambient
attestation, worker order, or real-time verdict dependency.

## Test constraints copied from AUTHORING §3b

**A. Nothing may make the verdict depend on how fast the machine is.**

- Do not set a monotonic deadline or sleep and then use elapsed time/iteration
  count as the assertion.
- Wait on a real synchronization point: join a process/thread, block on an
  event the code under test sets, or drain a queue. Prefer extracting and
  directly calling a pure step when possible.
- A timeout is legal only as a generous 60-second hang failsafe. It must never
  decide the expected result. A test that fails on a slow machine is a real
  race to fix, never a reason to widen the timeout or raise cgroup weight.

**B. Nothing may depend on test order, worker assignment, or a sibling test.**

- Do not mutate process-global logging, environment, module attributes, or
  singletons without restoring the prior value.
- Do not patch an object that synthesizes attributes through `__getattr__`;
  patch the namespace that owns the value.
- Use fresh `tmp_path` state and restore, rather than destroy, anything the
  test found. For a parallel-only failure, investigate earlier-test pollution
  before assuming a race.

**C. No hollow tests.**

- No empty bodies, “nothing raised” assertions, implementation-trivia-only
  checks, or weakened/deleted assertions.
- Assert the behavioral contract. Where a guard prevents a real crash or
  false result, include the input that reproduces that failure.

**D. No coverage evasion.**

- Do not add coverage-exclusion pragmas to changed lines; the gate rejects the
  token even when it appears in a comment describing the rule.
- Do not exclude an exception body while assuming its clause is also covered.
  Restructure genuinely unreachable code so it does not exist.

**E. Network, clock, and filesystem are controlled inputs.**

- No real network, registry, or model endpoint in a unit test.
- Do not use the live wall clock where an assertion depends on its value.
  Inject or mock the boundary and make offline behavior the default.

For every new test, ask whether it could change result on a slower machine, in
a different worker, or in a different order. If so, it is not an oracle yet.

## Verification and gate

The implementer runs focused/ordinary/locked diagnostics but not the
authoritative outer gate. The fresh reviewer reruns them, verifies packet
hashes, adds one novel combined-axis attack, and returns ACCEPT/REPAIR/BLOCKED.
The Luna controller alone runs foreground:

```text
bash /workspaces/vbpub/assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-P26-attested-evidence-cli-hardening
```

It requires outer exit 0, the validated background cgroup, `--network=none`,
the new marker in exact order after `wheel-installed`, all P25 markers, and
`ASSAY_REGISTERED_GATE_COMPLETE=1`; it saves and hashes the raw log. Then it
merges `--no-ff`, reruns locked acceptance at merged main, and routes P27 to
Sol. A cockpit pytest run is diagnostic, never the ship signal.

## Scope and mechanical BLOCKED rule

This package wires/hardens Tier-3 attested evidence and closes A-201/A-212
deadline propagation. It does not add adjudicated evidence, change v4, change
snapshot topology, add language semantics, or redesign R2/R3 computed results.
Locked P26 assets are carver-owned and byte-identical.

If an oracle requires a forbidden owner, an unspecified public/product choice,
an edited/inconsistent locked asset, or a computed terminal change, write
`BLOCKED: <exact trigger and evidence>` to the LOG, commit only that log, and
stop. Do not improvise a default, weaken an oracle, edit the locked packet, or
run the registered outer gate.
