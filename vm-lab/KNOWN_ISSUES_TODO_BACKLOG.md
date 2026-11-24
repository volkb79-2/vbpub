<!-- backlog-format: 1; prefix: VML-BL- -->
# vm-lab backlog

Normative behaviour is in [README.md](README.md) and [docs/](docs/). Closed items are one line each in [archive/](archive/).
The format is [docs/BACKLOG-FORMAT.md](../docs/BACKLOG-FORMAT.md).

## VML-BL-03 — vmscreen floor or pin

**State:** open · **Severity:** low · **Owner:** vm-lab · **Links:** vm-lab/pyproject.toml, vmscreen-v0.1.0

`pyproject.toml` depends on `vmscreen>=0.1.0`. The functional floor is the `--qemu-dump-dir` option (also checked at run time, `VMSCREEN_REQUIRED_OPTION`). The dependency has no public index to resolve from: install the vmscreen wheel from its GitHub Release (or from `libraries/vmscreen`) or use `--no-deps` for the vm-lab wheel.

The floor is 0.1.0, the first vmscreen release (`vmscreen-v0.1.0`, a GitHub Release wheel). The dependency-confusion risk of the bare name (an installer pointed at a public index could resolve a foreign `vmscreen`) is reduced by the floor but not removed: no public index carries the estate's vmscreen, so install the vmscreen wheel first (or use `--no-deps` for the vm-lab wheel) until the estate has a resolver for first-party wheels.

No vm-lab release exists yet, and its R2 mutation lane has never run (`core.py` is 62.8 % covered).

**Close-when:** vm-lab's first release notes state the install order (vmscreen wheel first, or `--no-deps`); first vm-lab release, then the R2 lane on it. The floor itself is done.

## VML-BL-04 — Start admission lock path in an installed wheel

**State:** open · **Severity:** medium · **Owner:** vm-lab · **Links:** vm-lab/src/vm_lab/core.py

A wheel start and a checkout start share one lock only when both name the same directory (the repository's common Git directory); a wheel in a different place takes its own lock. A venv inside a repository resolves that repository's Git directory through the probe. The lookup order is in the README.

**Close-when:** the mdt-cockpit image recipe sets `VM_LAB_ADMISSION_LOCK_DIR` to the directory every starter on that host shares, and one live start from the installed wheel has taken the lock.

## VML-BL-05 — Image build placement

**State:** open · **Severity:** low · **Owner:** vm-lab · **Links:** AGENTS.md

`vm-lab image build` runs a plain `docker build`; BuildKit ignores `--cgroup-parent`, so the build steps are not placed under `$CGROUP_PARENT_DEV_BACKGROUND` (AGENTS.md "Host cgroup placement"). The image is small (one apt layer), but the placement rule is not met.

**Close-when:** the build is placed on the background slice (or the rule is waived for this build in AGENTS.md), and the image is built live once on a test host.

## VML-BL-06 — Image never built; program name in messages

**State:** open · **Severity:** medium · **Owner:** vm-lab · **Links:** vm-lab/src/vm_lab/qemu-image

The Dockerfile has not been built (the package set mirrors `scripts/debian-install-v2/testing/vm/Dockerfile`). The profile `program` is still `vm_lab.py`, so messages read `vm_lab.py: ...` also from the `vm-lab` command; tests pin the text and the admission lock message.

**Close-when:** the image builds and one guest boots from it; the program name is `vm-lab` in messages, with the tests updated.

## VML-BL-07 — Start on a plain Docker host

**State:** in-progress · **Severity:** medium · **Owner:** vm-lab · **Links:** vm-lab-20261124_060821-plain-host, DR-95, H6

`vm-lab start` needs a cockpit container today (self-inspection), so debian-rollback keeps a declared copy of vm-lab's argv in `poc/dr-qemu.sh`. Direction: skip self-inspection outside a cockpit container and take an explicit cgroup parent.

Source: .controller-state/design-review-2026-11-24/debian-rollback.md H6

**Close-when:** one live start on a test host without a cockpit container; debian-rollback `poc/dr-qemu.sh` deleted.

## VML-BL-08 — Guest session

**State:** open · **Severity:** medium · **Owner:** vm-lab · **Links:** DR-88, DR-95, H6

debian-rollback's `poc/dr-serial.py` has its own QMP client and a marker-framed serial `Guest` session (login, marker-framed run, reboot proven by boot_id, reset, keys). Direction: move that session into vm-lab on top of vmscreen's `QmpClient`; test images take commands via the QEMU guest agent or SSH, serial only for boot observation.

Source: .controller-state/design-review-2026-11-24/debian-rollback.md H6

**Close-when:** dr-serial imports it and has no QMP client of its own.

## VML-BL-09 — Boot an ESP fixture

**State:** open · **Severity:** medium · **Owner:** vm-lab · **Links:** DR-87, H3

A cheap loader-contract oracle. Build an mtools ESP image from a directory, boot it with a Debian kernel and a tiny initramfs that prints `/proc/cmdline` to serial and powers off, with OVMF vars preseeded by `virt-fw-vars` (for example `LoaderEntryOneShot`).

Source: .controller-state/design-review-2026-11-24/debian-rollback.md H3

**Close-when:** the debian-rollback lane `loader-boot` passes on a head.

## VML-BL-10 — ciu service for the image (was RP-07)

**State:** open · **Severity:** low · **Owner:** vm-lab · **Links:** -

Offer the QEMU image through a ciu service instead of a local `docker build`, so every host pulls one built image.

## VML-BL-11 — TrueNAS docs miss the read-only cgroup and D-Bus mount requirement

**State:** open · **Severity:** low · **Owner:** vm-lab · **Links:** scripts/truenas/docs/CONSUMERS.md, scripts/truenas/docs/DESIGN-GUIDE.md, scripts/truenas/tcg_vm.py

**Problem.** The TrueNAS docs (`scripts/truenas/docs/CONSUMERS.md`, `scripts/truenas/docs/DESIGN-GUIDE.md`) do not mention the read-only cgroup and D-Bus mount requirement that `tcg_vm.py` inherits from vm-lab. No TrueNAS test covers the new scope path, and `host_pid_cgroup` still needs `/hostproc`. **Wanted.** Document the mounts, add a test for the scope path, and decide whether `host_pid_cgroup` keeps the `/hostproc` requirement.
