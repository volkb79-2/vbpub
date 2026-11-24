# M2: unattended installer ISO, btrfs with snapshots (2026-11-18)

This is a dated evidence report for the GRUB-target Live ISO builds named
below. It does not describe the later systemd-boot PoC or the planned second
M2 ISO variant. The current layer and consumer flow is in
[INSTALLATION-ARCHITECTURE.md](INSTALLATION-ARCHITECTURE.md).

**What this ISO does NOT do** (read this first):
- **No offline install.** The installer downloads Debian from `deb.debian.org` (debootstrap, `apt`); without network it stops.
- **No Secure Boot.** The boot loader written to the disk is an unsigned GRUB image (`poc/dr-state install-grub`).
- **No ZFS.** btrfs only. **UEFI only**: the added boot entries exist in the ISO's GRUB menu, not in the BIOS (isolinux) menu.
- **No authentication of the config.** Whoever can attach a `CIDATA` medium, edit the ISO or serve the `dr.config` URL decides which disk is wiped (the wipe guard limits which disks qualify, not who asks). A `CIDATA` medium overrides the config embedded in the ISO.
- **No `debian-install-v2` post-configuration** (kept separate by decision D19), no cloud-init user-data, no SSH-key setting, md/LVM/dm/multipath/crypt devices are only noticed through a second full `lsblk` listing (an unset target then refuses); an EXPLICIT `TARGET_DISK` is not checked against them (it may be a member of an array).
- **Tested only in blank-disk guests** on v1001 (TCG, 2.5 GiB). Never on a real disk, never written to a device.
- A medium that stays attached **reinstalls on every boot when the config says `REINSTALL=yes`**; the default is to refuse a disk that already holds our system (label `drroot`) and to power off after a finished install.
- **Weak disk identity on virtual or serial-less disks.** The check that the target is still the same disk after the slow tool install compares size, serial, WWN and model; with no serial or WWN (QEMU's root disk) it is only size and model, so two equal disks that swap names between the two plans go undetected (a warning is printed). The refusal path itself ("target changed") has never run, not even offline.
- **A plaintext `ROOT_PASSWORD` is still allowed** and then sits in the config on the ISO or the `CIDATA` medium; `ROOT_PASSWORD_HASH` is the alternative.

## What it is

`iso/build-iso.sh` remasters the official Debian 13.7.0 live ISO (`debian-live-13.7.0-amd64-standard.iso`, sha256 checked against its `.sha256`) with `xorriso`. Nothing inside the squashfs or the initrd is rebuilt; the overlay is plain files:

| Added to the ISO | Purpose |
|---|---|
| `/dr/{dr-autoinstall,dr-plan,dr-pick-disk,install-btrfs.sh,dr-state}` | the installer payload (the PoC scripts of M1) |
| `/dr/dr-install.conf` (optional, `--config`) | config embedded in the ISO |
| `/live/config-hooks/10-dr-autoinstall` | live-config hook that starts the installer service |
| `/boot/grub/grub.cfg` | two new first entries: **UNATTENDED install** (default, 10 s) and a live system with the payload in `/run/live/medium/dr` |

The UNATTENDED entry adds `live-config.hooks=file:///run/live/medium/live/config-hooks/10-dr-autoinstall dr.mode=auto`. **Finding (live-config 11.0.5, trixie):** the natural `live-config.hooks=medium` runs nothing, because that branch looks in `/lib/live/mount/medium`, which does not exist on this image (the medium is `/run/live/medium`). Found in a guest (the hook never started); the `file://` form of the same script works.

Flow in the live system (`/opt/dr/dr-autoinstall`, one systemd service): DHCP → config (first a block device labelled `CIDATA` holding `dr-install.conf`, else the file embedded in the ISO, else `dr.config=https://…`) → `dr-plan` (the wipe guard, below) → `apt-get install debootstrap btrfs-progs gdisk dosfstools parted` → plan **again** and require the same disk (size, serial, WWN, model) → `poc/install-btrfs.sh --yes` → power off. Console markers for drivers: `DR-AUTOINSTALL-START / CONFIG / PLAN / CONFIRM / INSTALL / TARGET-CONFIRMED / REFUSED / DONE rc=N`.

## Config (`dr-install.conf`, `KEY=VALUE`)

Keys: `TARGET_DISK`, `HOSTNAME`, `SUITE`, `MIRROR`, `SECURITY_MIRROR`, `ROOT_PASSWORD`, `ROOT_PASSWORD_HASH` (crypt hash; use it instead of the plaintext), `REINSTALL` (`yes|no`). Unknown keys, duplicate keys and characters outside `A-Za-z0-9_./:@,+=-` (hash: `$` too) are refused. Without a password the installer generates one and prints it on the console (`DR-AUTOINSTALL-ROOT-PASSWORD-GENERATED`); it is also written to the live system's journal and `/var/log/dr-autoinstall.log` (RAM only, gone at power off, but visible to anyone with a console log).
`build-cidata.sh --config F --out IMG` makes a `CIDATA` ISO9660 image (for a USB stick write it yourself; the script refuses `/dev` paths).

## Disk rule (the wipe guard, `iso/dr-plan`)

A pure function of `lsblk` output, the config and the caller's exclusions; it never touches a disk.
- `TARGET_DISK=/dev/X` (kernel name) or `/dev/disk/by-*/NAME` (resolved on every plan): exactly that disk, if it is a whole disk, not removable, not USB, ≥ 8 GiB, not the medium or config stick, not mounted. Otherwise **refuse** (never falls back to another disk).
- `TARGET_DISK=first-ssd`: the operator's rule, explicit: first non-rotational disk (NVMe before SATA), else the first rotational one. It is a rule, not a named disk: it refuses when other storage (md/lvm/crypt/mpath) is present; it still cannot know whether the chosen disk is a member of an array that is not assembled.
- **`TARGET_DISK` unset: only if the host has exactly one internal disk of any kind** (small, removable, USB and mounted disks count; only the booted medium and the config stick do not) **and no other top-level storage device** (md raid, multipath, lvm, crypt). Two or more disks, or any such device: **refuse**. The check uses a second `lsblk` listing WITHOUT `-d` (`--lsblk-full`), because `lsblk -d` shows only top-level devices and would never list a raid/lvm/crypt device stacked on a disk; this was found in review round 14, the fixtures that first hid it were synthetic. The full listing carries `PKNAME`, so devices stacked only on an excluded disk (a crypt device on the config stick) are ignored; an unreadable or empty listing refuses. Offline only (fixtures shaped like real lsblk output); on a real live boot with a plain disk it counted no other storage (guest check at the final head); never seen with a real md/lvm device.
- **No config at all:** never unattended: a typed confirmation `ERASE <disk>` on the console (120 s), and only with a single internal disk.

## Results (v1001, blank disks only, one guest at a time)

Wipe-guard tests were written before `dr-plan` existed: in the session the first run of `test-dr-plan.sh` failed all 25 cases with `dr-plan: not found` (rc 127), then passed; later rounds added cases first and showed them failing before the fix. **This cannot be seen in the git history** (test and tool were committed together), and the mutants (multi-disk allowed, exclusions ignored, unknown key allowed, nall dropped, password+hash allowed, substring label match) were applied to temporary copies and are not kept in the repo; both claims rest on the session log. Offline tests in the repo: `iso/test-dr-plan.sh` (65 cases), `iso/test-dr-autoinstall-guard.sh` (the re-install guard with a stubbed `lsblk`, including lsblk failing = refuse), `poc/test-dr-pick-disk.sh`. The config-disk launcher path is covered by vm-lab's plain-host boundary tests.

| Guest | Setup | Result |
|---|---|---|
| 1 | one blank 20 GiB disk, config embedded in the ISO | `PLAN install /dev/vda`, `TARGET-CONFIRMED`, `install-btrfs.sh` exit 0, `DONE rc=0` after **1462 s** of guest time; 1.4 GB allocated on the disk |
| 2 | two blank disks, config without `TARGET_DISK` | `REFUSED` at **88 s**, nothing downloaded (792 bytes on the container network), host-side allocation afterwards: root disk 0 KiB, second disk 196 KiB (empty qcow2 header) |
| 3 | the disk from guest 1 booted alone | `boot-check` 8 PASS: kernel from `/@os-0001`, systemd running, DHCP and DNS, `grub.cfg` and `grubenv` on the ESP, outside paths mounted separately, `/opt` and `/etc` inside the state |
| 4 | a read-only `CIDATA` disk (`HOSTNAME`, `ROOT_PASSWORD`, `TARGET_DISK=/dev/disk/by-id/virtio-drdata001`) plus the blank root disk and a blank second disk; the ISO's embedded config names no target | `CONFIG source=cidata:/dev/vdb` (the stick wins over the embedded config), `TARGET-RESOLVED …/virtio-drdata001 -> /dev/vdc`, `PLAN install /dev/vdc`, `TARGET-CONFIRMED`, `DONE rc=0` after **1358 s**; host-side allocation: **root disk 0 KiB (untouched)**, target 1.4 GB |

Guests 1 to 4 ran on the ISO built at `6bffd67c3` (guests 1, 2, 3: earlier builds of the same logic; 4: the final one of that commit).

**Final-ISO re-run** (ISO built at `014b71a86`, sha256 `af748c93…`, which has the round 11 changes `--busy`, by-id resolved on every plan, the re-install guard as a function), both detached on v1001, one after the other, 17:54Z to 18:18Z:

| Guest | Setup | Result |
|---|---|---|
| 5 | two blank disks, no `TARGET_DISK` (as guest 2) | `REFUSED` at 87 s, `DONE rc=3`, nothing downloaded (792 bytes), root disk 0 KiB, second disk 196 KiB |
| 6 | as guest 4 (CIDATA + by-id target) | `TARGET-RESOLVED … -> /dev/vdc` **twice** (once per plan: the second plan re-resolves the link), `TARGET-CONFIRMED`, `DONE rc=0` after **1316 s**, root disk 0 KiB, download 199,177,494 bytes |

Later commits (`6bffd67c3` to `a50af75b9`: guard fails closed when `lsblk` fails, no `-d` in the guard's call, doc fixes) are **not** in any guest ISO; the guard function itself (`holds_our_system`) has only run through the offline stub test, never against a real `drroot` disk in a guest, and the busy-disk counting only offline.

Download size: **≈ 185 to 190 MiB** (194,223,362 bytes in guest 4, 199,177,494 bytes in guest 6, received on the guest container's network, last sample before the end of the install; the live ISO itself is 1.9 GB and is not downloaded).
Two guest runs were lost before these results to the shared host (a memory limit applied to page cache; and a guest at `--cpu-shares 64` starving beside another team's lane: RCU stalls, never booted). The launcher now stops a guest on memory PSI (`some avg10 > 10` for 3 samples) and the guests run alone.

## Review

Independent review rounds 10, 11 and 12 (fresh Sonnet session) were FIX-FIRST and found real holes. **Fixed, each with an offline test:** a medium left attached re-wiped the installed system (guard on label `drroot`; fails closed when `lsblk` fails); "only disk" counted only qualifying disks (a small boot disk beside a big data disk made the data disk look alone) and then ignored mounted disks (now `--busy`); and the "only disk" rule now also refuses when other storage stacked on a disk (md raid, multipath, lvm, crypt, seen through a full `lsblk` listing) is present and no target is named. **Fixed and checked by hand once, no test in the repo:** an unchecked `--scratch` deletion and a broken re-exec in `build-iso.sh` (run from another directory with a parent directory holding a file; the file survived). **Fixed without a dedicated test:** the target is re-planned after the slow tool install (ran in every guest, but the refusal on a changed target never did); CRLF in the by-id spec. **Mitigated, not closed:** the plaintext root password (a hash option exists, plaintext still works); the weak identity check on serial-less disks (a warning). **Open, documented:** console confirmation can be eaten by a getty on the same console (fails safe: refusal); no md/dm/LVM awareness for an explicit `TARGET_DISK` and no loop-backed-medium detection; the `dr.config=https://` fetch and the `CIDATA` path are authenticated by nothing (TLS only for the URL).

## Not tested

The `drroot` re-install guard in a guest (offline stub test only); `dr.config=https://…`; the fallback "no config" confirmation in a guest; `first-ssd` in a guest; physical hardware, USB sticks, real NVMe/SATA naming; a second boot of the same medium after a finished install; the systemd-boot and ZFS variants.
