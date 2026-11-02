# Consuming the worktree library

CIU and CMRU package this source directly. During development, put the source
directory on `PYTHONPATH`; release and gate builds include `worktree` in the
wheel. Consumers receive a `WorkspaceContext` and pass it to their adapter.

```python
import worktree

context = worktree.create_workspace(
    source_git_root=source_repo,
    target=isolated_checkout,
    branch="cmru-release-abc123",
    base="HEAD",
    purpose="release",
    identity_path=source_repo.parent / "cmru-release-allocation",
    labels={"owner": "cmru"},
)
try:
    run_product_adapter(context)
finally:
    worktree.remove_workspace(context, cleanup=cleanup_product_resources)
```

Use `adopt_workspace` only for an already-created linked checkout. A product
cleaning up a pre-library checkout with no shared record may use
`remove_unrecorded_workspace`; it is a compatibility bridge that adopts and
immediately removes through the same lifecycle path. Use `ensure_workspace` for
recovery of a record that already exists. Do not pass a
shell's `REPO_ROOT`, `ciu.env`, or a translated container path as a substitute
for the typed context. Do not run local filesystem checks against
`physical_worktree_path` when that path belongs to another namespace.

To find an existing record by checkout, pass its exact absolute stored path:

```python
record = worktree.find_workspace(git_common_dir, recorded_worktree_path)
```

The lookup does not normalize or resolve the target path. A different spelling
is not a match; duplicate records claiming the same path and malformed record
state are refusals, not an arbitrary first match. `list_workspaces()` applies
the same duplicate-ownership refusal before returning its inventory.

For native Git inventory, use the shared API instead of parsing porcelain in a
product adapter:

```python
for checkout in worktree.list_git_worktrees(source_repo):
    if checkout.is_prunable:
        report_prunable_registration(checkout.path, checkout.head)
        continue
    report_checkout(checkout.path, checkout.branch, checkout.head)
```

The list is Git-ordered with the primary checkout first. Detached entries have
`branch is None`; bare records have `is_bare` and are not a primary checkout.
The paths are literal Git facts, not permission to run filesystem probes
against paths translated from a host/daemon namespace. `is_prunable` reports
only Git's registration marker; it does not prove that the checkout path is
absent or visible here. Keep the reported HEAD even when the marker is set, and
withhold operations that need a validated live checkout until the adapter's
own preflight succeeds.

Use `worktree.discover_git_root(path)` when only repository placement is needed
and the repository may not have a commit yet. `discover_git_context(path)` is
the stronger contract: it also requires a current branch/HEAD suitable for a
workspace operation.

Identity uses the lexically normalized absolute path string and does not
resolve symlinks or query the filesystem. When supplying `identity_path`, pass
the path whose spelling is the shared identity authority; it is recorded in
`workspace.identity_path` and cannot be removed or changed by a metadata
refresh. A missing key alone selects the default physical checkout path. A
present but malformed identity is an error, never a fallback. Removal verifies
the recorded source and live checkout/branch before calling adapter cleanup;
if the checkout cannot be inspected, fix the state rather than treating it as
missing or running cleanup against an unverified target.

## Removal options and phase events

The adapter owns removal policy. The low-level `delete_branch=True` default is
kept for compatibility and is used by CMRU's ephemeral gate and release
workspaces. Pass `delete_branch=False` to leave the ref after checkout
removal. When deletion is selected, pass the branch tip observed
before cleanup as `expected_branch_tip`; the library uses Git's compare-and-
delete form, so a ref moved during cleanup is preserved and the operation
fails after checkout and shared-record removal, leaving the branch ref intact.
`remove_unrecorded_workspace` accepts the same options.

```python
phase_events = []
worktree.remove_workspace(
    context,
    cleanup=cleanup_product_resources,
    delete_branch=False,
    on_phase=lambda phase, status: phase_events.append((phase, status)),
)
```

Callback events report `checkout_deletion`, `git_registration`,
`shared_record_removal`, and `branch_removal`, in that order. Their statuses identify when an
operation starts, completes, fails, deletes a branch, keeps it, or skips a
detached ref. A branch-delete failure happens after the workspace record is
removed; the branch itself remains. A checkout-delete failure says the
checkout may be partially removed. The callback is observational: it cannot
authorize an operation or repair a partial removal. A product-level report may
combine these events with its own resource cleanup and identity-resolution
phases.

The public failure categories are `collision`, `occupied`, `root-mismatch`,
`stale-record`, `invalid-record`, `cleanup-refusal`, `lease-held`,
`lock-error`, `git-error`, and `invalid-input`. A caller that cannot safely
classify a state must preserve the refusal and leave the record in place.

## Qualify the shared source

The internal library has a dedicated tester-unified lane. From this directory,
run the complete R0-R3 qualification through the project launcher:

`run-gate` reads the gate tier from the host-provided
`$CGROUP_PARENT_DEV_GATES` setting and validates that the systemd slice is
loaded before starting a container.

```console
run-gate worktree
```

This does not release a third package. It verifies the source that CIU and CMRU
embed; their own product lanes remain required for adapter, wheel, and release
behavior.
