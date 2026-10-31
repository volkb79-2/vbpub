# TrueNAS golden-image and restore workflow

This subproject prepares a configured TrueNAS 27+ source disk, captures its
whole disk as a golden image, and builds a Debian Live ISO that restores that
image unattended. The restore preserves the captured partition sizes: it does
not grow the ZFS partition or pool. The builder keeps the administrator
password, root Ed25519 key, webhook, and one-time API key in a mode-0600 TOML
sidecar beside the image; the ISO contains the full disk and notification
webhook, so both remain private artifacts.

Start with a manual vanilla TrueNAS installation on a source VM. The workflow
then has two scripts:

1. [`step1-install-prepare-truenas.py`](step1-install-prepare-truenas.py)
   configures the installed source VM, creates and validates its API key,
   shuts it down, and captures a verified raw whole-disk image.
2. [`step2-build-unattended-restore-iso.py`](step2-build-unattended-restore-iso.py)
   validates a Debian Live base ISO, embeds the golden disk and restore runtime,
   and produces the unattended restore ISO.

The captured QCOW2 is the size authority: step 1 accepts whole-GiB source disks
of at least 16 GiB and captures that exact virtual size. Its preferred source,
raw-image, and sidecar names use 20 GiB. Step 2 names the compressed payload
from the recorded image size, and restore writes exactly that many bytes. It
does not resize the captured ZFS partition or boot pool.

Use the [consumer guide](docs/CONSUMERS.md) for the tested manual installation,
source preparation, ISO build, QEMU replay, and Netcup install commands. The
[design guide](docs/DESIGN-GUIDE.md) records restore boundaries, safeguards,
and unresolved compatibility evidence. The workflow is separate from the
[boot-pool patch](../boot-pool-patch/README.md): a restored host can be patched
after boot, while patching the source before capture requires a manual step;
the two scripts do not invoke the patch workflow.

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

Step 2 verified the signed Debian Live 13.7.0 base, then failed twice while
installing `sgdisk`, `efibootmgr`, and `eject` inside the Debian chroot:
`apt-get` could not resolve `deb.debian.org`. Host DNS and an HTTP request to
the Debian repository succeeded during diagnosis, but the documented chroot
build continued to fail. No restore ISO, local restore replay, v1001 install,
new provider snapshot, or 20 GiB acceptance run exists. Keep the existing
16 GiB provider snapshot as the only available base until step 2, local replay,
and v1001 install finish. The exact logs and hashes are in the operator
evidence directory under `truenas-live-20261031`.

The earlier 16 GiB source run and restore payload remain historical evidence.
The official [TrueNAS 28 nightly index](https://iso.sys.truenas.net/TrueNAS-28-Nightlies/)
advances independently of the RC.1 source used here; record exact build and
installer hashes for each compatibility run.

The capture workflow does **not** apply the boot-pool relaxation patch. It
captures the source disk as it is. A patched source would require a separate,
manual patch step before capture. The raw golden and restore ISO are absent, so
the captured image's contents cannot currently be inspected. A read-only check
of v1001 found two custom middleware modules matching files in this worktree,
14 of 15 adapter targets differing from the current v27 adapter, and no patch
install record. That proves v1001 has unrecorded/custom code, but does not show
whether it came from the historical golden image or a later manual change. Do
not treat the historical source QCOW2 or any prior ISO as a verified patched
golden.

The official [TrueNAS 28 nightly index](https://iso.sys.truenas.net/TrueNAS-28-Nightlies/)
currently lists builds through `28.0.0-MASTER+20261019-175231`; the retained
source VM is v27 RC.1 dated 2026-10-15. The golden is therefore not current for
the v28 nightly test matrix. Re-run step 1 from a freshly installed, selected
TrueNAS release, then step 2, and record the exact source version and ISO
hashes before using it as a test fixture.

The v28 nightly ISO used for patch work belongs under
`../boot-pool-patch/artifacts/`, not in this golden-image project. Its
temporary hard-link alias under this directory was removed on 2026-10-17 after
all three retained VM manifests were rebound to the canonical path and the
active guest was cleanly recreated with its COW disk and EFI state preserved.
For future old-path manifests, use the shared runner's
[`rebind-media` procedure](../docs/CONSUMERS.md#rebind-an-iso-path-while-preserving-guest-state)
before removing an alias.
