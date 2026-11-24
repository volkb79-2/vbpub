# M1 run 1: installer in a guest (2026-11-18)

Result: **success, first try.** `poc/install-btrfs.sh` installed Debian 13 on a
flat btrfs layout onto a blank 8 GiB disk inside a TCG guest and ended with
`DR-BUILD-DONE rc=0` after 21.4 minutes (05:49:45Z to 06:11:08Z). The installed
system has **not been booted yet**; nothing about GRUB, the rollback menu or
snapshots is verified.

## Setup (reproducible)

- Host: v1001 (rented VM, 4 cores, 7.9 GB, no `/dev/kvm`, so TCG), user `runner`,
  work dir `/home/runner/jobs/dr-poc`. Identity-guarded access only
  (`scripts/controller/hx.sh`).
- **vm-lab was NOT used.** At that time `vm-lab start` required a cockpit container
  (`inspect_current_container()`, `vm-lab/src/vm_lab/core.py:682`, which inspects
  every running container) and needs a container with host cgroupfs and the
  D-Bus socket (`find_cgroup_inspector()`). Neither exists on a bare host.
  The run therefore used a historical direct launcher that mirrored vm-lab's
  `docker run` argv and QEMU command (main 5b107f4bd, core.py ~1740-1830 and
  ~1905-1950) without admission checks. The supported command is now
  `vm-lab/vm-lab start --plain-host`; the copied launcher and its flag test are retired.
  Differences from vm-lab: no `--label` pair (vm-lab's cleanup and admission
  never see these containers), no `--rm` (a restart under the same name needs the
  old container removed by exact name first), the disk is copied inside the
  container instead of reflinked on the host, and the data disk is created by the
  guest script. Admission was done by hand and logged: one guest,
  `dev-gates.slice` memory below 3.5 GB, memory PSI full avg10 below 3.
- Guest: 2 vCPUs, 2 GiB, container 2560 MiB with equal swap, `--cpus 2`,
  `--cpu-shares 64`, `--cgroup-parent dev-gates.slice` (no `dev-background.slice`
  on v1001), network `dr-poc-net` (normal bridge, no published ports).
  Disk: the registered raw Debian 13 genericcloud base (sha256 `f9856cf9...`),
  copied into the state dir inside the container (v1001 is ext4, no reflink),
  `cache=unsafe`. Seed: NoCloud ISO (`cidata`) that installs the tools and runs
  the installer on the blank second disk.
- Packages: from `http://deb.debian.org/debian`, suite `trixie`, signature
  checks on (no `--allow-unauthenticated`). Installed into the builder:
  `debootstrap btrfs-progs gdisk dosfstools grub-efi-amd64-bin parted`.
  Debootstrap `--include` **as run at 6697fbbe0**: `linux-image-amd64
  grub-efi-amd64 btrfs-progs snapper initramfs-tools systemd-sysv openssh-server
  sudo locales ifupdown isc-dhcp-client dosfstools ca-certificates`. The current
  script uses `systemd-resolved` instead of `ifupdown` and `isc-dhcp-client`.
- Evidence (local, ignored): `artifacts/debian-rollback/m1-run1/serial.log`,
  `run1.log`.

## Observations

| Item | Result |
|---|---|
| genericcloud kernel (6.12.115+deb13.1-cloud) has btrfs | yes (`modprobe btrfs`, listed in `/proc/filesystems`) |
| GPT + ESP + btrfs, 11 subvolumes (`@os-0001` + 10 outside paths of D18) | created |
| debootstrap trixie with the include list | completed, about 18 min of the 21 |
| `dr-state install-grub` and `gen-menu` in the chroot | exited 0 |
| Peak guest load | 186% CPU of 200%, 1.78 GiB of 2.5 GiB |

## What this run does and does not evaluate

The PoC tool is a **snapper-free variant**: states are plain `btrfs subvolume
snapshot` copies made by `dr-state`, and the menu comes from `dr-state gen-menu`.
`snapper` is installed in the target but has no configuration and no hook, so
the "btrfs + snapper + GRUB" row of the M1 plan is **not yet evaluated**. The
snapper evaluation (F7: snapper for taking, listing and pruning on the flat
layout) follows after the boot mechanics are proven.

## Review follow-ups (2026-11-18, after this run)

An independent read of the first head (6697fbbe0) found problems that run 1 did
not exercise. They are fixed in the scripts as committed after this run, so the
installed disk of run 1 does NOT contain these fixes and run 2 starts with a
fresh install: the boot menu is refreshed by kernel and apt hooks (a menu naming
a removed kernel does not boot) and refuses to be written empty; the target gets
DHCP through systemd-networkd and systemd-resolved (run 1's guest has no
network configuration); the wipe guard fails closed and checks swap, the running
root and that the device is a whole disk; a failed install unmounts its target;
`dr-state` takes a lock, requires the ESP to be mounted, and gives snapshots
unique names (`DR_STATE=none` skips the snapshot in a recovery chroot).
vm-lab's plain-host boundary tests now pin each launcher setting together with
its value. The cloud-init
seed that drove run 1 is committed as `poc/seed/`.

## What has and has not run in a guest

Ran in a guest, once, **as committed at 6697fbbe0** (the scripts have changed
since, see the follow-ups above): `poc/install-btrfs.sh` (all of it), and in the
chroot the `install-grub` and `gen-menu` subcommands of `poc/dr-state`.

**Never run in a guest, and new since that run:** the fail-closed wipe guard and
the cleanup trap, the networkd/resolved setup, the kernel and apt hooks, the
`dr-state` lock, ESP check and unique snapshot names, `DR_STATE=none`, the
refusal of an empty menu, `build-seed.sh` and the committed seed (a byte-identical
match with the seed of run 1 is not checked).

**Never run in a guest, in any version:** booting the installed disk; `dr-state snapshot`,
`list`, `set-default`, `boot-once`; the apt hook
`/etc/apt/apt.conf.d/80dr-state`; snapper; the ESP GRUB image
(`grub-mkimage`, prefix `(,gpt1)/EFI/dr`) actually starting; every scenario S2
to S10 of [EXPERIMENTS.md](EXPERIMENTS.md); the launcher's `--resume` and
qcow2-base path.

## Findings for vm-lab (owner: controller)

1. `start` needs a cockpit container and a cgroup-inspector container, and
   inspects all running containers; not runnable on bare hosts.
2. No cpu-weight or cpu-shares option.
3. A raw base needs btrfs for reflink; the refusal does not name btrfs as the
   cause.
4. `dev-background.slice` is assumed; hosts without host-setup have none.
5. A Docker network created `--internal` gives no egress; the README does not
   say so.
6. The admission lock is only shared inside a git checkout; an exported source
   tree gets its own lock directory.

## Next

Run 2: boot the installed disk (`installed-btrfs-1.qcow2` as a read-only qcow2
base) and check the ESP menu, then S2, S3, S5, S10. Waits for the controller's
go (v1001 is shared).
