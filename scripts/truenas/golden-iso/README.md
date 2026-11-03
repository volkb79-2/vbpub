# TrueNAS golden-image and restore workflow

This subproject prepares a configured TrueNAS 27+ source disk, captures its
whole disk as a golden image, and builds a Debian Live ISO that restores that
image unattended. The restore preserves the captured partition sizes: it does
not grow the ZFS partition or pool. The builder keeps the administrator
password, root Ed25519 key, webhook, and one-time API key in a mode-0600 TOML
sidecar beside the image; the ISO contains the full disk and notification
webhook, so both remain private artifacts.

Start with a manual vanilla TrueNAS installation on a source VM. The workflow
has three scripts:

1. [`step1-install-prepare-truenas.py`](step1-install-prepare-truenas.py)
   configures the installed source VM, creates and validates its API key,
   shuts it down, and captures a verified raw whole-disk image.
2. [`step2-build-unattended-restore-iso.py`](step2-build-unattended-restore-iso.py)
   validates a Debian Live base ISO, embeds the golden disk and restore runtime,
   and produces the unattended restore ISO.
3. [`step3-apply-restored-preseed.py`](step3-apply-restored-preseed.py)
   reapplies a protected administrator password and root-key SSH settings to a
   restored host over pinned SSH. It sends the password through middleware
   stdin, not command arguments; see the [design guide](docs/DESIGN-GUIDE.md#post-restore-preseed)
   and [consumer procedure](docs/CONSUMERS.md#apply-the-post-restore-preseed).

The captured QCOW2 is the size authority: step 1 accepts whole-GiB source disks
of at least 16 GiB and captures that exact virtual size. Its preferred source,
raw-image, and sidecar names use 20 GiB. Step 2 names the compressed payload
from the recorded image size, and restore writes exactly that many bytes. It
does not resize the captured ZFS partition or boot pool.

During its Debian Live package-install phase, step 2 temporarily copies the
build environment's `/etc/resolv.conf` into the chroot so `apt` can resolve
Debian mirrors. It restores the signed base image's original resolver entry
before repacking the Live filesystem. See the [design guide](docs/DESIGN-GUIDE.md#chroot-package-networking)
for the reason and the [consumer guide](docs/CONSUMERS.md#build-the-unattended-restore-iso)
for the build procedure.

Use the [consumer guide](docs/CONSUMERS.md) for the tested manual installation,
source preparation, ISO build, QEMU replay, and Netcup install commands. The
[design guide](docs/DESIGN-GUIDE.md) records restore boundaries, safeguards,
and unresolved compatibility evidence. The workflow is separate from the
[boot-pool patch](../boot-pool-patch/README.md): a restored host can be patched
after boot, while patching the source before capture requires a manual step;
the golden-image scripts do not invoke the patch workflow.

Credential rule (TN-03): no credential is chosen or documented by hand. Step 1
generates a random `truenas_admin` password and a dynamic API key at every
capture and stores both in the mode-0600 gitignored sidecar (0700 directory);
the public bootstrap password is never accepted as a final value. Scripts print
only sidecar paths, never values.

Capture ID: every capture gets an 8-hex-digit ID (`secrets.token_hex(4)`). It
is part of both file names, `truenas-27-20GiB-golden-<id>.raw` and
`truenas-27-20GiB-golden-<id>.secrets.toml`, is recorded inside the sidecar
(`capture_id`) and in the restore ISO manifest, and is what ties an image to its
secrets. Step 2 `build` locates the sidecar next to the image by that ID and
refuses a sidecar whose file-name ID or recorded ID differs from the image's.
Name the ISO `truenas-27-20GiB-unattended-restore-<id>.iso` so the Netcup
restore controller can show the ID.

A restore is unattended and does not rotate anything: the restored host keeps
the golden image's secrets, and rotating them is the user's job after the
restore.

## Secrets baked into a golden ISO / what to change after a restore

The golden disk is restored byte for byte, so these secrets travel with the
image (`...-<id>.raw`) and with every restore ISO built from it. Treat both as
private, credential-bearing artifacts; never publish, share, or attach them.

| Secret baked in | Where it lives in the image/ISO | Cleartext |
|---|---|---|
| `truenas_admin` password (hash) | TrueNAS config database on the disk | sidecar `[truenas_admin].password` |
| API key record (`truenas-27-20GiB-golden-image`) | TrueNAS config database on the disk | sidecar `[truenas_api].api_key` |
| root SSH authorized key (public half) | root user record on the disk | sidecar `[root_ssh].public_key`; private half `[root_ssh].private_key` |
| SSH host keys | `/etc/ssh` on the disk | not stored (pin via known_hosts) |
| machine-id / system identity | `/etc/machine-id` and TrueNAS system GUID on the disk | not stored |
| TrueNAS default TLS certificate and private key (UI/API) | TrueNAS config database on the disk | not stored |
| Mattermost webhook URL (token) | restore ISO only: `/etc/truenas-unattended-restore/runtime.json` (mode 0600 inside the Live root) | sidecar `[mattermost].webhook_url` |

Cleartext values exist only in the mode-0600 sidecar
`truenas-27-20GiB-golden-<id>.secrets.toml` (0700 `artifacts/` directory,
gitignored). The 8-hex ID in the file name identifies the pair. Nothing else
holds them: scripts never print them and tracked files never contain them.

Before the restored host is exposed to a network that you do not fully control,
rotate everything below (the restored host is reachable with the baked-in
secrets until you do). Run the commands over root SSH with the key from the
sidecar, as `root` on the host:

1. Admin password: pick a new random value and set it, then record it in your
   own password store.
   `midclt call user.update "$(midclt call user.query '[["username","=","truenas_admin"]]' | jq '.[0].id')" "{\"password\": \"$NEW\"}"`
   (or UI: Credentials > Users > truenas_admin > Edit).
2. API key: `midclt call api_key.query` to list, `midclt call api_key.delete <id>`
   for the golden key, then
   `midclt call api_key.create '{"name":"<name>","username":"truenas_admin"}'`
   (the returned `key` is shown once; or UI: top-right user menu > API Keys).
3. Root SSH key: replace the authorized key with your own public key,
   `midclt call user.update <root-id> '{"sshpubkey": "<your ssh-ed25519 line>"}'`,
   verify a login with the new key, and stop using the sidecar's private key.
4. SSH host keys: `rm -f /etc/ssh/ssh_host_*` (exact names only), then
   `ssh-keygen -A && midclt call service.control RESTART ssh`;
   update your known_hosts pins afterwards.
5. Machine identity (only if the host should be distinct from other restores of
   the same image): `rm -f /etc/machine-id && systemd-machine-id-setup` and
   reboot.
6. TLS certificate: generate a new certificate in the UI (Credentials >
   Certificates) and select it as the UI certificate (System > General), or
   `midclt call system.general.update '{"ui_certificate": <new-cert-id>}'`.
7. Mattermost webhook: revoke the golden webhook in Mattermost (Integrations >
   Incoming Webhooks) after the last restore that uses the ISO; build a new ISO
   with a new webhook for later restores.

A first-boot helper script that runs this checklist is optional (TN-14).

## Current artifact status

The 2026-10-31 rebuild installed vanilla TrueNAS `27.0.0-RC.1` on a 20 GiB
source disk and captured a 20 GiB raw image. The source installer SHA-256 is
`515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`; the raw
image SHA-256 is `721a8b22698f7be66d853c3719351849dacfc59271d023734a846615ebc6b1d7`.
At first boot `boot-pool` was 19 GiB with 15.1 GiB free. Step 1 completed and
the sidecar is mode 0600. The TCG admission guard refused three starts under
high memory PSI; the fourth start passed after pressure subsided. The guard
was not bypassed.

Step 2 verified the signed Debian Live 13.7.0 base and installed `gdisk`,
`efibootmgr`, and `eject` in the Debian chroot after temporarily supplying the
builder resolver. The resolver entry was restored before repacking; extracting
`/etc/resolv.conf` from the signed base and finished squashfs showed identical
empty files, so the builder's resolver was not carried into the ISO. The ISO
is 4,395,958,272 bytes with SHA-256
`33b88469c677635c6085de6670368cdf35ddb204b431d44986e2eb98b8ad92bd`. Local
restore replay passed with a 19 GiB boot-pool and about 16.16 GB free. On
v1001, the completion notification was unavailable, so the documented
disk-boot recovery path was used before reapplying the preseed. Pinned root SSH
verified RC.1 and an ONLINE boot-pool. The provider stock snapshot is
`tn-v27rc1-20g-stock-ssh-ready-20261031`
(`1239b205-b170-42ab-9c26-de95b4c7f37e`); restore certification remains
explicitly unset. The consumer guide records the full sequence and evidence.

The earlier 16 GiB source run and restore payload remain historical evidence.
The official [TrueNAS 28 nightly index](https://iso.sys.truenas.net/TrueNAS-28-Nightlies/)
advances independently of the RC.1 source used here; record exact build and
installer hashes for each compatibility run.

The capture workflow does **not** apply the boot-pool relaxation patch. It
captures the source disk as it is. The current 20 GiB source is vanilla TrueNAS
`27.0.0-RC.1`; its raw digest is recorded above, and step 2 has built a matching
restore ISO. The pre-reinstall read-only inventory found two custom middleware
modules matching files in this worktree, 14 of 15 adapter targets differing
from the current v27 adapter, and no patch install record. That inventory does
not establish whether the custom code came from the historical golden image
or a later manual change; the new provider snapshot is recorded as stock RC.1.
See the [20 GiB outcome](docs/CONSUMERS.md#20-gib-restore-and-v1001-snapshot-2026-10-31)
and [acceptance matrix](../docs/ACCEPTANCE-RESULTS.md) for the subsequent
patched-state update results.

The boot-pool patch's
[`supported-builds.json`](../boot-pool-patch/supported-builds.json) records
the exact source and target builds used by acceptance. The v27 RC.1 source is
intentionally older than its registered v28 nightly update target; retain the
source installer and image digests with each run rather than treating the
golden image as a rolling release.

The v28 nightly ISO used for patch work belongs under
the repository's `downloads/` folder, not in this golden-image project. Its
temporary hard-link alias under this directory was removed on 2026-10-17 after
all three retained VM manifests were rebound to the canonical path and the
active guest was cleanly recreated with its COW disk and EFI state preserved.
For future old-path manifests, use the shared runner's
[`rebind-media` procedure](../docs/CONSUMERS.md#rebind-an-iso-path-while-preserving-guest-state)
before removing an alias.
