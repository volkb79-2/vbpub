<!-- backlog-format: 1; prefix: DR- -->
# debian-rollback backlog

Normative behaviour is in [README.md](README.md) and [docs/](docs/). The current status page and work order are [TODO.md](TODO.md).
Closed items are one line each in [archive/](archive/). The format is [docs/BACKLOG-FORMAT.md](../../docs/BACKLOG-FORMAT.md).

## DR-82 — F11: stdlib Python, runtime outside the states, versioned formats

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-20, P-rollback-21, H2, M2, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** F11 is decided by DESIGN-CORE-SPLIT (stdlib Python, runtime outside every state, a `format` file per global format), but the code is still the bash `dr-state`. **Wanted.** Implement the decision in P-rollback-20 and P-rollback-21.

## DR-83 — One state-ID grammar; foreign `@os-*` unmanaged; legacy code removed

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-20, H1

**Problem.** Three name grammars coexist and about 25 legacy and `.conf` code paths serve no supported disk. **Wanted.** `states()` returns only names that pass the canonical grammar, other `@os-*` objects are listed as unmanaged and never touched, and the legacy readers, migrations and tests are deleted.

## DR-85 — Snapshot never fails on menu capacity

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-22, P-rollback-40, H5, F13

**Problem.** A full ESP makes `gen-menu` fail after the snapshot exists, apt aborts, and a retry inside the dedupe window slips through. **Wanted.** Snapshot success means subvolume plus sidecar, the menu degrades (default and running entries first), `prune` is capacity-aware (DESIGN-CORE-SPLIT D20) with no automatic retention (DESIGN-CORE-SPLIT D34, operator 2026-11-24), and a guest scenario exhausts the ESP.

## DR-86 — Bootloader from the ESP marker only

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-20, M1

**Problem.** The backend is chosen from two sources, one of which (`/etc/dr-state.conf` inside each state) rolls back, so a rolled-back state can make every command die, including `set-default`. **Wanted.** The ESP marker is the only source; `BOOTLOADER=`, the conflict logic, their tests and the dr-serial mirror are removed.

## DR-87 — Loader-contract tier: bootctl offline and ESP boot in vm-lab

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-24, P-rollback-32, H3, VML-BL-09

**Problem.** Loader contracts are verified only by a full guest, so a wrong reading of the loader costs a 19-minute build. **Wanted.** An offline test running the real `bootctl --no-variables list --json=short` on a generated tree, plus a small QEMU/OVMF ESP-fixture boot (lane `loader-boot`).

## DR-88 — `check`, `--json`; dr-serial observes only

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-24, P-rollback-26, H4, L4

**Problem.** The guest driver mirrors dr-state's grammars and temp names, and selects recovery entries by position. **Wanted.** `dr-state check` and `list/show --json` own the invariants, scenarios assert observable behaviour and select by title, and the mirror parsers are deleted.

## DR-89 — One APT hook

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, M3, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** Pre-Invoke and Pre-Install-Pkgs both snapshot, and only the dedupe window keeps it to one. **Wanted.** `DPkg::Pre-Install-Pkgs` alone (DESIGN-CORE-SPLIT D18), proven on removal-only runs.

## DR-90 — Metadata `kind` and `pinned`

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, M4

**Problem.** Retention policy is derived from name slugs, and the running state keeps stale install-time metadata. **Wanted.** Sidecar fields `kind=live|checkpoint` and `pinned`; `list` marks the live state as changed since capture.

## DR-91 — Kernel hash cache

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-23, M5

**Problem.** `gen-menu` re-hashes every kernel and initrd on each call, roughly 0.5-1 GB read several times per apt upgrade. **Wanted.** A hash cache keyed by device, inode, size and mtime in the control directory.

## DR-92 — Counter floor from names and sidecars

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, M6

**Problem.** A missing or corrupt counter file blocks all package management. **Wanted.** The floor is the maximum of the counter, the sequences in names and the sequences in sidecars; a repair under the lock with a warning, and the rare repeat documented.

## DR-95 — Matrix runner committed; dr-qemu.sh retired; evidence archived

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-30, P-rollback-33, H6, VML-BL-07, VML-BL-08

**Problem.** The runner scripts and evidence are archived only in controller state outside git; the runner is not committed and evidence is not durably archived. `poc/dr-qemu.sh` also copies vm-lab's argv. **Wanted.** The matrix runner (manifest, admission, fresh overlay per scenario, SHA256SUMS) is committed, `dr-qemu.sh` is deleted, and evidence is archived durably.

## DR-96 — ZFS snapshot checkpoints with clone/promote rollback

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/ZFS-POC-PLAN.md, P-rollback-42, M7

**Problem.** The ZFS storage and ZBM boot backends described in ZFS-POC-PLAN are unbuilt. **Wanted.** Build them in P-rollback-42 on the core: a checkpoint is `zfs snapshot <live>@<ID>`, a boot environment is created by clone and promote on rollback, and the dataset name equals the state ID.

## DR-97 — Release matrix on an ISO-built disk

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-40, L5

**Problem.** The current-source matrix ran on the NoCloud builder disk and the M2 ISO evidence is older. **Wanted.** "Matrix on an ISO-built disk" is the release criterion.

## DR-98 — kernel-install plugin instead of dpkg-divert

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-41

**Problem.** The `dpkg-divert` of `zz-systemd-boot` and the masking of `90-loaderentry` are fragile across systemd updates. **Wanted.** A kernel-install plugin, the supported extension point.

## DR-99 — The seed `user-data` embeds an unlinted disk-wiping script

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-25, L3, scripts/debian-rollback/poc/seed/user-data

**Problem.** `poc/seed/user-data` embeds `dr-build.sh`, which picks the disk by size and runs `install-btrfs.sh --yes`, and nothing lints it (found in the P-rollback-04 review). **Wanted.** Extract the script to a file, or lint the embedded text, in the offline lane.

## DR-100 — The APT hook wrapper must drain fd 3 and warn on a missing runtime

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-22, P-rollback-25, DESIGN-CORE-SPLIT D18, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** The wrapper's early exits (`DR_STATE=none`, missing runtime) leave fd 3 unread, which can break apt with a plan larger than 64 KiB, and a missing or unmounted `/usr/local` skips the snapshot silently (found in the P-rollback-10 review). **Wanted.** Every early exit drains fd 3, the DESIGN-CORE-SPLIT D18 proof uses a plan above 64 KiB, and a missing runtime warns on stderr.

## DR-101 — Installer cases that follow the wrapper

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-22, P-rollback-25, scripts/debian-rollback/poc/test-install-btrfs-bootloader.sh, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** The cases `apt-preinstall-hook`, `apt-plan-version` and `apt-plan-fd` in `test-install-btrfs-bootloader.sh` assert the old hook text (found in the P-rollback-10 review). **Wanted.** Adapt them to the wrapper and the `files/` payload in the same packages, and list them in DESIGN-CORE-SPLIT D29/D31.

## DR-103 — ZFS naming for duplicate checkpoint names, and related leaks

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-42, DESIGN-GUIDE D28, scripts/debian-rollback/docs/ZFS-POC-PLAN.md

**Problem.** Objects that repeat a checkpoint name without a clone or holder relation can pass as managed, and the received `<dup>@<bare>` snapshot of an ENTER duplicate collides with the checkpoint (found in the P-rollback-12 review). **Wanted.**
- Report such objects under a name that fails `parse()`, `running()` included.
- Verify whether the `org.dr-state:parent` source is local or received under `send -p`.
- Fix the `X@X` origin-snapshot leak after the dependent boot environment is deleted.
- Make S3 and U4 list non-OS boot environments the same way.
- Read the command line from the holder's current value.

## DR-104 — ZFS install hardening leftovers

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-43, scripts/debian-rollback/docs/ZFS-POC-PLAN.md

**Problem.** Small ZFS install items are unfiled (found in the P-rollback-12 review). **Wanted.**
- Keep the `zpool.cache` copy and the `umask=0077` option on the ESP fstab line.
- Move the `iso/zfs-config-hook` heredoc unit into files.
- `fetch-zbm-efi.sh` pins no mutable URL and records the key SHA-256.

## DR-105 — Stale lease status and unhomed operating rules

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/WORK-PACKAGES.md, scripts/debian-rollback/README.md

**Problem.** `WORK-PACKAGES.md` line 31 and the OP6 row carry a stale test-host lease status, and the old operating rules (cowtree for shared ISO and VM paths, tests only through registered lanes) have no home in the README (found in the P-rollback-02 review). **Wanted.** Correct both lines and add the rules to the README.

## DR-106 — E2: filesystem comparison on a real host

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/EXPERIMENTS.md

**Problem.** The filesystem comparison (E2) needs a real host, and every reinstall wipes that host's other installs and journals. **Wanted.** Revive it when the core is built on btrfs and ZFS and the operator offers a host for one reinstall per variant.

## DR-107 — Phase 2: automatic fallback after a failed boot

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/DESIGN-GUIDE.md

**Problem.** A state that fails its health check is not the default at the next boot only if phase 2 builds the probation and fallback; today a real host needs a watchdog. **Wanted.** Revive it after DR-93 (boot-counting rule) is implemented and the operator answers design question Q7 (what counts as "booted well").

## DR-108 — Secure Boot on systemd-boot

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/DESIGN-GUIDE.md

**Problem.** The GRUB target is non-Secure-Boot and systemd-boot is the Secure Boot path (DESIGN-GUIDE D27), but no signed boot chain is built or tested. **Wanted.** Revive it when a desktop target needs Secure Boot (DESIGN-GUIDE D10).

## DR-109 — Low-memory install and swap experiment (DESIGN-GUIDE D25)

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/DESIGN-GUIDE.md

**Problem.** DESIGN-GUIDE D25 asks for installed-system boot and recovery at 2048 MiB, then a 1-2 GiB usefulness experiment (no swap, ZRAM, Zswap on a swap partition outside the ZFS pool). **Wanted.** Revive it when the ZFS guest acceptance has reported and the operator wants the memory floor measured.

## DR-110 — Restore-image ISO and licence audit

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/ZFS-POC-PLAN.md

**Problem.** A prebuilt restore-image ISO (ZFS-POC-PLAN) is unbuilt, and shipping one needs a licence audit of its contents. **Wanted.** Revive it when the ZFS install path works end to end and the operator wants a distributable image.

## DR-111 — ZFS with restricted GRUB as a comparison

**State:** parked · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/docs/ZFS-POC-PLAN.md

**Problem.** DESIGN-GUIDE D23 keeps ZFS with a restricted GRUB as a later comparison only. **Wanted.** Revive it only if the ZFSBootMenu guest report leaves a specific question that the comparison answers.

## DR-113 — Python core is tested on 3.14, not the 3.13 minimum

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-20a, DESIGN-CORE-SPLIT.md U1

**Problem.** The `offline` lane runs the unit suite under CPython 3.14.7 (`/opt/tester-venv/bin/python`). The launcher's minimum is 3.13, the Debian 13 `python3`, and no lane runs on it. **Wanted.** A 3.13 run of the unit suite in a lane (for example `/usr/bin/python3` in a Debian 13 image), or the guest acceptance in P-rollback-40 recorded as the 3.13 evidence.

## DR-115 — Small core hardening gaps against bash

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-22, scripts/debian-rollback/files/usr/local/lib/dr-state/dr_state/{lock,metadata}.py

**Problem.** From the P-20a reviews:
- the lock file is opened without `O_NOFOLLOW`;
- a created control or metadata directory gets `0o700` minus the umask, an existing one keeps its mode (bash runs `chmod 700` every time), and a missing `/.top` exits 70 where bash `mkdir -p` would create it;
- a valid sidecar whose `sequence` is 0 or absent contributes nothing to the counter floor, while a corrupt one contributes the sequence in its name.
**Wanted.** `O_NOFOLLOW` on the lock, an explicit `chmod 0o700` after creation, and `max(name sequence, sidecar sequence)` for the floor, each with a unit test.

## DR-117 — btrfs unmanaged-object scans disagree

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** scripts/debian-rollback/files/usr/local/lib/dr-state/dr_state/storage/btrfs.py

**Problem.** `delete`'s dependents scan skips unmanaged `@os-*` objects, while `list()` reports `@os-*` entries that are not directories. This was found in the P-20b fix round 1.
**Wanted.** Scan every unmanaged `@os-*` object that can be a btrfs parent for delete safety, and classify non-directory `@os-*` entries consistently with the storage protocol.

## DR-118 — Initialize identifies a missing counter by message text

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** scripts/debian-rollback/files/usr/local/lib/dr-state/dr_state/core.py

**Problem.** `cmd_initialize` distinguishes a missing counter by comparing `CounterRead.issue` with the exact formatted message. This was found in the P-20b fix round 1.
**Wanted.** Carry a structured missing/absent state from the metadata reader instead of matching human-readable text.

## DR-119 — Initialize pins the live state before D22

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-22

**Problem.** `initialize-state` writes `pinned=True` before D22's `pin`/`unpin` and retention semantics land. This was found in the P-20b fix round 1.
**Wanted.** Check the default pin policy against D22 when metadata `kind` and `pinned` semantics are completed, and change the initialization value if the policy differs.

## DR-121 — Post-Invoke gen-menu under DR_STATE=none

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, DESIGN-CORE-SPLIT.md D18

**Problem.** The D18 apt wrapper guards only Pre-Install. Under `DR_STATE=none`, the Post-Invoke `gen-menu` still runs and selects from the command-line running state or candidates. P-20b restored command-line precedence to avoid publishing the oldest state, but the escape's intended Post-Invoke semantics remain undecided.
**Wanted.** Decide whether Post-Invoke `gen-menu` honours `DR_STATE=none`, then implement and test the chosen wrapper/configuration behavior.

## DR-122 — Replace the temporary --json refusal in P-24

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-24, DESIGN-CORE-SPLIT.md D25

**Problem.** P-20b temporarily refuses a global or verb-option `--json` with `Unsupported` (exit 4) while preserving `--json` as a value after `--note`, `--reason` or `--apt-actions`.
**Wanted.** In P-24, replace the refusal with per-verb support, including `show STATE --json` and `check --json`, using the D25 schemas.

## DR-123 — ZFS set-default mutates storage before boot resolution

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-42, scripts/debian-rollback/files/usr/local/lib/dr-state/dr_state/core.py

**Problem.** `cmd_set_default` calls `storage.activate`, which is a real `zfs clone`/`promote` for the ZFS backend, before `_require_boot` validates the marker and boot backend. A missing or bad marker therefore refuses only after storage has changed.
**Wanted.** Resolve the boot backend before `storage.activate` so every refusal happens before a storage mutation.

## DR-124 — Acceptance scenarios need a verify-only fresh-disk path

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-25, P-rollback-40, poc/dr-serial.py

**Problem.** Under `DR_ACCEPTANCE=1`, the hot-patch helpers refuse as designed, but s2-s3, recovery-kernel, recovery-library, recovery-dependency, s7, s8, window-negative and s9b still call one of them and therefore cannot run on a freshly built disk.
**Wanted.** Give those eight scenarios a verify-only path, such as `verify_current_dr_state`, before the controller's leased acceptance run.

## DR-125 — Payload modes come from the checkout

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-21, P-rollback-25, DESIGN-CORE-SPLIT.md D27, files/

**Problem.** Seed `-rock`, ISO `-map` and installer tar packing preserve source modes. A builder with umask 002 can produce root-owned files and directories with mode 0664 or 0775 in both the runtime tree and the three hooks. The hook mode tests expect 0644 and 0755; the runtime had the same source-mode behavior before P-25.
**Wanted.** Normalize or prove mode pins for the complete payload at the seed, ISO and installer boundaries, including runtime files and directories and all three hooks, while preserving root ownership and existing target directory metadata.

## DR-126 — Tight ESP refresh refusal happens after shrink work

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, P-rollback-40, DESIGN-CORE-SPLIT.md D19

**Problem.** On a tight ESP, an initrd refresh can pass selection-time fit using every owned pair of a kept state as credit, including the pair its entry is about to stop using. Phase 1 cannot remove that pair while the remaining entry still references it, so the menu shrinks and the later copy check refuses, leaving the stale initrd after filesystem mutations. This can happen on every `update-initramfs -u`, not only when the kernel version changes.
**Wanted.** Decide whether the superseded pair of a kept state counts as capacity credit and move the capacity refusal before any shrink mutation. P-40's ESP-exhaustion scenario should cover an initrd refresh as well as a kernel upgrade.

## DR-127 — Foreign selectors refuse strict delete and prune

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-24, DESIGN-CORE-SPLIT.md §3.3

**Problem.** A real foreign default such as `windows.conf` is rejected by strict selector parsing, so `delete` and `prune` exit 3 instead of treating it as an entry that contains no owned state.
**Wanted.** Decide and test foreign-selector behavior in P-24; preserve refusals for malformed selectors that could identify an owned entry unsafely.

## DR-128 — bootctl ignores `--sysroot` for firmware variables

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-24, DESIGN-CORE-SPLIT.md §3.3

**Problem.** `bootctl set-default` and `set-oneshot` ignore `--sysroot` and can mutate the host firmware variables while a command is pointed at a mounted target.
**Wanted.** Refuse firmware-variable mutations for a non-host sysroot or use an interface that demonstrably targets the requested firmware state; add a regression test.

## DR-129 — sdboot.py exceeds its design size target

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-23, P-rollback-24, DESIGN-CORE-SPLIT.md D23

**Problem.** `sdboot.py` is about 1,125 lines against the design target of about 750, which increases review and maintenance cost for the backend.
**Wanted.** Reassess the target during P-23/P-24 and split cohesive filesystem, publication and selector responsibilities where that reduces complexity without moving the boot contract across unclear boundaries.

## DR-130 — Serial runtime refresh removes the old tree before replacement

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-25, poc/dr-serial.py

**Problem.** `install_current_dr_state` removes `/usr/local/lib/dr-state` before extracting the replacement archive, and `_update_dr_state` removes it before copying from the seed. A failed extract or copy leaves the dev guest without the runtime.
**Wanted.** Stage each replacement in a temporary sibling tree, verify its digest, then atomically replace the current tree.

## DR-131 — Installer extract follows an intermediate destination symlink

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-21, poc/install-btrfs.sh

**Problem.** On a target with an intermediate symlink, the installer treats the link as an existing directory, omits it from the archive, and tar writes descendants through it. An absolute link resolves against the Live system root; a relative link escapes only when it points outward. A fresh debootstrap target has no such links, and the prior `cp -a` path behaved the same way, so this is not a regression.
**Wanted.** Refuse an intermediate symlink or resolve every destination beneath the target root without following links; add an offline regression fixture.

## DR-132 — Future runtime hook must join the hot-patch manifest

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-22, DESIGN-CORE-SPLIT.md D27, poc/dr-serial.py

**Problem.** P-22 plans `usr/local/sbin/dr-state-apt-hook`, but the current archive allow-list and guest digest path list do not include it. The archive refuses the file by name; if that refusal is removed without updating the guest list, the whole-`files/` manifest and guest digest differ.
**Wanted.** When adding the hook, extend the accepted archive paths and guest digest list together, then keep the offline equivalence test proving both sides hash the same file set.
