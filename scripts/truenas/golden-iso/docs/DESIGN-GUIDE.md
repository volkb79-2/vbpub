# Golden-image and restore design

## Golden disk restore ISO

The bare-metal restore path starts from a configured TrueNAS whole-disk raw
image. A Debian Live ISO provides the boot environment; the image is compressed
with a Zstandard frame checksum and shipped inside the ISO. The builder verifies
the Debian base ISO against its signed SHA-512 list, customizes the live
filesystem with the restore service and its tools, and replays the original
BIOS/UEFI hybrid boot metadata with xorriso.

Changing an ISO's GRUB menu is a valid way to choose a different boot program,
but the menu does not supply the restore program's Python runtime, storage
utilities, TLS client, system service manager, or required packages. Debian
netinst can be repurposed by replacing its boot configuration and supplying a
custom initrd or live root containing those dependencies; that couples this
workflow to Debian Installer's initrd and package setup. We use Debian Live
because its booted root filesystem can be customized directly and runs the
restore service as a normal systemd unit. The builder sets a five-second GRUB
timeout and verifies the first entry is the Live system, so it proceeds without
console input. A smaller `live-build` package list is a reasonable future size
optimization; it must retain every runtime dependency and the service boot
path. See Debian's [live image package customization
guide](https://live-team.pages.debian.net/live-manual/html/live-manual/customizing-package-installation.en.html)
and [Debian installer's netinst description](https://www.debian.org/releases/stable/amd64/install.en.pdf).

At boot, the service reports its discovery and decisions to Mattermost. It
excludes the live-media device and removable/read-only disks, then requires
exactly one other writable, unmounted whole disk. It checks the target capacity
and logical sector size before writing. It validates the compressed stream
before the write, hashes the decompressed stream as it is written, and reads
the restored image back for a second SHA-256 check.

The restore writes exactly the raw image's byte length. On a larger target,
sgdisk relocates the backup GPT header and updates GPT location metadata. The
installer compares all partition starts, ends, types, and unique GUIDs before
and after that repair, so the partition entries stay unchanged. The ZFS
partition stays at the captured size. The
service checks the EFI fallback loader, creates or reuses a firmware entry,
sets BootNext, and reboots. If firmware boots the still-attached ISO again, the
service skips another disk write only when both the GPT layout and a restore
receipt on the EFI partition match the currently bundled image. The receipt
contains the raw image SHA-256, image size, disk GUID, and EFI partition GUID;
it is written only after the streamed write, full read-back hash, and GPT
layout verification succeed. A missing or mismatched receipt causes a full
restore, even when the GPT identifiers match. This matters when a newer golden
image changes system data, such as adding an API key, while preserving the same
partition table. The receipt is a small file under
`EFI/TRUENAS-RESTORE/manifest.json`; it does not change partition geometry or
grow the ZFS partition.

EFI partition discovery asks `lsblk` for `--tree` explicitly. On Debian Live,
JSON output with only `PATH` and partition columns otherwise flattens the disk
and its partitions into separate top-level rows. The v5 runtime then rejected
a valid single disk before it could write the receipt; a regression test keeps
that observed output shape covered.

The sibling TOML secret file stores the TrueNAS admin password, root Ed25519
private/public key, Mattermost webhook, and one-time cleartext API key with
mode 0600. The builder reads the password, SSH material, and webhook; it does
not copy the cleartext API key or root private key into the ISO. The ISO does
embed the complete disk, including TrueNAS's stored API-key record, password
hash, root public key, SSH host keys, and machine identity. It also embeds the
live notification webhook, so the image, sidecar, and ISO must all be handled
as private artifacts. This workflow restores the disk as captured; it does not
rotate those values.

The ISO does not contain Netcup API credentials and does not call the provider
API. A guest cannot safely assume the provider's virtual-media attachment can
be detached by a local `eject`; the controller uses the Netcup CLI to detach
the ISO and restore the saved HDD-first boot order. After media or boot-order
changes, Netcup can leave `serverLiveInfo.configChanged` true and show a
**Reboot required** advisory. The v1001 test initially displayed UEFI setup;
Continue reached the TrueNAS GRUB menu, proving the disk image and boot entry
were usable. A provider cold power cycle cleared the pending-change flag. The
next boot started from the disk without console input, and SSH, middleware,
the boot pool, and API queries all worked. DHCP also worked on that host; no
static network adjustment was needed.

Follow-up tests found that guest `CDROMEJECT` does not detach the SCP ISO, but
an attached ISO does not prevent a disk boot while provider order is HDD-first.
A later v4 replay appeared to take the same path, but it had not restored the
new image: the v4 runtime treated matching GPT identifiers as proof of matching
disk contents, although the newer image included a newly captured API key. The
current runtime requires the image-specific EFI receipt described above. See
the detailed
[incident follow-up](CONSUMERS.md#boot-order-incident-follow-up-2026-10-15)
and [consumer procedure](CONSUMERS.md)
for commands to attach/detach the ISO, handle pending Netcup configuration,
and verify disk boot.

Step 1 starts shutdown through a nonblocking systemd job because a direct
`shutdown` call can outlive its SSH session while TrueNAS stops networking.
It waits for `system.ready` before using middleware, because root SSH can
become available before the API handshake is ready.
After the one-time API key is atomically saved, a powered-off source keeps that
matching sidecar even if disk capture must be retried. A key record with no
saved cleartext secret is refused by default; `--revoke-orphan-api-key` is an
explicit recovery action limited to one key with the reserved golden-image
name, so normal retries never delete an existing credential silently.
Password-update JSON uses `midclt`'s stdin payload form to avoid exposing the
admin password in either the local SSH command line or the guest process list.
