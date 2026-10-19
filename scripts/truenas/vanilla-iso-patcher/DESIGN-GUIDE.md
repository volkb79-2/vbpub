# Design guide

## Installer ISO structure and bounded partition change

The ISO contains `/live/filesystem.squashfs`, which contains the Python
installer at
`/usr/lib/python3/dist-packages/truenas_installer/install.py`. The installer
creates GPT partition 3 with `sgdisk -n3:0:0`, consuming the remaining disk.
The generator validates that exact source structure and changes its end
argument to `-n3:0:+<size>G`. It rebuilds the SquashFS payload, then writes it
into the same data extent in a byte-for-byte copy of the input ISO.

This boundary is structural rather than a version allowlist. That lets the same
patcher evaluate newly published images, but it does not claim compatibility
just because a version string is in the catalog. If the source path or command
changes, it refuses to edit the ISO. Each newly supported image still needs
source, boot-layout, GPT, and installation evidence recorded in
[`COMPATIBILITY.md`](COMPATIBILITY.md).

The ISO is not re-mastered. `xorriso report_lba` must report exactly one
contiguous extent for `/live/filesystem.squashfs`; the rebuilt file must fit in
the ISO9660 file record. The patcher copies the source ISO, replaces those
bytes in place, and zeroes the file-record padding. It refuses split extents,
an oversized rebuilt filesystem, or an extent outside the input file. This
keeps the original ISO directory tree, BIOS and UEFI boot records, hybrid
partition data, and every unrelated byte unchanged. The source's
Apple-specific HFS+/APM data is retained along with the PC boot metadata.

The outer image combines an ISO9660 directory with El Torito boot catalog
entries for BIOS and UEFI and hybrid-disk metadata. The ISO directory record
for `filesystem.squashfs` supplies its extent and byte length; changing only
that extent leaves the catalog, boot images, partition maps, and directory
records at their original offsets.

This also keeps VCDIFF useful. An earlier xorriso remaster moved the SquashFS
extent from LBA 40,063 to LBA 1,023,306; its delta had already reached
1,991,176,193 bytes after 13 minutes 36 seconds, so that incomplete attempt
was stopped. In-place bounded builds measured 167–186 KB. The unattended
flavors measure 39–45 MB: their added runtime, GRUB changes, and adapter are
small, but rebuilding SquashFS also changes compressed blocks. Thus the whole
ISO delta is much larger than the adapter archive. Exact source/target hashes,
patch sizes, and xdelta versions are listed in
[`COMPATIBILITY.md`](COMPATIBILITY.md#binary-delta-size-measurement).

## Experimental growing-mode SquashFS overlay

The in-place generator rebuilds the full live SquashFS, which gives a compact
ISO but makes xdelta encode many changed compressed blocks. The overlay PoC
keeps the upstream `/live/filesystem.squashfs` file record, extent, and bytes
unchanged. It puts only the patched installer, unattended runtime, profile,
and v27 relaxation adapter into a second small SquashFS. It then adds that
file and `/live/filesystem.module` to an xorriso growing-mode image and updates
the two GRUB configs. live-boot reads `filesystem.module` as a list of layers
and mounts the overlay over the original root filesystem.

The upstream GRUB entries use `toram=filesystem.squashfs`, which asks live-boot
to copy one named SquashFS into RAM. That single-image shortcut bypasses its
module list, so the PoC removes `toram` from the two vanilla entries and the
new unattended entry. They still boot from the ISO and the two original entry
labels remain available. The PoC checks the resulting module order, verifies
the new overlay's files, and compares the original SquashFS extent and SHA-256
before and after the ISO update.

xorriso appends new file data and directory-tree extents to the regular ISO
file, then makes the updated tree visible. Its `-toc` report for the output
still says `1 session`; this is not an ISO 9660 multisession-track image. The
name `multisession_overlay_poc.py` is historical shorthand for the appended
layout. The actual implementation is xorriso growing mode on a regular file.
The PoC reports the growing image's media summary and verifies that the
overlay extent begins after the vanilla file length. BIOS and UEFI El Torito
entries are also checked after the update.

For the v27.0.0-RC.1 unattended-relaxed 16 GiB PoC, the source image was
2,683,439,104 bytes. The appended ISO grew by 587,776 bytes, including the
24,576-byte overlay SquashFS; the original 294,522,880-byte live SquashFS
extent and contents stayed unchanged. xdelta3 3.0.11 produced a 50,601-byte
VCDIFF and verified it by reconstructing the full ISO and comparing its
SHA-256. The full-repack image of the same release and flavor needed
39,497,116 bytes, so the growing-mode layout reduced this measured patch by
about 780 times. Exact hashes and the xorriso report are in
[`COMPATIBILITY.md`](COMPATIBILITY.md#experimental-growing-mode-poc).

This is a useful delta-size result, not yet a supported generator mode. The
current CLI's production builder remains the in-place layout described above.
The PoC is limited to the inspected v27 release; [managed TCG installation
acceptance](COMPATIBILITY.md#experimental-growing-mode-overlay-vm-acceptance)
is recorded separately from its deterministic build checks.

## Unattended flavor and the vanilla installer flow

The inspected v25.10.7, v27.0.0-RC.1, and v28 nightly ISOs share the parts the
unattended helper calls: `truenas_installer.disks.list_disks()`, the seven
argument `truenas_installer.install.install()` function, and
`truenas_installer.serial.serial_sql()`. The generator checks those signatures
in the live SquashFS instead of trusting the version label. A source change
that moves or changes those APIs makes the build fail before it publishes an
ISO.

The standard GRUB configs have two entries, identified as `video` and
`serial`. They boot the same live installer with different console arguments;
neither is a separate text-installer entry. The default systemd unit starts
`python3 -m truenas_installer --server`, which provides the installer RPC
server. Running the module without `--server` starts its dialog-based console
menu. The unattended ISO leaves that vendor unit and both vanilla GRUB entries
intact.

The unattended flavor makes three small changes:

1. It adds a new GRUB entry with the kernel argument
   `truenas.autoinstall=1`, sets that entry as the default, and sets the menu
   timeout to five seconds. The original installer entries remain available
   during the timeout for a normal manual install.
2. It adds a systemd drop-in that starts a dispatcher instead of replacing the
   vendor unit. If the GRUB flag is absent, the dispatcher execs the original
   RPC-server command unchanged. If the flag is present, it runs the
   unattended helper and sends progress/errors to the journal and console.
3. Before calling TrueNAS's own installer function, the helper compares the
   full `lsblk` device tree with the installer disk list. It excludes the disk
   containing `/cdrom`; refuses mounted, removable, read-only, undersized, or
   inventory-mismatched target disks; and accepts only one target or two
   targets with equal known model, exact byte capacity, and a known matching
   transport. It runs read-only `zdb -l` probes against each target disk and
   every reported descendant block device before installation starts. No ZFS
   labels are allowed, except labels naming `boot-pool`; another pool name,
   missing probe output, or unrecognized output stops with the exact message
   `unsupported unsafe disk hardware configuration`. For a single disk, the
   kernel and installer inventories must agree on its name and exact size even
   when the device model is unknown. A mirror with an unknown model or
   transport stops with the exact message
   `unsupported unsafe disk hardware configuration`, before any partition or
   wipe command runs.

The helper passes all accepted targets to the stock installer. Its existing
`create_boot_pool()` function creates a single-disk pool for one target and a
ZFS mirror for two. The only partition layout change remains the bounded
partition 3 size. It passes the fixed `truenas_admin` / `truenas` credential,
uses the installer's no-PMBR-flag setting, and reboots after the install
finishes. The password is present in the ISO and in its live helper; change it
immediately after first boot. A manual install remains available by selecting
one of the original entries before the five-second timeout.

The unattended GRUB entry includes Debian live-boot's [`noeject` option](https://manpages.debian.org/bookworm/live-boot-doc/live-boot.7.en.html). This
prevents the live system from pausing for a physical media-removal prompt at
shutdown. The runtime profile defaults to `reboot`, preserving unattended
installation behavior. `--post-install-action poweroff` instead asks systemd
to power off after a successful install and any relaxed adapter application.
Netcup's generic ISO workflow uses that poweroff as its completion signal,
detaches the provider-side ISO while the server is off, restores the saved boot
order, and then boots the installed disk. A guest reboot alone cannot signal
the provider to detach media.

The ISO editing stays in-place. Each GRUB config is rewritten within its
existing ISO file record and padded with spaces to preserve the recorded
length. The live SquashFS carries the dispatcher, disk policy, profile, and
optional adapter bundle. The original ISO's BIOS/UEFI boot catalogs and all
unrelated extents remain at their original offsets.

## Applying the boot-pool adapter before first reboot

The `unattended-relaxed` flavor calls the stock install function first. That
function finishes the new boot environment, exports `boot-pool`, and returns
before the unattended helper reboots. The helper then imports the pool,
resolves its `bootfs` dataset, mounts the root and separate `usr` dataset, and
checks every file against the release adapter's `targets.sha256`. The
installed `/etc` tree is also a child dataset, so the helper reads
`/etc/version` by mounting that dataset inside a private mount namespace. This
keeps the target version check isolated from the live installer's own `/etc`.
The installed `/usr` dataset has a regular ZFS mountpoint property; the helper
uses ZFS's `zfsutil` mount option to attach it at its temporary target path
without changing that property. It applies
the adapter diffs with exact line matching and no offset/fuzz, installs the
adapter helpers, compiles the changed Python modules with the new environment's
Python, restores the `usr` dataset's original read-only property, unmounts, and
exports the pool. Any fingerprint or patch mismatch stops the run before
reboot; it does not try another adapter.

This is a useful hook point because the new system files are on disk but no
TrueNAS middleware service is running from that boot environment yet. The ISO
builder pins adapters to `27.0.0-RC.1` and
`28.0.0-MASTER+20261014-230204`. It does not infer that other v27/v28 builds
are compatible. The adapters are vendored under `adapters/` so the ISO
generator is self-contained, but their source remains experimental and has
open acceptance items; the combined flavor is for controlled evaluation.
There is no v25 relaxation adapter.

## Why a v28 update needs around 10 GB of pool space

The ISO's compressed `TrueNAS.update` image is about 2.3 GB, but its v28
manifest reports a system image size of `10,025,572,761` bytes (about 10.0 GB
decimal). `truenas_install` creates a new `boot-pool/ROOT/<version>` dataset
and separate system datasets, then expands `rootfs.squashfs` into them. An
upgrade retains the previous boot environment while the new one is assembled,
so the required space tracks the expanded system image, not the downloaded ISO
or a small set of changed files. The exact implementation can be inspected in
[`truenas_install/__main__.py`](https://github.com/truenas/scale-build/blob/master/truenas_install/__main__.py).

In the inspected v28 installation, a clean 16 GiB boot-pool had about 11.35
GB free, leaving only roughly 1.32 GB above that manifest size before other
metadata, update work, and user data. The 16 GiB flavor therefore bounds the
partition but does not promise that future updates will fit after the pool is
used for containers, VMs, cache, or files. Keep substantial headroom or choose
a larger boot-pool size.

## Upstream catalog and checksum gate

The CLI reads both official catalogs, `https://download.sys.truenas.net/` and
`https://iso.sys.truenas.net/`, extracts ISO links with matching `.sha256`
sidecars, and uses each catalog's exact URL. Their release coverage differs:
the direct-download index currently lists archived v25 images, while the ISO
index currently includes v28 nightlies. The catalog sources are merged and
duplicate family/version/filename entries are collapsed. Discovery fails if
either catalog cannot be read, because returning a partial list could make an
upstream network failure look like a release that does not exist. The observed
catalog counts and date are recorded in [`COMPATIBILITY.md`](COMPATIBILITY.md).

Upstream download reads the checksum sidecar first, validates its format and
any filename embedded in it, downloads into a same-directory temporary file,
verifies the exact SHA-256 and advertised length, and links the result into
place only after it passes. Existing targets are never overwritten. A
user-supplied local ISO has no automatically trusted publisher checksum, so
`create --input-iso` requires `--expected-input-sha256`. The patcher compares
that digest before extraction and repeats the check as part of the ISO build.

The catalog is discovery, not a compatibility certificate. It filters to
installer versions numbered 25 or later and requires a matching `.sha256` link
in the index. For a selected image, the CLI fetches and parses that sidecar,
checks that any filename it includes matches the image, then verifies the
downloaded bytes against the digest. Every selected source also passes the
installer-structure checks in `patch_iso.py`.

## Exact-base VCDIFF patches

There is no safe generic byte edit inside SquashFS. Its data is compressed in
blocks and its metadata stores those block sizes. Changing the installer
source requires rebuilding the SquashFS payload. The outer ISO can then keep
its existing file record and extent because the patched SquashFS fits within
the original recorded length.

The generator does the SquashFS rebuild on Linux, then xdelta3 computes a
maximum-compression VCDIFF from the exact vanilla ISO to the exact patched ISO.
It decodes that delta against the same vanilla image and compares the
reconstructed length and SHA-256 to the patched output. The Windows user only
applies this already-generated patch; Windows does not extract, edit, or repack
the ISO.

The resulting patch is specific to one base ISO and one pool size. The JSON
manifest records SHA-256 values and byte lengths for the base, VCDIFF, and
patched ISO, as well as the xdelta encoder version and partition size. The
PowerShell script checks those values before and after decoding, stages output
on the target volume, and publishes it without overwriting an existing file.
Changing one byte in the vanilla ISO invalidates the base SHA check. The same
release needs a separate patch for every pool-size choice.

The binary delta is measured on a full ISO pair because the SquashFS rebuild
can change compressed blocks even though the source edit is small. See the
measured input/output/patch sizes and encoder versions in
[`COMPATIBILITY.md`](COMPATIBILITY.md#binary-delta-size-measurement). The
measurement is tied to the exact TrueNAS input, pool size, and xdelta build.
