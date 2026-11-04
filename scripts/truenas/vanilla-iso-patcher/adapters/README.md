# Vendored experimental boot-pool adapters

These frozen adapter inputs were copied on 2026-10-17 from
`scripts/truenas/boot-pool-patch/` so the vanilla ISO generator is
self-contained. The source patch worktree remains independent; no files there
are modified by the generator.

The `v27/` adapter targets exactly `27.0.0-RC.1`. The
`v28-nightly-20261014/` adapter targets exactly
`28.0.0-MASTER+20261014-230204`. Their `targets.sha256` files fingerprint the
installed middleware sources. The generator verifies those fingerprints again
inside the newly installed boot environment before changing it.

These adapters remain experimental. Upstream boot-pool use is not a supported
TrueNAS configuration, and the source patch work still records open acceptance
items. The combined unattended flavor is for controlled evaluation, not a
general production image. Do not widen either release pin without new live
acceptance and an updated fingerprint manifest.
