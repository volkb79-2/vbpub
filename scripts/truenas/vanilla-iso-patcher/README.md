# TrueNAS vanilla ISO patcher

This CLI builds TrueNAS installer ISO flavors with a configurable `boot-pool`
partition. The default `bounded` flavor keeps the ordinary installer flow and
leaves the rest of each selected disk unpartitioned. `unattended` adds a
five-second default boot entry that installs to one safe disk or an exact-size,
matching two-disk mirror. Before it wipes, the helper reads ZFS labels from the
target disks and partitions; it permits no existing pool or `boot-pool`, and
stops if it finds another pool or cannot classify a label. The experimental
`unattended-relaxed` flavor also applies a
release-pinned boot-pool adapter after installation and before reboot. It is
available only for the listed v27 and v28 builds. The unattended flavors wipe
the target disks without asking and set the initial `truenas_admin` password
to `truenas`; see the [consumer guide](CONSUMERS.md) before building one.

The CLI can explore official upstream versions, verify a selected ISO against
its published SHA-256, patch a downloaded or checksum-pinned local ISO, and
emit an xdelta VCDIFF patch for Windows users.

The command surface is built with `cli-extended`. Production CLI flavors patch the
installer source embedded in `/live/filesystem.squashfs` and replace that
existing ISO data extent in place. The unattended flavors also add a small
systemd dispatcher and patch both BIOS and UEFI GRUB configs in place; the two
vanilla installer entries remain selectable. The generator validates the
installer API, boot layout, and exact adapter release before writing output.
The ISO is not remastered. See the [layout and design guide](DESIGN-GUIDE.md)
for the exact source changes and compatibility boundaries.

Start with upstream release exploration:

```bash
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py releases
python3 scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py browse
```

`releases` merges installer ISOs with `.sha256` sidecar links in the official
[direct-download archive](https://download.sys.truenas.net/) and
[ISO index](https://iso.sys.truenas.net/). These indexes have different
coverage: the direct-download archive includes older v25 installers, and the
ISO index currently includes v28 nightlies. Discovery requires both indexes
to be readable so a temporary outage cannot silently hide a release. `browse`
interactively chooses a release family and subversion, then downloads the ISO
and verifies its official `.sha256` file. For non-interactive use, give the
exact upstream version to `download` or `create`.

The generated VCDIFF patch is tied to the exact source ISO. A neighboring
`<delta>.json` manifest records source, patch and output hashes and sizes plus
the xdelta version. The PowerShell consumer verifies all three files before
and after reconstruction. See the [Windows apply script](windows/Apply-TrueNASIsoPatch.ps1),
the [consumer guide](CONSUMERS.md), and the
[design guide](DESIGN-GUIDE.md#exact-base-vcdiff-patches).

Current measurements range from about 0.17–0.19 MB for the `bounded` flavor
to about 39–45 MB for unattended flavors. The difference comes from rebuilding
the live SquashFS with the unattended runtime and, for `unattended-relaxed`,
the release adapter; it is not the size of the adapter by itself. See the
[design guide](DESIGN-GUIDE.md#exact-base-vcdiff-patches) and
[compatibility evidence](COMPATIBILITY.md) for exact measurements. Delta size
depends on the exact upstream ISO and generator build; regenerate and measure
it for every image and boot-pool size you publish.

The separate [`multisession_overlay_poc.py`](multisession_overlay_poc.py)
experiment preserves the original SquashFS extent and adds a small overlay
SquashFS instead. It is not a supported CLI flavor yet. The v27 PoC measured a
50,601-byte VCDIFF against 39,497,116 bytes for the full-repack ISO of the same
release and flavor. xorriso reports the regular-file image as one session, so
this is not an ISO 9660 multisession-track implementation. See the
[layout comparison](DESIGN-GUIDE.md#experimental-growing-mode-squashfs-overlay)
for the measurement, [VM installation evidence](COMPATIBILITY.md#experimental-growing-mode-overlay-vm-acceptance),
and [reproduction command](CONSUMERS.md#reproduce-the-growing-mode-overlay-poc).

For provider-managed unattended installation, pass
`--post-install-action poweroff`. The default unattended action remains a
reboot. The unattended GRUB entry includes Debian live-boot's `noeject` kernel
option so the guest does not pause at the remove-media prompt; the selected
poweroff action lets the Netcup workflow detach its provider ISO before
booting the installed disk. See the
[Netcup install guide](../../netcup/CONSUMERS.md#install-from-a-generic-iso).
