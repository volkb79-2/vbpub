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

As of 2026-10-21, `artifacts/` contains only the protected TOML sidecar; there
is no raw golden disk or unattended restore ISO to inspect or install. The
retained source QCOW2 is the 16 GiB TrueNAS `27.0.0-RC.1` VM disk dated
2026-10-15. It does not establish a current v27+ golden, and the official
[TrueNAS 28 nightly index](https://iso.sys.truenas.net/TrueNAS-28-Nightlies/)
already lists newer nightlies than the one used for the existing patch
comparison. Re-run source preparation and ISO construction before treating
the workflow as current. The historical build and replay records are in the
[consumer guide](docs/CONSUMERS.md).

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
