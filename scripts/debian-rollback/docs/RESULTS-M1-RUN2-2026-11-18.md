# M1 run 2: install, first boot, apt snapshot and rollback in a guest (2026-11-18)

Result: **the btrfs + own-snapshot + ESP-GRUB variant works end to end for the
scenarios below on Debian 13 under UEFI (TCG guest).** An install from the scripts
of `fe6e07bdd` (reviewed at 20e726aa0) plus `249c211cf` (ifupdown change, not reviewed
before the run; the installer at HEAD, which also adds the updates and security
suites, is not the one that ran) boots, an `apt install` takes a snapshot first, the earlier state
boots from the menu with its own kernel, data outside the state survives, `/etc`
comes back as it was, and the earlier state can be made the default.

This is the snapper-free variant (see [run 1](RESULTS-M1-RUN1-2026-11-18.md)):
states are `btrfs subvolume snapshot` copies made by `poc/dr-state`; snapper is
installed but not used. systemd-boot and ZFS are not yet tried.

## Setup

As in run 1 (v1001, `poc/dr-qemu.sh`, vm-lab not used, 2 vCPUs, 2 GiB, limits and
by-hand admission logged), with these changes:

- Scripts as merged in `fe6e07bdd` plus `249c211cf` (ifupdown + isc-dhcp-client and
  `net.ifnames=0`, replacing systemd-networkd/resolved, see Findings).
- **2a, install** (`drb2`, `seed4.iso` built by `poc/build-seed.sh`): `DR-BUILD-DONE
  rc=0` after 1220 s of guest time. First attempt failed in debootstrap, see
  Findings.
- **2b, boot and scenarios** (`drb3`): the installed disk as a read-only qcow2 base
  with a fresh overlay, 07:26:25Z to 07:30:47Z, driven over the guest's serial
  socket by `poc/dr-serial.py` (login as root, marker-based commands; transcripts
  are the evidence, kept in `artifacts/debian-rollback/m1-run2b/`, local).
- Slice rule of the shared host: stop above 5.4 GB anon+shmem or 6.2 GB
  `memory.current` of `dev-gates.slice`; never fired.

## What passed, and the evidence behind each line

| Scenario | Check | Evidence in the transcript |
|---|---|---|
| first boot | the ESP GRUB image loads the generated menu and boots `@os-0001` | serial: "GNU GRUB version 2.12-9+deb13u2", entry "Debian @os-0001 (kernel 6.12.107+deb13-amd64)", then `Welcome to Debian GNU/Linux 13 (trixie)` |
| first boot | root is the state subvolume | `findmnt`: `/dev/vda2[/@os-0001]`, `rootflags=subvol=@os-0001` on the command line |
| first boot | systemd healthy, network, name resolution | `is-system-running`: `running`; `eth0 UP 10.0.2.15/24`; `getent hosts deb.debian.org` answers |
| layout (D18) | outside paths are separate mounts, `/opt` and `/etc` inside | `mountpoint -q` for `/home /root /srv /var/www /var/log /var/lib/{docker,containers,postgresql,mysql} /usr/local /.top` all true; `/opt`, `/etc` false |
| S2 | `apt install` snapshots the running state first | `dr-state list` shows `@os-pre-20261118072759-647-apt` after installing `hello`; menu has two entries |
| S3 | the earlier state boots from the menu with its own kernel | `/proc/cmdline` has `rootflags=subvol=@os-pre-…-apt`, `BOOT_IMAGE=/@os-pre-…/boot/vmlinuz-6.12.107+deb13-amd64`; `hello` is absent |
| S3 | data outside the state survives | `/home/marker` holds the line written after the snapshot; `/srv/marker`, `/var/lib/docker/marker` present |
| S10 | `/etc` is the earlier one | `/etc/dr-etc-marker` holds `etc1` only; the later `etc2` line is gone |
| S4 | making the earlier state permanent | after `dr-state set-default`, a second boot sequence (GRUB banner, kernel start, `login:`) follows in the transcript and the command line still has the earlier state. **Caveat:** the PASS lines of this run came from the first driver version, which did not prove a reboot by boot id and had checks that could match the echoed command line; the rows above rest on the transcript text I read, not on those PASS lines. The strengthened driver (tagged values, boot-id proof) re-ran the same scenario in a fresh guest and passed: see [run 4](RESULTS-M1-RUN4-S2S3-S5-2026-11-18.md). |

## Findings

1. **systemd-resolved fails to configure inside the debootstrap chroot** (run 2a,
   first attempt: "Failure while configuring base packages … possibly the package
   systemd-resolved is at fault"). Cause not investigated; the installer now prints
   the tail of `debootstrap.log` on failure. The PoC uses ifupdown + isc-dhcp-client
   and `net.ifnames=0`. systemd-networkd/resolved remains the better answer for the
   product and is an open item.
2. The grub-mkimage standalone image with prefix `(,gpt1)/EFI/dr` works under OVMF
   (modules `part_gpt fat btrfs search search_fs_uuid normal linux configfile echo
   test ls loadenv serial terminal efi_gop gzio`); `load_env` and the menu logic
   (`saved_entry`, `next_entry`) behaved as written for boot-once and set-default.
   Secure Boot is not involved (no signed image yet, F18).
3. On the real debootstrap failure the cleanup trap unmounted the target (the retry was
   not blocked by stale mounts), and the wipe guard's pass path ran against the real
   `lsblk`, `swapon` and `findmnt`. The guard's refusal paths were not exercised in a guest.
   `poc/dr-pick-disk` is not called by anything yet.
4. Kernel in the target is 6.12.107 (debootstrap's `linux-image-amd64`); a newer
   kernel from updates was not installed in this run.
5. Slice memory: page cache counted in `memory.current` brought the shared slice near
   the first 5.5 GB line during the install; the rule was changed to anon-based.
6. Driver bugs found by use: `login(1)` discards input typed while it sets up the
   session (probe until the shell answers); a second scenario must accept an
   existing shell.

## Not yet verified

- A **kernel upgrade** (`apt full-upgrade`): that the kernel and apt hooks add the new
  kernel to the menu and keep the old state's entry bootable; S5 (broken initramfs
  and recovery through the menu), S6 (kernel removal), S7 (unattended upgrade), S8
  (many states, pruning), S9 (full disk), S11 (backports kernel), S12 (phase 2).
- That a one-shot `boot-once` returns to the default on the **next** reboot (the
  transcript does not exercise it: the next step was `set-default`).
- Other candidates: btrfs with systemd-boot, ZFS boot environments with
  ZFSBootMenu or GRUB, snapper on the flat layout, Secure Boot, encryption.
- Anything on real hardware, or with KVM (all runs are TCG).
