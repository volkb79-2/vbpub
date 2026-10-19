# Golden-image follow-up work

## Source and restore compatibility

- Exercise source preparation and restoration on a stable TrueNAS 27 release,
  in addition to the tested `27.0.0-RC.1` build.
- Confirm the current restore runtime on TrueNAS 28 with a live compatibility
  smoke test.

## Preserve the golden-image credential boundary

`step1-install-prepare-truenas.py` creates and validates the `truenas_admin`
API key before capture, stores its cleartext value in the mode-0600 sibling
sidecar, and refuses to create a duplicate on rerun. The restore ISO builder
does not read or embed that cleartext API key. The ISO contains the whole
golden disk and therefore includes TrueNAS's stored API-key record along with
the configured administrator, root SSH key, SSH host keys, and machine
identity. Treat the image and ISO as private credential-bearing artifacts.

Keep the feature summary in [`README.md`](README.md), adoption commands in
[`docs/CONSUMERS.md`](docs/CONSUMERS.md), and restore rationale in
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).

## Current artifact discrepancy

The consumer guide records the v6 runtime's `lsblk --tree` correction and its
regression test. The earlier v5 incident text says the v6 ISO was still being
rebuilt. No unattended restore ISO is present in this worktree's ignored
`artifacts/` directory, so the built v6 artifact and its final replay/provider
result still need to be confirmed before describing the restore as verified.
