# Compatibility evidence

## Upstream catalog coverage observed on 2026-10-17

The CLI reads both [TrueNAS direct downloads](https://download.sys.truenas.net/)
and the [TrueNAS ISO index](https://iso.sys.truenas.net/). Each catalog had 250
installer ISO entries paired with `.sha256` links and accepted by the
patcher's filename checks. The CLI verifies the selected sidecar contents
before downloading. Their version coverage was different:

| Catalog | v25 | v26 | v27 | v28 | Unique images |
|---|---:|---:|---:|---:|---:|
| `download.sys.truenas.net` | 40 | 193 | 17 | 0 | 250 |
| `iso.sys.truenas.net` | 0 | 133 | 103 | 14 | 250 |
| Merged after duplicate family/version/filename removal | 40 | 193 | 103 | 14 | 350 |

The CLI requires both catalogs to be reachable and reports an error rather
than silently presenting an incomplete release list. Counts are a discovery
snapshot; upstream nightly indexes change over time.

The patcher intentionally matches a small installer-source structure rather
than a version string. These tags were inspected directly in
`truenas/truenas-installer`; the v28 row was extracted from the exact ISO
listed below:

| TrueNAS release | Upstream source reference | SHA-256 of `truenas_installer/install.py` | Partition allocation |
|---|---|---|---|
| 25.04.2.6 | [`TS-25.04.2.6`](https://github.com/truenas/truenas-installer/tree/390ee5aa69096cd35cbb11420dbc7b448595ca03) | `2be17bbd17fd37aec48f9c0ea4cf7a3323a3d83ada4b1202c972af94023e5672` | `sgdisk -n3:0:0 -t3:BF01` |
| 25.10.7 | [`TS-25.10.7`](https://github.com/truenas/truenas-installer/tree/f66c7830bc511f3dc516ae0c5097662f5ed4d27b) | `2be17bbd17fd37aec48f9c0ea4cf7a3323a3d83ada4b1202c972af94023e5672` | `sgdisk -n3:0:0 -t3:BF01` |
| 27.0.0-RC.1 | [`TS-27.0.0-RC.1`](https://github.com/truenas/truenas-installer/tree/cdf7df94963918013ce74af418bacc9b10298157) | `bb9f07d3ada5b2117eb8d917e5775afed17c0ab62977b7a942465ddfcdeb8446` | `sgdisk -n3:0:0 -t3:BF01` |
| 28.0.0-MASTER+20261014-230204 | Installer source extracted from the ISO; source commit not independently resolved | `bb9f07d3ada5b2117eb8d917e5775afed17c0ab62977b7a942465ddfcdeb8446` | `sgdisk -n3:0:0 -t3:BF01` |

## In-place ISO build checks

All three inputs were verified against their official SHA-256 sidecars. The
patched ISO keeps the original byte length, BIOS and UEFI El Torito entries,
and every byte outside the `/live/filesystem.squashfs` file record. `sgdisk -v`
reported no GPT problems for each output.

| ISO release | Input ISO SHA-256 | Patched 16 GiB ISO SHA-256 | Source and patched ISO bytes |
|---|---|---|---|
| 25.10.7 | `54ce9441ce66966a392e28f63604ca3c2c083d0bec4db7bb5af2f74f7a007c8e` | `1dcda3e1fadb587b632c31516a5049a7b40c8e6d026e57e4c0507fd3b15a6cd9` | `2,339,317,760` |
| 27.0.0-RC.1 | `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e` | `012ad365859a3cfc6ea8d2950f1469447f6cbcbf205117db652b13b3077debce` | `2,683,439,104` |
| 28.0.0-MASTER+20261014-230204 | `9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a` | `d22c123f5d35376ad167ca89e71ea7ba68404e554b8a88c9379c81e0a5a36284` | `2,727,788,544` |

The 25.10.7 output came from the CLI's local-ISO path. The 28.0.0 nightly was
downloaded through `iso.sys.truenas.net` and SHA-256 verified before the CLI
build. The 27.0.0-RC.1 output was rebuilt through the same `patch_iso` path.
These are ISO build checks; they do not certify installation on every release.
The 25.04.2.6 entry above is source-level evidence only.

## Unattended flavor build verification

The following artifacts were rebuilt after the disk-safety rule was refined
to allow one inventory-matched disk whose device model is not reported by the
kernel and after the installed `/etc` and `/usr` ZFS datasets were validated
against the v28 layout. Each input hash was pinned before use. The generator
also applied its VCDIFF against that exact input and verified the reconstructed
target hash.

| Release | Flavor | Input SHA-256 | Patched ISO SHA-256 | Delta bytes | Delta SHA-256 |
|---|---|---|---|---:|---|
| 25.10.7 | `unattended` | `54ce9441ce66966a392e28f63604ca3c2c083d0bec4db7bb5af2f74f7a007c8e` | `ce3003c746d8c7fb62273fefed3fc3b7ef2b2b99b1a6d11ec6c24e3264206e56` | `44,799,066` | `3f39a741291faee946903e091388e7103eae1fffd2db66c94aacc7df500c703c` |
| 27.0.0-RC.1 | `unattended-relaxed` | `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e` | `eb1639344107d3fdf5c19d689e87a362b919f6daac45a2507cff5b05aa1d7063` | `39,497,116` | `2ff86bb5402bc7fbd6b569b96df8156fe2bef2fc1e734de86b9c2ba9cd63daa7` |
| 28.0.0-MASTER+20261014-230204 | `unattended-relaxed` | `9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a` | `cad3ff093accab316c36af2bb37dd6f27acb9d1c3047a99516cd52790a7ffc54` | `39,765,384` | `409a34f7e28facb8a34db55c5e7d850c98fea9eb58abaa5ff83a722e2783be66` |

These measurements apply to the exact source images and `xdelta3 3.0.11`;
they are not a promise about other nightly builds. Runtime acceptance is
recorded below separately from these deterministic build checks.

## Binary delta size measurement

The CLI generated whole-ISO VCDIFF patches with `xdelta3 -9 -S lzma`, then
decoded each patch against the exact source ISO and checked the reconstructed
SHA-256 against the target ISO:

| TrueNAS ISO flavor | Full ISO bytes | VCDIFF bytes | Patch as % of ISO | xdelta3 |
|---|---:|---:|---:|---|
| 25.10.7 bounded | `2,339,317,760` | `167,422` | `0.0072%` | `3.2.2` |
| 28.0.0-MASTER+20261014-230204 bounded | `2,727,788,544` | `186,252` | `0.0068%` | `3.0.11` |
| 25.10.7 unattended | `2,339,317,760` | `44,799,066` | `1.915%` | `3.0.11` |
| 27.0.0-RC.1 unattended-relaxed | `2,683,439,104` | `39,497,116` | `1.472%` | `3.0.11` |
| 28.0.0-MASTER+20261014-230204 unattended-relaxed | `2,727,788,544` | `39,765,384` | `1.458%` | `3.0.11` |

The v28 patch's JSON manifest is created beside the VCDIFF and records the
exact base, target, patch lengths and hashes, pool size, and xdelta version.
The Windows apply script was smoke-tested against that manifest and patch
using PowerShell 7.6.6 on Linux with its Windows-platform preflight enabled;
this is a cross-platform smoke test, not a run on a Windows host. It verified
the base, patch, and reconstructed output hashes and published an ISO with the
target SHA-256 above.

## Experimental growing-mode PoC

The standalone `multisession_overlay_poc.py` keeps the original live SquashFS
extent intact and adds a 24,576-byte SquashFS overlay with the unattended
installer changes. It was built from the checksum-pinned v27.0.0-RC.1 ISO.
The output's updated tree resolves the overlay and `filesystem.module`; the
BIOS and UEFI El Torito entries remain present. xorriso 1.5.6 reports the
regular-file image as one session, so this result does not use a second
ISO 9660 multisession track.

| Item | Value |
|---|---|
| Input ISO SHA-256 | `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e` |
| Output ISO SHA-256 | `8220a26fce4e263f93d3b813010929151813649a4df23de3a6b50a77b4a5c7f9` |
| ISO bytes, input → output | `2,683,439,104` → `2,684,026,880` |
| Appended bytes | `587,776` |
| Original live SquashFS bytes / SHA-256 | `294,522,880` / `17b3ad5f6fddeffc69b795637d9ea25fbe7289951ed5111fbce56676edfabb0c` |
| Overlay SquashFS bytes / SHA-256 | `24,576` / `06cc605c606059d19ad43f11541beab4110728ef8ef45dca9d38953d602d0b72` |
| xorriso `-toc` summary | `1 session, 1310403 data blocks` |
| xdelta3 VCDIFF bytes / SHA-256 | `50,601` / `d12c29b545c07196ca7abcd163b154ed522d8a68f782b0e7f813fe0a8cfd96f4` |
| VCDIFF as a fraction of input ISO | `0.0019%` |

The generated delta was decoded against the exact input ISO and its complete
output SHA-256 matched the target. Compared with the full-repack
unattended-relaxed v27 delta (`39,497,116` bytes), this PoC delta is about 780
times smaller. This is a build and delta result; live installation status is
recorded in the runtime acceptance section below.

The first VM boot safely stopped before disk formatting because `zdb -l` on
the blank QEMU `vda` returned status 2 with exactly four lines, `failed to
unpack label 0` through `failed to unpack label 3`. The parser originally
accepted that exact output only with statuses 0 or 1. It now accepts the same
four-line result with status 2 as well; unrecognized output and other errors
still refuse unattended installation. The rebuilt artifact above includes
this correction.

## Live installation acceptance

The rebuilt 25.10.7 `unattended` ISO (`119b5d09…6707cd107`) booted under
QEMU TCG with UEFI and a blank 20 GiB qcow2 disk. GRUB selected the unattended
entry automatically after five seconds. The runtime accepted the single
`vda` disk, passed `truenas_admin` / `truenas` to the installer, and the stock
installer reported success. The VM rebooted into the installed
`boot-pool/ROOT/25.10.7` kernel/root environment; the VM was stopped while
TrueNAS middleware was still completing first-boot initialization.

The installed disk image reported:

- GPT disk size: 20.0 GiB (41,943,040 sectors);
- partition 3: 16.0 GiB (33,554,432 sectors), type BF01, at sectors 1,054,720–34,609,151;
- free space: 7,337,917 sectors (3.5 GiB), in two segments;
- `sgdisk -v`: no GPT problems.

The accepted disk is a QEMU virtio device for which both inventories reported
no model name. This is why the single-disk guard permits an unknown model only
when the kernel and installer agree on device name and exact byte size. The
two-disk mirror rule still requires known matching model, capacity, and
transport.

The current in-place 25.10.7 ISO (`1dcda3e1…b15a6cd9`) completed the regular
installer under QEMU TCG using UEFI firmware and a blank 20 GiB qcow2 disk. The
operator selected the disk and chose Web UI authentication; the installer
reported success.

After the VM powered off, the installed virtual disk reported:

- GPT disk size: 20.0 GiB (41,943,040 sectors);
- partition 3: 16.0 GiB (33,554,432 sectors), type BF01, at sectors 1,054,720–34,609,151;
- free space: 7,337,917 sectors (3.5 GiB), in two segments;
- `sgdisk -v`: no GPT problems.

The acceptance ended after installer success and GPT verification; the
installed system was not rebooted. During shutdown, the live installer logged
warnings while unmounting its ISO loop device, then powered down.

An earlier remastered v28 ISO from the same base also completed installation
under QEMU TCG; its output SHA-256 was
`3fcb9bcdee4b597f1498f019890c1735fb4c68b55fcc94ed1d98fc40d9fd09d1`. That
acceptance predates the in-place ISO builder and does not certify the current
in-place v28 output. The 25.04.2.6 image has source-level evidence only.

## Experimental unattended-relaxed runtime acceptance

The v27 `unattended-relaxed` ISO
(`eb1639344107d3fdf5c19d689e87a362b919f6daac45a2507cff5b05aa1d7063`) booted
under QEMU TCG with UEFI and a blank 20 GiB qcow2 disk. GRUB selected the
unattended entry after five seconds. The guard accepted one inventory-matched
`vda`, the installer reported the 16 GiB boot-pool partition and fixed
`truenas_admin` / `truenas` credentials, and the stock installation completed.
Before reboot, the helper reported that it applied the pinned v27 adapter to
`boot-pool/ROOT/27.0.0-RC.1`. The installed kernel booted and reached
`getty.target`. In this isolated TCG guest, first-boot `ix-etc`, chrony,
nginx, and syslog-ng startup failed or timed out; this run certifies the
installer, adapter transaction, kernel boot, and disk layout, not those
network-dependent first-boot services.

The installed v27 disk reported a 20.0 GiB GPT, partition 3 at 16.0 GiB
(33,554,432 sectors), 3.5 GiB free in the remaining tail, and no errors from
`sgdisk -v`.

The v28 `unattended-relaxed` ISO
(`cad3ff093accab316c36af2bb37dd6f27acb9d1c3047a99516cd52790a7ffc54`) also
completed installation under QEMU TCG with UEFI and the same 20 GiB blank-disk
profile. It selected the unattended entry automatically, accepted one `vda`,
and reported successful installation. Before reboot, the helper reported
applying the v28-nightly-20261014 adapter to
`boot-pool/ROOT/28.0.0-MASTER-20261014-230204`; the installed kernel then
started from that root environment. The run did not certify all first-boot
middleware services.

Both runs used the repository VM runner in `dev-background.slice` with one
guest active at a time, a 2-vCPU cap, and a 3 GiB guest-memory cap. The exact
ISO and delta hashes above refer to these rebuilt outputs; the 27 and 28
adapters remain experimental and pinned to these exact releases.

These live runs exercised one target disk. The unit suite covers selection of
one disk, a matching two-disk mirror pair, and unsafe or mismatched inventories;
the runtime passes the approved disk list to the regular TrueNAS installer.
The current managed TCG runner attaches one disk at launch, and its Q35 guest
cannot hot-plug another virtio disk, so mirrored-pool creation is not yet live
QEMU-certified.

The version boundary is the source shape that the patcher validates:

- ISO file `/live/filesystem.squashfs`;
- module `usr/lib/python3/dist-packages/truenas_installer/install.py` inside
  that SquashFS;
- exactly one `run([...])` call containing `sgdisk`, `-n3:0:0`, and
  `-t3:BF01`.

If one of those assumptions changes, the patcher refuses the image. Re-check
the source and boot metadata before extending support to a new ISO build.

## Experimental growing-mode overlay VM acceptance

The corrected v27.0.0-RC.1 growing-mode PoC ISO
(`8220a26fce4e263f93d3b813010929151813649a4df23de3a6b50a77b4a5c7f9`) booted
in the managed UEFI QEMU TCG runner from `dev-background.slice`. After the
five-second timeout, GRUB selected the unattended entry. The read-only ZFS
label scan classified the blank `vda` as empty, the guard accepted that one
20 GiB disk, and the stock installer completed. Before reboot, the runtime
reported applying the experimental v27 boot-pool adapter to
`boot-pool/ROOT/27.0.0-RC.1`.

The installed disk image was converted from its preserved qcow2 overlay for
inspection. `sgdisk -p` reported a 20.0 GiB disk with a 16.0 GiB ZFS partition
3 (sectors 1,054,720–34,609,151) and 3.5 GiB free in the remaining tail;
`sgdisk -v` found no GPT problems. The installed kernel then booted with
`boot-pool/ROOT/27.0.0-RC.1` as root, loaded ZFS v2.4.4-1, imported
`boot-pool`, and reached `getty.target`. First-boot `ix-etc` was slow, and
middleware/network readiness was not established in this TCG run. The VM was
stopped cleanly after kernel and ZFS-root boot were confirmed.

An initial run with the preceding PoC artifact safely stopped before
formatting: blank-device `zdb -l` returned status 2 with the exact four known
`failed to unpack label 0` through `failed to unpack label 3` lines. The guard
was corrected to accept that specific blank-device result, and the rebuilt ISO
above passed the same pre-wipe check. The unit suite verifies that an existing
`boot-pool` label is permitted and another pool name is refused. The live disk
was blank, so this VM does not prove preservation of an existing data pool.
The VM runner currently attaches only one target disk; mirror creation and
the equal-byte-size refusal are covered by unit tests, not a live mirrored
installation.

## Poweroff and no-eject handoff acceptance

The provider-install PoC was rebuilt from the same vanilla v27.0.0-RC.1 ISO
with the unattended `poweroff` action and Debian live-boot `noeject` option.
The input ISO SHA-256 is
`515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`; the
resulting ISO SHA-256 is
`9aeb78b2e6dcae0700f7bb848eeea732d8900fc5bbe16cbb9ee27ae0624eb13d`. Its
VCDIFF is 50,701 bytes (SHA-256
`53135184605df21f35c87cbe8df0dc759304385371556041035176e6893f065a`), against
a 2,684,026,880-byte patched ISO.

The image installed in the managed UEFI QEMU TCG guest in
`dev-background.slice`. The serial log reported successful installation,
applied the experimental adapter to `boot-pool/ROOT/27.0.0-RC.1`, then ran
`systemctl poweroff`. QEMU exited with code 0 and no OOM kill. Shutdown logged
a failed unmount of the live medium and ignored busy loop devices, then
completed with `reboot: Power down`; it did not pause for a media-removal
prompt. `sgdisk -p` on the installed 20 GiB disk showed the 16 GiB `BF01`
partition 3 (sectors 1,054,720–34,609,151) and 3.5 GiB free at the disk tail;
`sgdisk -v` found no GPT problems.

This acceptance proves the unattended install, adapter application, selected
boot-pool size, and guest-poweroff handoff under TCG. Provider-side media
detachment and a fresh disk boot are exercised separately by the Netcup
generic `install` workflow; UI credential acceptance is checked on the live
Netcup install.

On 2026-10-18, the fresh install on Netcup server 804027
(`v1001.vxxu.de`) passed live UI acceptance through PWMCP. The HTTPS UI loaded
after the managed browser ignored the server certificate errors. Authentication
reached `https://v1001.vxxu.de/ui/dashboard`, titled `TrueNAS - v1001.vxxu.de`;
the sign-in fields were absent, and the dashboard's System Information showed
Generic platform, Community edition, and version `27.0.0-RC.1`. The Netcup
installer checkpoint was complete, with the server `RUNNING`, provider ISO
detached, and boot order restored to `HDD,NETWORK,CDROM`. Credentials are not
recorded here.
