# Worktree library specification

The `worktree` package is the neutral, internal workspace-instance substrate
shared by CIU and CMRU. It is packaged from this directory into both products;
it is not a third release target and it never reads either product's config.

## Contract

The package exposes these stable operations:

| API | Contract |
|---|---|
| `discover_git_context` | Given a path, returns the Git top level, canonical common directory, branch, and HEAD; Git errors are typed refusals. |
| `resolve_invocation` | Given a path and optional `root_folder`, resolves Git facts only. It never guesses a CIU or CMRU root and never reads ambient root variables. |
| `create_workspace` | Given a source, target, and optional `identity_path`, takes the Git-family lock, validates the branch/path, creates one linked checkout, writes one atomic record, and rolls back the checkout if record creation fails. `identity_path`, when supplied, is the canonical, durable identity input recorded in metadata. |
| `adopt_workspace` | Given a source, target, and optional `identity_path`, records an already-existing linked checkout only after proving it belongs to the source Git family. |
| `ensure_workspace` | Reopens a record only when checkout, branch, common directory, and the record's identity input still agree. |
| `inspect_workspace` | Reads the record and fresh Git facts without repairing state. |
| `remove_workspace` | Given a handle and cleanup callback, refuses active leases, runs adapter cleanup first, and removes Git state and the record only after cleanup succeeds. |
| `remove_unrecorded_workspace` | Compatibility-only bridge for a legacy checkout with no shared record; adopts it through the same record/lifecycle path and immediately removes it. |
| `acquire_lease` / `release_lease` | Writes bounded or perpetual ownership claims under the same family lock. |

`WorkspaceContext` is the typed handoff between the library and an adapter. It
contains the logical checkout path, physical checkout path, Git-family lock
anchor, branch, base commit, six-character lower-case base-36 workspace
identity, durable record path, and opaque resource namespace. Adapters must not
reconstruct these facts from environment variables.

The package also exports the supporting record and value types used at the
adapter boundary: `WorkspaceRecord`, `WorkspaceState`, `WorkspaceError`,
`WorkspaceCollisionError`, `Lease`, `ResourceNamespace`, and
`InvocationContext`. The record constants `CURRENT_RECORD_VERSION` and
`WORKSPACE_RECORD_DIR` are public so an adapter can diagnose or locate evidence
without copying the record grammar. The supporting
operations `canonical_path`, `physical_path`, `workspace_id_for_path`,
`workspace_lock`, `read_record`, `write_record`, and `list_workspaces` are the
typed, neutral primitives behind the lifecycle functions above; they do not
select a product root or read ambient environment state.

## Identity and paths

`workspace_id_for_path()` hashes an absolute, lexically normalized path into
exactly six lower-case base-36 characters. It collapses `.` and `..` but never
resolves symlinks or queries the filesystem: identity inputs can name a host
or daemon namespace this process cannot inspect. By default a record hashes its
physical checkout path. An adapter that must expose the identity while
constructing a name that itself contains that identity may pass
`identity_path`; the record stores that exact canonical absolute input as
`workspace.identity_path`. A missing key means the default physical path; a
present null, empty, relative, non-normalized, or identity-mismatching value is
malformed and refuses read/resume/inspect/removal. Resume also preserves the
durable identity if adapter metadata is refreshed. If two live records claim
one short identity for different paths, allocation refuses with both paths
named; it does not lengthen or silently replace the identity.

`physical_path()` translates lexically normalized logical paths relative to an
explicit logical root into an explicit physical root. Translation and identity
calculation perform no `resolve`, `stat`, or existence check on namespace
paths; namespace owners perform existence checks in their own namespace.

## Record and locking rules

Records are versioned JSON files below the Git common directory's
`.workspace-instances/`. Writes are temporary-file, `fsync`, and atomic rename.
Malformed, missing, mismatched, or unsupported records are refusals. Record
enumeration treats only a genuinely absent record directory as empty; access
errors and malformed entries refuse the inventory. The family lock serializes
Git worktree/branch and record allocation; product adapters add their own locks
for resources they own.

Removal first re-reads and validates the record, then verifies that the live
checkout top level, branch, Git common directory, and source checkout agree
with it. Missing/unreadable paths or any mismatch refuse before the adapter
cleanup callback. After preflight, cleanup is ordered: active lease check,
adapter callback, Git worktree removal, branch removal, record removal. A
failed callback or Git operation leaves the record and checkout available for
recovery. `inspect_workspace()` distinguishes a genuinely missing checkout
from an inaccessible one; inability to inspect is not reported as absence.

An adapter may retain a product-facing compatibility record, but it must not
implement a second generic Git lifecycle or lease authority. If it mirrors
lease state for an older product schema, acquisition and release must update
the neutral record before the checkout can be started or removed; a failed
mirror is a refusal, not a silent best-effort write.

## Deliberate non-ownership

The package does not discover CIU markers, render TOML, call Docker or Compose,
select release projects, publish tags, or interpret labels and metadata. CIU
owns root discovery and root-local resources. CMRU owns orchestration,
release policy, and per-project runtime adapters. Both must pass the resulting
`WorkspaceContext` through without reimplementing generic lifecycle rules.
