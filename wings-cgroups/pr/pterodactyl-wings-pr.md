# PR draft — pterodactyl/wings

> **Status: DRAFT — not submitted.** Patches:
> `../patchstack/patches/pterodactyl-v1.13.1/` — rebase onto `develop` before
> opening the PR (`scripts/rebase.sh pterodactyl develop`), since upstream PRs
> target `develop`, not the release tag.

**Title:** `Add cgroup parent support: node-wide docker.cgroup_parent + optional per-server override`

Body: identical to `pelican-wings-pr.md` (same feature, same commits) — with
these Pterodactyl-specific notes:

- Precedent: like the conditional `io.weight` handling (#324), this exposes an
  existing Docker/cgroup-v2 capability through node config without adding
  dependencies.
- No existing PR/issue covers cgroup parent placement (searched 2026-07).
- Submitted in parallel to pelican-dev/wings; whichever lands first, the other
  can cherry-pick — the diffs are intentionally identical in shape.
