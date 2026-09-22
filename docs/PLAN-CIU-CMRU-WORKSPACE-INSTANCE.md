# CIU and CMRU shared workspace-instance plan

Status: implemented; adversarial follow-up is in
[`REVIEW-CIU-CMRU-WORKSPACE-INSTANCE.md`](REVIEW-CIU-CMRU-WORKSPACE-INSTANCE.md)
and final aggregate gates are pending on the latest review fixes.
Prepared: 2026-09-19
Updated: 2026-09-22
Base commit: `467a889f70e5eac6fd6472c4fcdcbc0d95ba777a`
Worktree: `.worktrees/ciu-cmru-workspace-instance`
Branch: `feat/ciu-cmru-workspace-instance`

This document began as the design contract for review before implementation. It
records the problem, the intended ownership boundaries, the observable
behavior, and the proof needed to decide whether the change is safe. The
implementation and the sibling adversarial-review ledger now preserve that
contract as an inspectable worktree diff.

The original proposal is committed at `0e126afd`; the review edits after that
commit are retained as the rationale for the implementation. The current
worktree contains the implementation, adapted tests, and closure evidence.

## Response to the review

The following decisions are now part of the proposal:

- The shared project lives at `libraries/worktree` as an internal dependency.
  It is not a separately registered CMRU project or independently released
  wheel. CIU and CMRU package and test the same library source in their own
  release transactions, so a released pair is based on one estate commit and
  does not need a third release order or compatibility tag.
- The user-facing root override is `--root-folder`. The proposal does not
  introduce a second spelling called `--define-root`.
- `ciu worktree create` is a workspace operation. It discovers and prepares all
  committed CIU roots in the selected Git worktree by default. There is no
  worktree verb whose normal meaning is “prepare only one nested root”. A
  root-specific operation is an ordinary stack verb run from that root.
- Ordinary stack verbs select the nearest root above `pwd` (or `--dir`). An
  explicit `--root-folder` is an override for the containing root. Ambient
  `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, and identity variables are ignored as
  selectors.
- The existing `add` spelling has no distinct lifecycle meaning: it is an old
  synonym for `create`. The new contract uses `create`, `adopt`, `ensure`,
  `inspect`, `list`, `rm`, `up`, `exec`, `lease`, `branches`, and `reap`; the
  implementation will remove `add` rather than preserve two names for one
  operation. `create` allocates a new linked checkout, `adopt` records an
  already-existing checkout, and `ensure` resumes or repairs the record for a
  checkout that is already allocated; those are the distinct lifecycle states
  the names need to communicate.
- Workspace and root identities are six-character, lower-case base-36 values
  derived from canonical physical paths. A workspace identity normally uses the
  worktree directory; an adapter that must expose the identity in a name that
  contains it supplies an explicit canonical allocation path, which the shared
  record persists and rechecks. A nested CIU root gets a different identity from
  its own path. Runtime names include both the workspace identity and the root
  identity, so a container remains traceable to its enclosing worktree even
  when the CIU root is below it. A detected collision refuses and reports both
  paths; it never silently lengthens or replaces an identity.
- A recursive scan is useful only for discovering committed root markers. It
  cannot use `ciu.global.instance.toml.j2` as the marker: that file is optional,
  gitignored, and user-owned, so it may be absent in a new worktree and must not
  be overwritten with derived identity. The scan therefore enumerates the
  committed root marker grammar and writes each root's generated facts through
  `ciu env generate`.
- `--profile` is removed from `ciu worktree create`. Creation prepares every
  committed root in the selected Git worktree with that root's declared
  defaults; a non-prepared root cannot be safely started. Profile selection
  remains a root-scoped option for ordinary verbs, with the root selected from
  the nearest root above the invocation directory or an explicit
  `--root-folder`.
- The project-level runtime declaration is explicit (`[runtime] kind =
  "none"` or `"ciu"`); CMRU does not infer a runtime from Docker or Compose.
- Successful releases continue to retain logs and artifacts by default. The
  existing explicit discard options remain the only opt-outs.
- A CMRU root may be outside or above one or more Git repositories. Each
  registered project gets its own Git-family workspace context, so a CMRU
  transaction may coordinate several Git families without pretending that one
  worktree contains them all. A project path equal to the CMRU root is valid;
  containment is inclusive. The known limitation is that publication across
  independent repositories cannot be one Git atomic commit, so transaction
  state and recovery evidence must identify each project's promotion result.

The implementation is present in this worktree. The sibling review ledger
records the latest adversarial findings, their closures, local test evidence,
and the remaining final aggregate gate runs. Earlier checkpoints in that
ledger remain historical evidence, not current branch status.

## Why this change is needed

CIU and CMRU both create an isolated Git worktree, but they currently implement
the isolation independently. CIU owns the useful runtime identity machinery
(worktree records, collision admission, generated instance facts, network and
container naming); CMRU owns a separate release transaction allocator and adds a
timestamp plus `uuid8` to its branch and worktree names. The two mechanisms have
different answers to the same questions:

1. Which repository or project is the operator standing in?
2. Which checkout owns a runtime resource?
3. Which path is the logical path visible to the agent and which is the physical
   path visible to Docker?
4. Which lock protects allocation and cleanup?
5. How can an operator identify a container or network from the worktree that
   created it?

The duplication is already producing a concrete wrong-result failure. This
devcontainer has ambient `REPO_ROOT=/workspaces/dstdns`, while the operator is in
`/workspaces/vbpub`. The checkout's ignored `ciu.env` contains the correct
`/workspaces/vbpub` value, but `ciu worktree` resolves the root before it has a
workspace context and can therefore select the unrelated ambient repository. A
manual `--root-folder` currently repairs the invocation, but every verb must not
depend on an operator remembering that repair.

There is a second source of confusion in the current contract. `ciu.env` is a
useful shell export for a human, but it is also treated by portions of the
implementation as an input/cache. That permits a sourced sibling checkout to
leak identity into a new worktree. The generated
`[ciu.instance.generated]` TOML already provides the durable, exact-path record
needed for internal reads. One authoritative record is easier to reason about
and makes the ambient shell a transport/output concern instead of a root or
identity selector.

Finally, CMRU's release worktree can contain several CIU roots, including roots
below the Git top level and several stacks under each root. A release tool should
not need to know that a project happens to use CIU. It should create one generic
isolated workspace; CIU should discover and prepare its own roots inside that
workspace. A non-CIU project with a private Docker naming scheme should not be
silently renamed by CMRU, because CMRU cannot prove ownership of those resources.

## Intended outcome

Create a small, separately testable workspace-instance library used by both CIU
and CMRU. The library owns Git-worktree lifecycle, canonical path identity,
collision admission, instance records, leases, and cleanup. It does not know
Compose, CIU TOML, release tags, Docker commands, or project-specific runners.

CIU becomes an adapter over that library. It resolves one stack root for ordinary
stack verbs, or enumerates all committed CIU roots for a worktree operation. It
then gives every root its own generated identity facts, network namespace, local
overlay, and root-level locks while the Git worktree and workspace identity are
shared.

CMRU becomes another adapter. Its release/build transaction uses the same generic
workspace allocator and carries the resulting workspace identity into the
project runner environment. It does not inspect CIU files or special-case CIU.
Projects that need a multi-container runtime declare the supported `ciu` runtime;
build-only and one-shot commands declare `none`. An unsupported project-owned
runtime is refused when it is declared. A generic Docker runtime provider is
intentionally outside this change because CMRU cannot make arbitrary project
container names, networks, volumes, or cleanup safe.

The result is one mental model:

```text
invocation directory
        │
        ├── workspace resolver ──> Git root / selected CIU root(s)
        │                           │
        │                           ├── shared workspace instance
        │                           └── root-local CIU instances
        │                                ├── generated facts
        │                                ├── network namespace
        │                                └── root lock
        │
        └── CMRU runner context ──> same workspace instance, project-owned runtime adapter
```

## Boundaries and ownership

### Shared workspace-instance library

The library is an internal first-party package under `libraries/worktree`. It
is deliberately neutral rather than putting shared behavior in CIU and making
CMRU depend on CIU's product package. CIU and CMRU include the same source in
their own wheels and release gates; it has no independent registration or
release transaction.

It owns:

- Git top-level discovery and the primary worktree mapping;
- canonical logical and physical path identity, with explicit namespace labels;
- creation, adoption, inspection, resume, and removal of a Git worktree;
- a collision-safe workspace instance identifier and a durable workspace record;
- a shared worktree-family allocation lock;
- record versioning, lease state, ownership checks, and cleanup ordering;
- generic labels and an opaque resource namespace exposed to an adapter;
- stable error categories for root mismatch, path escape, collision, stale
  record, and cleanup refusal.

It does not:

- read `ciu.env` or any product-specific config;
- discover CIU markers or render TOML;
- call Docker, Compose, GitHub, BuildKit, or a project command;
- invent a runtime name for a project it does not own;
- use an ambient `REPO_ROOT` as a fallback.

The library must expose a typed context rather than requiring consumers to
reconstruct facts from environment variables. `source_git_root` is the top
level of the checkout selected as the base for the operation; `worktree_path`
is the top level of the checkout being created or operated on. They are equal
for a primary checkout and different for a linked worktree. Neither is the
worktree-family lock key: `git_common_dir` is the shared object/ref-store
anchor returned by Git and is what serializes the family. The exact public
names are to be fixed before implementation, but the shape is:

```text
WorkspaceContext
  source_git_root: LogicalPath
  worktree_path: LogicalPath
  git_common_dir: LogicalPath
  physical_worktree_path: PhysicalPath
  workspace_id: str
  branch: str
  base_commit: str
  record_path: Path
  namespace: ResourceNamespace

RootContext (CIU adapter)
  workspace: WorkspaceContext
  ciu_root: LogicalPath
  ciu_root_offset: RelativePath
  root_instance_id: str
  physical_ciu_root: PhysicalPath
```

The result must also carry the CMRU root when CMRU is the caller, but these
scopes are not interchangeable:

```text
InvocationContext
  invocation_dir
  source_git_root          # checkout selected as the Git source, if any
  git_common_dir           # Git object/ref family lock anchor, if any
  worktree_path            # selected/allocated checkout, if any
  cmru_root                # nearest orchestration file, if any
  ciu_root                 # selected CIU root, if any
  physical_* paths         # explicit host namespace translations
```

The library must distinguish a workspace identity from a root-local identity.
One Git worktree can contain several CIU roots. Each identity is the first six
characters of a collision-checked lower-case base-36 digest of its canonical
physical path: the workspace digest uses the worktree directory and the root
digest uses the CIU-root directory. This retains CIU's existing path-derived
identity idea while expanding its alphabet from hexadecimal. A collision is a
hard refusal naming both paths. The workspace identity identifies the
checkout; the root instance identity identifies the nested root. CMRU uses the
workspace identity. CIU resource names include both identities (omitting the
duplicated component when the paths are identical), so a nested-root container
still visibly belongs to its enclosing worktree.

### CIU adapter

CIU retains ownership of:

- CIU root-marker discovery;
- `ciu.global.instance.toml.j2` overlays;
- `ciu.instance.generated.toml` facts;
- Compose project, network, stack, service, and container names;
- `ciu.env` generation and manual `ciu env print` output;
- CIU stack locks, shared-infrastructure topology, and CIU cleanup policy.

The adapter receives a `WorkspaceContext` and never re-resolves the root from
`os.environ` after that point. Every downstream verb carries the context through
the pipeline. “One shared resolver” means one context model and one canonical
path/identity implementation; it does not mean one ambiguous function that
returns one root for every purpose. The API must expose separate entry points for
current-directory CIU stack resolution, Git-family resolution, CMRU orchestration
resolution, and physical-path translation.

### CMRU adapter

CMRU retains ownership of:

- central orchestration discovery and registered-project selection;
- release ordering, version/tag policy, candidate branch promotion, and
  publication;
- project runner commands and their declared environment;
- tester-gate and wheel-builder configuration;
- log, artifact, and evidence retention;
- runtime declaration validation.

The generic allocator is the only worktree constructor. CMRU passes a transaction
purpose and project scope to it, then writes CMRU transaction metadata through the
library's opaque record extension or a CMRU-owned sidecar. The shared record must
not become a dumping ground for release policy.

CMRU's existing promise that a central orchestration file may sit above several
repositories must be made explicit. A read-only `resolve` can operate on each
registered project independently, and a release transaction creates one
workspace context per selected project/Git family. The transaction may
coordinate several Git families, but it must never treat the CMRU root as a Git
root or pretend that independent repositories share one worktree. Promotion
and recovery evidence is recorded per project because Git cannot make those
independent commits atomic.

## Root and identity resolution contract

### Ordinary CIU stack verbs

For `up`, `down`, `render`, `check`, `graph`, `health`, `clean`, `profiles`,
`env`, and other single-root verbs:

1. An explicit `--root-folder` wins and is canonicalized.
2. Otherwise walk upward from the invocation directory (or the explicit `--dir`
   stack target) to the nearest CIU root marker.
3. If no marker is found, refuse with a typed `no-ciu-root` diagnostic naming
   the marker and the valid override. Do not use ambient `REPO_ROOT` and do not
   silently treat the current directory as a CIU root.
4. For read-only and inspection verbs, read that root's generated facts at the
   exact path `<ciu-root>/ciu.instance.generated.toml`. A missing or malformed
   record is a refusal, not an invitation to read a sibling or ambient file.
   For a runtime-start verb such as `up`, first derive the physical facts and
   perform the equivalent of `ciu env generate`, atomically refreshing the
   selected root's generated record; if derivation fails, refuse before any
   runtime resource is started. This automatic refresh is the only start-time
   repair path and is also available explicitly at any time.

`REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `INSTANCE_ID`, and network variables are
outputs in the child process after context selection. They are never root
selection inputs. A conflicting ambient value is reported as context information
or ignored according to the verb; it cannot redirect a destructive operation.

### CIU worktree verbs

`ciu worktree create`, `ensure`, `inspect`, `list`, `rm`, and related operations
need the Git family before they know which nested CIU root is active:

1. An explicit `--root-folder` identifies and canonicalizes the containing Git
   repository. It does not reduce the operation to one nested CIU root.
2. Otherwise derive the Git top level from the invocation directory using Git.
3. Enumerate every committed CIU root in that Git worktree using the closed
   marker grammar. The all-roots behavior is implicit and deterministic; there
   is no `--all` switch and no root selector on a worktree lifecycle verb. The
   marker set for the selected base commit is verified with `git ls-tree <base>`
   before the worktree is allocated.
4. Create or adopt one Git worktree through the shared library.
5. In the new worktree, recreate and prepare every discovered root
   independently. A root-specific repair or profile operation is performed by
   an ordinary CIU verb from that root, using its nearest-root resolver.

The discovery set must be based on tracked/committed marker files from the
selected base commit. Ignored personal configuration must not silently turn into
a new runtime root. A recursive scan is sufficient only when restricted to
tracked paths and validated against the closed marker grammar; the ignored,
optional `ciu.global.instance.toml.j2` is not a root marker. A repository with
no CIU markers can still use the generic library and CMRU, but `ciu worktree`
must say that there are no CIU roots to prepare.

### CMRU invocation context

CMRU keeps its nearest-upward `cmru.orchestration.toml` discovery. The selected
orchestration file establishes the **CMRU root**, even if it is above several Git
repositories. CMRU resolves project selection from that root and then passes the
selected repository path to the shared allocator. It must not infer a CIU root
or scan for CIU markers. A project runner that needs CIU enters its own declared
runtime adapter and performs CIU root discovery inside the prepared checkout.

This keeps the two notions separate:

- CMRU root: orchestration and project registry scope;
- Git root: worktree and object-store family;
- CIU root: one nested CIU configuration/runtime scope.

## Artifact and file authority

The implementation must make file roles explicit and test them mechanically.
The present generated TOML contains only six identity facts while CIU still
consumes machine facts from `ciu.env`. The proposed cutover therefore needs a
versioned generated-facts schema, not only a change to one reader. The
recommended design is to extend `ciu.instance.generated.toml` with a versioned
machine-facts table containing every non-secret fact CIU consumes internally;
`ciu.env` becomes a generated shell translation of that record. If the review
rejects that expansion, the alternative is a second explicitly named generated
machine-facts document. There must not be an undocumented third source.

| File | Owner | Internal read authority | Human role |
|---|---|---|---|
| `ciu.instance.generated.toml` | CIU | sole source of generated identity facts, exact path | none; generated |
| `ciu.global.instance.toml.j2` | CIU/user | instance overlay for the selected CIU root | deliberate per-worktree overrides |
| `ciu.env` | CIU | never read by internal root/identity resolution | shell export via `source`/`ciu env print` |
| shared workspace record | workspace library | authoritative lifecycle/ownership record | inspect/resume evidence |
| CMRU transaction metadata | CMRU | release state and scope only | release resume/diagnostics |

`ciu env generate` may continue to write `ciu.env` so existing manual shell
workflows remain useful, but no CIU internal code may load that file to select a
root, seed identity, or repair a generated record. A runtime-start verb invokes
the same generator and may overwrite derived facts for its selected root after
physical-fact derivation succeeds. Machine facts that are needed internally
must have an explicit generated-file owner and exact-path read.

The generated schema must define required keys, schema version, atomic write,
permissions, missing/corrupt behavior, repair authorization, and the exact
translation used by `ciu env print`. Values such as TLS paths may be facts, but
secret contents must never be copied into the generated record. A malformed
`ciu.env` must have no effect on product execution; a malformed generated record
must be regenerated by a runtime-start verb from physical facts or refused when
the verb cannot regenerate it, never falling back to ambient shell state.

The migration must inventory and remove or narrow every internal `ciu.env` read,
including bootstrap helpers, child-environment construction, compose environment
construction, worktree ensure, hooks, and diagnostics. A grep-only inventory is
not enough: the test suite must prove that a sibling `ciu.env` and stale ambient
variables cannot change the selected root or runtime identity.

## CIU multi-root and multi-stack behavior

For a Git tree such as:

```text
/repo/ciu-root1/ciu-stack1
/repo/ciu-root1/ciu-stack2
/repo/ciu-root2/ciu-stack1
/repo/ciu-root2/ciu-stack2
```

`ciu-root1` and `ciu-root2` are separate CIU roots and receive separate root
instance identities and networks. Stacks below one root may share that root's
network according to existing CIU configuration. Compose project names remain
unique for every stack; the naming algorithm must add a root-relative component
or another deterministic discriminator when two roots contain the same stack
basename. A basename collision must fail closed rather than attach two stacks to
one project.

One `ciu worktree` allocation creates one Git worktree and then prepares every
root discovered in that checkout. The lifecycle record has one workspace entry
with a list of root entries, each containing its offset, generated-facts path,
root instance ID, network, preparation state, and failure reason. Preparation is
idempotent and resumable per root. A failure in root 2 must not cause root 1 to
be silently forgotten; cleanup must inspect all root states before removing the
Git worktree.

`--profile` is a single-root option. The resolver selects the nearest CIU root
above the invocation directory and interprets the profile name against that
root's own profile table. `ciu worktree create` does not accept `--profile`:
creation prepares every root with its declared defaults, and a later ordinary
root-scoped command can select a profile for one root without applying it to
another root accidentally.

## Locks and concurrency

There are two different critical sections and they must remain distinct:

1. A Git-family allocation lock protects `git worktree add/remove`, branch
   allocation, shared records, and inventory. It is keyed by the Git common
   directory, as the current CIU `ciu-worktree-allocation.lock` is.
2. A per-CIU-root lock protects generated facts, root overlays, network
   admission, and root-local cleanup. Its key is the root-relative offset in the
   Git family, never only the Git repository directory.

When one operation touches multiple roots, acquire root locks in sorted offset
   order and release them in reverse order. A root operation must never lock root
   1, inspect root 2, and then acquire root 2 in an order that can deadlock with
   the reverse operation. The generic library must not own the CIU root lock; CIU
   owns it because only CIU knows which generated resources it protects.

The canonical lock namespace is derived from `git rev-parse --git-common-dir`.
The acquisition order is: CMRU release lock (when a release transaction is
being serialized), then the shared Git-family allocation lock, then sorted
CIU-root locks. The library must state whether each lock is blocking, reentrant,
and held across external Git/Docker work. The recommended rule is to hold the
Git-family lock only across local Git/record mutations, never across a gate or
network operation; root locks may cover atomic generated-file/runtime admission
but not a long-running test. This prevents a CMRU-to-CIU call from deadlocking a
direct CIU operation. CMRU's candidate branch and publication state must not be
conflated with CIU's runtime lock.

## CMRU runtime policy

The first implementation supports two declared runtime kinds:

| Runtime | Meaning | Isolation owner |
|---|---|---|
| `none` | build/test command is self-contained or one-shot; CMRU provides the workspace context only | project command / tester lane |
| `ciu` | project owns one or more CIU roots and needs an isolated multi-container stack | CIU adapter inside the released worktree |

CMRU must not attempt to parse Compose files or guess whether arbitrary `docker`
commands create a stack. The runtime value is a closed declaration loaded before
the runner starts. `none` means CMRU owns no external runtime lifecycle; the
project command must clean up anything it creates. `ciu` means the project step
is responsible for invoking the CIU commands and their explicit `up`, health,
and cleanup behavior in the CMRU-provided worktree context. CMRU supplies the
shared workspace identity and preserves evidence; it does not scan CIU roots or
invent Compose operations. This keeps CMRU from becoming coupled to CIU's stack
model while making the supported adoption path explicit.

An unknown runtime or a declared provider that CMRU does not support is refused
before Docker starts, with the actionable path to adopt CIU or run the stack
outside CMRU lifecycle ownership. The tool cannot mechanically detect every
arbitrary `docker run` hidden in a shell command; this limitation must be stated
in the CMRU contract rather than presented as an enforcement guarantee. A future
provider interface can add stronger lifecycle ownership without changing the
shared allocator.

The CMRU runner receives the shared workspace identity through a structured
environment/context contract. It must also expose the release branch and source
commit in labels/evidence so an operator can connect a runtime resource to the
release attempt. CIU-generated container and network names should use the same
workspace/root identity. CMRU must not independently append an unrelated UUID to
the Docker-facing namespace.

The existing CMRU branch/worktree `uuid8` is replaced by the six-character
shared workspace identity in the visible branch, worktree, and resource name.
The allocator retains a collision check and refuses with both paths if the
identity is already claimed; it does not add an unrelated UUID. The allocation
identity is stable because its canonical input is recorded in the shared
workspace record, while the CMRU transaction record still carries its full
branch and source metadata for recovery.

## Verb consistency

The implementation must introduce one context-resolution entry point and audit
every verb against it. The goal is consistent behavior, not one universal parser
that erases legitimate differences.

| Tool | Verb family | Default context | Explicit override | Refusal |
|---|---|---|---|---|
| CIU | stack verbs | nearest CIU root above cwd/`--dir` | `--root-folder` | no marker / failed start-time generation / malformed facts on non-start verbs |
| CIU | worktree verbs | Git root plus all committed nested roots | `--root-folder` selects the containing Git family; no nested-root selector | no Git root / invalid marker / collision / partial root preparation |
| CIU | `env generate` | selected CIU root | `--root-folder` | missing physical facts or ambiguous root |
| CMRU | project-aware verbs | nearest CMRU orchestration root; omitted target means cwd project or configured estate | explicit config and positional target list | no central config / unregistered project / path escape |
| CMRU | release/build transaction | selected CMRU root plus one Git-family context per project | explicit orchestration/config | non-fast-forward, runner/runtime declaration error |

All CIU and CMRU parser/configuration diagnostics retain the estate version
headline as line 1. `--version` is accepted as a top-level compatibility spelling
by both tools and is independent of root discovery. Normal command output stays
unchanged. CMRU's positional target grammar (`omitted`, `all`, one name, or
comma-separated names) must remain distinct from CIU's root selection grammar.

## Migration sequence

The change should land in small, reviewable commits. A suggested sequence is:

1. **Contract and probes.** Add the shared library API/spec skeleton, the path and
   identity decision table, and failing acceptance fixtures for stale ambient
   `REPO_ROOT`, sibling `ciu.env`, nested roots, and same-basename stacks.
2. **Shared allocator.** Move the generic Git worktree/record/lock mechanics out
   of CIU and add library tests for create/adopt/list/resume/remove, path
   containment, collision, stale records, and concurrent allocation.
3. **CIU resolver migration.** Replace all root/identity selection paths with the
   shared context. Remove internal `ciu.env` reads. Preserve `ciu env generate`
   and manual `ciu.env` output as an explicit export surface.
4. **CIU multi-root worktrees.** Rework worktree create/ensure/inspect/clean to
   enumerate committed roots, write root-local records, lock roots in order, and
   resume partial preparation safely.
5. **CMRU allocator migration.** Use the shared workspace allocator for release
   and build transactions, add identity labels/evidence, and run the path-ID
   collision and namespace acceptance probes.
6. **CMRU runtime declaration.** Add and validate `none`/`ciu`, refuse unsupported
   project-owned multi-container runtimes, and document the known limitation.
7. **Docs and compatibility.** Update CIU and CMRU SPEC, README, DESIGN-GUIDE,
   CONSUMERS, templates, changelogs, and trove references. Add tests that load
   every config example and resolve every cross-document anchor.
8. **External gates.** Run the complete CIU and CMRU lanes through their declared
   `run-gate.py` commands in tester-unified, with assay resume/progress. Run any
   Docker/VM acceptance lane in its designated container or VM. No evidence from
   this cockpit is a ship signal.

Each implementation commit must state the affected S-ID/spec section and keep
the public docs in the same commit as the capability they describe. The shared
library, CIU, and CMRU should be releasable in dependency order: library first,
then CIU/CMRU consumers, followed by the estate release.

## Acceptance matrix

The final review must be able to point from every behavior to a real oracle.

| Behavior | Required oracle | Negative case |
|---|---|---|
| cwd wins over stale ambient root | invoke from `/workspaces/vbpub` with `REPO_ROOT=/workspaces/dstdns` | selected root is dstdns or the command refuses despite a valid cwd root |
| explicit root wins | pass `--root-folder` from an unrelated cwd | explicit root is ignored |
| no silent fallback | invoke with no marker and no explicit root | cwd/ambient root is invented |
| `ciu.env` is export-only | sibling `ciu.env` has a different identity | sibling identity is adopted |
| generated facts are exact-path authority | remove/mangle selected facts | another checkout is read |
| nested root discovery | two committed markers below one Git root | an untracked/ignored overlay is adopted as a third root |
| root isolation | same stack basename below two roots | both attach to one network/project |
| multi-root rollback/resume | fail preparation of root 2, then resume | root 1 is lost or duplicated |
| lock scope | concurrent operations on different roots | all roots serialize unnecessarily or deadlock |
| Git lock scope | concurrent worktree allocation in one Git family | duplicate branch/worktree is admitted |
| CMRU/CIU identity relationship | release worktree starts a nested CIU root | runtime names omit the enclosing workspace identity |
| CMRU non-CIU safety | project runs arbitrary multi-container Docker | CMRU silently rewrites/cleans unknown resources |
| runtime declaration | `none`, `ciu`, unknown, missing | unknown falls through to Docker default |
| path containment | orchestration/project/root paths use `..` or symlinks | path escapes selected root |
| version/diagnostic contract | top-level, verb, nested help and errors | headline is absent or not first |
| installed-wheel behavior | invoke from project dir using installed CMRU | source checkout is accidentally required |
| external gate discipline | run complete lanes in tester-unified/VM | cockpit green is treated as release evidence |

The reviewer must add at least one combined-axis case not named by the
implementation tests, for example: stale ambient `REPO_ROOT` plus a sibling
`ciu.env`, two nested roots with identical stack basenames, or a CMRU release
worktree whose runner starts CIU from a subdirectory.

## First adversarial review

The fresh specification review found four blockers and several high-severity
gaps. They are incorporated into the contract above rather than left as review
comments:

- “One resolver” must return separate invocation, Git, CMRU, CIU, and physical
  path facts; CIU's current `dev` and deploy resolvers have intentionally
  different legacy behavior that must be cut over together.
- Recursive marker discovery cannot select an arbitrary root in this monorepo.
  Worktree creation therefore prepares all committed roots deterministically,
  validates the marker set with `git ls-tree` before allocation, and leaves
  root-specific operations to ordinary verbs selected by cwd.
- `[ciu.instance.generated]` currently covers identity only. The plan now
  requires a versioned machine-facts authority and explicit missing/corrupt
  behavior before claiming that `ciu.env` is export-only.
- `runtime = none|ciu` needs semantics. The plan makes it a closed declaration:
  CMRU supplies shared workspace context, while project steps own their CIU
  lifecycle; CMRU does not parse Compose or infer hidden Docker resources.

The review also identified the lock ordering, physical-root identity and
mount-namespace mapping, CMRU's cross-Git orchestration promise, shared-package
installation/versioning, `ciu.env` compatibility, and runtime cleanup/evidence
as contract items. They are represented below as acceptance cases or open
decisions. The reviewer made no product edits and ran no cockpit tests.

## Decisions closed; implementation evidence

The five decisions raised by the adversarial review are closed:

1. `libraries/worktree` is an internal dependency included and tested by CIU
   and CMRU; it has no independent project registration, wheel, or release
   order.
2. `ciu worktree create` has no `--profile` and prepares every discovered root
   with its declared defaults. A root that was not prepared cannot be safely
   started. Ordinary root-scoped verbs select the nearest root from cwd or
   `--dir`, with `--root-folder` as the explicit override.
3. The existing committed root-marker grammar is sufficient. No dedicated
   marker file is introduced; the ignored instance overlay remains user-owned.
4. Six-character lower-case base-36 path-derived IDs, collision refusal, and
   the logical-to-physical namespace contract are accepted.
5. Starting CIU automatically runs the equivalent of `ciu env generate` and
   deliberately refreshes/overrides derived facts. `ciu env generate` remains
   available for manual refresh at any time. Internal resolution still ignores
   `ciu.env`.

The implementation probes are closed by the sibling review and its executable
oracles:

- same-host allocation/collision, path translation, tracked-marker discovery,
  generated-facts refresh, lifecycle vocabulary, ambient-selector refusal, and
  CIU/CMRU wheel-content checks are covered by the CIU, CMRU, and library test
  slices listed in the review ledger;
- the neutral lifecycle is used by both consumers, including the legacy-adopt
  bridge, and the packaged shared modules are byte-identical;
- CIU, CMRU, and library local full-suite equivalents report 100% line and
  branch coverage, with Hypothesis property suites green.

At the 2026-09-20 checkpoint, the cockpit lacked the host-provided
`$CGROUP_PARENT_DEV_GATES` value and the tester-unified lanes refused before
launch. That checkpoint is superseded by the 2026-09-22 continuation in the
review ledger: the Assay self-hosted lane now passes with the explicit
`dev-gates.slice` parent, and the final CMRU aggregate plus CIU R0-R3 gate runs
are being performed serially on the latest review fixes. No gate is run in the
devcontainer itself; its work is a cockpit for the dedicated gate launcher.

## Out of scope

- A generic CMRU Docker/Compose provider for arbitrary non-CIU projects.
- Automatic adoption of ignored or uncommitted CIU root markers.
- Moving CMRU orchestration policy into CIU or making CIU understand release
  tags/publication.
- A redesign of CIU's existing Compose, secret, governance, or shared-infra
  semantics beyond replacing their root/identity input path.
- Running gates from this devcontainer or treating local cockpit tests as proof.
- A release or merge before the adversarial review closes the remaining product
  decisions and acceptance probes.
