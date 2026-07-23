# PR draft — pterodactyl/wings

> **Status: DRAFT — not submitted.** Patches:
> `../patchstack/patches/pterodactyl-v1.13.1/` — rebase onto `develop` before
> opening the PR (`scripts/rebase.sh pterodactyl develop`), since upstream PRs
> target `develop`, not the release tag.
>
> Three PRs, in order: config diagnostics (0006) alone, then placement
> (0001+0002+0003), then per-server slices (0004+0005) stacked on the second.
> See `README.md` here.

**Title:** `Add cgroup parent support: node-wide docker.cgroup_parent + optional per-server override`

Body: identical to `pelican-wings-pr.md` (same feature, same commits) — with
these Pterodactyl-specific notes:

- Precedent: like the conditional `BlkioWeight` handling added in #324, this
  exposes an existing Docker/cgroup-v2 capability through node config without
  adding dependencies (true of the placement PR; the per-server-slices PR does
  add go-systemd, and says so).
- That same code is where the naming problem in the third PR comes from: the
  per-server `io_weight` you already ship lands on the container **scope** on
  BFQ's 1..1000 scale, while this series adds admin-owned weights on the
  per-server **slice**, one of them on systemd's 1..10000 scale. The
  "Relationship to the existing `io_weight`" section of
  `pelican-wings-pr.md` covers the layering and asks maintainers to pick the
  config key names — lead the third PR with it here too.
- No existing PR/issue covers cgroup parent placement (searched 2026-07).
- Submitted in parallel to pelican-dev/wings; whichever lands first, the other
  can cherry-pick — the diffs are intentionally identical in shape.
