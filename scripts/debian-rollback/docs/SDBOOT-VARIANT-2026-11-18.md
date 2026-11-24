# systemd-boot variant: implementation and guest evidence (2026-11-18 onward)

This note records the systemd-boot implementation and the discovered loader
contracts. Current status and work order are in [TODO.md](../TODO.md). Exact
hashes, counts, harness corrections, and retained logs belong to the dated
[M2 report](RESULTS-SDBOOT-M2-2026-11-22.md) and
[NoCloud builder report](RESULTS-SDBOOT-NOCLOUD-2026-11-23.md).

> **Loader verification rule (D32):** Loader and firmware contracts are
> verified against the real loader, using the `bootctl` parser or a loader
> boot, and never only against our own reading. Full guests retain the apt,
> kernel, recovery, and data scenarios.

## Entry points

The installer selects the backend explicitly:

```sh
install-btrfs.sh --device /dev/vdX --yes --bootloader systemd-boot
```

The default remains `--bootloader grub`. With `systemd-boot`, debootstrap receives `systemd-boot` and
`systemd-boot-efi` instead of `grub-efi-amd64`, and the installer runs:

```sh
bootctl install --esp-path=/boot/efi --no-variables
```

`--no-variables` keeps installation independent of writable UEFI variables. The installer writes the same backend
selection in both supported places:

```text
/etc/dr-state.conf              BOOTLOADER=systemd-boot
/boot/efi/EFI/dr/bootloader     systemd-boot
```

`dr-state` also accepts either source alone. Absence of both means `grub`, preserving existing installs. Presence is
tracked separately from the value: an existing zero-byte marker, `BOOTLOADER=` in `/etc/dr-state.conf`, or any other
empty selected value is refused with a clear error instead of falling back to GRUB. If both sources exist and
disagree, or either contains an unknown value, the command also fails rather than guessing.

The user-visible state commands are:

```text
dr-state gen-menu [--default STATE]
dr-state snapshot [--reason WORD]
dr-state list
dr-state set-default STATE
dr-state boot-once STATE
dr-state delete STATE --dry-run
dr-state delete STATE --yes
dr-state prune [--keep N] [--dry-run]
```

The apt and kernel hooks continue to call `dr-state gen-menu`; they are not backend-specific.

## Design tradeoff

GRUB remains the smaller implementation because it reads kernels inside Btrfs states directly. The systemd-boot
PoC keeps a separate ESP kernel pool and BLS entry per state, so it must still coordinate snapshot creation/deletion,
default and one-shot selectors, kernel-pair identity, ESP capacity, and foreign-entry protection. A
`kernel-install` plugin could own package-driven kernel staging and entry writing, reducing the current hook
diversions, stock BLS-writer mask, and per-state copy glue. It would not own snapshot ordering, default and one-shot
selection, retention, or cleanup of kernel pairs no state references; those remain `dr-state` responsibilities.
This PoC keeps the current explicit path to test systemd-boot as a build-time variant. Revisit a plugin if the
package-to-ESP integration becomes the maintenance burden; it would simplify that boundary without replacing the
state manager.

## ESP layout

For a systemd-boot install, the relevant ESP files are:

```text
/boot/efi/loader/loader.conf
/boot/efi/loader/entries/os-<state-suffix>.conf
/boot/efi/EFI/dr/bootloader
/boot/efi/EFI/dr/kernels/<kver>-<kernel-initrd-pair-sha256>/vmlinuz
/boot/efi/EFI/dr/kernels/<kver>-<kernel-initrd-pair-sha256>/initrd.img
```

BLS paths are relative to the ESP root, so an entry names
`/EFI/dr/kernels/<id>/vmlinuz` and `/EFI/dr/kernels/<id>/initrd.img`, not a path under the host mount point
`/boot/efi`.

The kernel id is `<kver>-<lowercase SHA-256 of the concatenated full SHA-256 values of vmlinuz and initrd.img>`.
Both source files are hashed, and the copied pair is checked against those hashes before publication. States with the
same kernel version and byte-identical kernel/initrd pair share one copied pair; a different kernel binary gets a
different ESP directory even when its initrd is unchanged. The implementation also recognizes the old 12-hex
initrd-only IDs so existing ESP directories and entries remain protected until unreferenced. A state selects the
newest kernel version that has both a non-empty `vmlinuz-<kver>` and `initrd.img-<kver>`, using the same ordering
rule as the GRUB backend.

## Entry-id mapping

The BLS entry id is the file name without `.conf`. The implementation does not assume that systemd-boot accepts
`@` in an entry id: `@os-...` maps to `os-...` by stripping the single leading `@`. The inverse adds it back.

Accepted state names are `@os-` followed by an alphanumeric first character and then only ASCII alphanumerics,
underscore, period, and hyphen. Entry ids are capped at 240 bytes (240 is accepted and writes its entry, 241 is refused before any kernel is copied; entry temp files use a short fixed name, so the id length does not matter for them). `latest_kernel` only returns a version whose `vmlinuz` AND initrd are both non-empty, so a newer incomplete pair is skipped for the older complete one. Names with an unsafe character, an embedded `@`,
`..`, or a trailing period are rejected before any menu file or copied kernel is changed. This is conservative and
reversible. The implementation strips `@` before writing BLS entry names, so the guest loader sees only the
reversible `os-...conf` ids.

State names ending in `.conf` are invalid on the systemd-boot backend: snapshot creation sanitizes that suffix and
`gen-menu` skips such existing state names when building the menu. This avoids ambiguity between an internal state
such as `@os-0001.conf` and the entry file name `os-0001.conf`; the correctly namespaced entry would be
`os-0001.conf.conf`. GRUB keeps accepting its historical `.conf` state names. Both backends use the shared readable
slug for new snapshot names; the verbatim reason is retained in metadata, and existing legacy names are unchanged.

`/loader/entries` is on case-insensitive vfat, so all desired entry ids are additionally required to be
case-insensitively unique. For example, `@os-lower` and `@os-LOWER` map to distinct strings in software but the same
FAT file; such a menu is refused before any write. Every existing name in `/loader/entries` is compared case-folded
as a complete basename, including the extension. A foreign `OS-0001.CONF` therefore collides with the intended
`os-0001.conf` and is refused rather than overwritten. An exact target must carry the dr-state entry marker; a
same-named foreign file is also refused. This keeps the mapping reversible without silent aliasing.

One entry is written per state:

```text
# generated by dr-state sdboot; do not edit
title Debian @os-0001 (kernel 6.12.117+deb13-amd64)
linux /EFI/dr/kernels/6.12.117+deb13-amd64-<pair-sha256>/vmlinuz
initrd /EFI/dr/kernels/6.12.117+deb13-amd64-<pair-sha256>/initrd.img
options root=UUID=<btrfs-uuid> rootflags=subvol=@os-0001 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-0001
```

Only entries carrying this marker are removed when their state disappears. Foreign BLS entries are left alone and
still protect kernel directories they reference. The prune reference scan follows VFAT case-folding for the entry
extension, ESP path components, kernel ID, and `vmlinuz`/`initrd.img` names; a foreign uppercase `.CONF` entry protects
its referenced pair too.

## Guest finding: entry ids carry `.conf`

On the real Debian 13 guest (systemd-boot 257.13, OVMF 2.70), `bootctl list` reports Type #1 entry ids with the
`.conf` suffix: `os-0001.conf` and `os-pre-20261118224917-622-apt.conf`. The initial writer treated the suffix-less
mapping (`os-pre-20261118224917-622-apt`) as the selector string. systemd-boot did not match that string to an entry.

The observed consequences were:

- `loader.conf` contained `default os-pre-20261118224917-622-apt`; after `set-default` and reboot the guest still
  booted `@os-0001`, proving the suffix-less default was ignored.
- `LoaderEntryOneShot` contained the suffix-less snapshot id; after reboot the guest still booted `@os-0001`, proving
  the suffix-less one-shot was ignored.
- `bootctl set-oneshot os-pre-20261118224917-622-apt.conf` with the suffix booted
  `rootflags=subvol=@os-pre-20261118224917-622-apt`, proving the file-name form works. systemd-boot cleared the
  one-shot EFI variable when it consumed it.

The corrected write/compare form is always `<entry-id>.conf`:

```text
loader.conf: default <entry-id>.conf
bootctl set-oneshot <entry-id>.conf
LoaderEntryOneShot: <entry-id>.conf
```

Readers that must tolerate files made before this fix accept both `<entry-id>` and `<entry-id>.conf`, stripping the
suffix when mapping back to a state. Guest tooling treats a suffix-less current `loader.conf` default as a failure
with a message naming this finding.

Real-guest provenance:

- `sd-g2` established basic boot and ESP checks before the stricter `.conf`
  assertions existed.
- `sd-g3` showed that `boot-once` and `set-default` ignored suffix-less
  selectors.
- The diagnostic guest proved that a suffixed `bootctl set-oneshot` boots the intended snapshot and that systemd-boot
  clears the one-shot variable when consumed.
- A later historical guest established rollback, `/etc` restoration,
  persistent service data, default selection, and one-shot consumption.
- The initial `kernel-upgrade` runs `sd-g4` and `sd-g8` did not exercise the loader path: their generated command
  ended in `; ;` before the serial completion marker, and bash waited until the timeout after the syntax error. r10
  fixed that scenario-command construction defect.

## Historical r10 guest round (legacy state IDs)

The builder and installed target disk were created from the in-progress worktree
based on `c749db1`, with the uncommitted r10 changes included. The run installed
systemd-boot 257.13 and exercised the target disk. Exact counts, hashes and log
locations of this historical run are in git at `313ae9dcb` (this file); host
evidence is in the rollback evidence archive (controller state, P-rollback-01
index).

The first `sdboot-oneshot-consumed` attempt used a clean base with no second
state and failed its scenario precondition. The valid run created a second
state in the same guest before exercising the one-shot scenario.

In the pre-fix `kernel-upgrade` run, Debian installed the newer kernel and `/vmlinuz` pointed to it. The scenario then
found `/boot/efi/loader/entries/505628010daf4d44abef9a1e6201755e-6.12.117+deb13-amd64.conf`, which did not begin
with the dr-state BLS marker. `read_bls_entries` correctly refused to interpret it as a canonical dr-state entry,
so the scenario stopped before checking the upgraded menu and boot outcomes.

The follow-up guest inspection identified the producer. The diverted
`/etc/kernel/postinst.d/zz-systemd-boot` and
`/etc/kernel/postrm.d/zz-systemd-boot` hooks were not the only caller:
Debian's `systemd-boot` package post-install script loops over installed
kernels and directly calls `kernel-install add`. Its
`/usr/lib/kernel/install.d/90-loaderentry.install` plugin wrote the extra
Type #1 entry and a duplicate kernel copy under
`/boot/efi/<machine-id>/<version>/`. The entry's `options` selected the
currently running `@os-0001`, and `bootctl list` showed it as selectable while
the dr-state `os-0001.conf` remained the configured default. The installer now
also masks that plugin with the systemd-supported local override
`/etc/kernel/install.d/90-loaderentry.install -> /dev/null`. Exact counts,
hashes and log locations of this 2026-11-21 plugin-override run are in git at
`313ae9dcb` (this file); host evidence is in the rollback evidence archive
(controller state, P-rollback-01 index).

The initial installer change only moved both hooks out of the run-parts directories. A later guest confirmed that this
was insufficient: the systemd-boot package post-install script itself loops over installed kernels and calls
`kernel-install add`. The installer now also creates the systemd-supported override
`/etc/kernel/install.d/90-loaderentry.install -> /dev/null`, disabling the stock BLS entry writer while leaving
`bootctl update` and dr-state's own per-state hooks in place. Current gate and
work status is tracked in [TODO.md](../TODO.md).

## Default and one-shot

`loader.conf` is rewritten atomically through a temporary file and rename. The rewrite preserves unrelated lines in
their original order, removes every prior `default` and `timeout` line, then appends exactly one
`default <entry-id>.conf` and exactly `timeout 5`. It refuses a request without a default id and refuses malformed
existing `default` or `timeout` lines instead of silently discarding them.

`gen-menu --default STATE` and `set-default STATE` write the default only after every desired entry has been written
successfully. Without `--default`, `gen-menu` preserves an existing valid default only while its state/entry is still
in the rewritten menu; otherwise it selects the first entry in sorted state order. This guarantees that a generated
`loader.conf` never contains zero defaults. `boot-once STATE` first ensures the entries exist, maps the state to its
entry id, and calls one isolated function:

```sh
bootctl set-oneshot <entry-id>.conf
```

The one-shot is therefore the systemd-boot EFI variable `LoaderEntryOneShot`; there is no fallback marker. For
pruning, `dr-state` reads that efivarfs variable directly. If efivarfs is unavailable, or the variable exists but
cannot be read or decoded, prune fails closed instead of risk deleting its state. The read path accepts both old
suffix-less and corrected suffixed values because installs made before this finding can carry either form.

## ESP space and copy safety

Before creating directories, copying files, or touching entries or `loader.conf`, `gen-menu` computes the
block-rounded bytes needed for:

- every missing parent directory, including the shared `EFI`, `EFI/dr`, `loader`, and `loader/entries` components;
- each missing or incomplete kernel/initrd pair, including one allocation unit for its directory;
- every desired BLS entry;
- the rewritten `loader.conf`, measured with byte-safe `wc -c` arithmetic rather than shell character counts.

The persistent file/directory allocations still do not model FAT's LFN directory records. Space accounting therefore
also reserves a deliberately conservative estimate: two clusters for every child name this operation can create or
stage, plus one cluster for every involved parent directory. Temporary kernel, entry, marker, and `loader.conf` names
are included. This is an estimate, not a claim about a particular vfat implementation's directory packing. It can
refuse a menu that would fit, but it is intended to avoid accepting a menu that later runs out of space during
directory or rename operations.

The computed need is compared with the ESP's current free bytes. If it does not fit, the command exits non-zero with
the need and free byte counts and leaves the existing menu untouched.

New copies are built in a hidden temporary directory below `EFI/dr/kernels/`, checked against both source SHA-256
values, then renamed as a complete directory. If an interrupted older target directory exists with one file missing,
its two files are replaced through temporary names in that existing directory; the directory itself is not removed
because an installed entry may still reference it.

At the start of a non-dry-run `gen-menu` or `prune` for this backend, under the existing `dr-state` lock, the command
removes only the exact shapes this tool generates: a top-level kernel staging directory `.tmp.<six mktemp
characters>`, `.vmlinuz.tmp.<numeric-pid>` and `.initrd.img.tmp.<numeric-pid>` repair files inside valid kernel
directories, `.dr-state-entry.new.<numeric-pid>` entry temp files (the older `.<entry-id>.conf.new.<numeric-pid>` shape is still cleaned), `loader.conf.new.<numeric-pid>`, and
`bootloader.new.<numeric-pid>`. Prefix look-alikes such as `.tmp.not-ours`, `loader.conf.new.backup`, and
`bootloader.new.backup` survive. The destructive `_sdboot-clean-scratch` test hook refuses to run unless
`DR_STATE_TEST_MODE=1`.

`prune` deletes states using the existing selection policy (newest N, never the running, default, or pending one-shot
state). It first publishes a menu without all doomed states, then deletes their subvolumes, and finally republishes
the menu from surviving states; a menu publication failure leaves every doomed subvolume intact. It then removes only
ESP kernel directories referenced by no remaining BLS entry. It
does not delete a directory referenced by any path argument on any `linux` or `initrd` line, including BLS
multi-initrd lines. A trailing CR is removed before every BLS line is parsed, so CRLF entries protect their kernels
the same way LF entries do. Kernel ids are validated again immediately before deletion. For systemd-boot, prune
requires a readable `loader.conf` with at least one valid `default`; a missing file, an empty file, a timeout-only
file, or any malformed selector refuses before snapshot deletion because the implicit fallback entry cannot be
derived safely. Every valid default selector is protected.

## GRUB compatibility

GRUB continues to use `grubenv` for default and one-shot selection, while systemd-boot uses BLS entries and loader
selectors. The approved sequence/time/origin-reason state-ID schema applies to fresh installs on both backends; the
reason is normalized into a portable short slug and the full text remains in snapshot metadata. This PoC does not
migrate or adopt legacy `@os-0001` installations. The GRUB menu writer and selector protocol remain backend-specific.

## Offline tests

The failing-first suite for the new pure decisions is `poc/test-dr-state-sdboot.sh`. It covers the BLS entry text,
content-addressed kernel ids, entry-id mapping and rejection, backend selection, byte accounting decisions,
`loader.conf` rewriting, empty backend sources, multi-path reference extraction, FAT case collisions, interrupted-copy
cleanup, parent-directory allocation, CRLF references, and orphan-directory selection. Coverage includes
refusal-before-delete checks for missing, empty, and malformed `loader.conf` defaults; the all-backend
sequence/reason schema; and serial guest command wrapping so trailing shell control syntax cannot swallow the
completion marker. Exact case counts and run identities are in git at
`313ae9dcb` (this file).

The suite also runs workflow fixtures through the real `gen-menu`, `set-default`, `prune`, and `delete` paths against disposable
ESP trees. `DR_STATE_TEST_MODE=1` with `DR_STATE_TEST_ROOT` selects a temporary root and optional deterministic ESP
cluster/free inputs; without both the production mounts and paths remain in force. These fixtures prove that cleanup,
all-name collision refusal, space checking, first-entry default selection, `set-default`, and ESP kernel pruning are
actually called. A near-full fixture brackets the reported need by one byte and also checks that the conservative
parent/FAT-record reserve is present. For mutation evidence, disposable `/tmp` copies with each production call
removed were run through this suite; every injected cleanup, collision-scan, space-check, parent-directory, and
default-selection mutant was killed.

The systemd-boot workflow fixtures exercise exact-state deletion after a known-good state is the default, preview-only
deletion, `--yes` confirmation, protection of running/default/one-shot states, rejection of malformed or unknown
loader selectors, refusal before deletion when menu publication fails, removal of only the requested state, cleanup of
its now-unreferenced kernel pair, and removal of the matching metadata sidecar. They also check `snapshot --reason`
and `--note`, UTC timestamp names, pre-change OS/kernel/core-package capture, APT package-plan recording, and the
`list`, `list --long`, and `show` reports.

`poc/test-install-btrfs-bootloader.sh` dry-runs the installer and checks the default and explicit GRUB package path,
the systemd-boot package path, delegated systemd-boot installer call, backend config/marker writes, command ordering, refusal of an invalid
backend, the kernel-install BLS plugin override, and the APT pre-install hook configuration.

`poc/test-build-seed.sh` calls the staging function directly, then requires `xorriso` to pack both seed variants
and extract `/bootloader` from each ISO. The `tester-unified/Dockerfile` installs the shared `xorriso` package. The
registered lane fails if `xorriso` is absent, so ISO packing cannot silently
go untested. It executes the
marker-selection block extracted from the
guest's `user-data` through a fake `poc/install-btrfs.sh` against a disposable seed directory. The fake records the
installer argv, proving explicit `grub` and `systemd-boot` markers reach the invocation as `--bootloader <selected>`.
A missing marker fails closed, and empty/unknown values are refused. Disposable mutants with the validation case removed,
or with the argument removed from the invocation while its text remains in a comment, are killed. Isolated `TMPDIR`
cases verify that the staging directory is removed after both a successful pack and a packing failure. With
`xorriso` absent it reports that real ISO packing and extraction are not covered.

`poc/test-dr-serial.py` covers both backend sources with dr-state's empty/conflict/unknown rules, strict
`loader.conf` default parsing, exact BLS kernel/initrd path and shared-id validation, unsafe/malformed entries,
kernel-title parsing, and command-aware scripted scenario runs. The default parser requires `<entry-id>.conf`, rejects
unsafe values, and emits a distinct message naming the suffix-less real-guest finding. `sdboot-esp` and `s2-s3` both
fail a suffix-less default and require the suffixed name to resolve to an existing entry file. The scripted guest
emits backend, selection-evidence, one-shot and scratch results only when the real command contains the exact required
paths or globs. The scenario tests require the exact `ESP_FILE_OK` set per entry, fail on omitted or foreign tags,
nonzero file/scratch scan status, and missing completion tags, and cover every generated scratch name. The new
`sdboot-oneshot-consumed` scenario test requires running state == default, selects a target different from both, and
then requires the EFI one-shot probe's completion tag, rejects a present variable, and rejects a nonzero probe. Its
scripted regression makes the running state non-default and confirms the scenario fails before attempting a no-op
one-shot. `s2-s3` records `bootctl list` selection/default lines and the `LoaderEntrySelected` efivar as informational
transcript evidence without making them pass conditions; the set-default reboot remains its final pass condition. A
full config-only systemd-boot kernel flow proves that the baseline hashes the systemd-boot binary independently and
only the backend source files that exist. The fake kernel-upgrade guest runs every generated command through
`bash -n` after the serial marker is appended and rejects a command that already ends in `;`. Canonical BLS checks
require the exact dr-state marker and title lines, a valid entry id, reversible `@os-X` to `os-X` mapping, and a
title kernel matching the kernel-id directory. Fabricated, swapped-id, wrong-title and wrong-marker entries are
rejected in both `sdboot-esp` and the systemd-boot `s2-s3` state-set check.

The recovery scenario contract checks require all recovery cases to select through QMP keyboard input rather than a
pre-staged `boot-once`, then use the safe failed-state deletion path after the recovered state becomes default. The
historical `s2-s3` flow installed the then-current `dr-state` and APT hook payloads, then checked the timestamped APT
snapshot, pre-change package/kernel report, and planned `hello` action.
Exact counts, hashes and log locations of these historical runs are in git at
`313ae9dcb` (`docs/ROLLBACK-RECOVERY.md`); host evidence is in the rollback evidence archive
(controller state, P-rollback-01 index). They do not certify an integrated
release candidate.

The offline lane runs the pre-existing suites: `test-dr-state-window.sh`, `test-dr-state-prune.sh`,
`iso/test-dr-plan.sh`, `iso/test-dr-autoinstall-guard.sh`, and `poc/test-dr-pick-disk.sh`.
The `offline` lane runs those suites with the systemd-boot coverage in `tester-unified`, after `shellcheck`, `bash -n`,
and `git diff --check`.

## How to run the guests

This section records the supported systemd-boot run matrix. Dated provenance,
result counts, and retained log paths are in the
[2026-11-22 results report](RESULTS-SDBOOT-M2-2026-11-22.md). The builder row
below is a separate cloud-image automation path, not the product M2 installer.

Build a systemd-boot builder seed, then let the builder guest install the base disk:

```sh
scripts/debian-rollback/poc/build-seed.sh --bootloader systemd-boot /tmp/seed-systemd-boot.iso
```

The seed's `/bootloader` file contains `systemd-boot`; the builder guest reads it and invokes
`install-btrfs.sh --device "$DEV" --yes --bootloader systemd-boot`. A seed built without `--bootloader` carries
`grub`, exactly matching the installer's default. The systemd-boot guest runs only the systemd-boot seed, not the
GRUB seed.

Start each scenario against the running guest's serial socket, using the same state directory as
`vm-lab start --plain-host`
(`serial.sock` and `qmp.sock` live there):

```sh
scripts/debian-rollback/poc/dr-serial.py STATE_DIR/serial.sock SCENARIO \
  --log STATE_DIR/SCENARIO.log
```

The supported run matrix for the systemd-boot variant is:

| Scenario | Guest run | What it proves |
|---|---|---|
| Builder install | Disposable cloud-image guest with blank target disk and the systemd-boot seed attached; then boot the target disk alone. | The seed selects the target backend, the common installer creates the systemd-boot target, and the installed target boots without builder inputs. |
| `boot-check` | Fresh overlay on the installed target. | Running root, systemd/networking, split data paths, ESP loader/BLS entry/binary/kernel copies. |
| `sdboot-esp` | Fresh overlay on the installed target. | Canonical entries, safe state mapping, kernel/initrd paths, suffixed default, and no scratch files. |
| `s2-s3` | Fresh guest; do not reuse after another snapshot-producing scenario. | APT pre-change snapshot, metadata/package plan, rollback, persistent data, boot-once, and set-default. |
| `sdboot-oneshot-consumed` | Fresh guest; the driver creates a manual test snapshot if no non-default state exists. | One-shot state boots and `LoaderEntryOneShot` is absent after consumption. |
| `kernel-upgrade` | Fresh guest; do not reuse after another snapshot-producing scenario. | APT snapshots the old state, the new and old kernels have correct entries, and one-shot returns to the new default. |
| `backports` | Fresh guest; do not reuse after another snapshot-producing scenario. | Same kernel-state contract using Trixie backports. |
| `recovery-kernel` | Fresh guest; destructive. | Manual selection of the pre-change snapshot after kernel/initrd damage; modules, persistent data, default reset, and failed-state deletion. |
| `recovery-library` | Fresh guest; destructive. | Manual selection of the pre-change snapshot after dynamic-loader damage; persistent data, default reset, and failed-state deletion. |
| `recovery-dependency` | Fresh guest; destructive. | Manual selection after forcing a dpkg dependency break; consistency and dependent service restored, then default reset and failed-state deletion. |

Do **not** run `s5` against systemd-boot: it sends GRUB menu key presses over the serial line. Do **not** run `s6`,
`s7`, `s8`, `s9`, or `s9b` until their remaining GRUB-specific menu assumptions have been made backend-aware and
reviewed. `window-negative` and `live-inspect` are not part of this run matrix.

For `s2-s3` and `kernel-upgrade`, use a new overlay on the same installed systemd-boot base for each scenario. Their
initial precondition is that the running state is the default, and their snapshot counts depend on an empty dedupe
window. `boot-check` and `sdboot-esp` are read-only. `sdboot-oneshot-consumed` creates a manual test snapshot on a
fresh disk when necessary, then proves that the one-shot state boots and the variable is gone.

Each recovery scenario requires a running default and a fresh recovery snapshot. They do not stage a one-shot. They
select the known-good pre-change snapshot with QMP virtual-keyboard input, and after recovery verify `set-default`, an ordinary reboot,
and exact deletion of the now-inactive failed state. `dr-state prune --keep N` is covered offline; live systemd-boot
pruning and ESP-space behavior remain separate guest proof.

Before any scenario runs, the backend is resolved from both `/etc/dr-state.conf` and
`/boot/efi/EFI/dr/bootloader` using the same presence, empty-value, conflict and unknown-value rules as `dr-state`;
neither source may silently fall back to GRUB. The scratch check covers `.tmp.<six alphanumeric characters>`,
`.vmlinuz.tmp.<pid>`, `.initrd.img.tmp.<pid>`, `.dr-state-entry.new.<pid>`, the older
`.<entry-id>.conf.new.<pid>`, `loader.conf.new.<pid>` and `bootloader.new.<pid>`. Each per-entry file probe must
emit `ESP_FILE_SCAN_DONE:1` and the scratch scan must emit `SCRATCH_SCAN_DONE:1`; a scenario fails if either
completion tag is absent or its scan command returns nonzero. The kernel-upgrade baseline requires the
`EFI/systemd/systemd-bootx64.efi` hash independently and hashes whichever of `/etc/dr-state.conf` and
`/boot/efi/EFI/dr/bootloader` exist, so a valid config-only systemd-boot install needs no ESP marker.

## Historical fresh install and kernel-upgrade proof for the plugin override

The historical seed ISO was built from an earlier worktree:

- ISO: `debian-rollback-seed-systemd-boot-kernel-install-override-20261121T051859Z.iso`
- SHA-256: `8647cac8838ebe62d204ffea0a8a9ac2822ff1ecaf2e9a34a459c2cf4b1dd3d1`
- Base installed disk: `installed-systemd-boot-current.qcow2`
- Base SHA-256: `4a0a05be59a8981bc31e5fa167a740116be42223d3c46f7cf7dec358612aec3d`
- Base size: 8 GiB virtual size, 1.22 GiB disk use.

The builder guest completed the installation (`DR-BUILD-DONE rc=0`,
`DR-RUNCMD-END rc=0`). The host-side `run-builder.sh` then returned 127 because
`qemu-img` was absent on the host. The installed disk was copied and checked
with the detached `validate-base.sh` helper, which had `qemu-img`; that check
passed and produced the base hash above. This was a harness packaging issue
after guest installation, not an installer failure.

That historical guest ran `boot-check` and `kernel-upgrade`. The upgrade
installed Linux `6.12.117+deb13-amd64` over
`6.12.107+deb13-amd64`; the menu had exactly the two entries reported by
`dr-state list`, with no stock machine-ID entry. The upgraded default booted,
the older snapshot booted with its matching `loop` module, and the next boot
returned to the upgraded default after the one-shot was consumed. Source hashes
injected into the guest were:

| Input | SHA-256 |
|---|---|
| `install-btrfs.sh` | `71b23c5ac32ba561dde14fdf2b1cfb0d6f2397abd72ddec87afd21bcfe5a02f0` |
| `dr-state` | `ab7cf555eba00c96de24edb7afdba8437b83b506ee0f9c14fe4fc17a6bac3e52` |
| `dr-serial.py` | `fc215bd6799fbd218075a7c8d9d12d358487b3236df27032d7077c525cf8d246` |
| NoCloud `user-data` | `79af09d956e14188e08f02f52708d6a889b4d93d15d5b1b277467240c2f6a153` |
| NoCloud `meta-data` | `931e56dd46110d5953b050ae06e10d82cd4953e29a7ad03a443b79fbe278a5df` |

Exact counts, hashes and log locations of this historical run are in git at
`313ae9dcb` (this file); host evidence is in the rollback evidence archive
(controller state, P-rollback-01 index). The evidence proves the named
artifacts and injected files, not an integrated release candidate.

## Guest coverage and limits

The dated results reports record guest coverage and exact outcomes. Current
status and remaining work are tracked in [TODO.md](../TODO.md). This note does
not claim:

- that systemd-boot `delete`, `prune`, kernel removals, or ESP-full behavior work end to end in a guest;
- that live pruning and ESP cleanup have been guest-tested;
- automatic fallback after a failed default boot; it is outside the current recovery contract;
- anything about Secure Boot, real hardware, or backend conversion of an existing GRUB install.

Those items need their own acceptance work before they are claimed.
