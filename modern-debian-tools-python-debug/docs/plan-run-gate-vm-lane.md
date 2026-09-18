# Plan: additional VM lane for native Docker/OCI large-transfer acceptance

Status: design plan. This document does not provision storage, repartition the
host, install Docker in a guest, or start a new lane by itself.

## Decision

Add a second, explicit VM acceptance lane beside the existing
`r1-vm-real-commit` lane in `scripts/debian-install-v2/run-gate.toml`. The new
lane will exercise the large OCI transfer that is difficult to diagnose through
the shared host Docker daemon:

- the guest gets its own kernel, Docker daemon, BuildKit endpoint, registry
  fixture, cgroups, filesystem namespace, and daemon logs;
- the outer QEMU runner remains a normal host workload under
  `$CGROUP_PARENT_DEV_GATES`;
- the existing host gate lane stays in place for mount, cgroup, socket, and
  tester-unified contracts;
- the VM lane is an additional acceptance surface for Docker/BuildKit/proxy
  stream behavior, not a replacement for host integration coverage.

The guest must run Docker natively inside the VM. The host Docker socket must
not be mounted into the guest, because that would turn the test back into a
client of the shared daemon and would remove the isolation this lane is meant
to measure.

The lane should have an explicit name such as `r1-vm-large-oci`. Keep it out of
the aggregate `gate` lane until its first implementation has a stable,
repeatable result and its resource cost is measured. The existing
`run-vm-harness.sh` already supplies the correct outer placement,
worktree-specific persistent runner, QEMU lifecycle, and guest SSH transport.
The implementation should extend that harness with a separate test command
rather than duplicate its runner and storage logic.

## What `dm-1` would and would not mean

`dm-1` is a device-mapper name, not a second physical disk. It commonly appears
when an administrator creates a second LVM logical volume, encrypted mapping,
or another device-mapper target. The number is allocation-dependent and must
never be used as a stable configuration identifier.

With one physical disk, the options are:

| Layout | What it isolates | What it does not isolate |
| --- | --- | --- |
| qcow2 files on the current filesystem | guest filesystem namespace and disposable state | physical disk bandwidth, latency, queue, and host page cache pressure |
| a new filesystem on a free LV in the existing VG | VM-state free space, filesystem accounting, mount-level policy, and a stable path that can be targeted by policy | the underlying disk's physical queue, seek latency, throughput, and failure domain |
| a new partition on the same disk | partition boundaries and filesystem accounting | the physical disk queue and all contention with the root partition |
| a second physical disk or SSD | a genuinely separate block queue and failure domain | host CPU, memory, and any shared upper-layer network or daemon work |

A same-disk LV can still be useful. It makes VM state discoverable and gives us
a filesystem target for free-space alarms and, where supported, device-specific
I/O policy. It is not I/O isolation in the performance sense. A separate
partition on a full root disk also requires shrinking or migrating a
filesystem, which is a storage operation with backup and rollback requirements.
It is not justified for the first lane.

### Elastic capacity choices

An ordinary LV has a fixed allocation when created, but it can grow online
into free extents in its volume group. The filesystem then needs its own grow
operation (`resize2fs` for ext4 or `xfs_growfs` for XFS); `lvextend -r` can
coordinate those two steps where the filesystem supports it. Sibling ordinary
LVs do not automatically share all remaining free space.

LVM thin provisioning is closer to the ZFS dataset model. A thin pool owns the
finite backing extents, and thin LVs receive virtual sizes while physical
extents are allocated as blocks are written. Several VM-state thin LVs can
therefore grow on demand from the same pool, and the pool itself can be
extended later. This does not create space from nowhere: pool exhaustion can
write-block every thin LV, so the host must monitor both pool data and
metadata usage and reserve headroom. The filesystem inside each thin LV still
has to be grown to use a larger virtual size.

ZFS datasets and btrfs subvolumes provide stronger shared-pool accounting with
quotas and reservations, but adopting either for this lane would be a host
storage decision. A sparse qcow2 file on the existing filesystem gives the
first lane the same operational convenience without changing the host's
partition or volume layout. It shares the host filesystem's remaining free
space and therefore needs free-space alarms; it does not isolate the physical
disk queue. The initial lane should use this reversible option, then consider
an LVM thin pool or a second physical disk only if measured evidence justifies
the added host storage work.

Before choosing an LV, the host-side provisioning step must derive facts from
`lsblk`, `findmnt`, `pvs`, `vgs`, and `lvs`:

1. identify the filesystem containing the VM-state directory;
2. identify its real backing device and whether a VG has free extents;
3. measure free space and the existing filesystem's resize capability;
4. record the resulting filesystem UUID and mount path;
5. refuse to continue if the only proposal is an assumed `/dev/dm-1`, a guessed
   partition, or a loopback file described as an isolated disk.

The current cockpit view exposes a single `vda` disk with ordinary partitions
and no usable LVM metadata. That view is not sufficient evidence about the
Docker host's root mapping, so the plan must not mutate storage from inside the
cockpit. The first implementation should keep qcow2 state on the existing
filesystem. If later measurements show that host disk contention is the
remaining bottleneck, provision a dedicated physical disk; a same-disk LV is a
capacity/accounting improvement, not the promised performance separation.

## Proposed lane shape

Add a VM-specific test wrapper under
`scripts/debian-install-v2/testing/vm/`, for example
`run-vm-large-oci-tests.sh`. It should:

1. call the existing harness to prepare or reuse a ready Debian guest;
2. install or boot a pinned Docker Engine and BuildKit version inside the guest;
3. start a disposable registry fixture inside the guest, with its state on the
   guest filesystem;
4. generate an incompressible OCI payload with a recorded byte size and
   content digest;
5. exercise the same build/export operation through the direct endpoint and
   through the API proxy/relay endpoint from the current reproduction;
6. collect evidence from both paths before destroying the guest run;
7. return the job's captured exit status, with no pipeline or wrapper status
   substituted for it.

The direct and relay endpoints must be read from the existing reproduction
configuration during implementation. The lane must print their effective
endpoint names and transport mode, and refuse an empty or ambiguous endpoint;
it must not silently turn a relay test into a direct test.

The direct path should be guest-local: guest client -> guest Docker daemon ->
guest BuildKit -> guest registry. The relay path should use the actual proxy
under investigation, with an explicit guest-reachable transport such as a
controlled forwarded port or SSH tunnel. It must not use the host's Docker
socket. If the current proxy only exists on the host, add a narrow test-only
forwarding endpoint and record that fact in the evidence; do not broaden the
host daemon's public listener as a shortcut.

Use a fresh guest overlay for each acceptance case. Reusing the ready base is
fine; reusing a mutated test disk between cases is not. Run one case per VM
and serialize cases that share the runner's guest-side Docker daemon.

## Acceptance matrix

The first run should establish the smallest payload that previously hit the
failure, then test at least:

- direct endpoint at the reproducer size;
- relay endpoint at the same size;
- both endpoints at 2x the reproducer size, subject to the guest disk budget;
- one transfer and two controlled concurrent transfers;
- a repeat after the guest Docker daemon and BuildKit have been idle;
- a payload with incompressible bytes so compression cannot make a small test
  appear to accept a large stream.

A case passes only when all of these hold:

- the guest Docker command exits zero;
- the expected manifest and layer digests are present in the registry;
- the received byte counts and digests match the producer;
- the client does not see EOF, truncation, or a proxy-size refusal;
- Docker, BuildKit, proxy, and registry logs show no transport reset;
- the container's inspected exit status and `OOMKilled` state are recorded;
- the guest and outer cgroup evidence shows no memory-max, swap-max, or
  container-kill event explaining the result.

A failure is useful only if the lane preserves enough evidence to distinguish a
proxy limit, Docker API stream termination, BuildKit export failure, guest disk
pressure, outer cgroup pressure, and an ordinary application error.

## Evidence contract

For every case, preserve a compact result record and paths to the full logs.
The record should include:

- git revision, lane name, VM base image digest, Docker and BuildKit versions;
- direct/relay mode, endpoint identity, payload size, layer count, and digests;
- guest Docker daemon logs and `docker info`/`docker events` output;
- BuildKit debug log and worker metadata;
- proxy and registry logs for the exact case;
- `docker inspect` state, exit code, `OOMKilled`, `Error`, and timestamps for
  every involved container;
- guest `memory.current`, `memory.events`, `memory.max`, `memory.high`,
  `memory.swap.max`, `io.stat`, filesystem free space, and `iostat` samples;
- host-side outer-runner `docker inspect`, cgroup path, `memory.events`,
  `memory.current`, `memory.max/high/swap.max`, `io.stat`, and the relevant
  `dev-gates.slice` values;
- the final manifest and an independently calculated digest of the received
  OCI output.

Evidence should live in the lane's ignored evidence directory or an explicitly
configured external evidence root. The judged tree should contain only the
small result artifact the lane declares. The harness must append an explicit
exit marker to any detached/background log before the controller reports the
result.

## Cgroup and storage policy

The outer runner uses `--cgroup-parent="$CGROUP_PARENT_DEV_GATES"` and refuses
an unset or unverified slice. Its guest QEMU process, Docker daemon, BuildKit,
registry, and test command are therefore charged to the gate tier through the
runner. The guest's own cgroup files are collected for diagnosis, but they do
not replace the host placement check.

Size the guest memory and disk from measured use. If the lane needs more room,
change the host-setup `DEV_GATES_MEMORY_HIGH`/`DEV_GATES_MEMORY_MAX` policy and
rerun the host check; do not add an unbounded Docker `--memory` exception or
move the runner back to `dev-background.slice`. Keep BuildKit's persistent
host-managed service in `dev-buildkitd.slice`; this VM lane is a disposable
acceptance workload.

For the initial implementation, put guest qcow2 state in the existing
worktree-specific runner state directory. Add a separate host mount only after
an observed disk-contention result and a host-side storage review. If an LV is
provisioned later, mount it at a stable path such as
`/var/lib/mdt-run-gate-vm`, use its filesystem UUID in the host setup, and
keep the runner's path configuration independent of any `/dev/dm-*` number.

## Implementation phases

### Phase 0: reproduce and baseline

- Capture the existing direct and relayed failure with the current host lane.
- Record the exact payload threshold, endpoint configuration, and all available
  Docker/BuildKit/cgroup evidence.
- Run `mdt-host-check.sh` and record the effective `dev-gates.slice`,
  `dev-buildkitd.slice`, and shared `dev.slice` limits.
- Do not change host partitions, LVs, daemon listeners, or cgroup limits in
  this phase.

### Phase 1: guest image and native daemon

- Add a pinned guest preparation path to the existing QEMU harness.
- Install a pinned Docker Engine and BuildKit inside the guest, or bake them
  into a reproducible ready base.
- Add health checks for the guest daemon, BuildKit worker, registry, free disk,
  and guest cgroup controls.
- Prove that the host Docker socket is absent and that a guest `docker info`
  reports the guest daemon.

### Phase 2: lane adapter

- Add `run-vm-large-oci-tests.sh` and a new explicit lane in
  `scripts/debian-install-v2/run-gate.toml`.
- Keep the lane's outer environment `bare-host` because it launches the
  Docker-backed VM harness; require `CGROUP_PARENT_DEV_GATES` and the other
  facts needed by the existing wrapper.
- Use detached VM/guest operations with bounded waits and explicit exit markers.
- Add a live acceptance test for the new Docker argv and the guest endpoint
  selection. A fake Docker argv check alone is insufficient.

### Phase 3: direct/relay acceptance and diagnosis

- Implement the payload-size sweep and acceptance matrix.
- Add the evidence collector before the first large transfer, not only after a
  failure.
- Compare direct and relay behavior under identical guest disk, memory, CPU,
  registry, and payload conditions.
- Classify each failure using the evidence before changing limits. A larger
  cgroup limit is a valid fix only when cgroup pressure is the demonstrated
  cause.

### Phase 4: documentation and admission

Update the capability documentation in the same change:

- `scripts/debian-install-v2/run-gate.toml` and the VM README for how to run
  and interpret the lane;
- `scripts/debian-install-v2/testing/vm/DESIGN.md` for guest daemon and
  evidence boundaries;
- this MDT README and `docs/CONSUMERS.md` for adoption and storage choices;
- the build architecture documentation for direct versus relayed Docker/OCI
  paths and their cgroup placement.

Add a lane self-check that rejects a missing gates variable, a missing guest
Docker daemon, a host socket accidentally mounted into the guest, an
unverified endpoint, and a missing evidence directory. Keep the lane out of
the aggregate until those checks and the first large-output acceptance case
are green.

## Operational constraint

The controller must use blocking waits or event-driven completion for a running
VM gate. If an operator needs to inspect a still-running gate, status checks
must be no more frequent than once every 20 minutes. The lane should therefore
write progress at meaningful phase boundaries and rely on the existing VM/QMP
and Docker wait operations instead of a tight polling loop.
