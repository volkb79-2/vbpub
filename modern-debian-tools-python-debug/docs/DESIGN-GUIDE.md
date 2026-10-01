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

The rootless BuildKit process creates its Unix socket with its container
uid/gid, which may be translated by Docker `userns-remap` and need not match
the host `docker` group. A setgid parent alone does not repair that: a live
rootless BuildKit probe created the socket as `1000:1000`. Host setup waits for
the socket, makes its root-owned parent non-writable, rechecks that the
endpoint is a non-symlink socket, then compares socket owner and daemon uid
inside the same container namespace. Only then does it change the socket to
host group `docker`, mode `0660`. Locking the parent before the privileged
`chgrp`/`chmod` closes a path-replacement race; the next start temporarily
restores directory mode `01777` for socket creation. This shares the BuildKit
API only with principals already trusted for Docker access; it does not make
the socket world-accessible.

## Bootstrap uses the source filesystem type

Devcontainer bind-mount syntax declares a source and target but has no
source-kind field. Names and suffixes do not identify a missing source's type:
`.gitconfig` is usually a file, while `.ssh` is a directory, and custom dotfiles
can be either. The host bootstrap checks existing host sources by their actual
filesystem type, regardless of name or suffix. For missing sources, the
`DEVCONTAINER_MISSING_BIND_SOURCE_POLICY` setting controls whether MDT creates
the path or refuses it. The shipped default, `create-by-spelling`, interprets a
trailing `/` as a directory and no trailing `/` as an empty regular file. The
`fail` option requires every `$HOME` source managed by the bootstrap to exist
before Docker starts. The marker is an MDT bootstrap convention, not Docker
mount metadata; the shipped template marks every directory source with `/`. See the
[consumer example](CONSUMERS.md#optional-git-config-mount).

The template puts Codex's shared SQLite database under the persistent `.codex` mount with
`CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared`, so profile data survives a rebuild.
It sets `shutdownAction: "none"` so closing the attached Dev Containers session leaves the
container available. Adoption details are in the
[consumer guide](CONSUMERS.md#codex-profile-state-and-container-lifetime).

## BuildKit guard policy

The guard defaults to `report-only` because a reserved Buildx worker may be
legitimate during a development build or image rebuild. Automatically
removing it can abort work during export, which Docker reports as an RPC
unexpected EOF. The report mode leaves the worker available and records the
identity mismatch. Operators who intentionally want the guard to remove
unapproved workers can select `terminate`; the wizard requires a second
confirmation because this action stops a live container.
An existing `terminate` value is not confirmation for a later plain install:
the installer stops and disables the guard and requires the wizard to confirm
termination again.

## Per-container memory policy

Parent slices already bound the combined tier, while callers such as CIU may
set their own container memory limit or `MemoryMin` floor. An unconditional
1G leaf cap can stop an otherwise valid workload and may conflict with that
floor. The inotify watcher therefore ships with both per-container memory
values set to `-` for each tier. Operators can configure a leaf cap where a
workload needs one, then explicitly confirm enabling it in the wizard; the
wizard keeps it disabled when that confirmation is declined. Any configured
cap must remain compatible with the other memory properties on that scope.
`WATCHER_MEMORY_POLICY=disabled` is the shipped setting and requires all six
leaf values to be `-`; `WATCHER_MEMORY_POLICY=enabled` requires at least one
configured value. The installer rejects mismatched policy/value combinations
instead of ignoring configured values or starting an empty watcher. During
upgrades, if an older host config has leaf values but no policy key, the wizard
carries those values forward for review and confirmation.

## Shared io.cost generator

MDT and `debian-install-v2` use one generated copy of the Linux kernel's
`iocost_coef_gen.py`. The pristine vendor file stays byte-for-byte intact;
explicit patches carry the local LVM, file-target, no-COW, and partition
sysfs behavior. The generator records hashes for its source and patch series,
and `tools/build-iocost-generator.py --check` verifies the checked-in runtime
artifact. This keeps calibration behavior aligned without copying one
consumer's local fork over another. MDT always uses a persistent file target
on the Docker-data filesystem; automated scratch-partition setup remains a
separate reviewed change.

The file target is explicitly disposable benchmark scratch. Calibration
rewrites its contents, including when an existing exact-size file is reused.
The generator refuses to replace a symlink, non-regular file, multiply linked
file, or file with a different size, so changing a target's shape requires an
explicit operator action instead of silently unlinking the prior path.

## Host namespace access

An MDT devcontainer cannot inspect or change the host's cgroup tree through
its private cgroup namespace. `host-escape` provides one documented route for
host diagnostics: it starts a privileged helper and enters the selected host
process's mount, UTS, network, IPC, and cgroup namespaces. This keeps operators
from improvising several subtly different escape commands, but it is not a
sandbox: every command runs with host-root authority. Since that helper can
touch the host cgroup mount namespace, it runs `mdt doctor` on exit to restore
required cgroup2 flags if needed; `mdt doctor --check-only` provides a
read-only check.

## Release relationship

The `release` run-gate lane contains the image release-flow tests and is the
single declaration consumed by `cmru.toml`. The image build and push remain
CMRU steps, while the acceptance checks stay in `tester-unified`; this keeps
the release transaction from carrying a second hand-written test recipe.

See the [README testing section](../README.md#testing) for the user-facing
lane list and [CONSUMERS.md](CONSUMERS.md) for commands and environment
requirements.
