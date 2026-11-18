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
- **vm-lab was NOT used.** `vm_lab start` must run inside a cockpit container
  (`inspect_current_container()`, `vm-lab/src/vm_lab/core.py:682`, which inspects
  every running container) and needs a container with host cgroupfs and the
  D-Bus socket (`find_cgroup_inspector()`). Neither exists on a bare host.
  `poc/dr-qemu.sh` mirrors vm-lab's `docker run` argv and QEMU command (main
  5b107f4bd, core.py ~1740-1830 and ~1905-1950) without its admission checks;
  `poc/test-dr-qemu.sh` pins the hardening flags. Admission was done by hand and
  logged: one guest, `dev-gates.slice` memory below 3.5 GB, memory PSI full
  avg10 below 3.
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
  Debootstrap `--include`: `linux-image-amd64 grub-efi-amd64 btrfs-progs snapper
  initramfs-tools systemd-sysv openssh-server sudo locales ifupdown
  isc-dhcp-client dosfstools ca-certificates`.
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

## What has and has not run in a guest

Ran in a guest, once: `poc/install-btrfs.sh` (all of it), and in the chroot the
`install-grub` and `gen-menu` subcommands of `poc/dr-state`.

**Never run in a guest:** booting the installed disk; `dr-state snapshot`,
`list`, `set-default`, `boot-once`; the apt hook
`/etc/apt/apt.conf.d/80dr-state`; snapper; the ESP GRUB image
(`grub-mkimage`, prefix `(,gpt1)/EFI/dr`) actually starting; every scenario S2
to S10 of [EXPERIMENTS.md](EXPERIMENTS.md); `dr-qemu.sh --resume` and
`--base-qcow2`.

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
