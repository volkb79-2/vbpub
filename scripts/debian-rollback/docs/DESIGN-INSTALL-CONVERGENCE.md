# Design note: one installer for rollback-ready hosts

Status: proposal 2026-11-24, one review round to follow. Operator direction: install a host with root on btrfs (later
ZFS) on r1002 or v1001, combined with debian-install-v2; rollback is a layer adopted at install or later; one
installer. Read on main `60f6da6d4`. `v2/` = `scripts/debian-install-v2/`, `dr/` = `scripts/debian-rollback/`.
Unknowns are in section 4.

Once accepted, this supersedes D19 ("`debian-install-v2` stays separate", `dr/docs/DESIGN-GUIDE.md:46`), the F9/DR-06
handoff model (`dr/docs/WORK-PACKAGES.md:80`, `:291-294`) and the "layout owner" row
(`dr/docs/MILESTONES-2026-11-18.md:68`). It keeps D4 UEFI only, D7 new installs only (`DESIGN-GUIDE.md:31`, `:34`),
D18, and core-split D2, D27 and D32.

## 1. Install time versus later

**C1.** Install time creates everything that is storage layout or the firmware-to-kernel path, because changing
either later is a data migration or a boot-path swap. dr-state software, hooks and markers are the layer and can come
later.

### 1.1 Created at install time (the rollback-ready layout)

| Item | btrfs | ZFS |
|---|---|---|
| ESP | GPT, p1, vfat, 512 MiB (`dr/poc/install-btrfs.sh:79-83`). p1 because the standalone GRUB image hard-codes `(,gpt1)/EFI/dr` (`dr/poc/dr-state:606`) | p1, 512 MiB: three ZBM copies of 85,742,592 bytes plus 128 MiB free (`dr/docs/ZFS-POC-PLAN.md:197-198`) |
| Root container | p2 btrfs `drroot` (`install-btrfs.sh:80`, `:84`) | p2 `rpool`; `ROOT`, `DATA` with `canmount=off` (`ZFS-POC-PLAN.md:182-186`) |
| Top level | default subvolume stays id 5; states and data subvolumes are its children (`install-btrfs.sh:10`, `:92-97`); GRUB reads `/<state>/boot/...` from it (`dr-state:997-998`) | n/a |
| Initial state | `@os-000001-<UTC>-install` (`install-btrfs.sh:88-92`) | `rpool/ROOT/os-000001-<UTC>-install`: local `mountpoint=/`, `canmount=noauto`, `org.zfsbootmenu:commandline`; `bootfs` set (`ZFS-POC-PLAN.md:37-43`, `:185`) |
| Kernel, initramfs | inside the state at `/boot`, no `/boot` partition (`install-btrfs.sh:15`; `DESIGN-GUIDE.md:82-84`) | inside the BE |
| Persistent data | D18 subvolumes plus `@usr-local`; NOCOW on docker, containers, postgresql, mysql (`install-btrfs.sh:75-77`, `:96`) | `rpool/DATA/*` incl. `usr-local` (`ZFS-POC-PLAN.md:187-188`) |
| Control area | `/.top` = `subvolid=5` (`install-btrfs.sh:141`); the layer writes `/.top/.dr-state/` (`dr-state:1128`) | `rpool/DR-META` (`ZFS-POC-PLAN.md:141`, `:189`) |
| fstab | one option set for all btrfs mounts; `/` line without `subvol=`, the boot entry picks the state (`install-btrfs.sh:132-142`) | no `/` line (`DESIGN-CORE-SPLIT.md:36-38`) |
| Target packages | `python3`, full stdlib (core D2, `DESIGN-CORE-SPLIT.md:12-15`) | same, plus kernel, headers, `zfs-dkms`, `zfs-initramfs` together, `/etc/hostid`, `REMAKE_INITRD` (`ZFS-POC-PLAN.md:158-161`) |
| Swap | partitions, outside every filesystem (`DESIGN-GUIDE.md:75`, `:194-195`) | same |

**C2.** `/usr/local` outside the state is layout, not layer: moving it later is a data migration, and v2 stage one
writes its executables there before any data mount (`v2/docs/DESIGN-GUIDE.md:316-320`).

**C3.** For a btrfs or ZFS root, the rollback-ready layout is the only one offered. A plain btrfs root at the top
level would be a third layout that the layer could adopt only by the conversion in 1.3.

### 1.2 Added later (the layer)

The runtime and hook files from `dr/files/` (D27, `DESIGN-CORE-SPLIT.md:421-434`), the ESP marker and format file
(3.4, D26), `initialize-state` (counter `000001` plus the install sidecar), the first `gen-menu`, and the
systemd-boot diversions now inline in `install-btrfs.sh:182-187`.

**C4.** One layer entry point: a new verb `dr-state adopt --boot grub|systemd-boot|zfsbootmenu`, owned by
debian-rollback and shipped in `files/`. It runs `Boot.install()` (`DESIGN-CORE-SPLIT.md:158`), the diversions
(sdboot only), `initialize-state`, `gen-menu --default <initial>`, and `check` once P-24 has landed. The installer
runs it in the target chroot; on a running host it is "add later". It refuses unless the initial state is the only
`@os-*` object, as `initialize-state` already does (`dr/poc/dr-state:1278-1281`). A later adoption records the kernel
present at adoption in the install sidecar; accepted.

**C5.** Layer off is allowed per boot path as follows.
- **btrfs + GRUB: yes.** Stock Debian GRUB; `update-grub` writes `/boot/grub/grub.cfg` inside the state (U1). It is
  installed with `grub-install --removable --no-nvram`, so the only firmware path is `EFI/BOOT/BOOTX64.EFI`, the one
  file `install-grub` replaces on adoption (`dr-state:602-610`). No NVRAM entry for `EFI/debian/` is created, because
  it would keep booting the stock menu after adoption.
- **btrfs + systemd-boot: no.** systemd-boot cannot read btrfs; dr-state publishes kernels to the ESP
  (`install-btrfs.sh:15-17`), and the stock BLS writer is masked before debootstrap (`:108-113`). systemd-boot implies
  the layer (Q2).
- **ZFS + ZBM: yes.** ZBM discovers boot environments itself (`ZFS-POC-PLAN.md:35-43`). The installer sets `bootfs`
  and copies the verified EFI; adoption adds marker and runtime.

### 1.3 Converting an already-installed host

- **ext4 root** (netcup image: 243 MiB ESP, 977 MiB `/boot`, root `vda3`, `dr/docs/RESEARCH-2026-11.md:25`).
  Shrink ext4 offline (`v2/README.md:236`), create btrfs in the freed space, copy root and each D18 path into
  subvolumes, fold `/boot` into the state, rewrite fstab, grow the ESP to 512 MiB (partition moves), install the boot
  path, reboot, delete ext4. btrfs cannot grow backwards into the freed extent, so a second copy or
  `btrfs device add`/`remove` follows. Risks: two full copies, power loss mid-copy, partition moves, rescue systems
  without the tools. `btrfs-convert` is rejected (`DESIGN-GUIDE.md:218`).
- **btrfs root at the top level.** Snapshot the top level to the state name (nested subvolumes excluded), reflink the
  D18 data into new subvolumes, switch to `rootflags=subvol=`, reboot, delete the old OS tree from the top level. Cheap,
  but that delete runs beside every subvolume: one wrong path destroys data. ESP as above.
- **Debian-installer btrfs** (`@rootfs`, `DESIGN-GUIDE.md:129`): snapshot `@rootfs`; no top-level delete.

**C6.** Conversion stays out of scope (D7). r1002 and v1001 are reinstalled; what must survive is restored, not
converted. Only the `@rootfs` case may be reopened, as a backlog item after P-40 (Q4).

## 2. Convergence with debian-install-v2

### 2.1 Facts

- v2 configures a provider-installed Debian in place: stage one, reboot, stage two (`v2/README.md:5-10`;
  `v2/debian_install_v2/installer.py:4631`, `:4787`). Nothing in `v2/debian_install_v2/` runs debootstrap or edits
  GRUB.
- Root handling is ext4 only: `dumpe2fs`/`resize2fs` and an offline shrink hook (`installer.py:2644-2672`,
  `:3886-3950`).
- `_discover_root` refuses both new roots (`installer.py:307-316`; read, not run, U6). On btrfs,
  `findmnt -n -o SOURCE /` prints `/dev/vda2[/@os-…]`, which fails the `fullmatch`; a ZFS source is not under `/dev/`.
- Data volumes: `fs: zfs` refused (`layout_policy.py:356-363`), `subvol` refused (`:382-383`); mount policy at
  `:113-132`. v2's VM harness is BIOS only (`dr/docs/RESEARCH-2026-11.md:38`); dr guests use `vm-lab` with OVMF.
**C7.** The one installer is debian-install-v2. It gains a second entry mode, **fresh-disk**, run from a Live system,
a rescue system or a builder guest against one blank whole disk. It plans and writes the whole table, builds the
layout of 1.1, runs debootstrap, installs the boot path, optionally runs `dr-state adopt` (C4), and installs a
first-boot unit that runs its existing stage one and two on the target. In-place mode is unchanged and stays ext4
only. `install-btrfs.sh` is retired (2.4); the branch's `poc/install-zfs.sh` never lands. Reason: v2 already owns
config authority, plan preview, the whole-disk plan, notifications, SSH keys and host tuning, the larger released part
(`v2/docs/CONSUMERS.md`); the target install is one 234-line script plus the ZFS salvage.

**C8.** The layer stays in debian-rollback. v2 copies `dr/files/` verbatim with modes (D27), excluding `__pycache__`
(DR-114), and runs `dr-state adopt`; nothing else. It composes the initial state name from one format string and
leaves validation to `initialize-state` (`DESIGN-CORE-SPLIT.md:433-434`). It knows no menus, hooks or diversions.

### 2.2 Schema changes (v2 `schema_version` 2, `v2/debian_install_v2/config.py:27`)

- `install_mode`: `in-place` (default, today) or `fresh-disk`.
- `target` (fresh-disk only, refused in-place): `disk` (explicit by-id path; v2 never auto-picks), `suite`, `mirror`,
  `security_mirror`, `hostname` (v2 has none today), `bootloader` (`grub|systemd-boot|zfsbootmenu`), `network`:
  DHCP on a given MAC plus optional static IPv6, as systemd-networkd files (ZFS fix "not `eth0`",
  `ZFS-POC-PLAN.md:171`), replacing ifupdown on `eth0` (`install-btrfs.sh:154-158`).
- `partitioning.esp_mib` (fresh-disk only, default and minimum 512). Plan order: ESP, root, data, reserve,
  `iobench-raw`, swap.
- `partitioning.root.fs`: `ext4` in-place only; `btrfs`, later `zfs`, fresh-disk only. `keep` and shrink stay
  in-place only.
- `rollback.enabled` (default `false`): `true` needs `root.fs` btrfs or zfs; `bootloader: systemd-boot` needs `true`.
- The state-excluded set (D18 plus `/usr/local`, with NOCOW flags) is one constant in `layout_policy.py`, not config.
  These subvolumes exist before any package, so `_check_mount` and `_UNSEEDED_RUNTIME_MOUNT_TREES`
  (`layout_policy.py:113-132`) do not apply to them. Data-area mounts keep the policy and also refuse a mount at or
  above a root-layout path.
- The data-volume `subvol` and `fs: zfs` refusals stay; convergence needs neither. Root on ZFS is `root.fs: zfs`, a
  separate pool-and-datasets path (M6).

### 2.3 v2 code changes beyond the schema

- `_discover_root` (`installer.py:307-316`): btrfs uses `findmnt -n -o SOURCE --nofsroot /`; ZFS maps the pool vdev
  to its partition.
- Ext4-only steps run only for `root.fs: ext4`: shrink plan and hook (`installer.py:4667-4685`), growth (`:4827`),
  root-cap guard (`:2417`).
- First boot: stage one recomputes the plan, "a pure function of (config, disk size, root start)"
  (`v2/README.md:360-361`), finds the table equal to it and writes nothing. Credentials keep the root-storage path
  (`config.py:533`), copied into the target with mode 0600.
- v2's "next boot = highest `/boot/vmlinuz-*`" (`installer.py:1476`) matches dr-state's newest pair (`dr-state:207`).
- Layer on: the stage units export `DR_STATE=none`, honoured by the D18 wrapper before Python starts
  (`DESIGN-CORE-SPLIT.md:297-299`); stage two ends with one `dr-state snapshot --reason host-setup` (Q3).
- New bare-host lane `r1-vm-target-install` on `vm-lab` with OVMF, beside `r1-vm-real-commit`
  (`v2/run-gate.toml:117-126`).

### 2.4 `install-btrfs.sh` responsibilities

V2 = moves into v2 fresh-disk mode. L = layer payload step (`files/` per D27, or `adopt`). R = retired.

| Lines | Responsibility | Destination |
|---|---|---|
| 20-46, 144-146 | arguments, `--yes`, dry-run printing | V2: CLI verb plus `plan`-style preview |
| 48-59 | UEFI check; disk guard (whole disk, unmounted, not swap, not running root) | V2: preflight, repeated before the first write. `dr-plan`/`dr-pick-disk` stay in the Live launcher (C10) |
| 79-84 | `sgdisk`, `mkfs` | V2: planner gains ESP and root-container entries; v2's table write and read-back |
| 86-97, 99-106 | state and D18 subvolumes, NOCOW, target mounts | V2: root layout (constant) |
| 88-91 | initial name via `dr-state _state-name` | V2: format string (D27); `initialize-state` validates |
| 108-113, 119-123 | sdboot BLS writer mask before debootstrap, its check | V2, only for `bootloader: systemd-boot`, naming the dr requirement; P-41 may replace it (DR-98) |
| 115-117 | debootstrap `--include` | V2, plus `python3` (D2); `snapper` R (P-25, DR-94) |
| 125-153 | UUIDs, fstab, hostname, hosts, update sources | V2: v2's fstab writer (`installer.py:3732`) gains the btrfs lines; `target.*` |
| 154-158 | ifupdown on `eth0` | R, replaced by `target.network` |
| 160 | install the PoC `dr-state` | L: `cp -a files/` (P-21, D27) |
| 161-171 | bind mounts, chroot, root password, `/root` mode, `update-initramfs` | V2: SSH keys first; a password only through a private temporary file (`ZFS-POC-PLAN.md:171-172`) |
| 172-188 | `install-grub`/`install-systemd-boot`, diversions | layer off: V2 stock boot path (C5); layer on: L `adopt` |
| 189-204 | `initialize-state`, `gen-menu`, refuse unowned BLS entries | L `adopt` (postcondition, later `check`) |
| 206-230 | apt and kernel hook heredocs, lock-file removal | L: `files/etc` (P-25); `adopt` leaves no `/run` residue |
| 64-74, 231-234 | cleanup trap, debootstrap log tail | V2 |

The seed's embedded disk-wiping `dr-build.sh` (`poc/seed/user-data`, DR-99) retires with it; the NoCloud builder
calls v2.

### 2.5 D27, D32 and P-21

- D32 is unaffected: no Python at boot in either mode. The layer's Python runs only in the target, on its `python3`.
- D27's "the Live system needs no Python" (`DESIGN-CORE-SPLIT.md:434`) no longer holds: v2 runs in the Live or rescue
  system (U3). `dr-autoinstall` already installs tools by apt in Live (`dr/iso/dr-autoinstall:183`) and adds
  `python3`.
- **C9.** P-21 proceeds unchanged: it switches `install-btrfs.sh`, seed and ISO to `files/`
  (`DESIGN-CORE-SPLIT.md:576`), and that copy step is what M2 moves into v2. P-25 lands before M2 so `adopt` copies
  files, not heredocs. v2 gets `files/` from the medium or by adding `/scripts/debian-rollback/files/` to its sparse
  clone set (`v2/bootstrap-remote.py:362-366`).
- **C10.** `dr/iso/build-iso.sh` and `dr-autoinstall` stay as the Live launcher: config, guarded disk decision, then
  v2 fresh-disk in place of `install-btrfs.sh` (`dr-autoinstall:215`). The ISO carries v2, its wheel and `files/`.
- **C11.** dr-state's GRUB entry hard-codes `net.ifnames=0` and a serial console (`dr-state:998`). Before a real
  host, the kernel command line becomes target configuration (for example `/etc/kernel/cmdline` in the state); new
  backlog item DR-116.

## 3. Milestone path

| M | Content | Order | Acceptance (vm-lab guest first) | Evidence |
|---|---|---|---|---|
| M1 | v2 fresh-disk, btrfs, layer off, stock GRUB; 2.2, 2.3 | now, parallel with P-20b..P-22; no dr-state needed | Boots from the target disk alone; `/proc/cmdline` has `rootflags=subvol=@os-000001-…`; `btrfs subvolume get-default` = 5; every D18 subvolume mounted; stage-two marker; data, `iobench-raw`, swap match `plan`; second boot writes no table; v2 `r0-r1` and the new lane PASS on the head | lane logs, serial log, archived bundle (B13) |
| M2 | `dr-state adopt` in dr; v2 runs it at install; later adoption on an M1 guest (GRUB) | after P-21, P-22, P-25; `check` step after P-24 | dr `offline` covers `adopt` (`--sysroot`); GRUB and sdboot guests and the adopted M1 guest: apt snapshot, older state booted from the menu, `set-default` | lanes, guest bundles |
| M3 | Live ISO and NoCloud builder launch v2; `install-btrfs.sh`, `dr-build.sh` deleted | before P-40 | both ISO variants install a guest through v2; `dr-autoinstall` guards unchanged, P-04 lint PASS | `build-iso` outputs, bundles |
| P-40 | release matrix as planned | after M3 | as PLAN-rollback P-40, on v2-built disks | as planned |
| M4 | r1002: btrfs, GRUB, layer on | after P-40 PASS (Q5) | read-only facts first (U4, U5); boots, SSH up, stage two done, runner provisioning finds `MDT_COW`; one apt snapshot, previous state booted via the netcup console, back with `set-default` | install log, console captures, `dr-state list --json`, archived |
| M5 | v1001 | after r1002 has run gate lanes for an agreed period; quiet window | as M4 | as M4 |
| M6 | v2 `root.fs: zfs` + ZBM; P-43 retargeted: branch `install-zfs.sh` salvage goes into v2 | after P-42 | P-43 lanes on v2; P-44 on a v2-built ZFS disk at 2048 MiB | as P-44 |

Host leases and merges go through the main controller; one guest at a time.

## 4. Unknowns

- U1: stock `update-grub` adds `rootflags=subvol=` for a subvolume root (expected from Debian's `10_linux`, not read
  here). M1.
- U2: whether a `grub-efi-amd64` upgrade re-runs `grub-install` and overwrites `EFI/BOOT/BOOTX64.EFI`; this affects
  today's GRUB-target disks too. M2, with a GRUB package upgrade.
- U3: `python3` 3.13 or newer on Debian Live and the netcup rescue system (Grml).
- U4: whether the netcup rescue system boots UEFI and has `debootstrap`, `sgdisk`, `btrfs`.
- U5: r1002 network (DHCPv4; IPv6 by RA or static). A wrong `target.network` loses the host to the console.
- U6: `_discover_root` on btrfs, judged from code (2.1), not run.
- U7: Docker's storage driver on the NOCOW `@docker` subvolume under v2's Docker setup. M1.

## 5. Operator questions

- **Q1. Home of the one installer.** (a) v2 gains fresh-disk mode; (b) dr's installer absorbs v2's host setup;
  (c) a new project. **Recommended (a):** see C7; the part that moves is small (2.4).
- **Q2. systemd-boot with the layer off.** (a) refused, systemd-boot implies the layer; (b) stock `kernel-install`,
  cleaned up on adoption. **Recommended (a):** without ESP publication systemd-boot cannot boot a btrfs state, and
  cleanup would take ownership of foreign entries (B-3).
- **Q3. apt during host setup with the layer on.** (a) `DR_STATE=none` during the stages, then one `host-setup`
  snapshot; (b) adopt after stage two; (c) snapshot normally with the 900 s window (D21). **Recommended (a):** the boot
  path is dr's from the first boot, and there is exactly one meaningful recovery point.
- **Q4. Converting installed hosts.** (a) out of scope, reinstall (D7); (b) a backlog item for `@rootfs` after P-40;
  (c) build ext4 conversion. **Recommended (a), noting (b):** both hosts may be reinstalled at any time; ext4
  conversion has the most risk for the least use (1.3).
- **Q5. First real install on r1002.** (a) after P-40, btrfs, GRUB, layer on; (b) after M1, layer off (early host
  evidence; adoption stays possible); (c) after M2, before P-40. **Recommended (a):** r1002 is a working host, and
  P-40 proves recovery on the same installer first. (b) if host evidence is needed sooner.
- **Q6. Install medium on netcup.** (a) the rollback Live ISO attached through `scp-api`; (b) the netcup rescue
  system running v2 over SSH. **Recommended (b) if U3 and U4 hold, else (a):** no ISO upload (netcup deletes uploads
  after 48 h and does not restore boot order, `DESIGN-GUIDE.md:262-264`) and SSH-driven (`DESIGN-GUIDE.md:252`).
  Rehearse in a Grml guest first.
- **Q7. ZFS on a real host.** (a) guests only (P-44); (b) r1002 after M4 has run on btrfs. **Recommended (a) for
  now:** it adds DKMS kernel exposure (`DESIGN-GUIDE.md:182-188`) to a working gate worker for no gate benefit.
