# QEMU/TCG test-VM harness

A genuinely isolated test environment for anything that must never run
against this shared devcontainer host's own kernel: `inuse_partition_
editor.py`'s real `add-swap --commit` path (real `mkswap`+`swapon`), and
eventually the full installer's live-root-disk-edit-then-reboot flow.

## Why this exists, and why it is not a privileged container

Linux does not namespace loop devices or swap at all — there is no
per-container loop-device pool and no per-container swap concept.
`swapon(2)` adds a region to the ONE machine-wide page-reclaim pool the
kernel uses for every process on the box, containerized or not. A
`--privileged` docker container is the same host kernel with namespace/
capability wrappers around it, not a separate one — so the historical
privileged systemd container (built for `inuse_partition_editor.py`'s
real `--commit`-path partition tests) could never safely run `add-swap
--commit` for real. Confirmed the hard way, 2026-09-09: it did, and the
resulting host-wide swap leak hung this shared host badly enough to need
an operator reboot — see `resume-2026-09-08-netcup-debian-install-v2-
livetest.md`'s incident section and `cgroupns-host-loop-device-host-hang-
incident.md` in project memory. A QEMU guest has its own, genuinely
separate kernel; swap activated inside it is the guest's own, never the
host's. Those two swap-activating tests in `test_inuse_partition_editor_
r1.py` are hard-gated behind `VBPUB_ALLOW_VM_GLOBAL_SWAP_TEST=1` and a
QEMU/KVM virtualization check for exactly this reason — only ever set that
inside a guest booted by this harness, never against a container or the bare
host.

`qemu-system-x86_64 -accel tcg` (software CPU emulation) needs **no host
privilege at all** — no loop devices, no `/dev/kvm`, nothing the sibling
privileged image's whole reason-to-exist provided. This harness is therefore
a plain, unprivileged image and the only container used by this test path.

## Quick start

```bash
cd scripts/debian-install-v2/testing/vm
./run-vm-harness.sh prepare-base case-b   # one-time: download + stage the base image
./run-vm-harness.sh start smoke1 --case case-b
./run-vm-harness.sh wait smoke1
./run-vm-harness.sh ssh smoke1 -- uname -a
./run-vm-harness.sh copy smoke1 ./some-file /tmp/some-file
./run-vm-harness.sh console smoke1        # tail -f the serial console log
./run-vm-harness.sh destroy smoke1
```

For fast, repeated installer test iterations, use the "ready" base instead
of the raw stock image (see "Skipping repeated first-boot cost" below):

```bash
./run-vm-harness.sh prepare-ready-base case-b   # one-time: base-prep + cloud-init once + disable it
./run-vm-harness.sh start iter1 --case case-b-ready
./run-vm-harness.sh wait iter1     # boots straight past cloud-init entirely
./run-vm-harness.sh copy iter1 ../../debian_install_v2 /home/tester/debian_install_v2
./run-vm-harness.sh ssh iter1 -- 'cd debian_install_v2 && sudo python3 -m debian_install_v2 ...'
./run-vm-harness.sh destroy iter1   # the case-b-ready base itself is untouched -- next iter starts identical
```

`run-vm-harness.sh` builds (if needed) and reuses one persistent runner
container (a worktree-specific `debian-install-vm-harness-*`) across invocations — a VM started
by one call needs to still be reachable by a later `wait`/`ssh`/`destroy`
call against the same container. The runner enables Docker's init reaper so
QEMU's detached child does not accumulate as a zombie after a run.
`./run-vm-harness.sh --stop-daemon` tears that runner down; the next command auto-starts a fresh one. The standard
real-device test command is `./run-vm-tests.sh`; it copies the test package
into the guest and sets the VM-only opt-in there. That lane requires the
guest to report QEMU/KVM and all partition/swap tools, and verifies by JUnit
that both real commit tests actually passed. Its remote setup, copy, and test
phases are bounded; one run per worktree is admitted at a time.

The outer runner is placed in `$CGROUP_PARENT_DEV_BACKGROUND`; the wrapper
fails closed if that variable or the known interactive probe tier is absent or
not installed on the Docker host.

## `vmctl` directly (inside the runner container)

`run-vm-harness.sh <args>` is exactly `docker exec <runner> ./vmctl <args>`
plus the one-time setup. If you're already inside the runner container
(`docker exec -it <printed-worktree-runner-name> bash`), just run `./vmctl`
directly from `/work/vm`; `run-vm-harness.sh --daemon` prints the exact name.

Subcommands: `prepare-base <case>`, `prepare-ready-base <case>` (see below),
`start <run> [--case case-b] [--ssh-port N] [--mem MB] [--smp N]
[--no-apt-cache]`, `wait <run> [--timeout-s N]`, `ssh <run> [-- cmd...]`,
`copy <run> <src> <dst>`, `snapshot <run> <new-case>` (flatten a *stopped*
run's disk into a new runner-local `<new-case>-base.qcow2`), `console <run>`,
`status <run>`, `stop <run>` (graceful QMP `system_powerdown`, falling
back to SIGTERM then SIGKILL), `destroy <run>` (stop + remove all state).

## Skipping repeated first-boot cost: "ready" base images

`vmctl prepare-ready-base <case>` builds on `prepare-base`: boots the stock
base once, waits for cloud-init's full first-boot sequence to finish (see
"What happens before our own installer runs" below), then permanently
disables cloud-init (`touch /etc/cloud/cloud-init.disabled` + masking its
four unit files) and flattens the result into the runner-local
`<case>-ready-base.qcow2` via `vmctl snapshot`. Starting from `--case <case>-ready`
instead of the raw `<case>` skips cloud-init's network/user/SSH-key/apt-
sources setup entirely on every subsequent boot — this is the disk state
right at the moment a real host would hand off to this project's own
stage1, which is exactly the point: fast, deterministic, byte-identical
starting points for repeated installer-parameter test iterations.

## Caching apt traffic across resets

Every `vmctl start` (unless `--no-apt-cache`) lazily starts `apt-cacher-ng`
inside the runner container and pushes the guest's `Acquire::http::Proxy`
config over SSH once it's reachable (reachable at QEMU user-mode
networking's synthetic gateway address, `10.0.2.2`, from inside the
guest) — deliberately over SSH, not via cloud-init's `write_files`: a run
booted from a "ready" base (`prepare-ready-base`) has cloud-init
permanently disabled, so anything relying on cloud-init to land a file
would silently never apply there (live-confirmed 2026-09-09 — the first
version of this used `write_files` and the proxy config never actually
reached the guest on a `-ready` base until this was fixed).

This caches plain-HTTP traffic. HTTPS traffic is explicitly configured as
`DIRECT`, because apt-cacher-ng's HTTP proxy is not an HTTPS CONNECT endpoint;
that keeps Debian's HTTPS archive, security, and backports sources working
without making the cache a hidden availability dependency. Confirmed live:
the main `deb.debian.org` archive component can cache and install correctly
through it (e.g. `fio` installed via the proxy in under a minute). Also does
**not** cover
Docker's own apt repo (`https://download.docker.com`, HTTPS-only);
caching that would require rewriting debian-install-v2's own generated
`docker.sources` to use apt-cacher-ng's special `/HTTPS/` URL convention,
which isn't worth coupling the real installer to a test-harness detail for
a comparatively small, infrequently-hit package set. The cache lives inside the runner
container's own filesystem (not `.vm/`, not bind-mounted), so it persists
across VM resets within one runner lifetime but is lost on
`run-vm-harness.sh --stop-daemon`.

When invoked through `run-vm-harness.sh`, state lives in the persistent,
worktree-specific runner container under `/var/lib/mdt-debian-install-vm/`:
the base image is immutable and shared across runs, while each run's qcow2
overlay, cloud-init seed, QMP socket, pidfile, and serial log are disposable.
The SSH key and download cache are kept there too, so no root-owned generated
files are written into the judged worktree. Direct `vmctl` use without the
wrapper falls back to a local `.vm/` directory for interactive debugging.

## Base images

- **`case-b`** (implemented): the plain, unmodified Debian genericcloud
  qcow2 — root fills the disk, exactly Case B's real starting shape on a
  freshly-imaged host. `prepare-base case-b` calls the sibling
  `../download-base-image.sh` and stages a copy in the runner-local image
  directory.
- **`case-a`** (not yet implemented — `prepare-base case-a` says so and
  exits non-zero): needs a small root with real free trailing space,
  which the stock cloud image doesn't have (cloud-init's `growpart` grows
  root to fill whatever disk it's given). The recipe, not yet automated:
  1. Boot the stock image small (e.g. resize the qcow2 to ~16G first) and
     let cloud-init's `growpart`/`resize_rootfs` do their normal thing.
  2. Inside the guest, permanently disable both (`/etc/cloud/cloud.cfg.d/
     *.cfg`: `growpart: {mode: off}`, `resize_rootfs: false`, plus `touch
     /etc/growroot-disabled`) and shut down.
  3. From the host side, `qemu-img resize` the qcow2 up to the real target
     size (e.g. 64G) — root stays its original small size, the rest is
     free trailing space.
  4. Boot once more and run `sgdisk -e /dev/vda` to relocate the backup
     GPT header to the new real end of disk (the old header still
     describes the disk's original, smaller size).
  5. Shut down, `qemu-img convert -O qcow2` to flatten the prep chain into
     a single clean base image.

## Networking

QEMU user-mode networking (`-nic user,...hostfwd=tcp:127.0.0.1:<port>-:22`)
— fully unprivileged (no `/dev/net/tun`, no `CAP_NET_ADMIN`), forwarded to
`127.0.0.1` only, never published beyond the runner container. Good enough
for SSH-driven test fixtures; if a future need requires the guest to be
reachable as its own network node (multiple guests talking to each other,
arbitrary guest-side listening ports), that needs TAP + `CAP_NET_ADMIN` +
`/dev/net/tun` instead — not implemented here, ask before adding it (it's
a real privilege increase, unlike everything else this harness does).

## What happens before our own installer runs

On both a real Netcup host and this harness's own VM, cloud-init runs to
completion before anything of ours gets a chance to act — Netcup's own
provisioning API literally has a distinct `CloudinitWait` step, separate
from `SetupImage` (see `debian_install_v2/installer.py`'s
`_REBOOT_DELAY_SECONDS` comment, and `../DESIGN.md` for the raw JSON this
project captured from a live install task). The standard Debian
genericcloud `/etc/cloud/cloud.cfg` runs, in order:

1. `cloud-init-local.service` — finds the datasource (NoCloud/ConfigDrive
   on Netcup; this harness's own `seed.iso`) before networking exists.
2. `cloud-init.service` (network stage) — network bring-up, then modules
   including `growpart`/`resizefs`, `users-groups`, `ssh` (host key
   generation, `authorized_keys`).
3. `cloud-config.service` (config stage) — `apt-configure` (sources.list),
   `ssh-import-id`, `runcmd`.
4. `cloud-final.service` — `package-update-upgrade-install`, `scripts-user`
   (any final user-data script), `final-message`.

Working hypothesis, **not independently confirmed** against a real host's
actual `/etc/cloud/cloud.cfg` (flagging this as inference, not a checked
fact): `growpart`'s default behavior in stage 2 is why Case B (root filling
the whole disk) is the more common real-world starting shape rather than
the edge case its letter suggests — growpart grows root to fill whatever
disk it's handed unless something disables it first. Case A would then mean
either the disk wasn't grown for some other reason, or Netcup's own
provisioning disables/skips growpart for that host. Worth confirming
directly against a live host's `cloud-init analyze show` output next time
one is available, rather than relying on this inference.

Only after stage 4 completes is SSH reliably up with a stable environment —
which is exactly why `vmctl wait` already blocks on `cloud-init status
--wait` before returning control, and why `prepare-ready-base` snapshots
right after that point (see above).

## What this harness does NOT do (yet)

The standard `run-vm-tests.sh` lane now runs the real
`inuse_partition_editor.py` loop/swap commit tests in a Case-B guest. The
larger acceptance flow — booting a customized installer image, checking the
mount-source guard and byte-identical dry-run, then running the full
`debian-install-v2` stage1/stage2 install through its own reboot — is still
pending. Case A's small-root/free-tail base image is also designed but not
automated yet.
