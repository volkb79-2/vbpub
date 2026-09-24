# MDT testing design

This document records why the MDT test surface is split between
`tester-unified` and the shared QEMU harness.

## Two environments, two claims

The ordinary Python lane runs in `tester-unified`. It owns parser behavior,
configuration validation, pure sizing invariants, mocked command boundaries,
coverage, mutation, and the canary. Assay runs that lane at its declared R0,
R1, R2, and R3 levels and keeps the resume and progress evidence under the
git-ignored `.assay/` directory.

The system lane runs the product assertions inside a disposable QEMU guest.
The guest owns PID 1, systemd, cgroup v2, Docker, service files, and the
kernel-visible configuration. The cockpit or gate container only drives the
VM. This boundary is required because a Docker container shares the host
kernel; a privileged container would still be able to affect host-wide
systemd, loop, swap, or device state.

The VM controller is itself governed by `CGROUP_PARENT_DEV_GATES`, probes the
interactive tier before starting, and builds through the declared
`BUILDX_BUILDER`. It maps a worktree source through Docker's authoritative
mount table and refuses an unmapped or missing source. This prevents a
container path from being passed to the daemon as if it were a host path.

## Why Assay is not used to pretend the VM is a unit test

Assay judges the VM controller's R0 receipt. It does not move systemd or
Docker assertions into the Python mutation snapshot, because that would test
the controller's text and mocks while claiming to test the host boundary.
The VM lane therefore remains R0: its value is the real guest acceptance
receipt, while the Python lane supplies the deeper R1-R3 evidence for code
whose behavior can be reproduced safely in `tester-unified`.

MDT has no owned HTTP/OpenAPI contract. Hypothesis is used for pure
configuration invariants where generated values are meaningful; Schemathesis
would add no evidence to this product.

## Managed BuildKit socket access

The rootless BuildKit process creates its Unix socket with its host-mapped
uid/gid, which need not match the host `docker` group. A setgid parent alone
does not repair that: a live rootless BuildKit probe still created the socket
as `1000:1000`. Host setup therefore waits for the actual socket, verifies it
is a non-symlink socket owned by the running service container's effective
uid, and changes only that inode to group `docker`, mode `0660`, before the
service becomes ready. The sticky runtime directory prevents unrelated local
users from replacing or deleting the socket after it appears. This shares the
BuildKit API only with principals already trusted for Docker access; it does
not make the socket world-accessible.

## Release relationship

The `release` run-gate lane contains the image release-flow tests and is the
single declaration consumed by `cmru.toml`. The image build and push remain
CMRU steps, while the acceptance checks stay in `tester-unified`; this keeps
the release transaction from carrying a second hand-written test recipe.

See the [README testing section](../README.md#testing) for the user-facing
lane list and [CONSUMERS.md](CONSUMERS.md) for commands and environment
requirements.
