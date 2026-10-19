# TrueNAS golden-image and restore workflow

This subproject prepares a configured TrueNAS 27+ source disk, captures its
whole disk as a golden image, and builds a Debian Live ISO that restores that
image unattended. The restore preserves the captured partition sizes: it does
not grow the ZFS partition or pool. The builder keeps the administrator
password, root Ed25519 key, webhook, and one-time API key in a mode-0600 TOML
sidecar beside the image; the ISO contains the full disk and notification
webhook, so both remain private artifacts.

The workflow has two scripts:

1. [`step1-install-prepare-truenas.py`](step1-install-prepare-truenas.py)
   configures the installed source VM, creates and validates its API key,
   shuts it down, and captures a verified raw whole-disk image.
2. [`step2-build-unattended-restore-iso.py`](step2-build-unattended-restore-iso.py)
   validates a Debian Live base ISO, embeds the golden disk and restore runtime,
   and produces the unattended restore ISO.

Use the [consumer guide](docs/CONSUMERS.md) for the tested manual installation,
source preparation, ISO build, QEMU replay, and Netcup install commands. The
[design guide](docs/DESIGN-GUIDE.md) records restore boundaries, safeguards,
and unresolved compatibility evidence. The workflow is separate from the
[boot-pool patch](../boot-pool-patch/README.md): a restored host can be patched
after boot, while patching the source before capture requires a manual step;
the two scripts do not invoke the patch workflow.

The v28 nightly ISO used for patch work belongs under
`../boot-pool-patch/artifacts/`, not in this golden-image project. Its
temporary hard-link alias under this directory was removed on 2026-10-17 after
all three retained VM manifests were rebound to the canonical path and the
active guest was cleanly recreated with its COW disk and EFI state preserved.
For future old-path manifests, use the shared runner's
[`rebind-media` procedure](../docs/CONSUMERS.md#rebind-an-iso-path-while-preserving-guest-state)
before removing an alias.
