# Design guide: snapshot and rollback for Debian

Status: planning, 2026-11-12. Nothing here is implemented or tested. Evidence
for the statements below is in [RESEARCH-2026-11.md](RESEARCH-2026-11.md).

## 1. Goal

A user-friendly, safe Debian installation in which a package or kernel upgrade
can be undone by booting an earlier state of the operating system.

The model is a **safety belt for a normal, mutable Debian**: the user keeps
using `apt` as usual, installs packages and kernels, and has a way back into
"the old system" when apt broke the system or its dependencies.

It is not an appliance. TrueNAS ships each release as a complete image that is
unpacked into a new boot environment, and regenerates `/etc` from one
configuration database. Stock Debian has no such database, so that model does
not transfer (section 6).

## 2. Decisions (operator, 2026-11-12)

| # | Decision |
|---|---|
| D1 | Safety belt for a mutable, apt-managed system. Not an image-based or appliance model. |
| D2 | Reuse existing bootloaders and tools. Close gaps and build a usable product from vanilla Debian; do not reinvent. |
| D3 | Targets: remote VPS and rented servers, own bare-metal servers, desktops and laptops. |
| D4 | UEFI only. BIOS boot is dropped. |
| D5 | Failed boot: first a manual choice in the boot menu. Automatic fallback is planned as a later phase. |
| D6 | Booting an earlier state also brings back the earlier `/etc`. Accepted. |
| D7 | New installs only. No conversion of existing ext4 hosts. |
| D8 | Delivery: both an install medium and a cloud image. |
| D9 | Must work with the stable kernel and the backports kernel. Start on Debian 13 (trixie). If 13 makes it hard, targeting Debian 14 (forky, currently testing) and later is acceptable. |
| D10 | Disk encryption is nice to have. Secure Boot must be considered and is needed for desktops, not for cloud VPS. |
| D11 | Root on ZFS is acceptable even though cgroup v2 IO and memory limits do not apply to ZFS: production services and the repository can live on btrfs or ext4 on the same host. |
| D12 | The ZFS cache (ARC) should adapt to the host's memory demand, not be capped hard. Hosts run memory-overcommitted with about twice RAM as swap, plus zswap; the target is low memory pressure (PSI), and the OOM killer is not expected to act. |
| D13 | A mirrored (RAID1) root pool is nice to have, not needed now. |
| D14 | The filesystem (btrfs or ZFS) is **not decided**. It is decided after the experiments in [EXPERIMENTS.md](EXPERIMENTS.md). |
| D15 | The VM launcher in `scripts/truenas/tcg_vm.py` becomes a general tool in its own directory and stops being TrueNAS-specific (section 9). |
| D16 | Keep planning until a host is free; no host work before the lease. |

## 3. What "the operating system" means here

The snapshot must contain exactly what a package upgrade changes, and nothing
the user would lose by going back.

| Inside the rolled-back state | Outside (never rolled back) |
|---|---|
| `/usr`, `/bin`, `/lib`, `/sbin` | `/home`, `/root` |
| `/etc` (decision D6) | `/srv`, `/opt`, `/usr/local` |
| `/var/lib/dpkg`, `/var/lib/apt`, `/var/cache/apt` | `/var/log` (so the log of the failed boot survives) |
| the kernel, initramfs and modules that belong to that state | service data: `/var/lib/docker`, `/var/lib/postgresql`, `/var/lib/mysql`, game-server data, VM images |
| the rest of `/var/lib` not listed on the right | swap |

Rules that follow from this:

- **The package database travels with the OS.** If `/usr` goes back and
  `/var/lib/dpkg` does not, dpkg believes packages are installed whose files
  are gone. openSUSE moved its package database into `/usr` for this reason.
- **Each state boots its own kernel.** A snapshot whose `/lib/modules` does not
  match the kernel the bootloader loads does not boot. This is the central
  boot-design constraint (section 5).
- **Service data moves forward only.** When a package upgrade migrates data on
  disk (a database major upgrade, a container engine upgrade), rolling the OS
  back leaves old binaries facing new data. No filesystem solves this and
  neither openSUSE nor Fedora nor TrueNAS does. The product states this
  plainly, keeps such data on separate volumes, and can warn when a data volume
  was written by a newer OS state than the one being booted. A pre-upgrade
  snapshot of the data volumes may be offered as a recovery point; it is never
  rolled back automatically, because that would silently drop data written
  since.
- **Identity survives.** Machine ID, SSH host keys and the journal must not
  change when an earlier state is booted. TrueNAS copies host ID and machine ID
  across explicitly.

Known consequence of D6: a configuration edit, or a user added to
`/etc/passwd`, after the snapshot is absent in the earlier state. It is not
lost: it remains in the abandoned state and can be copied out.

## 4. Phases

| Phase | Content |
|---|---|
| 1 | Layout at install time; snapshot before every apt run; boot menu lists earlier states; a command to make an earlier state permanent; pruning of old states |
| 2 | Automatic fallback: a new state boots on probation and becomes the default only after a health check passes |
| Later, optional | Upgrade inside a clone and reboot into it (the running system is untouched until reboot); mirrored root pool; disk encryption |

## 5. The two candidate designs

Both use the same layout idea from section 3 and the same delivery pipeline
(section 8). They differ in filesystem and in how an earlier state is booted.

### 5.1 Candidate B: btrfs with snapper

- Root is a btrfs subvolume; the "outside" paths of section 3 are separate
  subvolumes, so a snapshot of root does not contain them. Database and VM
  image directories are created with copy-on-write disabled.
- Snapshots before and after each apt run come from Debian's packaged
  `snapper`, which already ships the apt hook (`/etc/apt/apt.conf.d/80snapper`).
- Rollback follows the openSUSE model: `snapper rollback` makes a writable copy
  of the chosen snapshot the default subvolume.

Gaps in Debian that this project must close:

| Gap | Note |
|---|---|
| The Debian installer creates only one subvolume, `@rootfs` | Our installer creates the layout |
| `grub-btrfs` (one boot-menu entry per snapshot) is not packaged in Debian | Package it, or write a small generator of our own |
| No support in Debian's initramfs-tools for booting a read-only snapshot with a writable overlay | The existing helper covers mkinitcpio and dracut only. Alternative: boot a writable copy of the snapshot instead |
| Debian's snapper is three minor versions behind upstream (0.10.6 against 0.13.2) | Check whether anything needed is missing |

Bootloader choices under UEFI (decision D4):

| Bootloader | Each state's kernel | Phase 2 | Secure Boot |
|---|---|---|---|
| GRUB | Stays inside the root subvolume; GRUB reads btrfs directly, nothing is copied | Built by us: one-shot boot of the new state, promoted by a health check. GRUB 2.12 in Debian 13 cannot write its environment file on btrfs; GRUB 2.14 in Debian 14 can | Signed by Debian |
| systemd-boot | Must be copied to the ESP, one copy per state that should stay bootable (as CachyOS does with Limine, deduplicated by hash) | Built in: boot counting and `systemd-bless-boot`, present in Debian 13 | A signed package is believed to exist in Debian 13; not yet verified |

### 5.2 Candidate Z: ZFS with boot environments

- One pool. Each OS state is a boot environment, a dataset
  `rpool/ROOT/<name>` that holds `/`, `/etc`, the package database and `/boot`.
  The "outside" paths are separate datasets with fixed mount points.
- An apt hook (to be written; nothing packaged exists) snapshots the active
  boot environment before each apt run.
- Rollback: clone the snapshot into a new boot environment and boot it; promote
  it to make it permanent.

Bootloader choices under UEFI:

| Bootloader | How it works | Cost |
|---|---|---|
| ZFSBootMenu | One EFI file on the ESP: a small Linux that imports the pool, shows the boot environments and their snapshots, and starts the chosen kernel from inside that environment with kexec | Not packaged in Debian; not signed for Secure Boot (we would sign it with our own key); no automatic fallback; kexec is tested on KVM, a failure on Xen is on record |
| GRUB with a separate boot pool | A small `bpool` partition created with `compatibility=grub2` holds the kernels, `rpool` holds the rest. This is the OpenZFS guide's design | A boot environment becomes two datasets in two pools that must be snapshotted, cloned and activated together (the bookkeeping Ubuntu's abandoned `zsys` did). See below on upgrading the boot pool |
| GRUB with one restricted pool | What TrueNAS does: the whole boot pool is created with `compatibility=grub2`, each environment's kernel is inside it, activation sets the pool's `bootfs` and regenerates the GRUB menu | The whole root pool is limited to the features GRUB can read |
| systemd-boot | Kernels copied to the ESP (what Proxmox's `proxmox-boot-tool` does) | Same kernel-copy work as with btrfs; gives built-in boot counting |

**What happens if the GRUB-readable boot pool is upgraded.** `zpool upgrade`
enables every feature the installed ZFS supports. GRUB can only read a pool
whose active features it knows; once a feature it does not know becomes active,
GRUB refuses the pool and the next reboot stops in the GRUB prompt. Features
generally cannot be switched off again. It is repairable: boot a rescue or live
system that has ZFS, create a fresh boot pool with `compatibility=grub2`, copy
the kernels and initramfs back (they are regenerable anyway), reinstall GRUB.
The damage is an unbootable host until someone does that, discovered only at
the reboot. With the `compatibility=grub2` property set on the pool,
`zpool upgrade` is expected to stay inside the allowed feature set, which
defuses most of the trap; this must be verified on Debian's ZFS version before
relying on it. Ubuntu additionally patched `zpool upgrade` to skip pools named
`bpool`.

Preference among the ZFS options, given UEFI only: ZFSBootMenu, because it
needs neither a second pool nor a feature restriction. The separate boot pool
is the choice only if a Debian-signed GRUB chain is required. Even then the
ZFS kernel module is built locally by DKMS and needs a locally enrolled
signing key, so Secure Boot with ZFS needs key enrolment either way.

Risks specific to ZFS:

- **Out-of-tree module.** ZFS is rebuilt by DKMS for each kernel. When a new
  kernel is newer than what the packaged ZFS supports, the module does not
  build and that kernel cannot mount root. Debian bug #1121154 left ZFS without
  support for kernel 6.18 for 52 days. The estate pins backports at priority
  600, so kernels move to the backports series, which is the exposed pairing.
  Mitigation: an earlier kernel inside the same boot environment stays
  selectable, and the pre-upgrade snapshot exists.
- **cgroup v2.** ZFS dataset writes are attributed to the root cgroup, so
  `io.max`, `io.weight` and `io.latency` do not constrain them, and the ARC is
  probably not charged to a container's memory limit. Accepted in D11 by
  keeping services and the repository on btrfs or ext4.
- **Memory.** The ARC is separate from the kernel page cache. See section 7.
- **Swap.** Swap on a ZFS volume can deadlock under memory pressure. Swap
  stays on plain partitions, as the estate already does.
- **Rescue systems** of VPS providers usually have no ZFS. Keep a ZFS-capable
  rescue ISO.

### 5.3 Position on 2026-11-12

No decision (D14). The research favours btrfs as the default: it is in the
kernel, GRUB reads it, Debian packages the snapshot tool with its apt hook, and
cgroup limits work. ZFS has the more elegant boot-environment model (clone,
promote, per-dataset properties, cheaper with many snapshots, gentler when
nearly full) and the operator has good experience with it on TrueNAS. Both
candidates are built far enough in the VM lab to compare the rollback product,
and compared for performance and memory on a real host.

## 6. Rejected and deferred

| Option | Verdict | Reason |
|---|---|---|
| Image-based A/B updates (systemd-sysupdate with mkosi, ParticleOS) | Rejected | Removes normal `apt install`; systemd-sysupdate calls itself experimental; nobody ships production Debian this way |
| bootc or OSTree on Debian | Rejected | No official Debian support; the composefs backend is marked experimental |
| Vanilla OS ABRoot, Kairos, RAUC, Mender | Rejected | Appliance models with their own image pipelines; apt is not the normal path |
| TrueNAS model (fresh image per version, configuration kept separately) | Rejected | Depends on a single configuration database that Debian does not have |
| LVM thin snapshots with ext4 | Rejected | No per-directory exclusion without one volume per path; the boot-entry tools (snapm, boom) are not in Debian; does not solve kernel matching |
| In-place conversion with `btrfs-convert` | Rejected (and out of scope by D7) | The btrfs documentation itself recommends a fresh filesystem and copy |
| BIOS boot | Dropped (D4) | |
| Upgrade inside a clone, then reboot (openSUSE transactional-update, FreeBSD `bectl` with `pkg`) | Deferred | Safer and atomic, but maintainer scripts then run in a chroot that is not the live system, and nothing maintained exists for apt. Same storage layout as phase 1, so it can be added later |
| Provider snapshots (netcup `scp-api.py snapshots`) | Not a substitute | Whole-disk revert takes user data and services back too, and a provider reinstall deletes the snapshots |

## 7. ZFS memory behaviour

- For ordinary reads and writes, ZFS caches file data only in the ARC, not in
  the page cache. The ARC therefore replaces the page cache for files on ZFS.
- Default ARC limit (OpenZFS 2.3 and 2.4): the larger of RAM minus 1 GiB and
  5/8 of RAM. The lowest allowed limit is 64 MiB.
- **Memory-mapped files are cached twice**, once in the page cache (where a
  mapping must live) and once in the ARC. This is a limitation of ZFS on Linux.
  It affects only mapped files: running programs, shared libraries, and tools
  that map their data (git maps its pack files). It is bounded: the ARC copy
  is ordinary evictable cache and is dropped under pressure. Not measured yet.
- The ARC shrinks when applications need memory, but more slowly than the page
  cache, and it is not counted as available memory, so tools that read
  `MemAvailable` see a fuller host than it is.
- With swap and zswap (D12) the question is not OOM but balance: under
  pressure the kernel chooses between shrinking the ARC and swapping
  application memory, and there is no swappiness-style control for that
  choice. Whether an adaptive ARC keeps memory pressure (PSI) low on an
  overcommitted host is the main thing the real-host experiment measures.
- "1 GB of RAM per TB" is deduplication folklore; no primary source states it
  as a general requirement.

## 8. Delivery

Three ways onto a disk, one installer core:

| Path | Use | How |
|---|---|---|
| Install medium | Bare metal, desktops, VPS with ISO attach | A Debian Live ISO that runs our installer unattended |
| Rescue-system install | VPS where only the provider's images and a rescue system exist | The same installer run from the rescue system |
| Cloud image | Providers that accept custom images | The installer's output captured as a disk image; cloud-init grows it on first boot (it supports btrfs and ZFS) |

Reuse from this repository:

- `scripts/truenas/golden-iso` builds a Debian Live ISO that restores a
  captured disk image with no interaction and reports by webhook. The same
  pipeline fits, with one change: it writes a fixed-size image and never grows
  it; ours runs the installer from the live system (partition, create the
  layout, install), so any disk size works.
- `scripts/netcup/install-restore-iso.py` and `scp-api.py` attach an ISO to a
  netcup host and control boot order. netcup deletes uploaded ISOs after 48
  hours and does not restore boot order when an ISO is detached.
- `scripts/debian-install-v2` configures a provider-installed Debian (apt
  policy, swap, data area, reboot timer). It cannot change the root
  filesystem and its mount policy refuses mounts covering `/`, `/var`, `/etc`
  or `/boot`. It runs after our installer as the configuration stage; the two
  must agree on who owns the partition table.

Things in the existing estate the design must respect:

- Unattended upgrades run in `full` mode with backports pinned at 600; the
  snapshot hook must also fire for unattended runs.
- `vbpub-reboot-check.timer` is the single reboot mechanism. Phase 2 must hook
  into it rather than add a second one.
- `Remove-Unused-Kernel-Packages "true"` removes old kernels. An earlier state
  must not depend on a kernel package that only exists in the current state.
- Initramfs content must be present for every kernel the bootloader might
  pick; a hookless initramfs was a real failure on r1002.

## 9. Test bed: a general VM tool

`scripts/truenas/tcg_vm.py` (1627 lines) runs QEMU in a Docker container
without KVM. It already provides what boot-menu and rollback tests need:

- UEFI firmware (OVMF) with a variable store per guest, so boot entries and
  one-shot boot settings persist across reboots;
- `console sendkey` and `console screendump` through QMP, to press keys in a
  boot menu and capture the screen;
- a two-way serial console socket with a log;
- a copy-on-write overlay over a base disk, up to eight extra disks, and an
  ISO as CD-ROM;
- `--resume` with the same disk and firmware state;
- host protection: memory headroom check, guest-count limit, cgroup checks.

What is TrueNAS-specific or missing:

- 3 GB RAM and 2 CPUs are fixed; TrueNAS names, labels and an HTTPS port
  forward are built in;
- nothing puts the boot menu on the serial line, so a menu can only be checked
  by screenshot;
- no reset command, no boot-loop detection, no failure injection.

Decision D15: lift the generic part into `scripts/vm-lab/`, a general VM
launcher and controller with no TrueNAS knowledge. `scripts/truenas` and this
project become its consumers; the existing Debian harness
(`scripts/debian-install-v2/testing/vm/vmctl`, BIOS only, no keyboard control)
can follow. Per the repository rule each VM keeps exactly one owning project
and its files stay in that project's `artifacts/vm-lab/`, so the tool takes the
state directory as an argument and owns no VM state itself. `tcg_vm.py` is a
gated, tested tool; the extraction is a change of its own with its own review,
and TrueNAS behaviour must not change.

### 9.1 Synergy between the VM tool and pwmcp (proposal, 2026-11-12)

Not part of the extraction change; candidates for the VM tool's and pwmcp's
backlogs. Read from `pwmcp/tools/console-capture/` and
`pwmcp/docs/DESIGN-OCR-PHASE2-3.md`.

| # | Idea | Built in | Benefit |
|---|---|---|---|
| Y1 | Replace `pwmcp/tools/console-capture/launch-truenas-vm.sh` with the VM tool | pwmcp consumes vm-lab | That script is a third QEMU launcher in the repo. It runs QEMU inside the devcontainer with no cgroup cap (its header says so); the VM tool runs guests in the host background slice with admission checks |
| Y2 | Move the generic part of `console_capture.py` into the VM tool: record changed frames, type text, queue keys | vm-lab | The recorder is generic QEMU console work. QMP serves one client, so today the recorder and the VM tool's own console verbs cannot both hold the socket; one owner removes that conflict |
| Y3 | `console wait-for-text`: screendump, then pwmcp's OCR (the exact glyph-grid engine for fixed-font consoles) | vm-lab calls `pwmcp ocr --image`; optional, vm-lab keeps working without pwmcp | Boot menus (GRUB, ZFSBootMenu, systemd-boot) become readable as text with no serial-console setup in the guest. Also gives boot-loop detection: the same screen text seen again after a reset |
| Y4 | One console interface with two backends: "give me the screen" and "send keys", served by QMP for a local guest and by pwmcp's browser for a provider web console (netcup VNC canvas) | small shared library; pwmcp owns the browser backend, vm-lab the QMP backend | A scenario such as "wait for the boot menu, pick the earlier state" is written once and runs against a local VM and against a real netcup host |
| Y5 | The VM tool as corpus source for pwmcp: screens of the Debian installer, Grml, GRUB, ZFSBootMenu, systemd-boot | pwmcp corpus, captured through vm-lab | pwmcp's recognisers get the screens this project needs before any live host is used |
| Y6 | Serve a guest's VNC socket through a web VNC page and point pwmcp's browser at it | test fixture in pwmcp, guest from vm-lab | pwmcp's canvas path (change detection, `wait_for_text`, expect-send) gets an end-to-end test without netcup |

Dependency direction: pwmcp's tools depend on the VM tool; the VM tool never
requires pwmcp (Y3 is an optional call to the `pwmcp ocr` command).

State 2026-11-12: Y1 and Y2 are merged (vbpub@c46b0dfe8).

**Correction 2026-11-12: `libraries/vmscreen` already exists** and was missed
when Y3 and Y5 were written. It reads a local QEMU guest's console as exact
text without OCR (VGA text buffer, or the serial log), has `wait-text`
(regex, timeout exit code), `send` (keys and typed text) and `ocr-pair`
(screenshot plus exact text taken at the same instant, made as ground truth
for pwmcp's OCR). Consequences:

- Y3 is mostly a wiring task: `console wait-for-text` should call vmscreen
  first (exact text for text-mode screens and serial consoles: GRUB in text
  mode, systemd-boot, ZFSBootMenu on serial, the Debian installer in text
  mode) and use `pwmcp ocr --image` only where vmscreen reports no exact
  source (framebuffer consoles such as the graphical GRUB theme or a kernel
  framebuffer console). Boot-loop detection can use either source.
- Y5 should use `vmscreen ocr-pair` for every screen that has an exact-text
  source, so corpus entries come with ground truth; the VM tool's recorder
  covers framebuffer screens.
- The key table now exists in both the VM tool and vmscreen (vmscreen backlog
  VMS-BL-03). Decide there which one stays.
- vmscreen's own notes say QMP serves one client; the VM tool's recorder uses
  short-lived connections, so both can be used on one guest in turn.

Suggested order: Y1 and Y2 (remove duplication), then Y3 (needed by
experiment E1 to read boot menus), then Y5, Y4, Y6.

## 10. Open questions

| # | Question | Settled by |
|---|---|---|
| Q1 | btrfs or ZFS (D14) | VM lab scenarios and the real-host comparison |
| Q2 | Bootloader per candidate: GRUB or systemd-boot for btrfs; ZFSBootMenu or GRUB with a boot pool for ZFS | VM lab |
| Q3 | Boot a snapshot read-only with an overlay, or always boot a writable copy | VM lab; a writable copy needs no initramfs work |
| Q4 | Does the signed systemd-boot package exist in Debian 13, and does `compatibility=grub2` stop `zpool upgrade` on Debian's ZFS | **Answered by desk check 2026-11-12, both yes.** Debian 13 ships `systemd-boot-efi-amd64-signed` (257.x, installs `systemd-bootx64.efi.signed`, loaded through shim; the installer offers systemd-boot). `zpool-upgrade(8)`: with `compatibility` set, only features in the named sets are enabled, so `zpool upgrade` on a `compatibility=grub2` boot pool cannot make it unreadable for GRUB. Two caveats: `zpool status` then no longer warns about features that stay off, and the `grub2` set changed with GRUB 2.12 (older file `grub2-2.06`), so the set must match the installed GRUB. Still to confirm in the VM: Secure Boot actually boots through shim to systemd-boot (one report of a failure with the release-candidate installer). |
| Q5 | Does Debian's `grub-install` use the btrfs-header environment block in GRUB 2.14 | **Partly answered 2026-11-12.** Upstream GRUB gained an environment block in the reserved area of the btrfs header (at 256 KiB), used by `load_env`/`save_env` when `env_block` is defined. It is not used when the filesystem sits on LVM, RAID or encryption, so an encrypted btrfs root would still have no writable GRUB environment. Whether Debian 14's `grub-install` sets it up remains to be checked in a Debian 14 VM (experiment E1). |
| Q6 | How many states to keep, and how pruning interacts with kernel removal | Design, then VM lab |
| Q7 | Health check for phase 2: what counts as "booted well" (reachable by SSH, key services up) | Design |
| Q8 | Project name and final location | Operator |
| Q9 | Partition-table ownership between our installer and `debian-install-v2` | Design |
