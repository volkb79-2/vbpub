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

An adapter may provide `checkout_recovery` to handle Git's explicit permission
denials. This callback is not a prompt: the product must establish consent and
validate its exact failed descendants before invoking any privileged helper.
The library invokes it only when Git reports `EACCES`/`EPERM`, then rechecks
the original checkout inode and Git administration identity before completing
the removal. CIU's exact-branch contract and durable resume example are in its
[worktree removal guide](../../ciu/docs/CONSUMERS.md#5-remove-and-read-the-removal-document).

When a permission recovery can leave the Git registration absent while checkout
files remain, pass `residual_checkout_guard` to `remove_workspace`. The
library calls it after verifying the captured checkout inode and immediately
before either the unprivileged Git retry or its `shutil.rmtree` of an
unregistered leftover. The adapter owns the guard's checkpoint, permitted
changes, walk limit, and refusal message; a guard error preserves the leftover
checkout and its workspace record. Do not use this callback as a replacement
for the earlier target validation inside `checkout_recovery`.

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
checkout may be partially removed. `on_phase` is observational; the separate
`checkout_recovery(context, git_admin_dir, error)` seam is permission-only and
is reached only for explicit Git permission errors. If Git already removed
the registration before reporting failure, `finish_workspace_removal` can
finalize a retry from the adapter's durable checkpoint after it verifies the
checkout is absent and the captured admin identity is unchanged. A
product-level report may combine these events with its own resource cleanup
and identity-resolution phases.

### Mounts under the checkout (WT-OW-01)

`remove_workspace`, `remove_unrecorded_workspace` and `remove_orphan_workspace` read the mount table
(`/proc/self/mountinfo`, octal escapes decoded, the checkout path symlink-resolved) and refuse with
category `cleanup-refusal` when any mount sits at or under the checkout: bind mounts, btrfs subvolume
mounts and nested mounts alike. The message names every mount point ("detach it first"). The check
runs after the identity and lease checks and before the `cleanup` callback and any deletion, emits no
phase event, never unmounts, and `force` does not override it. An unreadable or malformed table also
refuses (fail closed). `mounts_under(path, mountinfo_reader=None)` is public so an adapter can run the
same check before its own destructive steps (ciu does, ahead of `ciu clean`); tests inject
`mountinfo_reader` (a zero-argument callable returning the table text). Out of scope: mounts that exist
only in another mount namespace (the host's, when running in a devcontainer or tester container) are
invisible to this process. The check is time-of-check only, not a lock and not atomic: the table is
read after the identity and lease checks, again immediately before the Git deletion (after the
`cleanup` callback), and again before each permission-retry deletion (the `git worktree remove`
retry and the residual `rmtree`), which narrows but does not close the window in which a mount can
appear.

**Release coupling.** The library source is embedded in both the ciu and the cmru wheels (the same
top-level `worktree` package, both built from `libraries/worktree/src/worktree` through their
`package-dir` mapping, so the two copies are identical by construction). Release order: ciu first,
then cmru, each with the identical library copy. Each adapter has its own surface test: ciu
`test_remove_refuses_mount_under_checkout_before_any_cleanup`, cmru
`test_remove_workspace_refuses_mount_under_the_checkout_through_cmru_surface`. Affected consumer surfaces: `ciu worktree rm` and cmru's release/build
workspace cleanup and `cmru abandon`.

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

## Git version note

The library resolves linked-worktree paths from `git worktree list` and refuses a
non-absolute path. Git writes absolute links by default. If `worktree.useRelativePaths`
is enabled on a host whose Git is older than 2.48, that Git lists the raw relative
value and every worktree removal refuses with the non-absolute-path error. The refusal
is safe: nothing is deleted. Use Git 2.48 or newer wherever relative worktree paths
are enabled.

## Open work

- **WT-OW-01 refuse removal when a mount sits under the worktree.** FIXED 2026-11-04 (b59dbf12d; see "Mounts under the checkout" above). Original text: Removal today walks the tree and deletes. A bind mount, or
  a subvolume mounted at a path inside the worktree, makes `rm -rf` fail with EBUSY or, worse, descend into the mounted content
  and delete it (VM disks, for instance). Before any deletion, list the mounts under the worktree (`findmnt -R <worktree>`, or
  the mount table read directly) and refuse with a message naming each mount point and "detach it first". Exit as the other
  safe refusals do: nothing deleted, one removal phase event. Shared by ciu and cmru, so the check lives here, not in the
  adapters. Closes when a test with an injected mount table covers refuse and pass cases, and both adapters' removal tests
  show the refusal through their own surface.
- **WT-OW-02 delete subvolumes properly.** A btrfs subvolume inside a worktree (the declared CoW paths of `libraries/cowtree`
  COW-BL-02) is removed with the subvolume-delete ioctl, not by unlinking its files, and only on a filesystem mounted with
  `user_subvol_rm_allowed` for an unprivileged owner. The removal pipeline gets a pre-delete hook that the adapter fills
  (ciu: CIU-159); the library itself stays agnostic of which paths are subvolumes and detects them by inode number 256 on a
  btrfs filesystem. An undeletable subvolume is a refusal that names the path and the missing mount option, never a partial
  removal. Closes when a fake-backend test shows the order (subvolumes first, then the tree) and a live btrfs test removes a
  worktree holding a nested subvolume.
- **WT-OW-03 tests must scope process-wide `os.*` syscall fakes.** Filed
  2026-11-12 from the pytest 9.2 process-wide filesystem-mock audit. Pytest
  9.2 calls `os.fstat` during capture and teardown. The path-inspection tests
  now scope `os.lstat`/`os.stat` to the fixture path and delegate unrelated
  calls to the saved real function. Future process-wide `os.*`,
  `builtins.open`, or `io.open` patches must apply only to the fd/path under
  test. The admin-directory symlink refusal test proves the refusal outcome,
  but does not kill a guard-removal mutant: a later intact traversal check
  still refuses the path. Keep that distinction in the mutation evidence.
  Close when no non-delegating exact-symbol patch remains; the registered
  R0/R1/R3 lane and its 100% coverage floor pass on the clean final commit;
  and tests-only changes report R2 `INCONCLUSIVE/NO_MUTANTS` rather than
  claiming a mutation pass. Any production guard changed by this work has a
  focused mutant-killing oracle.
