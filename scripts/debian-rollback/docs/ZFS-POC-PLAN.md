# ZFS boot-environment PoC

Status, 2026-11-24: rewritten in P-rollback-12 for the core split (PLAN-rollback B8, design review M7). ZFS is one
storage backend (`dr_state/storage/zfs.py`) and ZFSBootMenu one boot backend (`dr_state/boot/zbm.py`) of the single
`dr-state` in [DESIGN-CORE-SPLIT.md](DESIGN-CORE-SPLIT.md). That design is binding; this plan adds only ZFS facts,
the installer and the acceptance. The parked branch's send/receive design (DESIGN-GUIDE D22) is superseded.

Evidence so far (`RESULTS-ZFSBOOTMENU-POC-2026-11-23.md` and `-24.md`, copied to main by P-rollback-02):
- the pinned ZBM v3.1.0 EFI was authenticated; the ZFS Live ISO was built; the Live ZFS DKMS module loads;
- the last candidate ISO installed a disk: `DR-ZFS-AUTOINSTALL-DONE rc=0` after 4,978 s under TCG at 3072 MiB;
- first-boot acceptance was not run on that disk. An earlier disk showed that ZBM puts no `root=ZFS=` on the kernel
  command line, so the running root comes from `findmnt -n -o SOURCE --target /`.

No ZFS code was reviewed; the gates ran on a dirty tree. Nothing here is product behaviour before P-44 passes.

## Goal and scope

Prove that a user recovers an installed Debian 13 UEFI system from the btrfs acceptance's dependency break by
selecting the pre-change state in ZFSBootMenu. Guests run on the test host under TCG: function, not performance.

## Layers

- **P-42:** `storage/zfs.py` (BEs are datasets, checkpoints are snapshots) and `boot/zbm.py` (ZBM discovers BEs;
  the default is `bootfs`). `findmnt -n -o FSTYPE` picks storage (core D6); the ESP marker `zfsbootmenu` picks boot.
- **P-43:** a separate ZFS ISO from the verified Debian Live ISO, with `/dr/target-filesystem` = `zfs` and its own
  hook and `dr-zfs-autoinstall` entry point (never `dr-autoinstall` or `install-btrfs.sh`; `dr-plan` only picks the
  disk). It installs `files/` (core D27) into `/usr/local`, a dataset outside every BE (core D2).
  `--bootloader` stays closed to `grub|systemd-boot`; the `--bootloader zfs` refusal must still pass.
- **P-44:** guest acceptance on a disk built by that ISO.

## ZFSBootMenu owns boot mechanics

- ZBM lists a dataset as a BE when it has `mountpoint=/`, is not hidden by `org.zfsbootmenu:active=off`, and holds a
  kernel and initramfs pair in its own `/boot`. `/boot` is inside each BE.
- Every BE has a **local** `canmount=noauto`. The property is not inherited, and clones do not copy local properties.
  Without it, several roots compete to mount at `/`. `activate` therefore sets it on every clone (core §3.5).
- Every BE has a local `org.zfsbootmenu:commandline` with the console options and no `root=`. ZBM supplies the root.
  `activate` copies the source BE's local value, so a change to a parent never rewrites older BEs.
- Nothing sets `org.zfsbootmenu:active`. The branch set it only to hide its staging datasets, which no longer exist.
- `bootfs` is the persistent default. A menu selection is temporary; `set-default` makes it persistent.
- There is no one-shot: `supports_oneshot = False`, and `boot-once` exits 4 (`Unsupported`).
- ZBM v3.1.0 counts down 10 s; the PoC sets neither `zbm.timeout=0` nor `zbm.skip` (EFI image options).
- Checkpoints appear in ZBM's snapshot menu, not as entries. dr-state writes no menu files and checks BEs itself (C12).

## ZFS state identity and apt flow

**Names.** One grammar for both filesystems (core D7). The dataset leaf and the snapshot name are both
`StateId.bare`, so name = ID. No sidecar maps an ID to a dataset, and no GUID or token carries authority.
- Initial BE: `rpool/ROOT/os-000001-20261124-101500Z-install`.
- Checkpoint of it: `rpool/ROOT/os-000001-20261124-101500Z-install@os-000002-20261124-120100Z-apt`.
- A dataset under `rpool/ROOT` whose leaf fails `parse()` is `unmanaged` (core D9): listed, never deleted or pruned.
  When it is running, `snapshot` and the apt hook exit 3.

**Kind.** Storage tells the kind: a dataset is `live`, a snapshot is `checkpoint` (core §3.1, D22). The sidecar, the
counter and the format file live in the control directory, a dataset outside every BE (see Pool layout).

**Checkpoint = `zfs snapshot`.** The apt path is the core's, unchanged:
1. apt calls `DPkg::Pre-Install-Pkgs` once per commit through the `dr-state-apt-hook` wrapper (core D18).
2. `dr-state apt-pre-install-pkgs` takes the writer lock (`/run/dr-state.lock`, 60 s, core §4).
3. It allocates the next sequence above the counter floor (core D13, D14).
4. `storage.snapshot(running, target)` runs
   `zfs snapshot -o org.dr-state:parent=<running name> <running dataset>@<target.bare>`. It is atomic and O(1) and
   copies no data.
5. The sidecar is written. If that fails, the snapshot is destroyed and the exit is 1 (core D16, S1).
6. Publication runs best-effort. For ZBM it only sets `bootfs` when it differs, so after a snapshot it changes
   nothing.

apt then changes the running BE. The checkpoint is the pre-change state. Exit codes and `DR_STATE=none` are as in core
D18. The dedupe window (core D21) keys on `org.dr-state:parent` equal to the running state; this property survives
`zfs promote`.

**A BE is created only by `set-default`.** Rollback is `set-default` on a checkpoint. The core calls
`storage.activate(X)`, then `boot.publish(..., default=X)` (core §3.5):
- `zfs clone -o canmount=noauto -o mountpoint=/ -o org.zfsbootmenu:commandline=<source's local value>
  <holder>@<bare> rpool/ROOT/<bare>`;
- `zfs promote rpool/ROOT/<bare>`;
- `zpool set bootfs=rpool/ROOT/<bare> rpool`.

This is what zectl and bectl do. Promote moves `@<bare>` and all older snapshots of the holder to the new BE, and
the old BE becomes a clone of `rpool/ROOT/<bare>@<bare>`. The holder of a checkpoint therefore changes over time.
Storage always resolves it from a fresh `list()`. `activate` is idempotent (S5): on an existing BE it only sets
missing local properties.

**Delete** follows core §3.5: a checkpoint with clones, or a BE holding other checkpoints or children, is refused,
naming them; a BE's own `@<leaf>` goes first when it has no clones; no `-r`; running and `bootfs` BEs are protected.

**Retention** comes from the core: core D20 (unpinned count at most N, default 10; pool `free + freeing` at least
max(2 GiB, 5 %), re-read after each destroy), core D34 (`prune` is manual) and core D22 (the install state is
pinned). D19 capacity is `None` for ZBM. The branch's "install plus newest 2" (DESIGN-GUIDE D24) is superseded.

**Space.** A checkpoint or clone costs nothing when made; a checkpoint's `used` grows as the live BE diverges. The
core has no space check before a snapshot; a full pool fails dpkg itself with ENOSPC.

## ZFS state-manager CLI

There is no `dr-zfs-state`. ZFS uses the verbs of the one `dr-state`, with the core's exit codes and JSON (core §4,
D25). Only the ZFS behaviour is listed here.

| Verb | ZFS + ZBM behaviour |
|---|---|
| `list [--long] [--json]` | `storage: zfs`, `boot: zfsbootmenu`. BEs as `live`, snapshots as `checkpoint`; a snapshot whose name equals its dataset's leaf is not a separate state. `default` = `bootfs`; `oneshot` = `null`. Unmanaged datasets listed. |
| `snapshot`, `apt-pre-install-pkgs` | `zfs snapshot` of the running BE (above); a manual snapshot bypasses the window. |
| `set-default STATE` | `activate` (clone + promote for a checkpoint), then `bootfs`. |
| `boot-once STATE` | Exit 4: ZBM has no one-shot. |
| `gen-menu` | Writes no files; sets `bootfs` when it differs. |
| `delete`, `prune`, `pin` | Core §3.5, D20, D34, D22 as above. |
| `check [--json]` | C3: `bootfs` names a managed BE. C6: ESP marker. C12: BE properties. C9 exempts checkpoints. |
| `initialize-state` | Run by the installer for the initial BE (`kind=live`, `pinned=1`). |

C12 checks each managed BE: local `canmount=noauto`, `mountpoint=/`, a local `org.zfsbootmenu:commandline` without
`root=`, `org.zfsbootmenu:active` not `off`, a kernel pair in `/boot`, and no local `quota`, `reservation`,
`refquota` or `refreservation`. These checks are salvaged from `dr-zfs-state` (P-42).

## Open ZFS details

The core design leaves these open. Each is decided in the named package, without changing core section 3.

| Item | Rule now | Decided in |
|---|---|---|
| U4: the dataset name ZBM gives a BE that the operator clones from its snapshot menu; renaming a mounted root | Core §3.5: a non-`os-*` name is unmanaged; `set-default <ID>` inside it renames it to `rpool/ROOT/<origin bare>`, then proceeds. The acceptance enters the bare ID as the name, so no rename is needed there. | Rule tested at the fake in P-42; ZBM behaviour measured in a guest in P-44 |
| U5: `.zfs/snapshot/<bare>/boot` access on the running BE | Read-only temporary mount as fallback (core §3.5) | P-42 |
| Control directory path | A dataset `rpool/DR-META` outside every BE; the branch mounted it at `/var/lib/debian-rollback` | P-42 (`paths.py`), P-43 (installer) |
| Verb that runs `Boot.install()` for ZBM (marker, format file, EFI copies) | Same pattern as `install-grub` / `install-systemd-boot` | P-42 |
| `prune` when a victim is refused (it has clones) | Skip it with the S2 reason in the output and continue with the next victim | P-42 |
| Checkpoint space growth and pool budget | Measure, then set the fixture size | P-44 |

## Installer and boot flow

1. **Build** a separate ZFS target ISO from the verified Debian Live base. The filesystem choice is fixed in the
   artifact. The ISO stages `files/` (core D27), not a separate state manager.
2. **Live preflight.** Boot Live through Debian Live's GRUB. Enable `contrib`, install `linux-headers-$(uname -r)`,
   `dkms`, `zfs-dkms` and `zfsutils-linux`, and require the module to build and load for the running Live kernel.
   Missing headers, a DKMS failure or a load failure is a refusal before any disk write.
3. **Disk guard.** Probe the explicit `TARGET_DISK` before the planner, or the planner's choice before confirmation.
   A ZFS signature on the disk or a partition refuses unless `REINSTALL=yes`; a probe that cannot prove the disk
   clear refuses. Repeat right before the first destructive write, so a still-attached ISO cannot wipe its own install.
4. **Pool and datasets** as in Pool layout. Debootstrap the target without a kernel. Its `--include` carries
   `python3` (full stdlib, core D2), not `python3-minimal`.
5. **Target packages.** Refresh target APT, then install `linux-image-amd64`, matching `linux-headers-amd64`, `dkms`,
   `zfs-dkms`, `zfsutils-linux` and `zfs-initramfs` together. Create `/etc/dkms` and set `REMAKE_INITRD=yes` in
   `/etc/dkms/zfs.conf`. Rebuild the initramfs after the module. Create a unique non-zero `/etc/hostid` with
   `zgenhostid`. Record the package versions.
6. **Mountpoint check.** With `-R <target>`, ZFS reports the root mountpoint as the altroot. The installer checks
   that the altroot equals the target path and that the initial BE's local mountpoint has that effective value.
7. **Runtime.** Copy `files/` into the target (core D27); the runtime lands in the `/usr/local` dataset. Run
   `initialize-state` for the initial BE inside the target, as `install-btrfs.sh` does. Set
   `bootfs` to the initial BE and verify it.
8. **ESP.** Fetch and verify the pinned ZBM EFI (ZBM artifact below). Install it at `EFI/ZBM/`, a backup copy beside
   it, and the same file at `EFI/BOOT/BOOTX64.EFI`, the UEFI fallback path. The disk must boot from fresh firmware
   variables without an NVRAM entry; `efibootmgr` may add one as a convenience. Write the ESP marker.
9. **Network and credentials.** DHCP on the Live default-route interface, not `eth0`. The password goes through a
   private temporary file, never an argument; the config and config-bearing ISOs are mode 0600.

**ZBM artifact.** Pin `zfsbootmenu-recovery-x86_64-v3.1.0-linux6.12.EFI`, SHA-256
`9910458dfe7fc2895214922e22b6ae2513cd0f5a43d4d3c5526eee26e69a2198`, never a mutable latest URL. Verify the release's
`sha256.sig` with `signify` and the pinned `zfsbootmenu.pub`. Verify the key's inline-signed OpenPGP message
`zfsbootmenu.pub.gpg` with GPG, signer `971D2693A47CA0E203E1C5DA312485BE75E3D7AC`, and record the key's SHA-256.
The EFI file stays unmodified.

## Pool layout and sizing

```text
GPT  p1 ESP 512 MiB vfat     p2 pool (rest of the disk)       no swap partition, file or zvol
rpool                          ashift=12, compatibility=openzfs-2.3-linux, mountpoint=none, canmount=off
rpool/ROOT                     mountpoint=none, canmount=off
rpool/ROOT/<bare>              one per BE: local mountpoint=/, canmount=noauto, org.zfsbootmenu:commandline
rpool/DATA                     mountpoint=none, canmount=off
rpool/DATA/{home,root,srv,www,log,docker,containers,postgresql,mysql}   DESIGN-GUIDE D18 paths
rpool/DATA/usr-local           /usr/local: the shared dr-state runtime (core D2)
rpool/DR-META                  control directory (path open, P-42)
```

`/etc`, `/usr`, `/var/lib/dpkg`, `/opt` and `/boot` stay inside each BE. The branch's `rpool/DR-STAGING`,
`@dr-pending-*` snapshots and `active=on/off` values are gone. ROLLBACK-RECOVERY.md gets ZFS cases after P-44.

Do not run `zpool upgrade`. A pool feature upgrade first needs a ZBM artifact verified against it. Record the pool
features, the Debian OpenZFS module version and the ZBM OpenZFS version.

**ESP.** The pinned EFI is 85,742,592 bytes by the release metadata. Three copies must leave at least 128 MiB free
on the formatted 512 MiB ESP. Measure the real file and the real free space at build and install time.

**Sizing (DESIGN-GUIDE D24/D25, landed on main by P-rollback-02).**
- The guest disk is 64 GiB. That is a fixture size, not a supported minimum.
- The TCG Live install runs at 3072 MiB. That is a run setting, not a measured minimum.
- Installed-system boot and recovery run at 2048 MiB. The ARC is not capped. Record RAM, disk and ARC policy.
- Pool budget: `R + C + D + M`. R is the measured BE `refer`, C the measured `used` of the retained checkpoints after
  representative updates, D the persistent-data budget, M the metadata and update reserve. The branch's
  `(keep + 2) * R` term came from full copies and is dropped.

## Acceptance

**P-44 sequence.** One disk built by the ZFS ISO from the candidate head (B15). Assertions use only observable
behaviour (B12): `dr-state list --json`, `check --json`, `/proc/cmdline`, `findmnt`, boot_id and `dpkg`.

1. **Install.** `DR-ZFS-AUTOINSTALL-DONE rc=0` at 3072 MiB.
2. **ZBM boot.** 2048 MiB, fresh firmware variables, no ISO attached. Firmware starts `EFI/BOOT/BOOTX64.EFI`, ZBM shows
   its countdown, and Debian reaches multi-user. `list --json`: running = default = `@os-000001-…-install`. `check`
   exits 0.
3. **Dependency break.** An apt run breaks the real app/library dependency. The hook made exactly one checkpoint,
   `…-install@os-000002-…-apt`, whose `apt_actions` names the change. The app fails.
4. **Select the pre-change state.** Reboot, interrupt the countdown, open the snapshot menu of the running BE, and
   clone and promote `@os-000002-…-apt` with the bare ID as the new name. Boot it. The damaged package state is
   absent; witnesses written to `/home` and `/srv` after the checkpoint are still there. Record the default name
   ZBM offered and the properties it set (U4).
5. **Set-default.** In the recovered BE, `dr-state set-default @os-000002-…-apt`. `bootfs` names it; C12 passes;
   `check` exits 0.
6. **Plain reboot.** No key press. A new boot_id, and `findmnt` shows `rpool/ROOT/os-000002-…-apt`.
7. **Delete the failed BE.** `dr-state delete @os-000001-…-install --yes` succeeds. The recovered BE, its data and
   unrelated datasets remain.
8. **Sequence unchanged.** The next `snapshot` takes sequence 3. No number is reused.

**Wider matrix (after P-44, not its gate).**

- Installer re-run: a second boot of the ISO refuses before any write unless `REINSTALL=yes`.
- Boot-manager integrity: signify and GPG pass; three ESP copies match the pin; 128 MiB stays free.
- Countdown: 10 s; interrupting it allows choosing an older BE.
- `set-default` on a checkpoint from the broken state: clone, promote, `bootfs`; a plain reboot boots it.
- Repeated dpkg calls: one checkpoint per window; later calls append plan lines (core D21).
- Follow-up apt in the recovered BE makes the next checkpoint.
- ZBM-named clone: unmanaged; `set-default` inside it renames it (U4); `snapshot` refuses until then.
- Delete and prune: a checkpoint with clones is refused, naming them; `prune --dry-run` matches core D20.
- Kernel coverage: old and new kernels boot, stable and backports where the ZFS module supports them.
- Feature ordering recorded; `swapon --show` empty.

Lock behaviour, counter repair, snapshot atomicity and delete refusals are tested offline in P-42 at a fake
`zfs`/`zpool` model that asserts the resulting datasets and snapshots, never the call tuples.

## Test and evidence path

- Registered lanes on the exact head are the only merge evidence; then an Opus review. `offline` runs on the shared
  tester-unified image. The ZFS lanes use a project layer (`signify-openbsd`, `gnupg`, `tesseract-ocr`) built by a
  documented step (P-43).
- Guests run through the vm-lab runner (P-33), one at a time. Evidence is archived durably, never only on the test
  host (B13). The report records hashes, ZBM verification, ESP free space, versions, RAM, disk and checkpoint `used`.

**Risks.**
- **R5.** The branch pins every lane, `offline` included, to the local tag `tester-unified:debian-rollback-zfs-tools`,
  which exists only where someone built it. P-43 moves the lanes to the shared image; the project layer serves only
  the ZFS lanes.
- **U4.** If ZBM's clone lacks `canmount=noauto` or the command line, `activate` in step 5 must set them.
- **Pool full.** Checkpoints pin old blocks and prune is manual (D34). P-44 measures the growth.
- **TCG time.** One install takes about 83 minutes; later scenarios reuse the installed disk read-only.

## Salvaged vs dropped branch files

The 30 paths of `330bdb835`. "Salvage" means the file lands from a fresh branch off main after review and a lane
pass; nothing lands unreviewed. Counts: 14 salvage, 13 partial, 3 drop.

| Path (`scripts/debian-rollback/` unless rooted) | Verdict | Pkg | Reason |
|---|---|---|---|
| `README.md` | partial | P-02 | Status lines only (M11). |
| `TODO.md` | partial | P-02 | Single status page. |
| `docs/DESIGN-GUIDE.md` | partial | P-02 | D24/D25 kept (prune default superseded); D22 superseded. |
| `docs/INSTALLATION-ARCHITECTURE.md` | partial | P-02 | ZFS lines that match this plan. |
| `docs/RESEARCH-2026-11.md` | partial | P-02 | ZBM research, altroot note; not "no snapshots". |
| `docs/RESULTS-ZFSBOOTMENU-POC-2026-11-23.md` | salvage | P-02 | Evidence, verbatim. |
| `docs/RESULTS-ZFSBOOTMENU-POC-2026-11-24.md` | salvage | P-02 | Evidence, verbatim, plus the install outcome. |
| `docs/ZFS-POC-PLAN.md` | partial | P-12 | Replaced by this rewrite. |
| `iso/build-zfs-iso.sh` | partial | P-43 | Stages `files/`, not `dr-zfs-state`. |
| `iso/dr-zfs-autoinstall` | salvage | P-43 | Preflight, disk guard, credentials. |
| `iso/fetch-zbm-efi.sh` | salvage | P-43 | Pinned, verified ZBM fetch. |
| `iso/test-build-zfs-iso.sh` | salvage | P-43 | Builder test. |
| `iso/test-zfs-live-dependencies.sh` | partial | P-43 | Minus its `dr-zfs-state` assertion. |
| `iso/zfs-config-hook` | salvage | P-43 | Live hook. |
| `poc/dr-serial.py` | partial | P-44 | ZBM OCR and menu selection (via P-31); `zfs_state_*` mirrors dropped (B12). |
| `poc/dr-zfs-state` | drop | P-42 | Superseded design. Helpers (lock, atomic write, apt plan, slug) may seed P-20a; BE checks seed C12. |
| `poc/install-zfs.sh` | partial | P-43 | Layout, kernel order, DKMS, hostid, altroot kept; staging, `active=on`, old hook out; `python3`. |
| `poc/test-dr-serial.py` | partial | P-44 | OCR tests; mirror tests dropped. |
| `poc/test-dr-zfs-state.py` | drop | P-42 | Dropped design; P-42 uses a fake model. |
| `poc/test-zfs-kernel-install-order.sh` | salvage | P-43 | Kernel order. |
| `poc/test-zfs-network-config.sh` | salvage | P-43 | Network test. |
| `poc/test-zfs-root-mountpoint.sh` | salvage | P-43 | Mountpoint test. |
| `poc/zfs-network-config.sh` | salvage | P-43 | DHCP interface. |
| `poc/zfs-root-mountpoint.sh` | salvage | P-43 | Altroot check. |
| `run-gate.toml` | partial | P-43 | ZBM/ISO lanes kept; shared image (R5); acceptance lane dropped. |
| `tester/Dockerfile` | partial | P-43 | ZFS-lane layer only. |
| `tester/zfs-recovery-acceptance.sh` | drop | P-44 | Drives the dropped manager. |
| `/vm-lab/README.md` | salvage | P-03 | Inspector docs. |
| `/vm-lab/src/vm_lab/core.py` | salvage | P-03 | Inspector without host PID ns. |
| `/vm-lab/tests/test_cgroup_inspector.py` | salvage | P-03 | Refusal tests. |

## Non-goals

Automatic fallback; a ZBM one-shot; Secure Boot; encryption; converting btrfs or `@os-0001` disks; performance claims
from QEMU; physical hardware and power loss; swap on the target; changing the test host's own filesystems; ZFS with
GRUB.

## Later experiments (outside P-44)

- **Low memory.** Boot a copy of the accepted disk at 1024, 1536 and 2048 MiB with no swap, ZRAM, and Zswap on a
  swap partition outside the pool. Record memory, swap, PSI and whether update, checkpoint and recovery work. The
  no-swap default stays until this reports.
- **Prebuilt restore image.** Whole-disk image versus a ZFS stream into a fresh pool. A clone needs new machine id,
  SSH host keys, hostid and pool GUID. Publishing needs a per-component licence inventory first.

## References

ZFSBootMenu v3.1 [Debian UEFI guide](https://docs.zfsbootmenu.org/en/v3.1.x/guides/debian/uefi.html) and
[snapshot management](https://docs.zfsbootmenu.org/en/v3.1.x/online/snapshot-management.html); OpenZFS
[zfsprops(7)](https://openzfs.github.io/openzfs-docs/man/master/7/zfsprops.7.html) and
[zpoolprops(7)](https://openzfs.github.io/openzfs-docs/man/master/7/zpoolprops.7.html).
