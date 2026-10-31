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

Treat the bootstrap `truenas_admin` password as public once a golden recipe is
published. Immediately after booting a live test host, change it to a fresh
random per-host password before running any test; save that value only in a
mode-0600 gitignored host-secret TOML so later gates can reconnect. This
host-side credential is separate from the golden source-image sidecar.

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
`../boot-pool-patch/artifacts/`, not in this golden-image project. Its
temporary hard-link alias under this directory was removed on 2026-10-17 after
all three retained VM manifests were rebound to the canonical path and the
active guest was cleanly recreated with its COW disk and EFI state preserved.
For future old-path manifests, use the shared runner's
[`rebind-media` procedure](../docs/CONSUMERS.md#rebind-an-iso-path-while-preserving-guest-state)
before removing an alias.
