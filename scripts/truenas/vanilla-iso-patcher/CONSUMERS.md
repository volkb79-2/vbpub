# Use the TrueNAS vanilla ISO patcher

The CLI discovers installer images from both official TrueNAS indexes and
downloads them only after checking the published SHA-256 sidecar. It can also
patch a local vanilla ISO when you provide the checksum you expect. Every
`create` run writes three artifacts:
the patched ISO, an xdelta VCDIFF file, and a `<delta>.json` manifest for the
Windows apply script.

## Install CLI support

From the repository root, install the in-repo CLI library and its optional
interactive prompt driver into the Python environment used to run the script:

```bash
python3 -m pip install -e 'libraries/cli-extended[interactive]'
```

For the ISO rebuild, install `xorriso`, `squashfs-tools` (`unsquashfs` and
`mksquashfs`), and xdelta3 3.x with LZMA support. Run `create` as root so the
rebuilt live filesystem retains ownership, device files, and extended
attributes. `releases`, `browse`, and `download` do not need root.

## Explore and download an upstream ISO

List installer versions with matching `.sha256` sidecar links across the
official direct-download archive and ISO index. Both indexes must be reachable
for the list to be complete. The `--json` output is suitable for scripting;
the CLI fetches and verifies the actual digest when a release is selected:

```bash
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py releases
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py releases --json
```

Interactively choose a release family and subversion, then save and verify its
ISO:

```bash
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py browse
```

For an exact version, use `download`. Supply `--family` if the same version is
present in more than one family:

```bash
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py download \
  --upstream-version 27.0.0-RC.1 \
  --family TrueNAS-27-RC \
  --output ./TrueNAS-27.0.0-RC.1.iso
```

The download is written only after its SHA-256 matches the official sidecar.
The CLI also writes `TrueNAS-27.0.0-RC.1.iso.sha256` beside it.

## Build an ISO and Windows patch from an upstream release

Choose a pool partition size in GiB (16–1024), set explicit output paths, and
run the build with the same Python environment that has `cli-extended`:

```bash
sudo "$(command -v python3)" \
  scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py create \
  --upstream-version 27.0.0-RC.1 \
  --family TrueNAS-27-RC \
  --output-iso ./TrueNAS-27.0.0-RC.1-boot-pool-16GiB.iso \
  --delta-output ./TrueNAS-27.0.0-RC.1-boot-pool-16GiB.vcdiff \
  --boot-pool-size-gib 16 \
  --yes
```

The command downloads the vanilla ISO to temporary storage, verifies its
published checksum, rebuilds the SquashFS, and replaces its existing data
extent in a byte-for-byte copy of the ISO. It creates the maximum-compression
xdelta patch, applies it to a temporary copy, and checks that the reconstructed
ISO hash matches the generator output. It publishes the ISO, delta, and JSON
manifest only after those checks succeed. Existing output paths are refused.
Budget for the measured delta size by flavor: current `bounded` examples are
167–186 KB, while unattended examples are 39–45 MB. The exact numbers depend
on the upstream ISO and build; they are listed in
[compatibility evidence](COMPATIBILITY.md#binary-delta-size-measurement).

## Build an unattended installer flavor

Add `--flavor unattended` to a verified source ISO to make the unattended
entry the default. The original video and serial installer entries remain in
the menu; interrupt GRUB and choose one within five seconds to use the vanilla
manual flow instead.

```bash
sudo "$(command -v python3)" \
  scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py create \
  --upstream-version 25.10.7 \
  --output-iso ./TrueNAS-25.10.7-unattended-16GiB.iso \
  --delta-output ./TrueNAS-25.10.7-unattended-16GiB.vcdiff \
  --boot-pool-size-gib 16 \
  --flavor unattended \
  --yes
```

This ISO installs to one eligible disk or two disks with exactly matching
known model, exact byte capacity, and transport. It excludes the disk containing the
installer media. For one disk, an unknown model is acceptable only when the
kernel inventory and TrueNAS installer API agree on its device name and exact
size. Removable, read-only, mounted, undersized, or inventory-mismatched
targets are unsafe. Before it calls the installer, the helper runs read-only
`zdb -l` label probes on every target disk and its reported partitions and
other block-device descendants. No existing pool labels are allowed except
`boot-pool`; another pool name, missing probe data, or unrecognized output is
unsafe. Three or more disks, no target disk, nonmatching pairs, and pairs with
unknown model or transport also stop before any wipe or partition command,
with the message `unsupported unsafe disk hardware configuration`.

When the check passes, the installer wipes every accepted target without a
confirmation prompt. One disk gets a single-disk boot-pool; two matching disks
get a mirror. The system partition is 16 GiB and the rest of each disk stays
unpartitioned. The new administrator is `truenas_admin` with password
`truenas`. This fixed password is embedded in the ISO and is not suitable for
leaving on an exposed network; change it immediately after first boot.

The single-disk and matching-pair safety decisions have unit coverage. Live
QEMU installation acceptance currently covers a single disk; the managed
runner cannot attach a second disk at launch. See the
[compatibility evidence](COMPATIBILITY.md#experimental-unattended-relaxed-runtime-acceptance)
for the tested releases and this limitation.

The 16 GiB size also leaves limited update headroom. The inspected v28 update
manifest describes about 10 GB of expanded system data. Avoid filling the
boot-pool with user data, containers, VM disks, or cache if you need room for
future system updates. See [why the updater needs this space](DESIGN-GUIDE.md#why-a-v28-update-needs-around-10-gb-of-pool-space).

## Build the separate experimental relaxed flavor

`--flavor unattended-relaxed` runs the pinned boot-pool adapter after the
TrueNAS installer finishes copying the new environment and before the first
reboot. It is limited to `27.0.0-RC.1` and
`28.0.0-MASTER+20261014-230204`; v25 does not have an adapter. Example for the
exact v27 release:

```bash
sudo "$(command -v python3)" \
  scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py create \
  --upstream-version 27.0.0-RC.1 \
  --family TrueNAS-27-RC \
  --output-iso ./TrueNAS-27.0.0-RC.1-unattended-relaxed-16GiB.iso \
  --delta-output ./TrueNAS-27.0.0-RC.1-unattended-relaxed-16GiB.vcdiff \
  --boot-pool-size-gib 16 \
  --flavor unattended-relaxed \
  --post-install-action poweroff \
  --yes
```

The helper checks the installed release and every upstream target-file hash
before it applies the adapter. A mismatch stops before reboot. These adapters
are experimental and still have open acceptance items; use this combined
flavor only on disposable or controlled systems. See the
[adapter design and release pins](DESIGN-GUIDE.md#applying-the-boot-pool-adapter-before-first-reboot).
`--post-install-action poweroff` shuts the guest down after the adapter succeeds
instead of rebooting. Use it when a provider-side workflow must detach its ISO
before powering on the new disk; without that option the unattended flavor
reboots as usual.

## Reproduce the growing-mode overlay PoC

The standalone PoC is separate from the production CLI builder. It needs
`xorriso`, `unsquashfs`, `mksquashfs`, and `xdelta3`, and must run as root so
SquashFS ownership and extended attributes are preserved. This example uses
the pinned v27.0.0-RC.1 ISO and creates an unattended-relaxed 16 GiB image:

```bash
sudo /home/vscode/.venv/bin/python3 \
  scripts/truenas/vanilla-iso-patcher/multisession_overlay_poc.py \
  --input-iso scripts/truenas/vanilla-iso-patcher/artifacts/compat/TrueNAS-27.0.0-RC.1.iso \
  --expected-input-sha256 515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e \
  --output-iso /tmp/TrueNAS-27.0.0-RC.1-unattended-relaxed-overlay.iso \
  --delta-output /tmp/TrueNAS-27.0.0-RC.1-unattended-relaxed-overlay.vcdiff \
  --boot-pool-size-gib 16 \
  --flavor unattended-relaxed \
  --post-install-action poweroff
```

The manifest is written beside the VCDIFF. The builder verifies that the
original live SquashFS remains at the same extent with the same SHA-256, that
the overlay files are visible through the updated ISO tree, and that the
VCDIFF reconstructs the exact output ISO. For measured sizes and hashes, see
the [PoC evidence](COMPATIBILITY.md#experimental-growing-mode-poc). The same
image completed a managed TCG install and booted its installed ZFS root; see
the [VM acceptance record](COMPATIBILITY.md#experimental-growing-mode-overlay-vm-acceptance).
This experimental image has only been explored on the pinned v27 release so
far.

## Build from a local vanilla ISO

Obtain the expected SHA-256 from a trusted checksum source and pass it
explicitly. The generator compares it before extraction and rechecks the input
before use:

```bash
sudo "$(command -v python3)" \
  scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py create \
  --input-iso ./TrueNAS.iso \
  --expected-input-sha256 REPLACE_WITH_64_HEX_DIGITS \
  --output-iso ./TrueNAS-boot-pool-24GiB.iso \
  --delta-output ./TrueNAS-boot-pool-24GiB.vcdiff \
  --boot-pool-size-gib 24 \
  --yes
```

## Apply the VCDIFF patch on Windows

Copy the vanilla ISO, `.vcdiff` file, matching `.vcdiff.json` manifest, and
`windows/Apply-TrueNASIsoPatch.ps1` to the Windows machine. Install the xdelta3
Windows executable whose version is written in the manifest. Put `xdelta3.exe`
beside the PowerShell script or make it available on `PATH`.

Open PowerShell 5.1 or later and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Apply-TrueNASIsoPatch.ps1 `
  -VanillaIso .\TrueNAS-27.0.0-RC.1.iso `
  -Delta .\TrueNAS-27.0.0-RC.1-boot-pool-16GiB.vcdiff `
  -OutputIso .\TrueNAS-27.0.0-RC.1-boot-pool-16GiB.iso `
  -Verbose
```

The manifest path defaults to `<delta>.json`; pass `-ManifestPath` to use a
different path. The script checks Windows and PowerShell versions, required
files, manifest schema and values, input/output path collisions, existing
output files, available space, xdelta version and LZMA support, source length
and hash, patch length and hash, and reconstructed output length and hash. It
writes into a temporary directory on the destination volume and publishes the
ISO only after verification. It never overwrites an existing file.

## Install and verify

For `bounded`, boot the ISO and install normally. For an unattended flavor,
the default GRUB entry starts after five seconds and installs without a
confirmation prompt. Choose an original video or serial entry during that
timeout to keep the regular installer flow. On an unsafe disk topology the
unattended helper prints `unsupported unsafe disk hardware configuration` and
stops before wiping. Partition 3 gets the configured size and the remaining
tail stays unpartitioned; the ISO does not create a second partition or pool.
The destination disk must be larger than the configured partition so the
installer can also create BIOS/EFI partitions and GPT metadata.

After installation, inspect the actual GPT before using the remaining space:

```bash
sudo sgdisk -p /dev/sda
sudo sgdisk -i 3 /dev/sda
sudo sgdisk -v /dev/sda
```

The standard TrueNAS UI expects whole disks for pool creation, so the
unpartitioned tail is not automatically available as a second pool. See the
[source-layout design](DESIGN-GUIDE.md#installer-iso-structure-and-bounded-partition-change),
[checksum design](DESIGN-GUIDE.md#upstream-catalog-and-checksum-gate), and
[delta design](DESIGN-GUIDE.md#exact-base-vcdiff-patches).
