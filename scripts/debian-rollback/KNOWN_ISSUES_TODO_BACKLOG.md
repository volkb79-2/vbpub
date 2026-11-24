<!-- backlog-format: 1; prefix: DR- -->
# debian-rollback backlog

Normative behaviour is in [README.md](README.md) and [docs/](docs/). The current status page and work order are [TODO.md](TODO.md).
Closed items are one line each in [archive/](archive/). The format is [docs/BACKLOG-FORMAT.md](../../docs/BACKLOG-FORMAT.md).
Parked, no ID: E2; phase 2 fallback; Secure Boot on systemd-boot; D25 RAM experiment; restore-image ISO and licence audit; ZFS restricted-GRUB comparison.

## DR-82 — F11: stdlib Python, runtime outside the states, versioned formats

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-10, H2, M2, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** The tool form (F11) was undecided, and `dr-state` on `/usr/local` manages global formats (ESP layout, counter) that carry no version. **Wanted.** Implement the decision in DESIGN-CORE-SPLIT (stdlib Python, runtime outside every state, `format` files per global format) in P-rollback-20/21.

## DR-83 — One state-ID grammar; foreign `@os-*` unmanaged; legacy code removed

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-20, H1

**Problem.** Three name grammars coexist and about 25 legacy and `.conf` code paths serve no supported disk. **Wanted.** `states()` returns only names that pass the canonical grammar, other `@os-*` objects are listed as unmanaged and never touched, and the legacy readers, migrations and tests are deleted.

## DR-84 — Core/backends split before any ZFS code

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-20, P-rollback-21, H2, L1, L2

**Problem.** Storage and boot logic are interleaved in one bash file through seven backend dispatches and about 20 test hooks, so ZFS would copy the core. **Wanted.** A core with a storage interface (btrfs, zfs) and a boot interface (grub, sdboot, zbm), one CLI, test seams removed, FAT accounting reduced to "sum plus margin".

## DR-85 — Snapshot never fails on menu capacity

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-22, P-rollback-40, H5, F13

**Problem.** A full ESP makes `gen-menu` fail after the snapshot exists, apt aborts, and a retry inside the dedupe window slips through. **Wanted.** Snapshot success means subvolume plus sidecar, the menu degrades (default and running entries first), retention is capacity-aware, and a guest scenario exhausts the ESP.

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

**Problem.** Pre-Invoke and Pre-Install-Pkgs both snapshot, and only the dedupe window keeps it to one. **Wanted.** `DPkg::Pre-Install-Pkgs` alone (D18), proven on removal-only runs.

## DR-90 — Metadata `kind` and `pinned`

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, M4

**Problem.** Retention policy is derived from name slugs, and the running state keeps stale install-time metadata. **Wanted.** Sidecar fields `kind=live|checkpoint` and `pinned`; `list` marks the live state as changed since capture.

## DR-91 — Kernel hash cache

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-23, M5

**Problem.** `gen-menu` re-hashes every kernel and initrd on each call, up to 1 GB of reads per apt upgrade. **Wanted.** A hash cache keyed by device, inode, size and mtime in the control directory.

## DR-92 — Counter floor from names and sidecars

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-22, M6

**Problem.** A missing or corrupt counter file blocks all package management. **Wanted.** The floor is the maximum of the counter, the sequences in names and the sequences in sidecars; a repair under the lock with a warning, and the rare repeat documented.

## DR-93 — Boot-counting suffix rule

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-10, M8, scripts/debian-rollback/docs/DESIGN-CORE-SPLIT.md

**Problem.** systemd-boot boot assessment renames entries to `os-X+3.conf`, which the current ownership rules would treat as foreign. **Wanted.** Implement the D12 rule (ownership by marker line, counter suffix stripped when mapping to an ID) in the core; phase 2 itself stays unbuilt.

## DR-94 — Payload as files; hot-patch dev-only; snapper removed

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-25, M10, L6

**Problem.** The APT and kernel hooks are heredocs in the installer, guests are hot-patched, and snapper is installed but unused. **Wanted.** The payload lives in `files/`, acceptance runs only on freshly built disks (hot-patching labelled dev-only), and snapper leaves `--include`.

## DR-95 — Matrix runner committed; dr-qemu.sh retired; evidence archived

**State:** open · **Severity:** high · **Owner:** rollback · **Links:** P-rollback-30, P-rollback-33, H6, VML-BL-07, VML-BL-08

**Problem.** The runner scripts and evidence bundles live only on a test host that may be reinstalled, and `poc/dr-qemu.sh` copies vm-lab's argv. **Wanted.** The matrix runner (manifest, admission, fresh overlay per scenario, SHA256SUMS) is committed, `dr-qemu.sh` is deleted, and evidence is archived durably.

## DR-96 — ZFS snapshot checkpoints with clone/promote rollback

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-12, P-rollback-42, M7

**Problem.** The planned ZFS checkpoint copies the whole boot environment by send/receive, which needs a registry, tokens and orphan verbs. **Wanted.** A checkpoint is `zfs snapshot <live>@<ID>`, a boot environment is created by clone and promote on rollback, and the dataset name equals the state ID.

## DR-97 — Release matrix on an ISO-built disk

**State:** open · **Severity:** low · **Owner:** rollback · **Links:** P-rollback-40, L5

**Problem.** The current-source matrix ran on the NoCloud builder disk and the M2 ISO evidence is older. **Wanted.** "Matrix on an ISO-built disk" is the release criterion.

## DR-98 — kernel-install plugin instead of dpkg-divert

**State:** open · **Severity:** medium · **Owner:** rollback · **Links:** P-rollback-41

**Problem.** The `dpkg-divert` of `zz-systemd-boot` and the masking of `90-loaderentry` are fragile across systemd updates. **Wanted.** A kernel-install plugin, the supported extension point.
