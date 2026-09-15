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

The launcher refuses missing cgroup configuration, workspace-root fallbacks,
high launch-time memory pressure, absent Docker access, missing images, more
than two concurrent tester containers, and any mismatch in Docker's accepted
user/cgroup/CPU/workdir/mount state. Count and launch are serialized by one
lock beneath the enclosing workspace's ignored `.assay/`, not by a per-worktree
lock. These are infrastructure outcomes, not functional test verdicts.

See [the user-facing overview](../README.md) and
[consumer commands](CONSUMERS.md#running-a-project-gate).
