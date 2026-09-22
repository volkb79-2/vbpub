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

Identity uses the lexically normalized absolute path string and does not
resolve symlinks or query the filesystem. When supplying `identity_path`, pass
the path whose spelling is the shared identity authority; it is recorded in
`workspace.identity_path` and cannot be removed or changed by a metadata
refresh. A missing key alone selects the default physical checkout path. A
present but malformed identity is an error, never a fallback. Removal verifies
the recorded source and live checkout/branch before calling adapter cleanup;
if the checkout cannot be inspected, fix the state rather than treating it as
missing or running cleanup against an unverified target.

The public failure categories are `collision`, `occupied`, `root-mismatch`,
`stale-record`, `invalid-record`, `cleanup-refusal`, `lease-held`,
`lock-error`, `git-error`, and `invalid-input`. A caller that cannot safely
classify a state must preserve the refusal and leave the record in place.
