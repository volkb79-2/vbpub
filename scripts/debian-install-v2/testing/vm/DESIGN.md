# QEMU/TCG VM test harness — design, reasoning, and walkthrough

Status: implemented and live-verified for Case B's shape (`case-b`);
Case A's shape (`case-a`) is designed but not yet built. This document
covers *why* this exists, the reasoning behind every real design
decision, and a step-by-step walkthrough of using it. For the terse
command reference, see `README.md` in this directory.

## 1. Why this exists

### 1.1 The problem it solves

`scripts/debian-install-v2` edits a disk that is currently in use — it
partitions and shrinks the *live root filesystem* of the machine it's
running on, then reboots into a second stage that finishes the job. Two
properties of that make it fundamentally hard to test safely:

1. **The premount initramfs hook (Case B's root-shrink) only runs during
   a real boot**, before anything mounts root — `e2fsck`, `resize2fs`,
   `sfdisk` against the actual root device, from inside the initramfs
   environment. No container can exercise this at all: a container shares
   the host kernel and never actually *boots* — no bootloader, no initramfs
   unpack/execute, no `switch_root`. This is true no matter how privileged
   the container is.
2. **The real `--commit` path activates real swap** (`inuse_partition_
   editor.py`'s `add-swap --commit` runs `mkswap` + `swapon -a` for
   real — that's the whole point of the command, matching what a real
   production install needs). Linux does not namespace loop devices or
   swap at all — there is no per-container loop-device pool and no
   per-container swap concept. `swapon(2)` adds a region to the *one*
   machine-wide page-reclaim pool the kernel uses for every process on the
   box, containerized or not. A `--privileged` docker container is not a
   separate kernel; it's the same host kernel with namespace/capability
   wrappers around it. Running that code path in a privileged container
   reaches directly into the same global kernel state a bare command on
   the host itself would touch.

Property 2 is not theoretical. It happened.

### 1.2 The incident that forced this design (2026-09-09)

Earlier this same live-testing effort, a `--privileged` systemd container
(`scripts/debian-install-v2/testing/` — built for `inuse_partition_
editor.py`'s partition-table tests, which are genuinely safe: they only
partition and format, never activate swap) had been unable to start on
this shared devcontainer host's docker daemon for a long time. A same-day
fix (`--cgroupns=host`) let it start for the first time — which also let
its full test suite run for the first time, including two tests
(`test_real_commit_via_loop_device_materializes_partition_nodes` and its
GPT sibling) that call `add-swap --commit`. Their cleanup only did
`losetup --detach`, never `swapoff` first. The result: a real, host-wide
swap area was left active, referencing an already-detached loop device.

Deactivating loop-backed swap whose backing device is gone is a
known-hazardous Linux operation — evacuating already-swapped pages can
itself require memory the loop driver needs to service the read, a
reclaim-recursion trap. `swapoff` hung in uninterruptible (D) sleep.
Memory pressure cascaded across the shared host. The **production game
server's own process got stuck in D state too** (SIGKILL pending, unable
to complete exit while blocked in the uninterruptible kernel wait). The
operator manually `cgroup.kill`'d multiple container cgroups — production
and dev alike — trying to free memory, and ultimately had to reboot the
host. Operator's own words, from the recovery session: *"the loop devices
should never have been added as swap devices on the host! this was some
mistake by claude building a test environment."*

That is the precise, confirmed mechanism this harness exists to make
structurally impossible to repeat: **anything that activates real swap
must run inside a genuinely separate kernel, never a container sharing
this host's own kernel.**

The immediate response (same session, before this harness existed) was
defense-in-depth at the code level, kept regardless of this harness:
- `test_inuse_partition_editor_r1.py`'s two swap-activating tests now
  `swapoff` before `losetup --detach`.
- Both tests additionally require an explicit
  `VBPUB_ALLOW_HOST_GLOBAL_SWAP_TEST=1` environment variable on top of
  the pre-existing `root + losetup available` skip gate — that old gate
  is true inside *any* privileged container on *any* host, so it never
  actually gated the thing that mattered. The new variable is meant to be
  set only inside a disposable VM/KVM guest, never a shared docker host.
- `--cgroupns=host` was reverted from the privileged container's launch
  script (a separate, independent live-state-contention hazard in its own
  right — sharing a container's cgroup namespace with the real host's
  lets its systemd-as-PID1 act on the actual host cgroup tree the real
  host PID 1 is simultaneously managing — not established to be *this*
  incident's mechanism, but not something to accept either).

Those fixes make the common path safe. They are not a complete guarantee:
a `docker rm -f`/SIGKILL mid-test skips Python `finally` blocks entirely,
so an interrupted run could still leak real host swap. The privileged
container therefore stays deliberately blocked from running those two
tests at all (the env-var gate), and this VM harness is where that class
of test — and the much larger one this project actually needs, the full
install-then-reboot flow — belongs instead.

### 1.3 Why a container can never be the answer, no matter how it's configured

This is worth stating plainly because it's tempting to reach for "a
different container flag" every time a container-based test hits a wall.
Namespaces are real Linux kernel objects that isolate *specific,
enumerable* subsystems: mount tables, PID trees, network stacks, UTS
(hostname), IPC, user/group ID mappings, and (separately) cgroup
hierarchies. **Loop devices and swap are not on that list.** No
combination of `--privileged`, capabilities, or namespace flags changes
that, because there is no namespace type that covers them. The only
kernel-level isolation boundary for "this process gets its own, separate
view of loop devices and swap" is a separate kernel — i.e., a VM.

## 2. Why QEMU + TCG, and why this design specifically

### 2.1 No hardware virtualization is available anywhere in this estate

Confirmed 2026-09-08 by the operator directly on the real host (not just
inside this devcontainer): `egrep -c '(vmx|svm)' /proc/cpuinfo` = 0, and
`/dev/kvm` does not exist. This entire estate runs as a VM at a hosting
provider with no nested-virtualization flags exposed. KVM, and anything
that depends on it (Firecracker, Cloud Hypervisor, Kata with KVM, nested
libvirt/KVM), is not an option and was not pursued further once this was
confirmed — hardware virtualization requires the *host* kernel to expose
VT-x/AMD-V; software emulation (QEMU's TCG) does not.

QEMU's TCG (Tiny Code Generator) backend translates guest instructions in
software. It needs no `/dev/kvm`, no host CPU virtualization extensions,
and — critically for this environment — **no host privilege at all**.

### 2.2 QEMU needs no privilege — a design-changing realization mid-build

The original plan (matching the operator's own early framing: "reuse the
existing container ... hand that image to a QEMU process in the same or a
sibling container") was to add QEMU into the existing privileged systemd
container, since that container already has loop-device access for
image customization. That plan was implemented once (packages added to
`../Dockerfile`), then reverted, because of a realization made while
actually building it: `qemu-system-x86_64 -accel tcg` reads and writes
qcow2 disk image files directly, in userspace. It needs no loop device
(there is no host block device involved in booting a qcow2 file at all),
no `/dev/kvm`, no extra capability beyond what any ordinary process has.
The privileged container's whole reason to exist — real loop devices,
real devtmpfs, real systemd-as-PID1 — is irrelevant to running QEMU.

This was proven empirically, not just reasoned about: the exact package
set this harness needs (`qemu-system-x86`, `qemu-utils`,
`cloud-image-utils`, `openssh-client`, `rsync`, `socat`) was installed
into a **bare `debian:trixie-slim` container with no `--privileged`, no
extra `--cap-add`, no device mounts of any kind**, and it booted a real
Debian 13 guest via TCG end-to-end (see §4 for the actual transcript).

Consequently, `testing/vm/` is a **separate, dedicated, genuinely
unprivileged image** (`testing/vm/Dockerfile`), decoupled entirely from
the sibling privileged container's own (unrelated, separately tracked)
startup problems on this host. This harness works regardless of whether
that other container can currently start at all.

### 2.3 The architecture

```
run-vm-harness.sh (wrapper, on the devcontainer host)
  └── docker exec → debian-install-vm-harness (persistent, unprivileged container)
        └── vmctl (the actual control script)
              ├── QMP (unix socket)  → qemu-system-x86_64 process lifecycle
              │                        (start/stop/reset/status)
              └── SSH (127.0.0.1:<port>, QEMU user-mode hostfwd)
                                       → guest operations (commands, file copy)

.vm/images/<case>-base.qcow2   (immutable, shared across every run)
.vm/runs/<run>/disk.qcow2      (disposable qcow2 CoW overlay, one per run)
.vm/runs/<run>/{qemu.pid, qmp.sock, serial.log, seed.iso}
.vm/ssh/id_ed25519{,.pub}      (one generated keypair, shared across runs)
```

Three deliberate separations, each solving a specific problem observed
while building this:

- **QMP for VM lifecycle, SSH for guest operations, never mixed.** QMP
  (QEMU's own machine-oriented management protocol, exposed on a unix
  socket via `-qmp unix:...,server=on,wait=off`) can query run state and
  request `system_powerdown`/`system_reset`/`quit` — it talks to the
  hypervisor, not the guest OS. Trying to do guest-level work through it
  would be fighting the wrong layer. SSH does everything inside the
  guest. `vmctl stop` tries QMP's graceful `system_powerdown` first (real
  systemd inside the guest gets a normal ACPI-triggered shutdown), falls
  back to SIGTERM then SIGKILL against the QEMU process itself if the
  guest doesn't respond in time — TCG-emulated shutdown can legitimately
  be slower than a graceful-shutdown timeout tuned for real hardware.
- **User-mode (slirp) networking, not TAP.** `-nic user,...,hostfwd=tcp:
  127.0.0.1:<port>-:22` is QEMU's built-in unprivileged NAT-like
  networking — no `/dev/net/tun`, no `CAP_NET_ADMIN`, no bridge, no
  iptables management, and forwarded ports bind to `127.0.0.1` only
  (never exposed beyond the runner container, let alone the host). This
  is sufficient for everything this harness currently needs: SSH into one
  guest at a time. TAP networking (a real virtual NIC on a host bridge)
  would only be needed if a test needed the guest reachable as its own
  network node from multiple directions, or several guests talking to
  each other — a real privilege increase, deliberately not implemented
  until an actual test needs it.
- **Immutable base images + disposable overlays, never write to a base.**
  See §3.

### 2.4 vmctl: the abstraction

A test (or a human) never constructs a `qemu-system-x86_64` command line
directly. `vmctl` exposes: `prepare-base <case>`, `start <run> [--case
case-b] [--ssh-port N] [--mem MB] [--smp N]`, `wait <run> [--timeout-s
N]`, `ssh <run> [-- cmd...]`, `copy <run> <src> <dst>`, `console <run>`,
`status <run>`, `stop <run>`, `destroy <run>`. This mirrors a pattern from
an independent design discussion the operator had in parallel (compared
against this implementation, 2026-09-09) that converged on the same
shape: keep the QEMU command line, the qcow2 CoW mechanics, and the QMP
protocol details behind one small script, so tests only ever see
`vmctl start/wait/ssh/destroy` — if hardware virtualization ever becomes
available on this estate, the only change needed is swapping `-accel
tcg,thread=multi -cpu max` for `-accel kvm -cpu host` inside `vmctl`
itself; nothing that calls it needs to know or care.

## 3. Base images and copy-on-write resets

### 3.1 The mechanism (already built, already proven)

```bash
qemu-img create -f qcow2 -F qcow2 -b <base>.qcow2 <run>/disk.qcow2
```

This creates a qcow2 file that stores only the *blocks that differ* from
its backing file (`<base>.qcow2`), which is opened read-only and never
modified. Booting the guest against the overlay makes every write during
that test run land in the overlay only. `vmctl destroy <run>` deletes the
overlay; the next `vmctl start` creates a fresh, byte-for-byte-identical
one from the same base in well under a second (it's a small metadata
file, not a copy of the whole disk). This is the actual "CoW reset" the
operator asked about, and it already works exactly this way today — there
is nothing further to build for the *mechanism*; what's still open is
which base images exist (see §3.3) and what tests actually run against
them (see §5).

### 3.2 Does this match what Netcup actually provides?

**Yes, to the extent that matters, and here is the reasoning, not just
the assertion.** `download-base-image.sh` (which `vmctl prepare-base`
calls) fetches Debian's own official `genericcloud` qcow2 image directly
from `cloud.debian.org` — not something built from scratch with
`debootstrap`. This is a deliberate choice: hosting providers that offer
"Debian" as a selectable OS overwhelmingly deploy from exactly this
family of pre-built cloud images (built with cloud-init already
integrated, virtio drivers built in, sized to grow into whatever disk
they're given) rather than running a full installer per deployment — it's
faster, more reproducible, and is precisely what these images exist for.
Corroborating evidence: the reinstall task's own step names, verbatim
from a real Netcup API response captured this session (e.g. `.../
scratchpad/live6-r1002-caseB.log`, a `--monitor` run's raw JSON —
ephemeral session scratch, not checked into this repo, but independently
re-obtainable by anyone with API access by firing a real reinstall and
inspecting the task JSON) —
```json
{"name": "ServerImageSetupTaskStepSetupImage", ...}
{"name": "ServerImageSetupTaskStepFixNetworkDriver", ...}
{"name": "ServerImageSetupTaskStepFixStorageDriver", ...}
```
— describe exactly the shape of "take a stock cloud image, apply
provider-specific driver/network fixups for our specific KVM setup, boot
it," not "run an OS installer from scratch."

What we've directly, independently confirmed matches real hosts this
session, from many live `sfdisk`/`lsblk`/`dpkg` checks against v1001 and
r1002:
- `virtio` block device naming (`/dev/vda`) — the harness's guest boots
  with the identical device naming (confirmed live, see §4, which runs
  `lsblk` but not `sfdisk`/`blockdev` — GPT-vs-MBR and sector size for the
  harness's OWN guest specifically were not directly re-checked in that
  same transcript, though they match what the `genericcloud` image is
  documented to ship and what every real host checked this session used).
- Kernel: the harness's smoke-test guest ran `6.12.107+deb13-cloud-amd64`
  — the same `-cloud-` kernel flavor and the same version family observed
  on the real, currently-provisioned hosts.
- Case B's real starting shape — root partition already filling the disk
  — is *exactly* what booting the plain, unmodified `genericcloud` image
  produces (cloud-init's `growpart` grows root to fill whatever disk size
  it's given, which is exactly Case B's defining starting condition). No
  synthetic construction needed for this case at all; the stock image
  already *is* the right base image.

What is **not** yet independently verified, and is worth someone
checking if strict fidelity ever matters (e.g. a test that starts failing
in the harness but not on a real host, or vice versa): whether Netcup's
own `FixNetworkDriver`/`FixStorageDriver` steps inject anything beyond
driver/network plumbing that `debian-install-v2` itself depends on (they
almost certainly don't touch partitioning or package state, which is what
this project actually cares about, but this is an assumption, not a
proven fact) — and whether Netcup pins a specific point release of the
`genericcloud` image rather than always deploying current `latest` (this
harness always downloads current `latest`, matching `download-base-
image.sh`'s existing behavior; a version skew here is possible but would
show up as a real Debian point-release difference, not a
Netcup-vs-Debian difference).

### 3.3 What's built vs. what's designed but not yet built

| Case | Base image | Status |
|---|---|---|
| **Case B** (root fills disk) | Plain, unmodified `genericcloud` image | **Implemented, live-tested.** `vmctl prepare-base case-b` stages it as-is — no customization needed, because the stock image's own default behavior (`growpart` fills the disk) already *is* Case B's starting shape. |
| **Case A** (small root + free trailing space) | Needs a two-stage build | **Designed, not yet implemented.** `vmctl prepare-base case-a` currently `die`s with a clear message rather than doing something wrong. |

The Case A recipe, worked out but not yet automated into `vmctl`:

1. Boot the stock image at a *small* disk size (e.g. resize the qcow2 to
   ~16G first) and let cloud-init's `growpart`/`resize_rootfs` do their
   completely normal thing — this gives a real, normally-provisioned
   ~16G root, exactly like a genuine small first boot.
2. Inside that booted guest, **permanently disable** further growth:
   `/etc/cloud/cloud.cfg.d/99-....cfg` with `growpart: {mode: off}` and
   `resize_rootfs: false`, plus `touch /etc/growroot-disabled` (cloud-init
   documents this file as its own explicit growroot-disable mechanism,
   read by the initramfs growroot integration too, not just cloud-init
   itself). Shut down.
3. From the host side (not inside the guest): `qemu-img resize <qcow2>
   64G` — the disk is now logically 64G, root is still ~16G. This is the
   step that actually produces free trailing space; nothing inside the
   guest needs to run for it.
4. Boot once more and run `sgdisk -e /dev/vda` to relocate the *backup*
   GPT header to the real new end of the disk — the old backup header
   still describes the disk's original, smaller size and needs to be
   told the disk grew, or partitioning tools downstream will notice the
   inconsistency. This does not touch or enlarge any partition.
5. Shut down, then `qemu-img convert -O qcow2 <prep-chain> case-a-
   base.qcow2` on the host to flatten the whole preparation chain into a
   single, clean image with no dependency on the intermediate steps.

This is genuinely the only case-shape-specific customization step in the
whole design — Case B needs zero base-image customization because the
stock image already matches it.

## 4. Detailed walkthrough (what was actually run, live, 2026-09-09)

This is the real transcript of the harness's first end-to-end proof,
not an idealized example.

```
$ ./run-vm-harness.sh prepare-base case-b
+ starting persistent VM-harness runner (debian-install-vm-harness)
+ fetching current SHA512SUMS
+ staging /work/.cache/debian-13-genericcloud-amd64.qcow2 -> /work/vm/.vm/images/case-b-base.qcow2 (copy, not symlink: ...)
+ case-b base ready: /work/vm/.vm/images/case-b-base.qcow2 (root fills the disk -- ...)
```

`download-base-image.sh` fetched and SHA512-verified the current Debian
13 `genericcloud` qcow2 (~339MB) and staged a copy under `.vm/images/`.
The copy (not a symlink) is deliberate: `.vm/images/case-b-base.qcow2` is
the thing every overlay is backed by and must never move or change out
from under a running test; the original download cache under
`testing/.cache/` can be independently re-verified or refreshed later
without touching an in-use base.

```
$ ./run-vm-harness.sh start smoke1 --case case-b --mem 1024 --smp 1
+ generating VM fixture SSH key (/work/vm/.vm/ssh/id_ed25519)
+ creating CoW overlay over /work/vm/.vm/images/case-b-base.qcow2
+ starting qemu-system-x86_64 (TCG, no KVM) -- ssh on 127.0.0.1:2222
+ started (pid 64); vmctl wait smoke1 to block until SSH is reachable

$ ./run-vm-harness.sh wait smoke1 --timeout-s 120
+ SSH reachable after ~66s; waiting for cloud-init to finish
+ run 'smoke1' ready
```

~66 seconds from `start` to SSH-reachable, on a single emulated vCPU with
1GB RAM — this is TCG's real cost (every guest instruction is translated
in software), and is exactly why `vmctl` defaults to modest resources and
`README.md`/this document both call out: don't throw more vCPUs at TCG
expecting proportionally faster boot; reducing guest activity (fewer
services, no GUI, a prepared/pre-warmed base once test scenarios exist)
matters more than raw core count.

```
$ ./run-vm-harness.sh ssh smoke1 -- 'uname -a; findmnt -n -o SOURCE /; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS; df -h /'
Linux vbpub-test 6.12.107+deb13-cloud-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.107-1 (2026-08-29) x86_64 GNU/Linux
/dev/vda1
NAME     SIZE TYPE FSTYPE  MOUNTPOINTS
sr0      366K rom  iso9660
sr1     1024M rom
vda        3G disk
├─vda1   2.9G part ext4    /
├─vda14    3M part
└─vda15  124M part vfat    /boot/efi
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda1       2.8G  595M  2.1G  23% /
```

A real Debian 13 guest, real `-cloud-` kernel, `/dev/vda1` mounted `/`,
root filling the (default, un-resized) 3G disk — exactly Case B's real
starting shape, confirmed by an actual boot rather than assumed.

```
$ ./run-vm-harness.sh destroy smoke1
+ requesting graceful shutdown via QMP
+ graceful shutdown didn't land in time -- SIGTERM
+ still alive -- SIGKILL
+ destroyed run 'smoke1'
```

The QMP graceful path fell back to SIGTERM here (see §2.3's note on TCG
shutdown latency and the 15-second poll window; `qmp_cmd`'s response is
now captured and logged on a non-success reply, added after an
independent review of this harness flagged the original fire-and-forget
version as undiagnosable). SIGKILL against a throwaway qcow2 overlay
that's about to be deleted anyway is harmless — there is no filesystem
consistency requirement to preserve here, unlike a real disk.

## 5. What this harness does not yet do

Only the harness plumbing is built and proven: stage a base image, boot
it, SSH in, tear it down cleanly, do it again cheaply via a fresh
overlay. The actual acceptance tests this whole thing exists for are not
built yet. The suggested first milestone (matching the design discussion
this implementation was cross-checked against): a single `case-a-
base.qcow2` plus one test script proving, in sequence —

1. `/` is mounted from `/dev/vdaN` (fixture sanity: this is the load-
   bearing property `inuse_partition_editor.py` depends on that no other
   test tier can exercise).
2. A dry-run (`inuse_partition_editor.py ... add-swap ... ` with no
   `--commit`) leaves the GPT byte-for-byte logically unchanged.
3. `--commit` adds a live `/dev/vda(N+1)` swap partition without
   disturbing the mounted root.
4. `systemctl reboot` inside the guest, then re-establish SSH and confirm
   root is still correctly mounted, the partition table is still valid
   from a cold kernel read, and the swap partition activates again from
   its `fstab`/label entry.

Once that works, the full `debian-install-v2` install → reboot →
automatic stage2 flow (already proven live on real Netcup hosts, see
`resume-2026-09-08-netcup-debian-install-v2-livetest.md` for that
history) is the natural next thing to run inside this same harness — the
same acceptance shape, just driving the real installer instead of
`inuse_partition_editor.py` directly, and without needing a real,
billable Netcup host or a live production reboot to prove it.

## References

- `README.md` (this directory) — command reference.
- `resume-2026-09-08-netcup-debian-install-v2-livetest.md` (project
  memory) — the full live-testing history this harness grew out of,
  including the incident writeup in more operational detail.
- `cgroupns-host-loop-device-host-hang-incident.md` (project memory) —
  the incident's standing prevention rules, estate-wide, not specific to
  this project.
- `../CASE-B-ROOT-SHRINK-DESIGN.md` — the root-shrink mechanism itself
  (the initramfs hook this harness is meant to eventually test directly).
