# Archived Wings patch-stack evidence

The active layout is `../`. These files preserve prior patch-stack content and
are not inputs to the active clone, apply, check, or image-build workflow.

- `deployed-v1/pterodactyl-v1.13.3/` is a byte-for-byte copy of the series
  consumed by the former `stack.conf` before metadata trailers were added.
- `deployed-v1/stack.conf` and the two captured README files record the former
  configuration and documentation state.
- `legacy-pelican/pelican-main/` preserves the stale July 2026 Pelican series
  and its recorded base commit. It predates the Pterodactyl scope redesign.
- `v2-proposal/` preserves the earlier v2 README and series metadata before
  consolidation.

`scripts/prove-v1-equivalence.sh` checks the archived v1 patch bytes against
the metadata-bearing active series on the pinned upstream commit.
