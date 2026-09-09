# QEMU/TCG test-VM harness

A genuinely isolated test environment for anything that must never run
against this shared devcontainer host's own kernel: `inuse_partition_
editor.py`'s real `add-swap --commit` path (real `mkswap`+`swapon`), and
eventually the full installer's live-root-disk-edit-then-reboot flow.

## Why this exists, and why it isn't the privileged container

Linux does not namespace loop devices or swap at all — there is no
per-container loop-device pool and no per-container swap concept.
`swapon(2)` adds a region to the ONE machine-wide page-reclaim pool the
kernel uses for every process on the box, containerized or not. A
`--privileged` docker container is the same host kernel with namespace/
capability wrappers around it, not a separate one — so the sibling
`../` privileged systemd container (built for `inuse_partition_editor.py`'s
real `--commit`-path partition tests) can never safely run `add-swap
--commit` for real. Confirmed the hard way, 2026-09-09: it did, and the
resulting host-wide swap leak hung this shared host badly enough to need
an operator reboot — see `resume-2026-09-08-netcup-debian-install-v2-
livetest.md`'s incident section and `cgroupns-host-loop-device-host-hang-
incident.md` in project memory. A QEMU guest has its own, genuinely
separate kernel; swap activated inside it is the guest's own, never the
host's. Those two swap-activating tests in `test_inuse_partition_editor_
r1.py` are hard-gated behind `VBPUB_ALLOW_HOST_GLOBAL_SWAP_TEST=1` for
exactly this reason — only ever set that inside a guest booted by this
harness, never against the privileged container or the bare host.

`qemu-system-x86_64 -accel tcg` (software CPU emulation) needs **no host
privilege at all** — no loop devices, no `/dev/kvm`, nothing the sibling
privileged image's whole reason-to-exist provides. This harness is
therefore a plain, unprivileged image, decoupled from that other
container's own (separate) startup concerns, and works regardless of
whether that container can currently start on this host.

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

`run-vm-harness.sh` builds (if needed) and reuses one persistent runner
container (`debian-install-vm-harness`) across invocations — a VM started
by one call needs to still be reachable by a later `wait`/`ssh`/`destroy`
call against the same container. `./run-vm-harness.sh --stop-daemon` tears
that runner down; the next command auto-starts a fresh one.

## `vmctl` directly (inside the runner container)

`run-vm-harness.sh <args>` is exactly `docker exec <runner> ./vmctl <args>`
plus the one-time setup. If you're already inside the runner container
(`docker exec -it debian-install-vm-harness bash`), just run `./vmctl`
directly from `/work/vm`.

Subcommands: `prepare-base <case>`, `start <run> [--case case-b]
[--ssh-port N] [--mem MB] [--smp N]`, `wait <run> [--timeout-s N]`,
`ssh <run> [-- cmd...]`, `copy <run> <src> <dst>`, `console <run>`,
`status <run>`, `stop <run>` (graceful QMP `system_powerdown`, falling
back to SIGTERM then SIGKILL), `destroy <run>` (stop + remove all state).

State lives under `.vm/` (gitignored): `.vm/images/<case>-base.qcow2`
(immutable, shared across runs), `.vm/runs/<run>/` (disk overlay, cloud-init
seed, QMP socket, pidfile, serial console log — everything for one
disposable VM), `.vm/ssh/` (one generated SSH keypair, shared across runs).

## Base images

- **`case-b`** (implemented): the plain, unmodified Debian genericcloud
  qcow2 — root fills the disk, exactly Case B's real starting shape on a
  freshly-imaged host. `prepare-base case-b` calls the sibling
  `../download-base-image.sh` and stages a copy under `.vm/images/`.
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

## What this harness does NOT do (yet)

Only the harness plumbing (boot a base image, SSH in, tear down) is
built and proven. The actual acceptance tests this exists for — installing
`inuse_partition_editor.py`'s real generated hook/plan into a customized
image, or running the full `debian-install-v2` installer end-to-end inside
a guest and verifying it survives its own reboot into stage2 — are not
built yet. See project memory for the suggested first milestone (mount-
source check → dry-run is byte-identical → `--commit` adds swap without
disturbing `/` → reboot preserves it).
