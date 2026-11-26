# Design note: rollback-ready installs (ISO installer, v2 configures, rollback enroll add-on)

Status: r2, proposed 2026-11-24. Binding input: operator answers of 2026-11-24 ~10:35Z
(`.controller-state/operator-decisions.md`). Read on `80e4947bf`. `v2/` = `scripts/debian-install-v2/`, `dr/` =
`scripts/debian-rollback/`, `nc/` = `scripts/netcup/`, `py/` = `dr/files/usr/local/lib/dr-state/dr_state/`. Until
r2 is accepted, P-21, P-22 and P-25 go on and IC-M1 stays unstarted. Milestones are IC-M1.. (not
`MILESTONES-2026-11-18.md`'s); P-27a/b/c are new packages, to be added to PLAN-rollback.

Amends D7 (`dr/docs/DESIGN-GUIDE.md:34`): enrolling an existing btrfs or ZFS root is in scope; ext4 conversion stays
out. Amends D8 (`:35`): cloud image deferred (backlog, Q-B), and the swap rule (`:194-195`) for btrfs (C6). Core
`install()` "install time only" (`DESIGN-CORE-SPLIT.md:158`) becomes "install or enrollment". Restores D19 (`:46`),
F9 and DR-06 (`WORK-PACKAGES.md:80`, `:291-294`) and the layout owner row (`MILESTONES-2026-11-18.md:68`); its clause
"v2 not changed or run in M1 or M2" is superseded (C4, C9). Keeps D4, D18, core D2, D27, D32.

## 1. Boundaries and the one layout creator

**C1.** `nc/` automates the netcup API and VMs. `v2/` configures an installed Debian host to our configuration. `dr/`
owns the install medium, the disk layout and the rollback layer.

**C2.** The dr ISO path (`dr/iso/build-iso.sh`, `dr-autoinstall`, `dr/poc/install-btrfs.sh`; ZFS: P-43) is the only
layout creator: root on btrfs now, ZFS later (guests only, Q7). It is the common medium for netcup, VMware, Hyper-V,
KVM, TrueNAS and Proxmox guests and bare metal. `install-btrfs.sh` stays; P-21 and P-25 edits to it are durable.

**C3.** netcup, checked against `nc/`:
- Custom ISO: yes. Upload, attach and CD-ROM boot are implemented (`nc/README.md:688-720`); the generic ISO install
  waits for poweroff, detaches the ISO and restores the boot order (`:798-810`).
- Custom cloud-init: no user-data. Image setup takes only a `customScript` string (`nc/netcup-scp-openapi.json`,
  `POST /api/v1/servers/{serverId}/image`), run by the provider's cloud-init on its own image, which then restarts the
  host (`nc/README.md:4-7`, `:767-773`). That image's root is ext4, observed on netcup hosts
  (`nc/KNOWN_ISSUES_TODO_BACKLOG.md:228`).
- Custom disk image: the API snapshot (2026.1015) has user image endpoints (Q-B), contrary to the operator's belief;
  `nc/scp-api.py` implements neither; format, size and UEFI handling are unverified (U8). The ISO stays (Q6).

**C4.** The handoff to v2 is a generic first-boot hook in `dr/files/`: a oneshot unit that runs one given command once
after network-online. For v2 the command is the `customScript` from `build-customscript`
(`v2/debian_install_v2/customscript.py:123-190`), written by the installer to `/root/.vbpub/firstboot.json` (0600,
in `@root`, outside every state). `ConditionPathExists=` names that unstarted file; the unit's first action renames
it to `firstboot.started`; the bundle is deleted when v2's `completionMarker` appears (`ExecStartPost=` on v2's
stage-two unit). So the boot between stage one and two does not re-run `customScript`: v2's guard
(`installer.py:518-544`) refuses only on its marker or a state of phase done or status success. On failure the unit
fails visibly, keeps the bundle, no auto-retry. Retry: rename it back, `dr-state set-default <initial>`,
reboot. The bundle comes embedded in the ISO (secrets then sit in provider ISO storage up to 48 h: delete the
ISO after install) or fetched with `dr.config=https` (needs an auth design). Reason: no cloud-init on ISO hosts,
no secret in an OS state, and a rollback to the pre-v2 initial state cannot re-run v2's install, which v2's guard
cannot prevent: its marker (`:99`) is in `/var/lib`, inside the state.

## 2. What the ISO installer creates

| Item | btrfs (now) | ZFS (P-43, guests) |
|---|---|---|
| Table | GPT p1 ESP vfat 512 MiB, p2 the one filesystem (Q8; Q-A) (`install-btrfs.sh:79-84`) | p1 ESP, p2 `rpool` (`ZFS-POC-PLAN.md:182-189`) |
| ESP | p1: GRUB image hard-codes `(,gpt1)/EFI/dr` (`dr/files/usr/local/lib/dr-state/dr_state/core.py:1118`) | p1 |
| States | top-level `@os-*`, default subvolume 5 (`install-btrfs.sh:88-92`; `py/storage/btrfs.py:47-48`, `:60-80`) | `rpool/ROOT/os-*` (`ZFS-POC-PLAN.md:37-43`) |
| Kernels | in each state's `/boot`, no `/boot` partition | in each BE |
| Excluded | D18 + `@usr-local`, NOCOW on docker, containers, postgresql, mysql (`install-btrfs.sh:75-77`), plus C14 | `rpool/DATA/*` |
| Swap | `@swap` at `/swap`, NOCOW, never snapshotted (C6) | none (`ZFS-POC-PLAN.md:182`) |
| Control | `/.top` = `subvolid=5` (`install-btrfs.sh:141`; `py/paths.py:38-39`); mode 0700: IC-M1 change, `chmod 0700` on the subvolume-5 root after mounting (no code sets it) | `rpool/DR-META` |
| fstab | one option set for all btrfs mounts, `/` without `subvol=` (`install-btrfs.sh:132-143`) | no `/` line |
| Packages | `+python3` (core D2, `DESIGN-CORE-SPLIT.md:12-15`; missing at `install-btrfs.sh:117`) | + ZFS set (`ZFS-POC-PLAN.md:158-161`) |
| Network | systemd-networkd, DHCP on a given MAC, optional static IPv6; replaces ifupdown on `eth0` (`install-btrfs.sh:154-158`) | same |

**C5.** One filesystem holds OS states, D18 data and Docker; `@docker` is NOCOW and its storage driver is measured in
IC-M1 (U6). States' old blocks share the free space; P-22's capacity rule prunes states, but data growth is not
limited: cowtree admits by `statvfs` alone (`libraries/cowtree/src/cowtree/space.py:86-100`, `gc.py:331`).
**Open decision**, recommended: btrfs simple quotas on `@cow` and `@docker` if IC-M1 measures them cheap, else a
cowtree free-space floor that reserves space for OS states.

**C6.** btrfs swap is a swapfile in `@swap`, made by v2 (C10) with `btrfs filesystem mkswapfile` (U7). The rules:
NOCOW, uncompressed, fully allocated, one-device filesystem; a subvolume with an active swapfile cannot be
snapshotted, so it never lives in a state; balance, shrink and device removal can fail on its block groups.
Hibernation is not supported. ZFS has no swap: a zvol can deadlock (`DESIGN-GUIDE.md:194`).

**C7.** The install config takes `ROLLBACK=on|off`, default on (our hosts: on). On: today's in-chroot lines
(`install-btrfs.sh:172-190`), from P-27a `dr-state enroll --sysroot` (C12). Off: the same layout with stock boot,
enrollable later. Per boot path:
- **btrfs + GRUB, off:** stock GRUB via `grub-install --removable --no-nvram`; the firmware path
  `EFI/BOOT/BOOTX64.EFI` is what `install-grub` replaces on enrollment (`files/usr/local/lib/dr-state/dr_state/core.py:1103-1120`). The flags are not
  remembered: a `grub-efi-amd64` upgrade re-runs `grub-install` (U2), writing `EFI/debian/grubx64.efi` and an NVRAM
  entry, possibly in v2 stage one's upgrade (`installer.py:1545`), and that entry ignores dr's default. So off
  installs preset debconf `grub2/force_efi_extra_removable=true` and `grub2/update_nvram=false`; enrollment
  neutralises stock `grub-install` (`grub-efi-amd64-bin`, else a diversion), removes `EFI/debian` and its NVRAM entry,
  and checks that. dr-serial's ESP probe (`dr/poc/dr-serial.py:1846-1850`) gains an NVRAM listing. On installs get
  `grub-efi-amd64-bin` only.
- **btrfs + systemd-boot: on only.** systemd-boot cannot read btrfs; dr-state publishes kernels to the ESP and masks
  the stock BLS writer (`install-btrfs.sh:108-113`).
- **ZFS + ZBM: off allowed.** ZBM finds BEs itself; `dr-state install-zfsbootmenu` stays the one ESP writer
  (`ZFS-POC-PLAN.md:142`).

**C8.** The kernel command line becomes target configuration before IC-M4. It is hard-coded as `net.ifnames=0
console=tty0 console=ttyS0,115200` (`files/usr/local/lib/dr-state/dr_state/boot/grub.py:105-107`; `files/usr/local/lib/dr-state/dr_state/boot/sdboot.py:656-657`), and the ISO entries end with
`console=ttyS0,115200n8` (`build-iso.sh:89`, `:93`). The last `console=` is `/dev/console`; netcup's console is VNC
only (`RESEARCH-2026-11.md:37`), so emergency shells and the no-config ERASE prompt (`dr-autoinstall:172`) never
reach it. Each state carries `/etc/kernel/cmdline`, gen-menu reads it, the install config sets it (`tty0` last on
netcup, `ttyS0` last in vm-lab), and the ISO gets a netcup build option. A new DR entry.

## 3. What v2 needs on a btrfs or ZFS root

**C9.** On a btrfs or ZFS root v2 writes no partition table and resizes no filesystem. It detects the root type at
start and accepts only `partitioning.root: "*"`, `reserve_gb: 0`, data `none` or `subvolumes`; anything else is
refused at load (`layout_policy.disk_spec`, `v2/debian_install_v2/layout_policy.py:550`). `schema_version` 2 (`config.py:27`).

| Change | Where |
|---|---|
| Root discovery with `findmnt -n -o SOURCE,FSTYPE --nofsroot /` (without it btrfs prints `/dev/vda2[/@os-…]` and fails the `fullmatch`); ZFS maps the pool's one vdev to its partition; multi-device refused | `_discover_root`, `installer.py:307-316` |
| Shrink facts, plan, hook and verify: ext4 only | `:2627-2672`, `:2701-2776`, `:2778-2862`; stage one `:4659-4685` |
| Growth: ext4 only | `:3845-3950`, `:3966-3975`; stage two `:4827` |
| Root-cap guards (cloud-init, growroot): ext4 only | `:2416-2418`, `:4777-4785` |
| Swap: btrfs uses `swap_partition_count` swapfiles in `/swap`, same size formula; ZFS needs `swap_policy: off`; health gate and removal know swapfiles | `:2187-2380`, `:3590-3643`; `v2/docs/DESIGN-GUIDE.md:283-296` |
| Data kind `subvolumes`: top-level btrfs subvolumes outside every state, `nocow` per volume, `subvol=` with the root's option set; the partition-volume `subvol` and `fs: zfs` refusals stay | `layout_policy.py:356-363`, `:382-383`; `installer.py:3730-3732`, fstab `:3827`, `:4213` |
| Data mounts at or above an excluded path refused, below accepted | `layout_policy.py:112-132` |
| Layer present: stage units export `DR_STATE=none` (`DESIGN-CORE-SPLIT.md:297-299`); stage two ends with one `dr-state snapshot --reason host-setup` | stage units |
| No cloud-init: the cleanup policy is a no-op | `installer.py:4752-4771` |
| Lane `r1-vm-iso-btrfs` (vm-lab OVMF guest from the dr ISO) beside `r1-vm-real-commit`; v2's own harness is BIOS only (`RESEARCH-2026-11.md:38`) | `v2/run-gate.toml:117` |

Unchanged: the next-boot kernel is the highest `/boot/vmlinuz-*` (`installer.py:1475-1480`), dr-state's newest pair
(`files/usr/local/lib/dr-state/dr_state/storage/btrfs.py:195`, `files/usr/local/lib/dr-state/dr_state/ids.py:198`); v2 never edits GRUB or the command line (`update-grub` allowlisted, unused, `actions.py:130`).

**C10.** v2 owns swap size and data subvolumes (host tuning); the installer owns `@swap` and the excluded set (layout).

**C11.** r1002's `MDT_COW` partition (`nc/examples/debian-install-v2/r1002.json:9-13`) becomes data subvolume `@cow`
at `/srv/cow` (growth limit: C5). v2's btrfs option `user_subvol_rm_allowed` (`installer.py:3731`) joins the root's
option set; `/.top` at 0700 (IC-M1, section 2) keeps states out of users' reach. The runner finds the CoW area by
`PARTLABEL=MDT_COW` (`nc/runner/provision-runner.sh:70-90`) and must accept a btrfs mount at the path (an `nc`
change in IC-M1).

## 4. The rollback add-on: `dr-state enroll`

**C12.** One verb, `dr-state enroll` (prepare), with `commit`, `abort` and `finish`, owned by dr in `files/`; no
`adopt` verb. A **ready** layout (the ISO's: one `@os-*`, `/.top`, the excluded set) gets the layer in place; a
**foreign** layout is migrated into a new state and switched over on reboot. `initialize-state` stays the validator
of the fresh case (`py/core.py:959-975`).

**C13.** Minimal requirements, each a refusal:
- Debian 13 or later, `python3` >= 3.13 with full stdlib (core D1, D2), UEFI, Secure Boot off (`mokutil
  --sb-state`; dr's GRUB image is unsigned, D27).
- Root on btrfs or ZFS, one device, not on LUKS, LVM or md (Q10).
- ESP vfat, GPT partition 1 of the boot disk (`dr-state@d5f449971`); >= 512 MiB free for systemd-boot (ready layouts only).
- Kernel and initramfs in the root's `/boot`; a separate `/boot` partition is folded into the new state and left
  unused. For an ext `/boot`, which the source's entry still reads, `install-grub` adds `ext2` (absent at
  `dr-state@d5f449971`), else the case is refused.
- btrfs free space: estimated free >= max(2 GiB, 5 %) plus every path copied into a NOCOW subvolume, since btrfs
  refuses clones between COW and NOCOW files (proposal, U9). ZFS: the size of every copied path (U10).
- No `@os-*` except a ready layout's one state; `/.top`, if mounted, is `subvolid=5`; no active swapfile in the
  source (`swapoff` first); no dpkg run; for a copy, prepare stops docker, containerd, postgresql and mariadb/mysql
  and runtime-masks them until reboot (`systemctl mask --runtime`), or enrollment runs from the Live ISO
  (`dr.mode=enroll`). Excluded paths on other filesystems keep their fstab lines.

| Source layout | Handling |
|---|---|
| ISO ready layout | no copy, layer in place, no reboot |
| Debian installer `@rootfs` (`DESIGN-GUIDE.md:129`); Ubuntu-style `@` with `@home` | snapshot of the top-level child; an existing `@home` etc. is taken as that excluded subvolume |
| Root in subvolume 5 | snapshot of 5; the old top-level entries go into `.dr-enroll-old/` at `finish` |
| Default subvolume not 5 | refused unless it is the source; `commit` sets 5 |
| Nested root, snapper layout | refused |
| ZFS `rpool/ROOT/<be>`, `/boot` in the BE | IC-M6 (C16) |
| ZFS root at the pool root, Ubuntu `bpool`, TrueNAS `boot-pool` | refused: no BE container, kernels outside the BE, or an appliance (D1) |
| Proxmox `rpool/ROOT/pve-1` | refused for now: `proxmox-boot-tool` is a second ESP writer (U11); use our ISO plus `nc/pve-on-debian.sh` |

**C14.** The excluded set (Q9) is configurable at setup and enrollment. What dpkg writes is inside a state and rolls
back; user and service state and data stay outside and move forward. Base: D18 plus `/usr/local`
(`DESIGN-GUIDE.md:45`, `:71-75`). Install config `EXCLUDE_ADD` (path, subvolume, `nocow`) and `EXCLUDE_DROP`;
`enroll --exclude`/`--include`. Refused: a path at or above `/`, `/boot`, `/etc`, `/usr` (but `/usr/local`), `/bin`,
`/sbin`, `/lib*`, `/var/lib/dpkg`, `/var/lib/apt`, `/var/cache/apt`, `/var/lib/systemd`; nesting with another
excluded path; outside the base, a dpkg-owned regular file below it (`/var/lib/dpkg/info/*.list`). The set is stored
in `.dr-state/layout.json`, versioned like the D26 formats. Changing it later is a later item.

**C15.** btrfs foreign enrollment, all under the one `/.top` mount. Each step checks its postcondition first, so a
re-run resumes; `.dr-state/enroll.json` records the source, its generation and every object created, each with a
completion entry written after `sync` by write-then-rename; an object without one is deleted and redone. A re-run
against another source refuses.
1. C13 checks; mount `/.top`; journal.
2. Read-only snapshot `@dr-enroll-src`; record its generation.
3. Writable snapshot `@os-000001-<UTC>-enroll` from it.
4. Each excluded path without a subvolume: create it (`chattr +C` while empty for NOCOW), copy with `cp -a
   --preserve=all,xattr --reflink=auto`, empty the path in the new state. Nested subvolumes below a copied path are
   refused (U6).
5. Fold `/boot`; new fstab (the ISO form plus the source's other-filesystem and swap-partition lines; no
   swapfile from the source root); empty `@swap`; `update-initramfs -u -k all` inside the new state with
   `RESUME=none` or the new resume target.
6. `files/` into `@usr-local`, and without hooks into the source's `/usr/local`, so `abort` runs there.
7. Back up the ESP and NVRAM entries into `.dr-state/enroll-esp/`; `install-grub`, new ESP files written to a temp
   name, then renamed; dr's menu holds the new state and an unmanaged entry booting the source where it
   keeps its kernel. The stock `EFI/debian` entry stays; a new NVRAM entry for dr's image is armed with `efibootmgr
   --bootnext` (a GRUB one-shot with the source as default only without writable NVRAM).
8. Verify, then print "reboot": `findmnt --verify --tab-file` on the new fstab, every source resolves; a
   vmlinuz/initrd pair with matching `/lib/modules`; btrfs in the initramfs; `dr-state check --sysroot` (P-24); both
   menu entries with the right `rootflags=`; the GRUB image hash.
9. Reboot; the one-shot boots the new state. `enroll commit` there: running state = new state (`/proc/cmdline`),
   excluded paths mounted from their subvolumes. Late writes: a second read-only source snapshot, diffed by `btrfs
   send --no-data -p @dr-enroll-src <snap2> | btrfs receive --dump` (`find-new` misses unlinks, renames, metadata).
   `commit` refuses only on a change under `/usr`, `/boot` or `/var/lib/dpkg` (`status`: an apt run) or in a
   stopped service's path (databases, docker); under other excluded paths changed files are copied to
   `.dr-enroll-late/<path>`, never merged file by file; all else (random-seed, timer stamps, logrotate, leases,
   `system.journal`) is listed in the journal; `--discard-late` overrides. Then the install sidecar (kind
   `enroll`), `set-default`, C7's neutralisation with its check (removes `EFI/debian` and its NVRAM entry; U3),
   `gen-menu`.
10. If the new state fails, a reset boots the source; `enroll abort` there restores ESP and NVRAM, re-runs stock
    `grub-install` and deletes exactly the journal's objects. Rebooted into the source before `commit`: re-running
    `enroll` re-arms the one-shot.
11. `enroll finish`, later: deletes `@dr-enroll-src`, the source (or `.dr-enroll-old/`) and its menu entry.

Ready layouts run 1, 6, 7 without source entry or one-shot, `initialize-state`, `gen-menu --default <initial>`,
`check` and C7's neutralisation; no reboot.

**C16.** ZFS (IC-M6, after P-42): snapshot the source BE, clone to `rpool/ROOT/os-000001-…-enroll` with local
`canmount=noauto`, `mountpoint=/`, `org.zfsbootmenu:commandline`, then promote; the source then depends on the clone,
so `abort` re-promotes it first. Excluded paths become `rpool/DATA/*` by copy; `bootfs` moves only at
`commit` (no one-shot: a failed boot needs ZBM's console menu); late writes via `zfs diff`. D25's later
swap-partition experiment may revise C6's ZFS no-swap rule.

## 5. Milestone path

| IC-M | Content | Order | Acceptance (vm-lab OVMF guest) |
|---|---|---|---|
| IC-M1 | ISO btrfs + GRUB, layer on; v2 via C4; section 2, C8, section 3, C11 | after r2 acceptance, P-21, P-25, P-31b; v2 code may start at acceptance | boots from the disk alone; `rootflags=subvol=@os-000001-…`; default subvolume 5; D18 and `@cow` mounted; swapfile on; v2 stage two marker; `host-setup` snapshot; bundle gone; GRUB upgrade leaves ESP and NVRAM unchanged; v2 `r0-r1`, `r1-vm-iso-btrfs`, dr `offline` PASS |
| P-40 | release matrix | Depends 22-26, 33, IC-M1 (C17) | as PLAN-rollback P-40 |
| IC-M2 | P-27a enroll, ready layouts; P-27b foreign btrfs | 27a after P-22, P-24; 27b after 27a | `offline` covers refusals and re-runs; guests: `ROLLBACK=off` then enroll; `@rootfs`, subvolume-5 and `@`/`@home` guests; an injected boot failure falls back to the source and `abort` restores it; no `EFI/debian` after a GRUB upgrade |
| IC-M4 | r1002 reinstall (C18) | after IC-M1, P-40 or C17 subset, C8 | boots, SSH, v2 stage two, runner finds `/srv/cow`, restore list applied; apt snapshot, older state via the console, `set-default` back |
| IC-M5 | v1001 | the controller's call (Q12) | as IC-M4 |
| IC-M6 | ZFS: P-42, P-43, v2 ZFS root, P-27c, P-44 | P-43 after P-42 | P-44 on an ISO-built disk at 2048 MiB |

**C17. Proposed P-40 order.** P-40 runs after IC-M1, so the release matrix binds to the installer r1002 gets. If
IC-M1 is not merged when P-22..P-26 and P-33 are, P-40 runs on the current ISO and IC-M1's acceptance adds the subset
boot-check, s2-s3 and recovery-kernel on IC-M1's ISO, for GRUB and systemd-boot (IC-M1 changes networkd, python3 and
swap for both). IC-M2 is not a P-40 dependency: enrollment has its own acceptance.

**C18. r1002 preconditions (Q11).** A complete fresh reinstall with `nc/install-restore-iso.py install` and the dr
ISO, only after:
1. IC-M1 PASS in vm-lab, and P-40 PASS on IC-M1's ISO or the C17 subset PASS on IC-M1's ISO (Q5); C8 merged with
   the netcup console order.
2. The P-44 ZFS base disk (`/root/jobs/debrollback-zbm-20261123`) and all rollback evidence in the P-rollback-01
   off-host archive, SHA-256 verified there.
3. A read-only inventory of what to restore (runner registration and env, secrets), copied off-host and verified.
   Everything else is installed fresh.
4. The main controller's lease; U5 settled from the inventory.

P-43 stays whole in dr (ZFS ISO, `dr-zfs-autoinstall`, `fetch-zbm-efi.sh`, `install-zfs.sh`: the ZFS layout
creator); v2's ZFS-root support is its own v2 package in IC-M6. One guest at a time; leases and merges via the main
controller.

## 6. Unknowns

- U1: stock `update-grub` adds `rootflags=subvol=` for a subvolume root (expected, not read). IC-M2.
- U2: when `grub-efi-amd64`'s postinst re-runs `grub-install`, and whether it honours the presets. IC-M1, IC-M2.
- U3: firmware fallback to `EFI/BOOT/BOOTX64.EFI` without the Debian NVRAM entry: expected on OVMF, unknown on
  netcup and bare metal. IC-M2; IC-M4 observes netcup.
- U5: r1002 network (DHCPv4; IPv6 by RA or static); a wrong config loses the host to the console.
- U6: Docker's storage driver on NOCOW `@docker` (btrfs driver with nested subvolumes, or overlay2). IC-M1.
- U7: `btrfs filesystem mkswapfile` in Debian 13's btrfs-progs. IC-M1.
- U8: netcup user image format, size, UEFI, and whether setup runs provider cloud-init. Untested.
- U9: btrfs free-space margin for enrollment. IC-M2.
- U10: OpenZFS block cloning across datasets on Debian 13. IC-M6.
- U11: `proxmox-boot-tool` beside dr-state. Not planned.

## 7. Operator questions

- **Q-A. Raw benchmark partition.** v2 benchmarks a raw `iobench-raw` partition (`installer.py:3493-3501`) and skips
  io.cost without a result (`:1907-1920`); r1002 has it (`r1002.json:8`). (a) strictly one partition, no io.cost;
  (b) an exception to Q8: the ISO adds an 8 GiB raw `iobench-raw` partition at the disk end, v2 unchanged; (c) the
  Live ISO runs iobench on the p2 range before `mkfs`, writes the result to `@root` or `/var/lib/vbpub`, and v2
  imports it. **Recommended (c):** io.cost matters on the shared hosts, and Q8's one partition stays.
- **Q-B (information).** netcup has user image endpoints (spec 2026.1015: `POST
  /users/{userId}/images/{key}`, `POST /servers/{serverId}/user-image`), not implemented in `nc/`. The ISO stays the
  medium (Q6). Backlog item: netcup user images, after U8.
