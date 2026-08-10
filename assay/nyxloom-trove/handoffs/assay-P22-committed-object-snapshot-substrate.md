---
schema_version: 1
id: assay-P22-committed-object-snapshot-substrate
project: assay
title: "Committed-object snapshots preserve repository topology and bytes"
tier: implement-2
input_revision: "678104ad32f26b9fbccdbb38b3298149a1d8f8e0"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P21-verdict-v4-evidence-contract]
session: fresh
scope:
  touch: ["src/assay/git.py", "src/assay/isolation.py", "tests/test_isolation.py", "tests/fixtures/isolation/**", "tests/test_verdict_conformance.py", "tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json", "README.md", "docs/DESIGN-GUIDE.md", "nyxloom-trove/reports/assay-P22-committed-object-snapshot-substrate-LOG.md"]
  forbid: ["nyxloom-trove/carve-assets/P22/README.md", "nyxloom-trove/carve-assets/P22/skeleton.patch", "nyxloom-trove/carve-assets/P22/test_acceptance.py", "nyxloom-trove/carve-assets/P22/fixture-manifest.json", "nyxloom-trove/carve-assets/P22/probe_snapshot_plumbing.py", "nyxloom-trove/carve-assets/P22/expected/r0-snapshot-limit-v4.json", "src/assay/schemas", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/attestation.py", "src/assay/adapters", "pyproject.toml", "assay.toml", "tools", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "One bounded preparation transfers the supplied full commit's complete reachable object closure into a private seed; repeated base and replacement contexts still materialize exact clean repositories after the source Git directory becomes unavailable"
    negative: "A convenient implementation reopens the consumer object store for every mutant, uses a source alternate, or copies only apps/p and loses tracked shared/"
    gate: tester-unified
  - id: O2
    observable: "Every yielded root has an independent self-contained .git, detached HEAD at its exact base or child OID, a clean index/worktree, exact tracked bytes and executable modes, and no source inode, alternate, hook, filter, replace ref, partial-clone fetch, ignored byte, or consumer-controlled process"
    negative: "A source filter runs, a replace ref supplies different bytes, two snapshots hardlink a pack, or an ignored FIFO/profile enters the snapshot"
    gate: tester-unified
  - id: O3
    observable: "Raw tree parsing accepts ordinary/executable blobs and lexically-contained relative symlinks, preserves UTF-8/newline/backslash path bytes, and refuses unsafe modes, gitlinks, .git components, malformed/colliding paths, incomplete object topologies, and every fixed limit before exposing a seed or snapshot"
    negative: "An escaping symlink is dereferenced, a gitlink becomes a directory, or max_entries+1 yields a partial runnable tree"
    gate: tester-unified
  - id: O4
    observable: "A repo-top-relative whole-blob replacement creates a deterministic clean child commit with the exact base parent and fixed neutral identity while the source, seed, base snapshot, and sibling snapshots remain byte- and inode-independent"
    negative: "The replacement is interpreted below project_prefix, invokes git commit/checkout, trusts ambient author data, mutates a source object, or accepts mismatched expected bytes"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the complete reachable object closure cannot be transferred once and materialized repeatedly within the fixed limits without source alternates or hardlinks"
  - "a supported tracked mode cannot be materialized without checkout, archive, a consumer hook, or a filter"
mutexes: []
---

# P22 — committed-object snapshot substrate

The claim to attack: **Assay can prepare one bounded, inert, byte-faithful
repository seed from a full commit and use it concurrently for independent base
and replacement repositories without ever returning to consumer-controlled Git
state.**

## Dispatch contract

- Contract class: **2b — complex solution-bearing execution** (`implement-4`
  when deployed; frontmatter names today's live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Opus xhigh implementer → a fresh
  Opus xhigh independent reviewer session**.
- Readiness: **READY at input revision
  `678104ad32f26b9fbccdbb38b3298149a1d8f8e0`** under A-184–A-187. The
  carver-owned compiling skeleton, locked acceptance, literal manifest,
  complete v4 artifact, and real-Git tracer live under
  `nyxloom-trove/carve-assets/P22/`; their hashes are recorded in
  `nyxloom-trove/reports/assay-P22-JIT-CARVE.md`.
- Implementer freedom: private streaming/process supervision, raw-tree parser
  decomposition, pack-copy optimization, and helper names only. Public shapes,
  namespaces, object source, limits, modes, terminals, fixed child identity,
  side-effect order, and locked assets are not degrees of freedom.

## Worktree and branch

Work only in
`/workspaces/vbpub/.worktrees/assay-P22-committed-object-snapshot-substrate` on
branch `feat/assay-P22-committed-object-snapshot-substrate`.

## Context to read first

Read only these items before implementation:

1. This handoff in full, then
   `nyxloom-trove/reports/assay-P22-JIT-CARVE.md` §§Result, 1–6, and Witnessed
   evidence.
2. `nyxloom-trove/carve-assets/P22/README.md`, `skeleton.patch`,
   `fixture-manifest.json`, `expected/r0-snapshot-limit-v4.json`, and
   `test_acceptance.py`. Do not read the tracer implementation until after
   writing your private construction plan; its output in the JIT report is the
   evidence. The locked directory is forbidden to edit.
3. `src/assay/git.py` in full, especially `_REPLACEMENT_ENV`, `_FIXED_CONFIG`,
   `_resolve_repo`, `_run_bounded`, `_run_bytes`, and `_resolve_revision`; plus
   `tests/test_git_hostile_boundary.py`. Preserve P20's single Git-process
   owner and explicit git-dir/work-tree identity.
4. `src/assay/errors.py::ReasonCode`,
   `tests/test_verdict_conformance.py::{VOCABULARY,EXCLUDED_ENTIRELY}`, and
   `tests/fixtures/verdicts/r2_budget_exceeded_mutant_limit_exceeded.json`.
   P21 reviewer disposition `SB-P21-R2` is closed in this package exactly as
   specified below; do not invent another v4 shape.
5. `src/assay/config.py::_load_canary` lines 908–971 only to observe that
   `judge.canary.target` is already normalized at load. P22 never reads a lane
   file or re-normalizes it; this closes `SB-P21-02` without a second config
   path.
6. `docs/DESIGN-GUIDE.md` §§5, 9, 11, and 12; decisions A-145, A-149,
   A-154–A-161, A-163, A-173–A-175, A-180–A-187; and P20's final handoff
   `Implementation packet / Interfaces and ownership` Git subsection.

Do not orient across all tests or historical handoffs. The controller's frozen
Opus base already carries the wave context; reconcile its anchor-to-HEAD diff
as instructed by the controller.

## Implementation packet (normative)

### Owned public interface

`src/assay/isolation.py` owns the only snapshot abstraction. Apply
`nyxloom-trove/carve-assets/P22/skeleton.patch` exactly once before editing.
The public shape is fixed:

```python
@dataclass(frozen=True, kw_only=True)
class SnapshotLimits:
    max_objects: int
    max_entries: int
    max_path_bytes: int
    max_total_path_bytes: int
    max_blob_bytes: int
    max_total_object_bytes: int
    max_pack_bytes: int

DEFAULT_SNAPSHOT_LIMITS = SnapshotLimits(
    max_objects=100_000,
    max_entries=200_000,
    max_path_bytes=4_096,
    max_total_path_bytes=64 * 1024 * 1024,
    max_blob_bytes=64 * 1024 * 1024,
    max_total_object_bytes=1024 * 1024 * 1024,
    max_pack_bytes=512 * 1024 * 1024,
)

@dataclass(frozen=True, kw_only=True)
class SnapshotSpec:
    repo_top: Path
    commit: str
    project_prefix: PurePosixPath
    scratch_root: Path
    limits: SnapshotLimits = DEFAULT_SNAPSHOT_LIMITS

@dataclass(frozen=True, kw_only=True)
class Snapshot:
    root: Path
    project_root: Path
    commit: str

class SnapshotRepository:
    @property
    def spec(self) -> SnapshotSpec: ...
    def read_regular_file(self, path: PurePosixPath, *, timeout: float) -> bytes: ...
    def materialize(self, *, timeout: float) -> ContextManager[Snapshot]: ...
    def materialize_replacement(
        self, *, path: PurePosixPath, expected: bytes, replacement: bytes,
        timeout: float,
    ) -> ContextManager[Snapshot]: ...

def prepare_snapshot(
    spec: SnapshotSpec, *, timeout: float
) -> ContextManager[SnapshotRepository]: ...
```

`prepare_snapshot` transfers the source closure once and yields an unexposed
private seed. `SnapshotRepository` is safe for concurrent `materialize*` calls.
Every call requires the positive, finite, non-boolean **remaining lane seconds**
computed by P23 immediately before the call; there is no unbounded/default
timeout and P22 never resets the lane budget.
Every returned context creates a new repository and removes it at exit; the
seed itself is removed only when the outer context exits. Calling any method
after outer-context closure is `RuntimeError`. P23 must close/join every child
context before closing the outer context; outer closure with a live child is
programmer misuse and raises `RuntimeError`. P23 must prepare once per lane, not
once per mutant.

`Snapshot.root/.git` is a normal, self-contained private repository with a
detached HEAD and a clean index/worktree. `Snapshot.project_root` is exactly
`root / project_prefix` (`root` itself for `PurePosixPath('.')`). A base
snapshot's `commit` equals `spec.commit`; a replacement snapshot names its
deterministic child. No context yields until all validation is complete.

### Validation and authoritative namespaces

All integer limits reject booleans, non-integers, and values below one.
`max_blob_bytes <= max_total_object_bytes`; other limits are independent fixed
ceilings. Invalid constructor values are `ValueError`, before filesystem or Git
work. Every `timeout` rejects booleans, non-numeric values, non-finite values,
and values `<= 0` as `ValueError`. For each call, convert the supplied remaining
seconds to one internal monotonic deadline; every child launch and bounded copy
uses only what remains and never receives the original duration again.

`SnapshotSpec` requires:

- `repo_top` and `scratch_root` are absolute, already-resolved paths;
- `repo_top` is the exact path `git.repo_top(repo_top)` resolves through P20;
  a non-symlink `.git` gitfile/linked worktree is supported and its common Git
  directory is resolved only by P20's sanitized boundary;
- `scratch_root` exists as a real non-symlink directory and is outside
  `repo_top`; P22 creates/removes only uniquely named children beneath it;
- `commit` is exactly 40 lowercase hexadecimal characters and resolves to that
  same commit, never a ref or abbreviated spelling;
- `project_prefix` is normalized repo-top-relative POSIX (`.` for repository
  root), has no empty/`.`/`..` component, and resolves to a tree in the commit.

Namespaces never cross implicitly:

```text
consumer repo_top / project_prefix
          |                (identity only; no working-tree copy)
          v
private seed: full reachable object closure + validated HEAD tree manifest
          |
          +--> snapshot.root/.git      private Git namespace
          +--> snapshot.root/<paths>   repo-top-relative tracked paths
          +--> snapshot.project_root   root / project_prefix

replacement path: repo-top-relative, even when project_prefix != '.'
scratch_root: caller-owned Assay scratch namespace, never consumer repository
```

P22 never reads `assay.toml`, `Lane`, `judge.canary.target`, coverage output, or
ambient command inputs. P21 already normalizes the canary target; P23 passes
that value as a repo-relative replacement identity after its one explicit
project-prefix conversion.

### Single sanitized Git owner

`git.py` remains the only module that launches Git. `isolation.py` may call new
private P22 helpers in `git.py`; it may not invoke `subprocess`, locate Git,
parse a Git config path itself, or call the public text decoder for blob bytes.
Do not expose a generic caller-supplied argv/environment escape hatch.

Preserve P20's exact executable, repository anchoring, closed replacement
environment, and output bounds. Add these fixed defenses for object reads:

```text
environment: GIT_NO_LAZY_FETCH=1
config:      -c core.commitGraph=false -c core.multiPackIndex=false
```

They are product policy, not caller parameters. No ambient `GIT_OBJECT_*`,
alternates, identity, config counter, locale, pager, editor, ref, or temp-index
path crosses the boundary. P22's own high-level private operations may add only
an exact seed/destination git-dir/work-tree, a private `GIT_INDEX_FILE`, and the
fixed replacement identity named below.

Before inventory, resolve the common Git directory through the sanitized
boundary and refuse any of these source topologies with
`ERROR/GIT_FAILED`: a symlink/non-regular/nonempty
`objects/info/alternates`; `info/grafts`; a shallow repository; a partial-clone
extension or promisor remote; non-SHA-1 object format; or a missing/corrupt
object. Do not merely clear ambient `GIT_ALTERNATE_OBJECT_DIRECTORIES`—a local
alternates file otherwise still participates.

### Preparation: exact bounded object closure

Perform these phases in order; any failure deletes the partial private child
before returning:

1. Validate the spec and source topology without creating a seed.
2. Resolve the literal full commit and root tree under P20's boundary with
   replacement refs disabled.
3. Enumerate the **unique complete reachable closure**, including commit
   history, trees, and blobs, with fixed full OIDs. Use a NUL/line-safe Git
   grammar whose object names are only validated 40-hex OIDs; do not retain
   display paths from this phase.
4. Batch-check each OID's exact type and uncompressed size. Count every object,
   every blob, and the total before pack acceptance. Refuse the first limit+1
   as `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED`; never truncate. Existing
   P20 stdout/stderr ceilings remain additional fail-closed bounds.
5. Feed only that frozen OID list to source `pack-objects --stdout` **without
   `--revs`**, stream it directly to private `index-pack --stdin`, and count the
   compressed bytes in transit. Kill both process groups on overflow/failure;
   expiration of the caller-supplied product budget kills the owned process
   group and maps to `BUDGET_EXCEEDED/LANE_TIMEOUT`. A separate generous
   controller/reviewer failsafe is evidence-only and reports
   `PROBE_INCONCLUSIVE_HUNG`, never a product verdict.
6. Verify the requested commit and exact closure from the private seed after
   disconnecting the source. The seed contains no alternates, source path,
   copied config/ref/hook/index, or consumer-visible worktree.
7. Parse and validate the HEAD tree from raw tree-object bytes in the seed;
   freeze its manifest and required blob bytes/metadata. Only now yield the
   `SnapshotRepository`.

The transfer happens once per prepared repository. After the outer context
yields, removing the source `.git` must not affect `read_regular_file`, base
materialization, or replacement materialization. This is both a security
boundary and the performance requirement that makes P23's repeated units
affordable.

The private seed and snapshots deliberately contain no branch or tag refs: the
only declared identity is the literal commit, and copied refs would reintroduce
mutable source names that are neither needed nor bound by this contract. Their
`HEAD` is detached at the exact base or deterministic child commit.

### Raw tree grammar, modes, paths, and symlinks

Parse each raw SHA-1 tree object as repeated
`<canonical-octal-mode> SP <name-bytes> NUL <20-byte-oid>`. Traverse
iteratively from the commit's root tree. Verify each referenced object has the
type/size frozen by the inventory. Reject malformed/truncated records,
duplicate full paths, a file/directory prefix collision, and a tree cycle.

Count both trees and leaf entries against `max_entries`. For each full path,
count its UTF-8 byte length (including separators) against `max_path_bytes` and
the sum against `max_total_path_bytes`. Decode strictly as UTF-8. Each component
is nonempty and not `.` or `..`; any component exactly `.git` is refused because
it would collide with or create a second Git namespace. Newline, non-ASCII, and
a literal backslash are valid POSIX filename bytes and must survive unchanged.
NUL and slash cannot occur inside a raw tree name by grammar.

Supported modes are exactly:

| mode | object | materialized form |
|---|---|---|
| `040000` | tree | directory mode `0755` |
| `100644` | blob | independent regular file mode `0644` |
| `100755` | blob | independent regular file mode `0755` |
| `120000` | blob | symlink whose blob is its target bytes |

Reject gitlink `160000`, any other mode/type pair, devices/FIFOs/sockets, and
malformed objects as `ERROR/GIT_FAILED`. A symlink target must decode as UTF-8,
be nonempty and relative, and lexically resolve from its link parent without
ever leaving snapshot root. It may dangle or form a contained cycle; never
dereference it. Because every link is checked, a chain cannot escape.

Materialization writes blob bytes directly from the seed without filters,
checkout, archive, hardlinks, or source paths. Each snapshot gets new inodes;
an optional reflink optimization is allowed only when the resulting inode is
different and writes are copy-on-write. Set tracked files/directories to the
exact modes above and a fixed source mtime of `946684800` seconds; Git does not
record owner, inode, or ctime and P22 makes no claim about them. Build the index
from the exact commit with non-checkout plumbing, set detached HEAD, then verify
HEAD, clean status, bytes, modes, private closure, absence of alternates/hooks/
foreign config, and project prefix before yielding.

### Replacement construction

`path` is a normalized **repo-top-relative** `PurePosixPath`; it must name a
`100644` or `100755` blob in the prepared base. `expected` and `replacement`
are whole blob bytes. Compare `expected` byte-for-byte with the seed blob before
creating a materialization. A mismatch, absent path, or non-regular target is
`ERROR/MUTATION_DISCOVERY_FAILED`: a supposedly frozen mutation descriptor no
longer names the committed syntax bytes it claimed.

Copy the private seed into a fresh independent repository, populate a private
index from the base, write the replacement blob, update exactly that index
entry while preserving its mode, write the new trees, and use `commit-tree`
with:

```text
parent:          spec.commit
author/committer: Assay <assay@invalid>
author/commit time: 946684800 +0000
message bytes:   b"assay snapshot replacement\n"
```

No `git commit`, checkout, ref update, hook, filter, editor, signer, or ambient
identity is allowed. The same base/path/bytes produces the same child OID.
Re-enumerate the child closure and include every new blob/tree/commit in object
count, per-object, and total-uncompressed limits before yield. The child has a
clean index/worktree and detached HEAD at its OID. Source, seed, any base
snapshot, and sibling materializations remain unchanged and inode-independent.

### Terminal and side-effect table

| state | result | side effects allowed |
|---|---|---|
| invalid limit/spec/path/timeout grammar | `ValueError` before Git/temp work | none |
| source Git identity/object/topology failure; unsafe tree/mode/symlink; private verification or expected filesystem I/O failure | `AssayError(ERROR, GIT_FAILED)` | cleaned private partial only |
| any object/entry/path/blob/total/pack limit+1, including replacement closure | `AssayError(BUDGET_EXCEEDED, SNAPSHOT_LIMIT_EXCEEDED)` | cleaned private partial only; source unchanged |
| supplied remaining seconds expire during preparation/read/materialization | `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)` | kill owned process group; cleaned private partial only |
| replacement expected mismatch, absent path, or non-regular target | `AssayError(ERROR, MUTATION_DISCOVERY_FAILED)` | no materialization/object write |
| valid preparation | one live private seed, no source writes | uniquely owned scratch child only |
| valid base/replacement | fully verified context; remove on exit | independent snapshot only |
| method after outer context closes | `RuntimeError` | none |

Never translate an unexpected programmer error into a plausible terminal.
Cleanup must not mask an exception already in flight; on a normal exit, an
expected cleanup failure is `ERROR/GIT_FAILED` and is reported rather than
silently leaking state.

### Complete artifact obligation inherited from P21

Copy
`nyxloom-trove/carve-assets/P22/expected/r0-snapshot-limit-v4.json` byte-for-byte
to
`tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json`.
Remove only `("BUDGET_EXCEEDED", "SNAPSHOT_LIMIT_EXCEEDED")` from
`tests/test_verdict_conformance.py::EXCLUDED_ENTIRELY` and correct its adjacent
comment from reserved to P22-reachable. The fixture is deliberately R0-only:
snapshot preparation precedes the baseline, so the whole invocation is refused
with no `judgment` or mutation payload. Schema/model/raw verifier already accept
this exact complete document; do not edit v4 or `verify.py`.

### Prepared proof and traceability

Run the locked suite unchanged. Each row names a wrong implementation the
fixture must distinguish:

| work/owner | oracle | locked fixture/observable | controlled break that must go red |
|---|---|---|---|
| one-time source closure / `git.py` | O1/O2 | hostile nested repo, linked-worktree gitfile, remove source `.git` after prepare, then base+replacement still exact | assume `.git` directory, reopen source, or add an alternate |
| private concurrent materialization / `isolation.py` | O1/O2 | six concurrent units, clean HEAD/status, disjoint source/sibling object inodes | hardlink seed/source pack or share one root |
| raw tree/path/mode/symlink validation / both | O2/O3 | literal manifest plus newline/backslash path, gitlink, malformed `100664`, absolute/escaping links | dereference links, drop awkward path, accept gitlink/mode |
| all fixed bounds / both | O3 | entry/path/blob/object/pack limit+1 returns exact pair and leaves scratch empty | truncate, check only pack bytes, or yield partial |
| fixed child / both | O4 | repo-relative sibling replacement, exact parent, repeated child OID under hostile ambient author | interpret under project root, use ambient identity, or mutate base |
| v4 reachability / tests only | O3 | complete hand-authored R0 snapshot-limit fixture passes Schema and raw verifier | leave conformance exclusion or invent R2 mutation payload |

The implementation LOG repeats this table with actual production tests and
records the failure count from breaking at least: source re-read protection,
alternate refusal, entry limit, symlink containment, no-hardlink independence,
expected-byte equality, and fixed child identity. The reviewer adds a new
combined-axis attack before seeing those counts and calls the raw verifier
directly whenever claiming P21/A-182 layer independence.

### Degrees of freedom

Private helper names; an equivalent iterative raw-tree parser; bounded
pipe/poll/select decomposition; and copy versus verified reflink optimization.
Nothing else above is discretionary.

## Work

1. Apply the locked skeleton and write the P22 LOG immediately. Run the locked
   suite once and record the controlled `18 failed, 2 passed` baseline from the
   JIT anchor. The skeleton already implements/fixes constructor and timeout
   grammar, so that case is green; the other green case validates the already-
   landed v4 artifact layers. All eighteen unimplemented construction cases
   remain red before work.
2. Extend only `git.py`'s private process boundary for bounded input/streaming,
   full-closure inventory/transfer, source-topology refusal, private-index
   plumbing, and fixed child creation. Preserve every P20 behavior and test.
3. Implement `isolation.py`'s exact types, preparation lifecycle, raw-tree
   validation, direct byte materialization, independent contexts, cleanup, and
   concurrent seed reads.
4. Add ordinary `tests/test_isolation.py` coverage and reusable literal fixtures
   under `tests/fixtures/isolation/`. Do not make ordinary expected manifests
   by calling the implementation.
5. Close the snapshot-limit conformance exclusion with the exact locked
   complete artifact. Do not touch schema/model/raw verifier.
6. Run the locked suite unchanged and focused ordinary tests in the foreground.
   Apply the seven named controlled breaks one at a time, run the narrowest
   owning test with a process-group hang failsafe, restore, and record exact red
   counts. A timeout is `PROBE_INCONCLUSIVE_HUNG`, never the expected red.
7. Update README/design prose only for the public interface and invariants that
   actually landed. Commit implementation plus LOG and stop. Do not run the
   registered container gate; the controller owns its log, markers, digest, and
   verdict.

## Test constraints copied from AUTHORING.md §3b

- No wall-clock deadline or sleep decides a verdict. Wait on real process/
  context completion; a generous process-group timeout is a hang failsafe only.
- Restore environment and config mutations; every repository/scratch tree is
  fresh per test. No test depends on order, xdist worker, another test, a shared
  temp name, or process-global state left behind.
- No hollow assertions: assert literal bytes, modes, targets, OIDs, parent,
  status, object-store digest, inode independence, absence, cleanup, and exact
  terminal—not only “did not raise”, a private call count, or a log string.
- No coverage-evasion pragma, assertion weakening, real network, ambient clock,
  ambient Git identity/config, or implementation-generated expected manifest.
- Patch the namespace that owns a boundary. Do not monkeypatch synthesized
  attributes or leave module/process globals changed at teardown.

## Environment setup and evidence

No network or live stack is needed. The implementing agent runs the skeleton
and focused suites in its P22 worktree. The independent reviewer runs the
locked suite first, adds at least one combined-axis test, and returns an
ordinary reviewed commit. Only the controller runs `[gates.tester-unified]`'s
exact registered argv from that reviewed commit under the validated background
cgroup and preserves the raw log, digest, target commit, exit, and all four
required phase/completion markers.

## Scope / forbid

P22 builds the reusable committed-object substrate and closes its already-
reserved v4 fixture. It does not integrate snapshots into lanes, resolve or
replay command plans, change configuration/runner/mutation/canary behavior,
change schema/model/verifier, add dependencies, alter the gate, or touch a
consumer project. P23 owns those integrations over this landed API.

## BLOCKED rule

If a named contract cannot be met as specified, a locked asset would need
editing, or scope requires a forbidden file, STOP — write
`BLOCKED: <reason>` to the P22 LOG, commit, and exit. Do not improvise a
workaround. Product choices are recorded as a named `D-<NNN>`; they are not
silently converted into implementation discretion.
