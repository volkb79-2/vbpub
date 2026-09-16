# Debian-install-v2 isolated test environments

This directory contains the test environment for operations that need a real
Linux kernel and real block-device behavior. Those operations run in the
QEMU guest harness under [`vm/`](vm/), never in a privileged Docker
container and never in the cockpit itself.

## Safety boundary

Docker containers share the host kernel. In particular, Linux has no
container namespace for loop devices or swap:

- `losetup`, `partx`, and partition-device creation use host-kernel state;
- `mkswap` and `swapon` can activate swap in the host-wide reclaim pool;
- a force-killed container can skip cleanup and leave that state behind.

Therefore the following tests are VM-only:

- loop-device and partition-device commit tests;
- `mkswap`, `swapon`, and `swapoff` tests;
- initramfs, boot, reboot, systemd-as-PID1, and kernel-behavior tests;
- any test that opens a real `/dev/*` block device or changes global kernel
  state.

The old privileged systemd-container harness has been removed. It was not a
valid isolation boundary for these tests. The ordinary R0/R1 suite remains
safe in `tester-unified` only when it uses regular-file fixtures, mocked
commands, and dry-run paths; real-device tests are explicitly skipped there.

## Run the isolated tests

The VM runner itself is an unprivileged Docker container that executes QEMU
with TCG software emulation and QEMU user-mode networking. It passes no host
`/dev`, raw block device, KVM device, or TAP device into the guest.
It must be started from a governed cockpit/gate environment with
`CGROUP_PARENT_DEV_BACKGROUND` and `CGROUP_PARENT_DEV_INTERACTIVE` set by the
host setup. The wrapper refuses to start if either is missing or if the
background value is not a live, installed host slice.

```bash
cd scripts/debian-install-v2/testing/vm
./run-vm-tests.sh
```

The runner downloads and checksum-verifies an official Debian generic-cloud
image, creates a disposable qcow2 overlay, boots the guest, installs only the
test dependencies, copies the current test package into the guest, runs the
real loop/swap tests there, and destroys the overlay on exit. The guest's
swap and loop devices belong to the guest kernel and cannot become host swap.

For interactive work, the lower-level controls are documented in
[`vm/README.md`](vm/README.md):

```bash
./vm/run-vm-harness.sh prepare-base case-b
./vm/run-vm-harness.sh start shell1 --case case-b
./vm/run-vm-harness.sh wait shell1
./vm/run-vm-harness.sh ssh shell1 -- uname -a
./vm/run-vm-harness.sh destroy shell1
```

The host can still be affected by QEMU CPU and qcow2-file I/O. The runner
must therefore remain in the configured governed cgroup tier, and test state
must stay on disposable files. Never attach a production block device or
bind-mount the host's `/dev`.
