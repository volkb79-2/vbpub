# CIU and CMRU shared workspace-instance plan

Status: proposal after first adversarial review; open decisions remain  
Prepared: 2026-09-19  
Base commit: `c22fa2f40edd3ab90ede20160b2edd0a85bba02e`  
Worktree: `.worktrees/ciu-cmru-workspace-instance`  
Branch: `feat/ciu-cmru-workspace-instance`

This document is the design contract to review before implementation. It records
the problem, the intended ownership boundaries, the observable behavior, and the
proof needed to decide whether the change is safe. It is deliberately separate
from the implementation commits so that a reviewer can reject an unsafe seam
without first untangling a large patch.

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
manual `--define-root` currently repairs the invocation, but every verb must not
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

The library is a first-party package with a releaseable API. Its final package
name is an open decision; the recommended role is a neutral name such as
`workspace-instance`, rather than putting shared behavior in CIU and making CMRU
depend on CIU's product package.

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
reconstruct facts from environment variables. The exact public names are to be
fixed before implementation, but the shape is:

```text
WorkspaceContext
  git_root: LogicalPath
  worktree_path: LogicalPath
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
  git_root                 # Git object/worktree family, if any
  cmru_root                # nearest orchestration file, if any
  ciu_root                 # selected CIU root, if any
  physical_* paths         # explicit host namespace translations
```

The library must distinguish a workspace identity from a root-local identity.
One Git worktree can contain several CIU roots. The workspace identity identifies
the checkout; the root instance identity includes the root offset so that two
roots do not share a network or collide merely because they contain stacks with
the same basename. CMRU uses the workspace identity; CIU uses the root identity
for CIU-owned runtime resources.

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
registered project independently. A single source-first release transaction can
only use one Git common directory unless CMRU gains a separate multi-repository
candidate/promotion model. The recommended first implementation refuses a
selected release set spanning multiple Git families before allocating anything;
it must not treat the CMRU root as a Git root by accident.

## Root and identity resolution contract

### Ordinary CIU stack verbs

For `up`, `down`, `render`, `check`, `graph`, `health`, `clean`, `profiles`,
`env`, and other single-root verbs:

1. An explicit `--define-root`/`--root-folder` wins and is canonicalized.
2. Otherwise walk upward from the invocation directory (or the explicit `--dir`
   stack target) to the nearest CIU root marker.
3. If no marker is found, refuse with a typed `no-ciu-root` diagnostic naming
   the marker and the valid override. Do not use ambient `REPO_ROOT` and do not
   silently treat the current directory as a CIU root.
4. Read that root's generated facts at the exact path
   `<ciu-root>/ciu.instance.generated.toml`. A missing or malformed record is a
   repair/refusal case, not an invitation to read a sibling or ambient file.

`REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `INSTANCE_ID`, and network variables are
outputs in the child process after context selection. They are never root
selection inputs. A conflicting ambient value is reported as context information
or ignored according to the verb; it cannot redirect a destructive operation.

### CIU worktree verbs

`ciu worktree create`, `ensure`, `inspect`, `list`, `rm`, and related operations
need the Git family before they know which nested CIU root is active:

1. An explicit root is canonicalized and used as the containing Git repository.
2. Otherwise derive the Git top level from the invocation directory using Git.
3. Determine the CIU-root selection. If the invocation is below one root, that
   nearest root is the default. If the Git tree contains multiple roots, the
   command must either receive an explicit root selector or use an explicit
   “all roots” mode. It must not select an arbitrary marker by directory scan
   order. The marker set for the selected base commit is verified with
   `git ls-tree <base>` before the worktree is allocated.
4. Create or adopt one Git worktree through the shared library.
5. In the new worktree, recreate the selected relative offset(s) and prepare
   each root independently. An explicit “all roots” request may prepare every
   committed root, but the resulting list must be deterministic and must not
   include test fixtures or ignored personal markers accidentally.

The discovery set must be based on tracked/committed marker files from the
selected base commit. Ignored personal configuration must not silently turn into
a new runtime root. The marker grammar must be closed and documented; the
current `ciu.global.defaults.toml.j2` marker appears in test fixtures in this
repository, so a recursive glob alone is insufficient. The recommendation is a
small committed root marker or an explicit root declaration in the global
defaults file, with a test proving that fixtures are not adopted. A repository
with no CIU markers can still use the generic library and CMRU, but `ciu
worktree` must say that there are no CIU roots to prepare.

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
root, seed identity, or repair a generated record. Machine facts that are needed
internally must have an explicit generated-file owner and exact-path read.

The generated schema must define required keys, schema version, atomic write,
permissions, missing/corrupt behavior, repair authorization, and the exact
translation used by `ciu env print`. Values such as TLS paths may be facts, but
secret contents must never be copied into the generated record. A malformed
`ciu.env` must have no effect on product execution; a malformed generated record
must refuse the operation or enter an explicit repair command, never fall back to
ambient shell state.

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

`--profile` is currently a single-root concept. For a multi-root worktree command,
the implementation must either require an explicit root/profile mapping or refuse
an unqualified profile with a diagnostic. It must not apply one profile to all
roots by accident. The recommended grammar is a repeatable root-qualified form,
for example `--root-profile ciu-root1=dev --root-profile ciu-root2=ci`, but this
needs a final CLI decision before code is written.

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

The existing CMRU branch/worktree `uuid8` is therefore subject to a deliberate
replacement decision. Recommended direction: use the shared workspace identity
in the visible branch/worktree/resource name, retain an internal collision check,
and refuse/retry if an existing name is present. If the shared identity cannot
provide adequate collision freedom for two same-second attempts, retain a
transaction-only nonce in CMRU metadata while keeping Docker-facing names tied to
the shared identity. This decision must be resolved by an acceptance probe before
the allocator API is frozen.

## Verb consistency

The implementation must introduce one context-resolution entry point and audit
every verb against it. The goal is consistent behavior, not one universal parser
that erases legitimate differences.

| Tool | Verb family | Default context | Explicit override | Refusal |
|---|---|---|---|---|
| CIU | stack verbs | nearest CIU root above cwd/`--dir` | `--define-root` | no marker / malformed generated facts |
| CIU | worktree verbs | Git root plus the cwd-selected nested root | explicit root selector or explicit `all` / path / base | no Git root / ambiguous root / collision / partial root preparation |
| CIU | `env generate` | selected CIU root | explicit root | missing physical facts or ambiguous root |
| CMRU | project-aware verbs | nearest CMRU orchestration root; omitted target means cwd project or configured estate | explicit config and positional target list | no central config / unregistered project / path escape |
| CMRU | release/build transaction | selected CMRU root plus Git root for each project | explicit orchestration/config | non-fast-forward, runner/runtime declaration error |

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
   and build transactions, add identity labels/evidence, and resolve the visible
   name/nonce decision.
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
| cwd wins over stale ambient root | invoke from `/workspaces/vbpub` with `REPO_ROOT=/workspaces/dstdns` | selected root is dstdns |
| explicit root wins | pass `--define-root` from an unrelated cwd | explicit root is ignored |
| no silent fallback | invoke with no marker and no explicit root | cwd/ambient root is invented |
| `ciu.env` is export-only | sibling `ciu.env` has a different identity | sibling identity is adopted |
| generated facts are exact-path authority | remove/mangle selected facts | another checkout is read |
| nested root discovery | two committed markers below one Git root | only the first marker is prepared |
| root isolation | same stack basename below two roots | both attach to one network/project |
| multi-root rollback/resume | fail preparation of root 2, then resume | root 1 is lost or duplicated |
| lock scope | concurrent operations on different roots | all roots serialize unnecessarily or deadlock |
| Git lock scope | concurrent worktree allocation in one Git family | duplicate branch/worktree is admitted |
| CMRU/CIU identity relationship | release worktree starts a CIU root | runtime names cannot be traced to release worktree |
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
  Worktree creation needs a cwd-bound or explicit root selector, an explicit
  `all` mode, and base-commit validation with `git ls-tree` before allocation.
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

## Open decisions requiring review

These are product/contract decisions, not implementation details:

1. **Shared package name and release ownership.** Should the library be a new
   first-party project with its own CMRU entry, or a versioned internal package
   shipped by one existing tool? The recommendation is a new neutral project so
   CMRU never depends on CIU's product package.
2. **Identity and collision algorithm.** Can the existing CIU path-derived ID
   safely replace CMRU's `uuid8` for every visible name, or must CMRU retain a
   transaction-only nonce? Decide from a same-second concurrent allocation probe.
3. **Multi-root selection.** Should the default for a Git-root worktree command
   be the cwd-selected root, with explicit `--all-roots` for recursive creation?
   The command must never choose a marker by scan order.
4. **Multi-root profile grammar.** Require explicit `root=profile` mappings,
   support one profile per root, or keep profile selection out of multi-root
   worktree creation and require a later root-scoped command.
5. **Root marker.** Is the committed
   `ciu.global.defaults.toml.j2` marker sufficient, or should CIU add a small
   unambiguous root marker that does not require rendering a config template?
6. **Generated-facts migration.** Should old generated records be repaired
   automatically on first use, or should the tool refuse and require an explicit
   `ciu env generate`? The safer default is refusal for destructive/runtime verbs
   and an explicit repair command that records what it changed.
7. **CMRU runtime declaration location.** Put the runtime under each project's
   `cmru.toml`, under a dedicated `[runtime]` table, or per runner step? The
   recommendation is project-level `[runtime]` with an explicit kind and no
   inference, while allowing a step to state which declared runtime it consumes.
8. **CMRU release transaction identity.** Which metadata must survive after a
   successful release so an operator can map retained logs/evidence to the source
   worktree once the branch is deleted?
9. **Cross-Git orchestration.** Preserve central-config discovery across
   repositories for read-only/project-local operations, but refuse one aggregate
   release spanning multiple Git common directories until a multi-repository
   candidate model exists, or design that model now.

No implementation agent should resolve these by intuition. The adversarial
review and the acceptance probes must either close each item or return it here as
a blocking product decision.

## Out of scope

- A generic CMRU Docker/Compose provider for arbitrary non-CIU projects.
- Automatic adoption of ignored or uncommitted CIU root markers.
- Moving CMRU orchestration policy into CIU or making CIU understand release
  tags/publication.
- A redesign of CIU's existing Compose, secret, governance, or shared-infra
  semantics beyond replacing their root/identity input path.
- Running gates from this devcontainer or treating local cockpit tests as proof.
- A release or merge before the adversarial review closes the open decisions.
