# systemd-boot variant: offline implementation (2026-11-18)

Status: **offline tooling updated from real-guest findings; broader guest behaviour remains unverified.** This note
records the systemd-boot PoC as built. Offline implementation and test work was constrained to the worktree, `/tmp`,
and offline checks: no VM, Docker container, SSH connection, network access, host contact, or signal-sending operation
was used by the offline tooling job. The real-guest observations quoted below were supplied as evidence from the
separate guest workstream.

> **Guest verification rule:** Loader and firmware contracts (entry ids, one-shot variables, ESP layout, boot order)
> are verified only by guest scenarios, never by offline tests or review rounds. The guest `sd-g3`/diagnostic runs
> showed that 6 review rounds and 123 offline cases missed that systemd-boot entry ids carry `.conf`.

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

The user-visible state commands stay the same:

```text
dr-state gen-menu [--default STATE]
dr-state snapshot [--reason WORD]
dr-state list
dr-state set-default STATE
dr-state boot-once STATE
dr-state prune [--keep N] [--dry-run]
```

The apt and kernel hooks continue to call `dr-state gen-menu`; they are not backend-specific.

## ESP layout

For a systemd-boot install, the relevant ESP files are:

```text
/boot/efi/loader/loader.conf
/boot/efi/loader/entries/os-<state-suffix>.conf
/boot/efi/EFI/dr/bootloader
/boot/efi/EFI/dr/kernels/<kver>-<initrd-sha256-first-12>/vmlinuz
/boot/efi/EFI/dr/kernels/<kver>-<initrd-sha256-first-12>/initrd.img
```

BLS paths are relative to the ESP root, so an entry names
`/EFI/dr/kernels/<id>/vmlinuz` and `/EFI/dr/kernels/<id>/initrd.img`, not a path under the host mount point
`/boot/efi`.

The kernel id is exactly `<kver>-<first 12 lowercase hex digits of the source initrd's SHA-256>`. States with the
same kernel version and byte-identical initrd share one copied pair. A state selects the newest kernel version that
has both a non-empty `vmlinuz-<kver>` and `initrd.img-<kver>`, using the same ordering rule as the GRUB backend.

## Entry-id mapping

The BLS entry id is the file name without `.conf`. The implementation does not assume that systemd-boot accepts
`@` in an entry id: `@os-...` maps to `os-...` by stripping the single leading `@`. The inverse adds it back.

Accepted state names are `@os-` followed by an alphanumeric first character and then only ASCII alphanumerics,
underscore, period, and hyphen. Entry ids are capped at 240 bytes (240 is accepted and writes its entry, 241 is refused before any kernel is copied; entry temp files use a short fixed name, so the id length does not matter for them). `latest_kernel` only returns a version whose `vmlinuz` AND initrd are both non-empty, so a newer incomplete pair is skipped for the older complete one. Names with an unsafe character, an embedded `@`,
`..`, or a trailing period are rejected before any menu file or copied kernel is changed. This is conservative and
reversible; whether systemd-boot itself accepts `@` remains unverified because no guest or bootloader binary was run.

State names ending in `.conf` are invalid under fix 8a: snapshot creation sanitizes that suffix and `gen-menu` skips
such existing state names when building the menu. This avoids ambiguity between an internal state such as
`@os-0001.conf` and the entry file name `os-0001.conf`; the correctly namespaced entry would be `os-0001.conf.conf`.
The fix 8a code change is owned by another job; this documentation change neither implements nor verifies it.

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
linux /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/vmlinuz
initrd /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/initrd.img
options root=UUID=<btrfs-uuid> rootflags=subvol=@os-0001 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-0001
```

Only entries carrying this marker are removed when their state disappears. Foreign BLS entries are left alone and
still protect kernel directories they reference.

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

- `sd-g2`: `boot-check` and `sdboot-esp` passed with the older checks, before the stricter `.conf` assertions.
- `sd-g3`: `s2-s3` failed the `boot-once` and `set-default` boot outcomes because suffix-less selectors were ignored.
- The diagnostic guest proved that a suffixed `bootctl set-oneshot` boots the intended snapshot and that systemd-boot
  clears the one-shot variable when consumed.
- `kernel-upgrade` is **NOT RUN**: its first attempt timed out in `dist-upgrade` at 55 minutes; the rerun is pending.

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

New copies are built in a hidden temporary directory below `EFI/dr/kernels/`, checked against the source initrd
SHA-256, then renamed as a complete directory. If an interrupted older target directory exists with one file missing,
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
state), regenerates the BLS menu, then removes only ESP kernel directories referenced by no remaining BLS entry. It
does not delete a directory referenced by any path argument on any `linux` or `initrd` line, including BLS
multi-initrd lines. A trailing CR is removed before every BLS line is parsed, so CRLF entries protect their kernels
the same way LF entries do. Kernel ids are validated again immediately before deletion.

## GRUB compatibility

When neither backend source exists, dispatch is GRUB. The original `cmd_gen_menu` body was compared against the base
revision after only renaming the function; its GRUB text and operations are byte-for-byte identical. `install-grub`,
`grubenv` default/one-shot handling, and the GRUB apt/kernel-hook path keep their prior behaviour.

## Offline tests

The failing-first suite for the new pure decisions is `poc/test-dr-state-sdboot.sh`. It covers the BLS entry text,
content-addressed kernel ids, entry-id mapping and rejection, backend selection, byte accounting decisions,
`loader.conf` rewriting, empty backend sources, multi-path reference extraction, FAT case collisions, interrupted-copy
cleanup, parent-directory allocation, CRLF references, and orphan-directory selection. It currently passes 156 cases.

The suite also runs workflow fixtures through the real `gen-menu`, `set-default`, and `prune` paths against disposable
ESP trees. `DR_STATE_TEST_MODE=1` with `DR_STATE_TEST_ROOT` selects a temporary root and optional deterministic ESP
cluster/free inputs; without both the production mounts and paths remain in force. These fixtures prove that cleanup,
all-name collision refusal, space checking, first-entry default selection, `set-default`, and ESP kernel pruning are
actually called. A near-full fixture brackets the reported need by one byte and also checks that the conservative
parent/FAT-record reserve is present. For mutation evidence, disposable `/tmp` copies with each production call
removed were run through this suite; all five mutants — cleanup, collision scan, space check, parent-directory
accounting, and default selection — were killed.

`poc/test-install-btrfs-bootloader.sh` dry-runs the installer and checks the default and explicit GRUB package path,
the systemd-boot package path, exact bootctl argv, backend config and marker, command ordering, and refusal of an
invalid backend. It currently passes 17 cases.

`poc/test-build-seed.sh` calls the no-xorriso staging function directly and, when `xorriso` is available, packs both
seed variants and extracts `/bootloader` from each ISO. It executes the marker-selection block extracted from the
guest's `user-data` through a fake `poc/install-btrfs.sh` against a disposable seed directory. The fake records the
installer argv, proving absent/grub/systemd-boot selection reaches the real invocation as
`--bootloader <selected>` while empty/unknown values are refused. Disposable mutants with the validation case removed,
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
only the backend source files that exist. Canonical BLS checks require the exact dr-state marker and title lines, a
valid entry id, reversible `@os-X` to `os-X` mapping, and a title kernel matching the kernel-id directory. Fabricated,
swapped-id, wrong-title and wrong-marker entries are rejected in both `sdboot-esp` and the systemd-boot `s2-s3`
state-set check.

The pre-existing offline suites remain green: `test-dr-state-window.sh`, `test-dr-state-prune.sh`,
`iso/test-dr-plan.sh`, `iso/test-dr-autoinstall-guard.sh`, `poc/test-dr-pick-disk.sh`, and `poc/test-dr-qemu.sh`.
All touched shell files pass `shellcheck`.

## How to run the guests

This section separates the real-guest evidence from later scenario runs that are still pending. Results obtained
before the `.conf` fix are labelled as such and do not certify the stricter current checks.

Build a systemd-boot builder seed, then let the builder guest install the base disk:

```sh
scripts/debian-rollback/poc/build-seed.sh --bootloader systemd-boot /tmp/seed-systemd-boot.iso
```

The seed's `/bootloader` file contains `systemd-boot`; the builder guest reads it and invokes
`install-btrfs.sh --device "$DEV" --yes --bootloader systemd-boot`. A seed built without `--bootloader` carries
`grub`, exactly matching the installer's default. The systemd-boot guest runs only the systemd-boot seed, not the
GRUB seed.

Start each scenario against the running guest's serial socket, using the same state directory as `dr-qemu.sh`
(`serial.sock` and `qmp.sock` live there):

```sh
scripts/debian-rollback/poc/dr-serial.py STATE_DIR/serial.sock SCENARIO \
  --log STATE_DIR/SCENARIO.log
```

The supported run matrix for the systemd-boot variant is:

| Scenario | Guest run | What it proves | Status |
|---|---|---|---|
| `boot-check` | May share a fresh overlay with `sdboot-esp`; must be first boot after install. | The installed state is the running root, systemd and networking work, D18 paths are split correctly, and the systemd-boot ESP contains `loader/loader.conf`, at least one BLS entry, `EFI/systemd/systemd-bootx64.efi`, and non-empty kernel copies. | **sd-g2 PASS with OLD checks**. Current strict suite has not been rerun. |
| `sdboot-esp` | May share a fresh overlay with `boot-check`. | Every BLS entry is canonical: exact dr-state marker/title, valid `@os-X` to `os-X` entry mapping, and exactly `/EFI/dr/kernels/<valid-id>/vmlinuz` with the matching `initrd.img`; the exact non-empty files reported by the guest equal that entry's expected set. `loader.conf` has exactly one suffix-form `<entry-id>.conf` default that resolves to an existing entry file; its state matches `dr-state list`; no generated scratch remains; and ESP free space is reported. | **sd-g2 PASS with OLD checks**. Current `.conf` assertions are **NOT RUN**. |
| `s2-s3` | Fresh overlay; do not reuse after another snapshot-producing scenario. | Apt takes one snapshot; canonical marked systemd-boot entry states, not merely `.conf` count, equal `dr-state list`; the suffixed default exists; boot-once boots the earlier package state with `/etc` rolled back while service data survives; `bootctl list` and `LoaderEntrySelected` are recorded as evidence; the one-shot is consumed; and set-default changes the next plain boot. | **sd-g3 FAILED** the boot-once and set-default outcomes because of the suffix-less entry-id finding. Corrected flow is **NOT RUN**. |
| `sdboot-oneshot-consumed` | Fresh overlay; requires running state == default and another state available. | `dr-state boot-once` selects a state different from both running and default; that reboot reaches its userspace; the exact `LoaderEntryOneShot` efivar is absent there; omission or a failed probe fails the scenario. A non-default running state fails the precondition before any one-shot is attempted. | **NOT RUN** as a scenario. The diagnostic guest separately proved suffixed one-shot selection and clearing. |
| `kernel-upgrade` | Fresh overlay; do not reuse after another snapshot-producing scenario. | A kernel upgrade snapshots the old state, gives the running state the new kernel and the snapshot the old kernel in BLS entries, boots both kernels, loads the old state's modules, and consumes the systemd-boot one-shot. | **NOT RUN**: the attempt timed out in `dist-upgrade` at 55 minutes; rerun pending. |

For the next systemd-boot guest pass, run `boot-check`, `sdboot-esp`, `s2-s3`, `sdboot-oneshot-consumed`, and
`kernel-upgrade` as separate fresh-overlay runs where required. Do **not** run `s5` against systemd-boot: it sends
GRUB menu key presses over the serial line. Do **not** run `s6`, `s7`, `s8`, `s9`, or `s9b` until their remaining
GRUB-specific menu assumptions have been made backend-aware and reviewed. `backports`, `window-negative`, and
`live-inspect` are not part of this run matrix.

For `s2-s3` and `kernel-upgrade`, use a new overlay on the same installed systemd-boot base for each scenario. Their
initial precondition is that the running state is the default, and their snapshot counts depend on an empty dedupe
window. `boot-check` and `sdboot-esp` are read-only and may share the first guest. `sdboot-oneshot-consumed` needs a
guest whose running state is the default and which has another non-default state available; it consumes that one-shot
while proving the variable is gone.

Before any scenario runs, the backend is resolved from both `/etc/dr-state.conf` and
`/boot/efi/EFI/dr/bootloader` using the same presence, empty-value, conflict and unknown-value rules as `dr-state`;
neither source may silently fall back to GRUB. The scratch check covers `.tmp.<six alphanumeric characters>`,
`.vmlinuz.tmp.<pid>`, `.initrd.img.tmp.<pid>`, `.dr-state-entry.new.<pid>`, the older
`.<entry-id>.conf.new.<pid>`, `loader.conf.new.<pid>` and `bootloader.new.<pid>`. Each per-entry file probe must
emit `ESP_FILE_SCAN_DONE:1` and the scratch scan must emit `SCRATCH_SCAN_DONE:1`; a scenario fails if either
completion tag is absent or its scan command returns nonzero. The kernel-upgrade baseline requires the
`EFI/systemd/systemd-bootx64.efi` hash independently and hashes whichever of `/etc/dr-state.conf` and
`/boot/efi/EFI/dr/bootloader` exist, so a valid config-only systemd-boot install needs no ESP marker.

## Not verified

Only the quoted `sd-g2` checks, the failed `sd-g3` boot outcomes, and the separate one-shot diagnostic are real-guest
evidence. The corrected strict tooling has not been run end to end in a guest. In particular, this note does **not**
claim:

- that Debian's `systemd-boot` and `systemd-boot-efi` packages install under the debootstrap flow;
- that `bootctl install --no-variables` succeeds inside the chroot and produces an OVMF-bootable loader;
- that systemd-boot parses the generated BLS entries, accepts the mapped ids, shows the title, or boots each state;
- that kernels copied from btrfs execute with the expected state's modules and root subvolume;
- that the current `boot-check`, `sdboot-esp`, `s2-s3`, or `sdboot-oneshot-consumed` scenarios pass after the `.conf`
  fix;
- that `kernel-upgrade` completes, or that prune can read a corrected pending one-shot value from efivarfs;
- that `set-default`, snapshots from apt/kernel hooks, kernel upgrades/removals, prune, and ESP-full behaviour work
  end to end;
- anything about Secure Boot, real hardware, or backend conversion of an existing GRUB install.

These are guest-test questions. The offline work deliberately goes no further than deterministic file construction
and guarded local operations.
