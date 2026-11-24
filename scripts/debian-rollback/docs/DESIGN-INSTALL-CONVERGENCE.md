# Design note: one installer for rollback-ready hosts

Status: accepted 2026-11-24, review applied. Operator direction: install a host with root on btrfs (later ZFS) on
r1002 or v1001, combined with debian-install-v2; rollback is a layer adopted at install or later; one installer.
Read on main `60f6da6d4`. `v2/` = `scripts/debian-install-v2/`, `dr/` = `scripts/debian-rollback/`.

This supersedes D19 ("`debian-install-v2` stays separate", `dr/docs/DESIGN-GUIDE.md:46`), the F9/DR-06 handoff model
(`dr/docs/WORK-PACKAGES.md:80`, `:291-294`) and the "layout owner" row (`dr/docs/MILESTONES-2026-11-18.md:68`). It
keeps D4 UEFI only, D7 new installs only (`DESIGN-GUIDE.md:31`, `:34`), D18, and core-split D2, D27 and D32.

## 1. Install time versus later

**C1.** Install time creates the layout: partition table, ESP, root container, subvolumes or datasets, and the one
firmware entry `EFI/BOOT/BOOTX64.EFI`, no NVRAM entry; changing these later is a migration or a boot-path swap. The
layer is dr-state software, hooks, markers and the loader image behind that entry. `DESIGN-CORE-SPLIT.md:158`
("`install()` install time only") is amended to "install or adoption" in C4's package.

### 1.1 Created at install time (the rollback-ready layout)

| Item | btrfs | ZFS |
|---|---|---|
| ESP | GPT, p1, vfat, 512 MiB (`dr/poc/install-btrfs.sh:79-83`). p1 because the standalone GRUB image hard-codes `(,gpt1)/EFI/dr` (`dr/files/usr/local/lib/dr-state/dr_state/boot/grub.py`) | p1, 512 MiB: three ZBM copies of 85,742,592 bytes plus 128 MiB free (`dr/docs/ZFS-POC-PLAN.md:197-198`) |
| Root container | p2 btrfs `drroot` (`install-btrfs.sh:80`, `:84`) | p2 `rpool`; `ROOT`, `DATA` with `canmount=off` (`ZFS-POC-PLAN.md:182-186`) |
| Top level | default subvolume stays id 5; states and data subvolumes are its children (`install-btrfs.sh:10`, `:92-97`); GRUB reads `/<state>/boot/...` from it (`dr-state:997-998`) | n/a |
| Initial state | `@os-000001-<UTC>-install` (`install-btrfs.sh:88-92`) | `rpool/ROOT/os-000001-<UTC>-install`: local `mountpoint=/`, `canmount=noauto`, `org.zfsbootmenu:commandline`; `bootfs` set (`ZFS-POC-PLAN.md:37-43`, `:185`) |
| Kernel, initramfs | inside the state at `/boot`, no `/boot` partition (`install-btrfs.sh:15`; `DESIGN-GUIDE.md:82-84`) | inside the BE |
| Persistent data | D18 subvolumes plus `@usr-local`; NOCOW on docker, containers, postgresql, mysql (`install-btrfs.sh:75-77`, `:96`) | `rpool/DATA/*` incl. `usr-local` (`ZFS-POC-PLAN.md:187-188`) |
| Control area | `/.top` = `subvolid=5` (`install-btrfs.sh:141`); the layer writes `/.top/.dr-state/` (`dr-state:1105`) | `rpool/DR-META` (`ZFS-POC-PLAN.md:141`, `:189`) |
| fstab | one option set for all btrfs mounts; `/` line without `subvol=`, the boot entry picks the state (`install-btrfs.sh:132-142`) | no `/` line (`DESIGN-CORE-SPLIT.md:36-38`) |
| Target packages | `python3`, full stdlib (core D2, `DESIGN-CORE-SPLIT.md:12-15`) | same, plus kernel, headers, `zfs-dkms`, `zfs-initramfs` together, `/etc/hostid`, `REMAKE_INITRD` (`ZFS-POC-PLAN.md:158-161`) |
| Swap | partitions, outside every filesystem (`DESIGN-GUIDE.md:75`, `:194-195`) | same; `ZFS-POC-PLAN.md:182` ("no swap partition") is updated in M6 |

**C2.** `/usr/local` outside the state is layout, not layer: moving it later is a data migration, and v2 stage one
writes its executables there before any data mount (`v2/docs/DESIGN-GUIDE.md:316-320`).

**C3.** For a btrfs or ZFS root, the rollback-ready layout is the only one offered. A plain btrfs root at the top
level would be a third layout, adoptable only by conversion (C6).

### 1.2 Added later (the layer)

`dr/files/` (D27, `DESIGN-CORE-SPLIT.md:421-434`), ESP marker and format file (D26), `initialize-state` (counter
`000001`, install sidecar), the first `gen-menu`, the systemd-boot diversions (`install-btrfs.sh:182-187`).

**C4.** One layer entry point: a new verb `dr-state adopt --boot grub|systemd-boot|zfsbootmenu`, owned by
debian-rollback, shipped in `files/`, its own package **P-rollback-27 adopt** after P-21, P-22, P-25 (a D note and
CLI line in core split follow). It runs `Boot.install()`, the diversions (sdboot), the GRUB neutralisation (C5),
`initialize-state`, `gen-menu --default <initial>`, and `check` after P-24. The installer runs it in the target
chroot (`--sysroot`); on a running host it is "add later". It refuses unless:
- btrfs: the initial state is the only `@os-*` object (`dr-state:1278-1281`); `/.top`, `@usr-local` and every
  configured D18 subvolume exist;
- ZFS: exactly one BE under `rpool/ROOT`, no snapshots, no clones (ZBM's menu can create BEs on a layer-off host);
- on a running host, the running root is the initial state (`/proc/cmdline` on btrfs, `findmnt` on ZFS);
- the ESP holds only the installer's stock files.

It is re-runnable after a partial failure: each step checks its postcondition first, as `initialize-state` already
does (`dr-state:1282-1291`). A later adoption records the kernel present at adoption in the install sidecar; accepted.

**C5.** Layer off is allowed per boot path as follows.
- **btrfs + GRUB: yes.** Stock GRUB; `update-grub` writes `/boot/grub/grub.cfg` in the state (U1); installed with
  `grub-install --removable --no-nvram`, so the firmware path is `EFI/BOOT/BOOTX64.EFI`, the file `install-grub`
  replaces on adoption (`dr-state:602-610`). The flags are not remembered: a `grub-efi-amd64` upgrade re-runs
  `grub-install` (postinst not in the repo; Debian's does so when `EFI/debian` exists, reading debconf; U2), writing
  `EFI/debian/grubx64.efi` and a first NVRAM entry, possibly in stage one's unattended upgrade (`installer.py:1545`)
  before adoption. After adoption that entry boots stock GRUB with the prefix of the state where `grub-install` last
  ran, ignoring dr's default. Today's dr disks never run stock `grub-install`, so dr-serial's ESP probe holds
  (`dr-serial.py:1846-1850`; `RESULTS-M1-RUN3-KERNEL-2026-11-18.md:29` does not record a `grub-efi` upgrade). So the
  layer-off install presets debconf `grub2/force_efi_extra_removable=true` and `grub2/update_nvram=false`; `adopt`
  neutralises stock `grub-install` (switch to `grub-efi-amd64-bin`, else a diversion), removes `EFI/debian` and its
  NVRAM entry, and checks that postcondition. Layer-on installs get `grub-efi-amd64-bin` only.
- **btrfs + systemd-boot: no.** systemd-boot cannot read btrfs; dr-state publishes kernels to the ESP
  (`install-btrfs.sh:15-17`), and the stock BLS writer is masked before debootstrap (`:108-113`). systemd-boot implies
  the layer (Q2).
- **ZFS + ZBM: yes.** ZBM discovers boot environments itself (`ZFS-POC-PLAN.md:35-43`). The installer sets `bootfs`;
  adoption adds marker and runtime. The verified EFI has one writer, `dr-state install-zfsbootmenu`
  (`ZFS-POC-PLAN.md:142`, `:168`); a v2 copy would duplicate it, so v2 runs that verb even with the layer off.

### 1.3 Converting an already-installed host

**C6.** Conversion stays out of scope (D7). An ext4 root (netcup image, `dr/docs/RESEARCH-2026-11.md:25`) needs two
full copies and partition moves; `btrfs-convert` is rejected (`DESIGN-GUIDE.md:218`); a top-level btrfs root needs a
top-level delete beside every subvolume. Only `@rootfs` (`DESIGN-GUIDE.md:129`) is cheap (Q4). r1002 and v1001 are
reinstalled; what must survive is restored (Q11).

## 2. Convergence with debian-install-v2

### 2.1 Facts

- v2 configures a provider-installed Debian in place: stage one, reboot, stage two (`v2/README.md:5-10`;
  `installer.py:4631`, `:4787`). Nothing in it runs debootstrap or edits GRUB; `update-grub` is allowlisted
  (`actions.py:130`) and never called.
- Root handling is ext4 only: `dumpe2fs`/`resize2fs` facts (`installer.py:2641-2672`), shrink plan (`:2701`), grow
  path (`_grow_root_to_plan`, `:3845-3950`).
- `_discover_root` refuses both new roots (`installer.py:307-316`; read, not run, U6). On btrfs,
  `findmnt -n -o SOURCE /` prints `/dev/vda2[/@os-…]`, which fails the `fullmatch`; a ZFS source is not under `/dev/`.
- Data volumes: `fs: zfs` refused (`layout_policy.py:356-363`), `subvol` refused (`:382-383`), mount policy
  `:113-132`; mount lines from `_data_mount_entry` (`installer.py:3730-3732`); fstab written at `:3827`, `:4213`.
- v2's VM harness is BIOS only (`RESEARCH-2026-11.md:38`). vm-lab boots OVMF guests on a plain Docker host since
  P-rollback-30 (VML-BL-07, merged `2e96071a1`).

**C7.** The one installer is debian-install-v2. A second entry mode, **fresh-disk**, runs from a Live system, a
rescue system or a builder guest against one blank whole disk: whole table, layout of 1.1, debootstrap, boot path,
optionally `dr-state adopt` (C4), and a first-boot unit running stage one and two on the target. In-place mode stays
as is, ext4 only. `install-btrfs.sh` retires (2.4); the branch's `poc/install-zfs.sh` moves into v2 (M6). Reason: v2
owns config, plan preview, the whole-disk plan, notifications, SSH keys and host tuning (`v2/docs/CONSUMERS.md`); the
target install is one 234-line script plus the ZFS salvage.

**C8.** The layer stays in debian-rollback. v2 copies `dr/files/` verbatim with modes (D27), excluding `__pycache__`
(DR-114), and runs `dr-state adopt` (for ZFS also `install-zfsbootmenu`); nothing else. It composes the initial state
name from one format string; `initialize-state` validates it (`DESIGN-CORE-SPLIT.md:433-434`).

### 2.2 Schema changes (v2 `schema_version` 2, `v2/debian_install_v2/config.py:27`)

- `install_mode`: `in-place` (default, today) or `fresh-disk`.
- `target` (fresh-disk only): `disk` (explicit by-id path; never auto-picked), `suite`, `mirror`, `security_mirror`,
  `hostname`, `bootloader` (`grub|systemd-boot|zfsbootmenu`), `kernel_cmdline` (C11), `network`: DHCP on a given MAC
  plus optional static IPv6, as systemd-networkd files (`ZFS-POC-PLAN.md:171`), replacing ifupdown on `eth0`
  (`install-btrfs.sh:154-158`).
- `partitioning.esp_mib` (fresh-disk only, default and minimum 512). Plan order: ESP, root, data, reserve,
  `iobench-raw`, swap.
- `partitioning.root.fs`: `ext4` in-place only; `btrfs`, later `zfs`, fresh-disk only. `keep` and shrink stay
  in-place only.
- `rollback.enabled` (default `false`): `true` needs `root.fs` btrfs or zfs; `bootloader: systemd-boot` needs `true`.
- The base state-excluded set (D18, `/usr/local`, NOCOW flags) is a constant in `layout_policy.py`. Per host, config
  sets `rollback.opt_outside` (D18's `/opt` opt-out, `DESIGN-GUIDE.md:45`) and `rollback.extra_excluded` (refused at
  or above a system path), and may drop a base path a data volume takes over (Q8, Q9). These subvolumes predate any
  package, so `_check_mount` and `_UNSEEDED_RUNTIME_MOUNT_TREES` (`layout_policy.py:113-132`) skip them.
- Data mounts keep the policy and refuse a mount at or above a root-layout path; below one is accepted (r1002's
  `MDT_COW` at `/srv/cow` in `@srv`, `scripts/netcup/examples/debian-install-v2/r1002.json:12`).
- The data-volume `subvol` and `fs: zfs` refusals stay; root on ZFS is `root.fs: zfs` (M6).

### 2.3 v2 code changes beyond the schema

- `_discover_root`: btrfs uses `findmnt -n -o SOURCE --nofsroot /`; ZFS maps the pool vdev to its partition.
- Ext4-only steps run only for `root.fs: ext4`: shrink plan and hook (`installer.py:4667-4685`), growth (`:4827`),
  root-cap guard (`:2417`).
- First boot: stage one recomputes the plan ("a pure function of (config, disk size, root start)",
  `v2/README.md:360-361`), finds the table equal, writes nothing. Credentials (`config.py:533`) go in with mode 0600.
- v2's next boot = highest `/boot/vmlinuz-*` (`installer.py:1476`) = dr-state's newest pair (`dr-state:207`).
- Layer on: stage units export `DR_STATE=none` (D18 wrapper, `DESIGN-CORE-SPLIT.md:297-299`); stage two ends with
  one `dr-state snapshot --reason host-setup` (Q3).
- New bare-host lane `r1-vm-target-install` (vm-lab OVMF guest), beside `r1-vm-real-commit` (`v2/run-gate.toml:117`).

### 2.4 `install-btrfs.sh` responsibilities

V2 = moves into fresh-disk mode. L = layer payload (`files/` per D27, or `adopt`). R = retired.

| Lines | Responsibility | Destination |
|---|---|---|
| 20-46, 144-146 | arguments, `--yes`, dry-run printing | V2: CLI verb plus `plan`-style preview |
| 48-59 | UEFI check; disk guard | V2: preflight, repeated before the first write (C10) |
| 79-106 | `sgdisk`, `mkfs`, subvolumes, NOCOW, mounts; name via `_state-name` | V2: planner, table write, read-back; layout (2.2); name format (D27) |
| 108-113, 119-123 | sdboot BLS writer mask, check | V2, systemd-boot only; P-41 may replace it (DR-98) |
| 115-117 | debootstrap `--include` | V2, plus `python3` (D2); `snapper` R (P-25, DR-94) |
| 125-153 | UUIDs, fstab, hostname, hosts, sources | V2: fstab writer (`installer.py:3827`, `:4213`), `target.*` |
| 154-158 | ifupdown on `eth0` | R, replaced by `target.network` |
| 160, 206-230 | PoC `dr-state`; hook heredocs, lock-file removal | L: `cp -a files/` (P-21, P-25); no `/run` residue |
| 161-171 | chroot, root password, `/root` mode, `update-initramfs` | V2: SSH keys first; password only via a private temp file (`ZFS-POC-PLAN.md:171-172`) |
| 172-204 | boot installers, diversions, `initialize-state`, `gen-menu`, BLS refusal | layer off: V2 stock boot path (C5); layer on: L `adopt` |
| 64-74, 231-234 | cleanup trap, debootstrap log tail | V2 |

Deleting it in M3 touches: `poc/test-install-btrfs-bootloader.sh` (D31 drops 4 cases, `DESIGN-CORE-SPLIT.md:542-545`;
the rest ported to v2 fresh-disk dry-run tests); `poc/test-build-seed.sh:26`, `:31`, `:96-104`, `poc/build-seed.sh:23`,
`iso/build-iso.sh:68` (rewritten to stage v2, its wheel, `files/`); `iso/dr-autoinstall:215` (calls v2);
`poc/dr-serial.py:1270-1304` (reads the APT hook from `install-btrfs.sh`; P-25 moves it to `files/`); seed
`dr-build.sh` (`poc/seed/user-data`, DR-99; deleted). Two install paths live until M3 and P-21, P-25 edit the
retiring script. Accepted: core-split packages need not wait for fresh-disk mode, the edits are copy steps (D29 g),
and M3 removes them.

### 2.5 D27, D32 and P-21

- D32 is unaffected: no Python at boot. D27's "the Live system needs no Python" (`DESIGN-CORE-SPLIT.md:434`) no
  longer holds (U3); `dr-autoinstall` installs tools by apt (`dr/iso/dr-autoinstall:183`) and adds `python3`.
- **C9.** P-21 proceeds unchanged: it switches `install-btrfs.sh`, seed and ISO to `files/`
  (`DESIGN-CORE-SPLIT.md:576`); M2 moves that copy step into v2. P-25 lands before M2. v2 gets `files/` from the
  medium or its sparse clone set (`v2/bootstrap-remote.py:362-366`).
- **C10.** `build-iso.sh` and `dr-autoinstall` stay as the Live launcher: config, disk decision, then v2 fresh-disk
  (`dr-autoinstall:215`). `dr-plan`/`dr-pick-disk` choose the disk; v2's preflight re-checks it before the first
  write. Both apply one refusal list (whole disk; not removable, USB or boot medium; unmounted; not swap; not running
  root) on the same `lsblk` fixtures; P-04's scope covers both (shellcheck, shared fixtures).
- **C11.** dr-state hard-codes `net.ifnames=0 console=tty0 console=ttyS0,115200` in GRUB (`dr-state:998`) and
  systemd-boot entries (`:296`). The netcup console is browser VNC only (`RESEARCH-2026-11.md:37`); `console=ttyS0`
  last makes serial primary, so emergency shells never reach VNC. Before M4 the command line becomes target
  configuration (`target.kernel_cmdline`, e.g. `/etc/kernel/cmdline` in the state), `console=tty0` last on netcup; a
  new DR entry.

## 3. Milestone path

| M | Content | Order | Acceptance (vm-lab guest first) | Evidence |
|---|---|---|---|---|
| M1 | v2 fresh-disk, btrfs, layer off, stock GRUB; 2.2, 2.3; OVMF guest via vm-lab's plain-host start | now, parallel with P-20b..P-22 | boots from the disk alone; `rootflags=subvol=@os-000001-…`; default subvolume 5; D18 mounted; stage-two marker; layout = `plan`; second boot writes no table; GRUB upgrade leaves ESP and NVRAM unchanged (U2); v2 `r0-r1` and the new lane PASS | lane and serial logs, bundle (B13) |
| M2 | P-rollback-27 `adopt`; v2 runs it; later adoption on an M1 guest | after P-21, P-22, P-25; `check` after P-24 | `offline` covers `adopt` (refusals, re-run); GRUB, sdboot, adopted M1 guests: apt snapshot, older state booted, `set-default`; GRUB upgrade after adoption: no `EFI/debian`, no Debian NVRAM entry | lanes, bundles |
| M3 | ISO and NoCloud builder launch v2; `install-btrfs.sh`, `dr-build.sh` deleted (2.4) | before P-40 | both ISO variants install through v2; guards and P-04 PASS | ISOs, bundles |
| P-40 | release matrix on v2-built disks | Depends 22-26, 33, M3 | as PLAN-rollback P-40 | as planned |
| M4 | r1002: btrfs, GRUB, layer on, ISO (Q6) | after P-40 and C11; first P-rollback-01's ZFS base disk for P-44 (`/root/jobs/debrollback-zbm-20261123`), run dirs, evidence archived off-host (B13), verified; Q11 list backed up | read-only facts (U5); boots, SSH, stage two, runner finds `MDT_COW`; Q11 list restored; apt snapshot, previous state via the console, `set-default` back | logs, console captures, `dr-state list --json` |
| M5 | v1001 | after the r1002 period (Q12); quiet window | as M4 | as M4 |
| M6 | v2 `root.fs: zfs` + ZBM | after P-42 | P-43 lanes on v2; P-44 on a v2-built disk at 2048 MiB | as P-44 |

M3 before P-40 makes P-40 and B15 wait for M1-M3 (was "Depends 22-26, 33"); fallback Q5 (d). M6 splits P-43:
`build-zfs-iso.sh`, `dr-zfs-autoinstall`, `fetch-zbm-efi.sh` stay in dr; `install-zfs.sh` moves into v2. Layer-off
ZFS still needs dr's verified-ZBM pipeline (`ZFS-POC-PLAN.md:174`) and `install-zfsbootmenu` (C5). Leases and merges
go through the main controller; one guest at a time.

## 4. Unknowns

- U1: stock `update-grub` adds `rootflags=subvol=` for a subvolume root (expected from `10_linux`, not read). M1.
- U2: when Debian 14's `grub-efi-amd64` postinst re-runs `grub-install` and whether it honours the presets. Verified
  in M1 with a GRUB upgrade on a layer-off guest and again after adoption (M2); the existing probe is
  `dr-serial.py:1846-1850` (ESP listing, `BOOTX64.EFI` hash), which gains an NVRAM listing.
- U3: `python3` >= 3.13 on Debian Live and, for Q6 (b), the netcup rescue system (Grml).
- U4: for Q6 (b), the rescue system boots UEFI and has `debootstrap`, `sgdisk`, `btrfs`.
- U5: r1002 network (DHCPv4; IPv6 by RA or static). A wrong `target.network` loses the host to the console.
- U6: `_discover_root` on btrfs, read from code (2.1).
- U7: Docker's storage driver on NOCOW `@docker`. M1 (void under Q8 (c)).

## 5. Operator questions

- **Q1. Home of the installer.** (a) v2 fresh-disk mode; (b) v2 in-place on a dr-built disk (the F9 handoff, rejected
  by the operator: two installers, split layout owner); (c) a new project. **Recommended (a):** C7.
- **Q2. systemd-boot, layer off.** (a) refused; (b) stock `kernel-install`, cleaned up on adoption. **Recommended
  (a):** without ESP publication it cannot boot a btrfs state; cleanup would own foreign entries (B-3).
- **Q3. Layer on: when to adopt.** (a) in the chroot, `DR_STATE=none` in the stages, one `host-setup` snapshot;
  (b) layer off, adopt after stage two (every install adopts); (c) in the chroot, normal snapshots, 900 s window
  (D21). **Recommended (a):** (b) is not one path (systemd-boot cannot run layer off, C5) and keeps stage one's
  upgrade and first boots in C5's stock-GRUB window; M2 tests running-host adoption anyway.
- **Q4. Converting installed hosts.** (a) out of scope (D7); (b) `@rootfs` backlog item after P-40; (c) ext4
  conversion. **Recommended (a), noting (b):** hosts may be reinstalled; ext4 conversion is most risk, least use.
- **Q5. First install on r1002.** (a) after P-40 on v2 disks; (b) after M1, layer off; (c) after M2, before P-40;
  (d) P-40 on today's installer, M3 after, matrix re-run on v2 disks before M4. **Recommended (a):** one matrix run,
  on the installer r1002 gets, P-40 waiting for M1-M3; (d) if that passes an agreed date.
- **Q6. Install medium on netcup.** (a) the Live ISO via `scp-api`; (b) the rescue system running v2 over SSH.
  **Recommended (a):** P-40 proves that path (B15); 48 h upload deletion and unrestored boot order
  (`DESIGN-GUIDE.md:262-264`) cost one manual step. (b) needs U3, U4 and a milestone M3b before M4: v2 fresh-disk
  from a Grml-like rescue guest in vm-lab over SSH.
- **Q7. ZFS on a real host.** (a) guests only (P-44); (b) r1002 after M4. **Recommended (a):** DKMS exposure
  (`DESIGN-GUIDE.md:182-188`) for no gate benefit.
- **Q8. netcup disk layout.** r1002: root 160 GiB plus `MDT_COW` at `/srv/cow` (`r1002.json:6`, `:12`); OS states
  and D18 data share root, snapshots keep old blocks. (a) keep 160 GiB; (b) enlarge root; (c) keep it, Docker's data
  root on its own ext4 data volume, no `@docker`. **Recommended (c):** Docker grows most, its data root stays ext4
  with overlay2 by measurement (`RESEARCH-2026-11.md:35`), U7 goes. Confirm `MDT_COW` under `@srv` (2.2).
- **Q9. D18 set per host.** (a) base constant plus per-host `/opt` opt-out and extra exclusions (2.2); (b) one fixed
  constant. **Recommended (a):** D18 puts the `/opt` opt-out in the install config (`DESIGN-GUIDE.md:45`).
- **Q10. Encryption (D10).** (a) out of scope for r1002/v1001; (b) LUKS plus `dropbear-initramfs` now.
  **Recommended (a):** with a VNC-only console a failed unlock needs a browser session, dropbear needs initramfs
  networking (U5), and GRUB's btrfs environment block fails under encryption (`DESIGN-GUIDE.md:367`).
- **Q11. What survives the r1002 reinstall.** (a) a restore list (runner registration and state, secrets, named
  Docker volumes) plus evidence and the P-44 disk, in the P-rollback-01 archive, SHA-256-verified before the wipe;
  (b) evidence and P-44 disk only. **Recommended (a):** C6 restores; one read-only inventory settles the list.
- **Q12. r1002 period before M5.** (a) 7 days; (b) a lane count; (c) 7 days, one unattended kernel upgrade with its
  snapshot and 20 gate-lane runs, whichever is later. **Recommended (c):** kernel upgrades are the risk.
