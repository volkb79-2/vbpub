# Research record, November 2026

Collected 2026-11-12 by five research agents (four web, one repository survey).
This file keeps the findings and their sources so they need not be gathered
again. Conclusions drawn from them are in [DESIGN-GUIDE.md](DESIGN-GUIDE.md).

How far a claim was checked:

- **[V]** read in a primary source during the research (man page, package
  tracker, project documentation, source code, bug report).
- **[S]** from a secondary source (blog, news, forum, search snippet).
- **[I]** inferred, or not confirmed.

Nothing in this file was tested by us. Section 9 lists what could not be
confirmed.

## 1. What the repository already has

| Fact | Where |
|---|---|
| No OS-level snapshot, rollback or boot-environment support for Debian exists in the repo, implemented or planned. No apt `DPkg::Pre-Invoke` hook anywhere | repo-wide search |
| `debian-install-v2` (2.0.1) is a two-stage bootstrap on top of a provider-installed Debian 13 cloud image; fresh installs only | `scripts/debian-install-v2/README.md`, `installer.py:413` |
| Root is ext4 only; bootloader is GRUB; the installer never edits GRUB configuration | `installer.py:1242`, `docs/AUGEAS-EVALUATION.md:18` |
| netcup hosts r1002 and v1001: UEFI image "Debian 13.2.0 UEFI amd64", one 512 GiB disk `vda`, ESP 243 MiB, `/boot` 977 MiB, root on `vda3` | `scripts/netcup/KNOWN_ISSUES_TODO_BACKLOG.md` NC-12, `scripts/netcup/target-host.jsonc` |
| Disk order: root, data partitions or one LVM volume group, reserve, `iobench-raw`, swap partitions at the end | `layout_policy.py:5` |
| Data area filesystems: btrfs, ext4, xfs or raw. `fs: zfs` and btrfs subvolumes are refused as deferred | `README.md`, `CHANGES.md:58`, `TODO.md:11` |
| Mount policy refuses mounts at or covering `/`, `/var`, `/var/lib`, `/srv`, `/home`, `/usr`, `/etc`, `/boot`, `/root`, `/var/lib/dpkg`, `/var/lib/apt`, `/var/log` | `layout_policy.py:112-114` |
| Backports pinned at 600 by operator decision of 2026-10-06; a `full` unattended upgrade moves the kernel to the backports series (6.12 to 7.2 observed) | `templates.py:83-91` |
| `Remove-Unused-Kernel-Packages "true"`, `Automatic-Reboot "false"`; `vbpub-reboot-check.timer` is the only reboot mechanism | `templates.py:620-745` |
| An initramfs hook is installed for every kernel and verified with `lsinitramfs`; a hookless initramfs on a kernel GRUB picked was a real failure | `installer.py:2450-2600` |
| Partition-table writes are planned, backed up, applied, read back and rolled back on mismatch | `inuse_partition_editor.py` |
| btrfs was chosen over ZFS for the copy-on-write data area: in the kernel, writes charged to the originating cgroup, no second cache. That decision was about scratch data, not root | `mdt-cockpit/docs/BTRFS-COW.md:24` |
| Measured on r1002 (Debian 13, kernel 7.2.9): fsync p99 0.26 ms alone, 1.03 ms during a snapshot storm in a sibling subvolume | `BTRFS-COW.md:130-136` |
| Docker's data root stays on ext4 with overlay2 | `BTRFS-COW.md:32-34` |
| netcup provider snapshots revert the whole disk, and a provider reinstall deletes them | `scripts/netcup/CONSUMERS.md:97-136`, backlog NC-08 |
| netcup console is browser VNC only, no serial API documented; rescue system is Grml; ISO attach and boot order are scriptable; uploaded ISOs are deleted after 48 h | `scripts/netcup/CONSUMERS.md:61-95`, `PROXMOX-VE.md` |
| Debian VM harness: QEMU without KVM, BIOS boot only, no keyboard control, no failed-boot simulation | `scripts/debian-install-v2/testing/vm/vmctl:313-329` |
| TrueNAS VM launcher: UEFI with per-guest variable store, sendkey, screendump, serial socket, extra disks | `scripts/truenas/tcg_vm.py` |
| Unattended restore ISO builder (Debian Live plus restore service) | `scripts/truenas/golden-iso/` |
| TrueNAS boot-environment handling in our tooling: activate, clone, keep; never destroy | `truenas/tnt/src/tnt/workflows.py:42-66` |

The repository survey excluded `.worktrees/`, so an unmerged branch could hold
something not listed here.

## 2. btrfs route

**Debian today**

- The Debian installer creates one subvolume, `@rootfs`, and nothing else; no
  sign of a change for Debian 14. [V]
  <https://lists.debian.org/debian-boot/2025/04/msg00157.html>,
  <https://salsa.debian.org/installer-team/partman-btrfs/-/raw/master/debian/changelog>
- btrfs is in the kernel. Debian 13 ships btrfs-progs 6.14; 7.3 is in testing
  and backports. [V] <https://tracker.debian.org/pkg/btrfs-progs>
- Upstream status: RAID5/6 unstable; quota groups "mostly OK" and slow with
  many snapshots; a swap file must be no-copy-on-write, on a single device,
  and its subvolume cannot be snapshotted while it is active. [V]
  <https://btrfs.readthedocs.io/en/latest/Status.html>,
  <https://btrfs.readthedocs.io/en/latest/Swapfile.html>

**Tools**

| Tool | Maintained (last release) | In Debian 13 | Snapshot on apt | Boot-menu entry | Automatic fallback |
|---|---|---|---|---|---|
| snapper | yes, 0.13.2 (2026-09-07) | 0.10.6 | **yes**, ships `/etc/apt/apt.conf.d/80snapper` [V] | no | no |
| grub-btrfs | yes, v4.14 (2025-09) | **no**; packaging request #1121616 closed | no | yes, GRUB submenu | no |
| snapper-rollback | stale (2023) | no | no | no | no |
| timeshift | yes, 25.12.4 | 24.06.6 | no | no | no |
| timeshift-autosnap-apt | stale (2023) | no | yes | via grub-btrfs | no |
| btrbk | yes, 0.32.7 | yes | no (backup tool) | no | no |
| btrfs-assistant | yes | 2.1.1 | no (GUI) | no | no |
| refind-btrfs | 2025-11 | no | no | rEFInd | no |
| limine-snapper-sync | yes | no (Arch ecosystem) | via snapper | yes | no |
| snapm and boom | active (Fedora, RHEL) | not found | yes (dnf) | yes | no |
| siduction-btrfs | active (2026-04) | siduction only | uses 80snapper | GRUB and systemd-boot | no |
| health-checker (SUSE) | rewritten 2025-26 | no | n/a | n/a | **yes** |

Sources: <https://tracker.debian.org/pkg/snapper>,
<https://sources.debian.org/data/main/s/snapper/0.10.6-1.2/debian/>,
<https://github.com/Antynea/grub-btrfs>,
<https://lists.debian.org/debian-wnpp/2025/11/msg00645.html>,
<https://gitlab.com/Zesko/limine-snapper-sync>,
<https://github.com/siduction/siduction-btrfs>.

**CachyOS** [V] <https://wiki.cachyos.org/installation/filesystem/>,
<https://wiki.cachyos.org/installation/boot_managers/>,
<https://wiki.cachyos.org/configuration/btrfs_snapshots/>

- btrfs is the default, with zstd compression and subvolumes `@`, `@home`,
  `@root`, `@srv`, `@cache`, `@tmp`, `@log`.
- Limine is the installer default since January 2026 [S]; GRUB, systemd-boot
  and rEFInd are selectable. Snapshot booting exists for GRUB (grub-btrfs) and
  Limine only.
- `snap-pac` takes snapper snapshots around each pacman run.
- `limine-snapper-sync`: Limine reads only FAT, so each snapshot's kernel and
  initramfs are copied to the FAT boot partition, deduplicated by hash and
  checksummed. Entries are capped by count and by 85% ESP usage. Snapshots boot
  read-only under an overlay. Restore is by rsync, by replacing the subvolume,
  or by snapper.
- ZFS is offered for advanced users with a prebuilt module; boot environments
  only through community ZFSBootMenu setups. [S]

**openSUSE, the reference design** [V]
<https://documentation.suse.com/sles/15-SP6/html/SLES-all/cha-snapper.html>

- Only `/` is rolled back. Excluded subvolumes: `/home`, `/opt`, `/srv`,
  `/tmp`, `/usr/local`, `/var`, and the GRUB architecture directories.
- GRUB offers "Start bootloader from a read-only snapshot".
- `snapper rollback` snapshots the current state and makes a writable copy of
  the target the default subvolume.
- The documentation warns that data in excluded directories may not match an
  older OS after rollback.
- `transactional-update` (MicroOS) upgrades inside a new snapshot and
  activates it at reboot; `/etc` is a per-snapshot overlay. [V]
  <https://kubic.opensuse.org/documentation/man-pages/transactional-update.8.html>
- `health-checker` rolls back automatically on a failed first boot. [V]
  <https://manpages.opensuse.org/Tumbleweed/health-checker/health-checker.8.en.html>

**Others**

- siduction (Debian unstable derivative) ships snapper with grub-btrfs on
  Debian packages: layout `@`, `@home`, `@root`, `@var@log`, `@snapshots`; uses
  the apt hook; only `/var/log` is separated, not service data. [V]
  <https://manual.siduction.org/sys-admin-btrfs-snapper_en.html>
- Fedora has no snapshot boot; a proposal for Fedora 45 adds snapm and boom.
  [V] <https://fedoraproject.org/wiki/Changes/BtrfsWithFullSystemSnapshots>
- Garuda ships snapper, snap-pac and grub-btrfs. [S]

**Kernel and boot**

- With `/boot` inside the root subvolume and GRUB reading btrfs, kernel,
  initramfs and modules roll back together and nothing is copied.
- With kernels on the ESP (systemd-boot, unified kernel images) a snapshot's
  kernel disappears when a newer one replaces it, unless versioned copies are
  kept. [I]
- GRUB up to 2.12 (Debian 13) cannot write its environment file on btrfs, so
  `grub-reboot` and `savedefault` fail. [S] GRUB 2.14 stores the environment
  block in the btrfs header [V]; Debian 14 has 2.14.
  <https://www.gnu.org/software/grub/manual/grub/html_node/Environment-block.html>
- grub-btrfs's read-only-snapshot overlay helper covers mkinitcpio and dracut
  only; no initramfs-tools equivalent was found. [V for the helper, I for the
  absence]

**Conversion**: `btrfs-convert` works from ext4 and keeps a rollback image, but
the documentation recommends a fresh filesystem and copy. [V]
<https://btrfs.readthedocs.io/en/latest/btrfs-convert.html>

## 3. ZFS route

**Debian today**

- OpenZFS "Debian Trixie Root on ZFS" guide (updated 2026-07-27): GRUB only,
  a 1 GiB `bpool` with `compatibility=grub2` plus `rpool`; recommends 4 GiB
  RAM and says under 2 GiB is slow; warns that swap on a ZFS volume can lock
  up. [V]
  <https://openzfs.github.io/openzfs-docs/Getting%20Started/Debian/Debian%20Trixie%20Root%20on%20ZFS.html>
- Versions: Debian 13 has 2.3.2; security 2.3.9 (2026-08-24); backports 2.4.4
  (2026-09-12); testing 2.4.4. [V] <https://tracker.debian.org/pkg/zfs-linux>
- ZFS is in `contrib` because of the licence question; the Debian installer
  cannot install to ZFS. [V] <https://wiki.debian.org/ZFS>
- Debian bug #1121154: ZFS 2.3.5 refused kernel 6.18; reported 2025-11-21,
  fixed with 2.4.0-1 on 2026-01-12, 52 days. Upstream 2.4.0 was released on
  2025-12-18, so most of the gap was packaging. [V]
  <https://bugs.debian.org/1121154>
- Kernel support by upstream release: 2.4.0 to 6.18, 2.4.1 to 6.19, 2.4.2 to
  7.0, 2.4.4 to 7.2. [S] Backports ZFS 2.4.4 (2026-09-12) predates the
  backports 7.2 kernel (2026-10-03), so the two are aligned today. [V]

**Tools**

| Tool | Maintained (last release) | In Debian | Snapshot on apt | Boot-menu choice | Automatic fallback |
|---|---|---|---|---|---|
| ZFSBootMenu | yes, v3.1.0 (2026-01-01) | no | no | yes | no |
| zectl (johnramsden) | low (2025-02) | no | no | via systemd-boot | no |
| truenas/zectl fork | yes, no releases | no | no | via GRUB | no |
| beadm (Linux port) | unknown, "not for production" | no | no | n/a | no |
| zsys (Ubuntu) | abandoned since 2022 | not checked | n/a | n/a | n/a |
| zfs-auto-snapshot | yes | contrib | no (timer) | no | no |
| sanoid | yes, 2.3.0 | main | no | no | no |
| zfs-apt-snapshot | low, no release | no | yes | no | no |
| proxmox-boot-tool | yes | Proxmox only | no | kernel pin | no |

**ZFSBootMenu** [V] <https://docs.zfsbootmenu.org/en/latest/>

- A small Linux image that imports the pools, shows a menu and starts the
  chosen kernel with kexec.
- Finds boot environments as datasets with `mountpoint=/` and
  `canmount=noauto`; each must hold `/boot` with its kernel and initramfs.
  Several kernels inside one environment can be chosen.
- Recommends one filesystem per environment containing `/usr`, `/etc` and the
  package database, with `/home` separate.
  <https://docs.zfsbootmenu.org/en/latest/general/bootenvs-and-you.html>
- Debian guide: Debian 13, UEFI, no separate `/boot`, no `bpool`; set
  `REMAKE_INITRD=yes` in `/etc/dkms/zfs.conf`.
  <https://docs.zfsbootmenu.org/en/latest/guides/debian/uefi.html>
- Remote access: dropbear in the image, key-only, port 222.
  <https://docs.zfsbootmenu.org/en/latest/general/remote-access.html>
- KVM is the maintainers' test platform; a kernel panic after kexec on Xen is
  on record. <https://github.com/zbm-dev/zfsbootmenu/discussions/787>
- Update ZFSBootMenu before upgrading pools; its guides recommend a
  `compatibility=openzfs-2.3` setting. Images are signed with signify, not for
  Secure Boot.
- v2.3.0 added hook stages at boot selection; a boot counter could be built on
  them (untested idea). [I]

**TrueNAS** [V] installer source
<https://github.com/truenas/scale-build/blob/master/truenas_install/__main__.py>,
<https://www.truenas.com/docs/scale/26/systemsettings/boot/managebootenviron/>

- The bootloader is GRUB. `boot-pool` is created with `compatibility=grub2`.
- Each boot environment is `boot-pool/ROOT/<version>` with child datasets
  (`usr`, `etc`, `var`, `var/lib`, `var/log`, `home`, `root`, `data`, ...).
- An update does not use apt: a new dataset is filled by unpacking
  `rootfs.squashfs`. Some child datasets are cloned from the old environment
  (`data`, `home`, `root`, `var/log`); host ID and machine ID are copied; the
  configuration database is migrated in a chroot.
- Activation sets `zpool set bootfs=` and runs `update-grub`. `usr` is
  read-only with a `@pristine` snapshot. On failure the old `bootfs` is
  restored.
- "Keep" protects an environment from pruning; two are kept by default.
- Changes made in one environment do not appear in another.

**Other references**

- Proxmox VE: ZFS root `rpool/ROOT/pve-1`, no boot-environment manager;
  `proxmox-boot-tool` copies kernels to every ESP; systemd-boot or GRUB. [V]
  <https://pve.proxmox.com/wiki/Host_Bootloader>
- FreeBSD `bectl`: create, activate, `activate -t` (next boot only), mount,
  destroy; the loader reads the pool's `bootfs`. No automatic fallback. [V]
  <https://man.freebsd.org/cgi/man.cgi?query=bectl&sektion=8>
- Solaris: shared datasets live outside the boot environment and are visible
  from every one; package changes happen on an automatically created clone.
  [V] <https://docs.oracle.com/cd/E37838_01/html/E60980/aboutbes.html>
- GRUB and pool features: a `zpool upgrade` that activates a feature GRUB does
  not know makes GRUB refuse the pool; Ubuntu patches `zpool upgrade` to skip
  `bpool`. [V] <https://bugs.launchpad.net/bugs/1847389>

## 4. Image-based and atomic approaches

| System | State | Source |
|---|---|---|
| systemd-sysupdate, repart, mkosi | Debian 13 has systemd 257, Debian 14 has 262; the man page calls sysupdate "experimental and subject to change"; no boot assessment or rollback of its own [V] | <https://www.freedesktop.org/software/systemd/man/latest/systemd-sysupdate.html> |
| ParticleOS | lists Debian as supported, no compatibility guarantees, needs UEFI with Secure Boot in setup mode [V] | <https://github.com/systemd/particleos> |
| Debian's own effort | a wiki page of links only (edited 2026-11-07); no "Debian Atomic" [V] | <https://wiki.debian.org/immutable> |
| bootc on Debian | no official support; a personal repository last touched 2025-09; composefs backend "experimental" [V] | <https://bootc-dev.github.io/bootc/experimental-composefs.html> |
| Vanilla OS 3 (ABRoot v2) | two root partitions, OCI images, desktop only; shipped 2026-09-01 [V] | <https://abroot.vanillaos.org/> |
| Kairos | active/passive/recovery images; automatic fallback on GRUB only; Debian validated in CI but no longer the default base [V] | <https://kairos.io/docs/upgrade/boot_assessment/> |
| Flatcar | A/B `/usr`; GRUB reads partition priority and tries attributes [V] | <https://www.flatcar.org/docs/latest/diagnostics/manual-rollbacks/> |
| RAUC | slot status in the GRUB environment file kept outside the root slots [V] | <https://rauc.readthedocs.io/en/latest/integration.html> |
| OSTree `/etc` | three-way merge [V] | <https://ostreedev.github.io/ostree/atomic-upgrades/> |

Prior art for "upgrade inside a clone":

- openSUSE transactional-update. [V]
- FreeBSD: `bectl create`, `pkg -c <mount> upgrade`, `bectl activate -t`. [S]
  <https://fy.blackhats.net.au/blog/2026-08-07-freebsd-update-bectl/>
- Debian: scripts only, for example a Proxmox gist that creates a boot
  environment, chroots and runs `apt dist-upgrade`. No maintained package. [V]
  <https://gist.github.com/ohmantics/bf3821d5e988ab7ee9a941e23031ca5e>

## 5. Automatic fallback after a failed boot

- **systemd Automatic Boot Assessment** [V]
  <https://systemd.io/AUTOMATIC_BOOT_ASSESSMENT/>: the try counter is in the
  boot entry's file name (`+3` becomes `+2-1`); `systemd-bless-boot` clears it
  after a good boot; `boot-complete.target` is where health checks attach;
  exhausted entries sort last. UEFI only. Debian 13's systemd-boot supports
  it. Other bootloaders may reuse `boot-complete.target` with their own
  blessing step.
- **GRUB**: `fallback` only covers failures inside GRUB, not a kernel panic.
  [V for the manual, I for the panic point]
  <https://www.gnu.org/software/grub/manual/grub/html_node/fallback.html>.
  Fedora's boot counter patches are downstream only; greenboot drives them.
  [V] <https://developers.redhat.com/articles/2024/08/12/greenboot-automate-rollbacks-atomically-updated-systems>
- **GRUB environment file**: writable only on a plain disk and a filesystem
  without checksums, so not on ZFS; on btrfs via the reserved header area
  since 2.14. [V]
- **A pattern that works on GRUB** [I, design]: keep the default on the
  known-good entry; `grub-reboot` the new entry for one boot; boot with
  `panic=10` and a watchdog; a unit after `boot-complete.target` runs
  `grub-set-default` on the new entry. Any failure ends in a reset and the next
  boot is the old default. Same idea as Kairos `next_entry` and FreeBSD
  `bectl activate -t`.
- ZFSBootMenu and FreeBSD have no automatic fallback. [V]

## 6. Where to draw the line between OS and data

- openSUSE moved its package database into `/usr` because a rolled-back system
  otherwise "still thinks it has packages installed". [V]
  <https://lists.opensuse.org/archives/list/factory@lists.opensuse.org/thread/43GM3FTT563V2HHC6HQNEYMFCMYSMANJ/>
- Debian has no such move; some downstreams patch dpkg into `/usr/share`. [V]
  <https://lists.debian.org/debian-devel/2025/03/msg00263.html>
- Data migrated by a package's install script is forward-only everywhere. [I]
- `/etc` after the snapshot: openSUSE keeps the snapshot's version on conflict
  and warns; OSTree merges; TrueNAS regenerates from its database. [V]

## 7. ZFS and btrfs compared

**Memory** [V] man pages of the 2.3 and 2.4 branches:
<https://raw.githubusercontent.com/openzfs/zfs/zfs-2.3-release/man/man4/zfs.4>

- `zfs_arc_max` default: the larger of RAM minus 1 GiB and 5/8 of RAM;
  minimum 64 MiB. `zfs_arc_min`: the larger of 32 MiB and RAM/32.
- Reads through `mmap` are double cached; ordinary reads and writes use the
  ARC only. [V for mmap, from `zfs_vnops_os.c`; I for the rest]
- 2.3.0 could exceed `zfs_arc_max` through pinned dnodes; fixed by PR #17542
  (July 2025); which release carries it was not confirmed.
  <https://github.com/openzfs/zfs/issues/17052>
- The kernel-memory code of the ZFS module has no cgroup accounting flags, so
  the ARC is probably not charged to a container's memory limit. [I, from
  reading `spl-kmem-cache.c`]
- No published measurement of root on ZFS with a small ARC cap was found.

**cgroup IO** [V] <https://github.com/zfsonlinux/zfs/issues/4275>,
<https://github.com/openzfs/zfs/issues/19771>: ZFS dataset IO appears only in
the root cgroup; throttling works only for ZFS volumes. A report from
November 2026 on 2.4.1 is still open.

**Feature comparison**

| Area | ZFS | btrfs | Better |
|---|---|---|---|
| Kernel upgrades | out-of-tree, DKMS | in the kernel | btrfs |
| cgroup memory and IO control | not applied | applied | btrfs |
| Boot | restricted pool for GRUB, or ZFSBootMenu | GRUB reads it | btrfs |
| Resize | no shrink of a single-disk pool | online grow and shrink | btrfs |
| Swap | volume swap can deadlock | swap file with restrictions | btrfs |
| Many snapshots, quotas | cheap | quota groups slow down | ZFS |
| Nearly full disk | slows down | "no space" with space free, needs balance | ZFS |
| Redundancy | mirror, raidz, self-heal | raid1 fine, RAID5/6 unstable | ZFS |
| Encryption | native | LUKS underneath | ZFS |
| Send and receive | mature, bookmarks | available | ZFS |
| Compression | zstd per dataset | zstd per subvolume | equal |
| Reflink copies | on by default since 2.3 | yes | equal |
| Recent corruption bugs | 2023 dnode bug linked to block cloning | CVEs in 2026 | neither |
| Write-heavy databases | trails ext4 and XFS | trails ext4 and XFS | neither |

**Performance data found** (thin; none matches our case)

- Blog test, October 2026, 4 GiB VM, kernel 7.0, OpenZFS 2.4, direct IO on a
  16 GiB file, ZFS defaults [S]
  <https://computingforgeeks.com/ext4-vs-xfs-complete-comparison/>:

  | Test | ext4 | btrfs | ZFS (128K / 16K record) |
  |---|---|---|---|
  | 4K random read, IOPS | 81k | 63k | 6k / 13.6k |
  | 4K write with fsync, ops/s | 313 | 231 | 349 / 454 |
  | Untar of 105k files | 4.6 s | 6.1 s | 9.2 s / 7.9 s |

  Direct reads are the worst case for ZFS.
- EDB, October 2022, kernel 5.17, OpenZFS 2.1, PostgreSQL pgbench on 64 GB:
  in-memory data set, ext4 and XFS about 59k TPS, ZFS 33-36k, btrfs about 28k;
  larger than RAM, ext4 about 30k, btrfs about 6.5k, ZFS 3.8-6.1k. [V]
  <https://www.enterprisedb.com/blog/postgres-vs-file-systems-performance-comparison>
- No Phoronix comparison of 2025-2026 including OpenZFS was found.

## 8. Cloud images

- cloud-init's `resizefs` supports btrfs and ZFS; `growpart` needs the
  partition to grow to be the last one. [V]
  <https://raw.githubusercontent.com/canonical/cloud-init/main/cloudinit/config/cc_resizefs.py>
- Debian's cloud images are built with FAI. [V]
- Many providers accept no custom image; writing the disk from the rescue
  system is the portable route. [I]

## 9. Not confirmed

- Whether a signed systemd-boot package is in Debian 13.
- Whether Debian's `grub-install` uses the btrfs-header environment block.
- Whether `compatibility=grub2` makes `zpool upgrade` stay inside the allowed
  features on Debian's ZFS version.
- ARC accounting against container memory limits, and how PSI reflects ARC
  pressure (inferred from source, not run).
- Whether ZFS dataset IO attribution improved in 2.4.
- Any performance figure for a small or adaptive ARC on a 1-16 GB host.
- Whether ZFS DKMS builds or apt run out of memory on a 1 GB machine.
- ZFSBootMenu menu details (the page could not be fetched) and its behaviour on
  the netcup hypervisor.
- Filesystem of the official Debian cloud images (probably ext4).
- A btrfs regression reported for kernel 7.2-rc1 and the 2026 btrfs CVE details
  (search snippets only); the estate runs kernel 7.2.
- GRUB `fallback` behaviour on a kernel panic.
- Status of fscrypt for btrfs.
