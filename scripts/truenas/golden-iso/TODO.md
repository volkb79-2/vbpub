# Golden-image follow-up work

## Source and restore compatibility

- Exercise source preparation and restoration on a stable TrueNAS 27 release,
  in addition to the tested `27.0.0-RC.1` build.
- Confirm the current restore runtime on TrueNAS 28 with a live compatibility
  smoke test.

## Preserve the golden-image credential boundary

Rule (TN-03, implemented): credentials are generated, never chosen by hand and
never documented.

- Step 1 sets a random `truenas_admin` password (32 characters, `secrets`) and
  creates a dynamic API key at every capture. Both live only in the mode-0600
  sidecar (0700 directory, gitignored `artifacts/`); step 1 prints only the
  path. The bootstrap password is refused as a final value, and a key that
  belongs to a finished capture is revoked and replaced by the next capture.
- Every capture has an 8-hex-digit ID in both the image and the sidecar file
  name (`...-golden-<id>.raw`, `...-golden-<id>.secrets.toml`); step 2 refuses
  a pair whose IDs differ.
- The restore is unattended and does not rotate: the restored host keeps the
  golden image's secrets by design, and the user rotates them after the
  restore with the README checklist ("Secrets baked into a golden ISO / what to
  change after a restore").
- Values never appear in tracked files, docs, or logs
  (`tests/test_credentials.py` scans tracked files for credential-like values).
- The ISO contains the whole golden disk, so treat the image and ISO as private
  credential-bearing artifacts. Capture the disk and its matching sidecar
  together.

Open (optional, low priority): a first-boot helper script that implements the
post-restore checklist (TN-14).

Keep the feature summary in [`README.md`](README.md), adoption commands in
[`docs/CONSUMERS.md`](docs/CONSUMERS.md), and restore rationale in
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).

## Current artifact discrepancy

The consumer guide records the v6 runtime's `lsblk --tree` correction and its
regression test. On 2026-10-31, vanilla TrueNAS `27.0.0-RC.1` installed and
booted on the 20 GiB source disk. The first three managed TCG starts were
refused because memory PSI `some avg10` at `/dev.slice/dev-gates.slice` was
22.57%, 30.72%, and 38.47%, above the 10% admission limit. A later admission
passed after pressure subsided; the guard was not bypassed. The source pool
reported 19 GiB size and 15.1 GiB free. Step 1 captured a 21,474,836,480-byte
raw image with SHA-256
`721a8b22698f7be66d853c3719351849dacfc59271d023734a846615ebc6b1d7`; its
sidecar remains mode 0600.

Step 2 copies the builder resolver into the Debian Live chroot only for apt,
then restores the signed base's original `/etc/resolv.conf` entry before
squashfs creation. Chroot DNS resolution, package installation, resolver
non-leak, and local QEMU restore replay all passed. The raw image SHA-256 is
`721a8b22698f7be66d853c3719351849dacfc59271d023734a846615ebc6b1d7`; the ISO
SHA-256 is `33b88469c677635c6085de6670368cdf35ddb204b431d44986e2eb98b8ad92bd`.

On v1001, the provider completion notification was unavailable. The documented
`recover --try-disk-boot` procedure brought the restored RC.1 host up, pinned
SSH and ONLINE boot-pool checks passed, and the post-restore preseed was
reapplied. The stock snapshot is
`tn-v27rc1-20g-stock-ssh-ready-20261031`, UUID
`1239b205-b170-42ab-9c26-de95b4c7f37e`. Restore certification remains unset.
Path A and Path B update-survival cells passed. The Path A+B cell also passed
on v28 after the acceptance router was corrected to anchor the v28 adapter on
Path A, restoring its automatic mount integration in the new boot environment.
Two attempts were stopped by registered PSI admission without bypass; the
successful run began from the already-reverted clean A+B snapshot. See the
acceptance results and consumer guide for the matrix and run IDs.
