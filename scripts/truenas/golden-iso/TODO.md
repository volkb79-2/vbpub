# Golden-image follow-up work

## Source and restore compatibility

- Exercise source preparation and restoration on a stable TrueNAS 27 release,
  in addition to the tested `27.0.0-RC.1` build.
- Confirm the current restore runtime on TrueNAS 28 with a live compatibility
  smoke test.

## Preserve the golden-image credential boundary

Every live test of an installed golden image must rotate the publicly known
`truenas_admin/truenas` bootstrap password before any other test action. Use a
fresh random password per host, store it only in a mode-0600, gitignored
host-specific TOML file, and have later access gates read that file. Never put
the value in the golden recipe's public documentation, tracked configuration,
or test logs. Keep these live-host credentials separate from the golden
source-image sidecar and its API key.

`step1-install-prepare-truenas.py` creates and validates the `truenas_admin`
API key before capture, stores its cleartext value in the mode-0600 sibling
sidecar, and refuses to create a duplicate on rerun. The restore ISO builder
does not read or embed that cleartext API key. The ISO contains the whole
golden disk and therefore includes TrueNAS's stored API-key record along with
the configured administrator, root SSH key, SSH host keys, and machine
identity. Treat the image and ISO as private credential-bearing artifacts.

On 2026-10-21, an attempt to check public host v1001 used the password from
this golden sidecar. The user corrected the target credentials: v1001 still
uses the install-time account password, so the earlier rejection was a
credential-selection mistake, not evidence that the host password changed or
failed. Keep the v1001 credential separate from the golden sidecar. The
sidecar's own administrator password appeared in an earlier browser tool
snapshot, so rotate it on the source TrueNAS before capturing another image.

The saved API key was sent to v1001 by the shared API client with TLS
verification disabled; the server returned `Invalid API key`, so no API
method ran. Treat that key as exposed. Before the next capture, revoke it on
the source TrueNAS, remove its `truenas_api` block from the sidecar, and run
`step1-install-prepare-truenas.py --revoke-orphan-api-key` so step 1 creates
and validates a replacement. Capture the disk and matching mode-0600 sidecar
together; never update only the sidecar while leaving the captured disk
unchanged.

Keep the feature summary in [`README.md`](README.md), adoption commands in
[`docs/CONSUMERS.md`](docs/CONSUMERS.md), and restore rationale in
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).

## Current artifact discrepancy

The consumer guide records the v6 runtime's `lsblk --tree` correction and its
regression test. The earlier v5 incident text says the v6 ISO was still being
rebuilt. As of 2026-10-21, the ignored `artifacts/` directory contains only
the mode-0600 TOML sidecar: the raw golden disk and unattended restore ISO are
absent. The v6 artifact and its final replay/provider result are therefore not
available to verify from this checkout. The retained source QCOW2 is dated
2026-10-15 and uses TrueNAS 27.0.0-RC.1; it is not a current v27+ golden
artifact. PWMCP inspection of the official v28 nightly index found builds
through `28.0.0-MASTER+20261019-175231`. The live v1001 host has two custom
middleware modules matching this worktree but 14 of 15 release-adapter source
targets differ, with no patch-install record. This does not establish that the
custom code came from the golden image; the missing raw image and ISO prevent
provenance verification. Step 1 and step 2 must be rerun before another install.
