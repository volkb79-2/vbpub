# tester-unified design guide

## Cockpit and gate boundary

The cockpit has editor tooling and Docker control, but its own cgroup and
Python environment are not release evidence. `tester-unified/run` makes the
boundary executable: it starts `tester-unified:local` detached in the declared
background slice and waits for Docker's job status before reading logs.

The workspace is mounted twice because linked-worktree gitfiles can name the
cockpit path while Docker resolves bind sources in the host namespace. A
temporary source directory beneath the worktree's ignored `.assay/` is mounted
at `/tmp`, then exported as `TMPDIR`, `TMP`, and `TEMP`. Tests that create
throwaway repositories can derive a real host path while remaining outside the
judged worktree's `.git` ancestor. When the workspace mount is itself `/tmp`,
the launcher selects `/var/tmp/tester-unified` instead; the workspace's own
host-backed `/tmp` remains available to an environment-scrubbing nested judge,
and the two mount destinations never collide.

The Docker socket is deliberate. Several product tests exercise real nested
container boundaries, including host-lane exit propagation. The socket's
numeric group is read from the socket and added to the tester process rather
than assumed. The image still supplies the complete uid 1003 passwd/group,
HOME, and XDG identity.

The tester image also carries Assay's build-system requirements and gives the
test uid write access to its venv. Internal vbpub lanes install the selected
worktree's Assay source with `--no-build-isolation`; baking only the backend
closure keeps that install offline and deterministic while still recording the
exact runtime Assay version in the verdict. The image does not install a
copied Assay tree, because the judged worktree is the source of truth.

The launcher refuses missing cgroup configuration, workspace-root fallbacks,
high launch-time memory pressure, absent Docker access, missing images, more
than two concurrent tester containers, and any mismatch in Docker's accepted
user/cgroup/CPU/workdir/mount state. Count and launch are serialized by one
lock beneath the enclosing workspace's ignored `.assay/`, not by a per-worktree
lock. These are infrastructure outcomes, not functional test verdicts.

See [the user-facing overview](../README.md) and
[consumer commands](CONSUMERS.md#running-a-project-gate).

## Offline gates

`--network none` lets exact-OID wheel/self-hosting gates reuse this launcher
while retaining their existing offline container boundary. Reimplementing the
P4 namespace, identity, admission and transport recipe just to disable network
access would recreate the duplication the canonical launcher removed. Docker
must accept `NetworkMode=none`, and this fact is recorded with the launch
metadata; a mismatch aborts only the owned container. The only explicit network
value is `none`. Omitting the option preserves existing Docker selection;
the launcher does not invent a network name. The Docker socket remains mounted
for product boundary tests, so this is network isolation, not a security sandbox.
