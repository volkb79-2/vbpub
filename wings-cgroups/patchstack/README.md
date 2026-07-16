# Patch stack — T1+T2 Wings patches, rebasing workflow

The **committed truth** is the patch series under `patches/`; the clones under
`../build/` are disposable working trees. One clean commit per feature so
upstream PRs can cherry-pick, and rebases stay reviewable:

| # | Commit | Tier |
|---|---|---|
| 0001 | `Add docker.cgroup_parent to place containers under a systemd slice` | T1 |
| 0002 | `Support per-server cgroup parent override via WINGS_CGROUP_PARENT` | T2 |
| 0003 | `Add docker integration tests for cgroup parent placement` | tests (build-tagged; include in PR or drop) |

Targets: `pterodactyl` (tag `v1.13.1` — what production runs) and `pelican`
(`main` — the faster-merging upstream; same commits, ported). See `stack.conf`
for refs, go images, and the `FORK_REPO` placeholder.

## Workflows

**Fresh machine → deployable image**

```bash
scripts/clone.sh
scripts/apply.sh pterodactyl
INTEGRATION=1 scripts/test.sh pterodactyl   # needs /var/run/docker.sock
scripts/build-image.sh pterodactyl          # -> wings-local:1.13.1-cgroup.1
```

**New upstream release (the recurring ~1–2h/release chore)**

```bash
scripts/rebase.sh pterodactyl v1.13.2       # rebases commits onto the new tag
scripts/export-patches.sh pterodactyl       # refresh committed series
INTEGRATION=1 scripts/test.sh pterodactyl
scripts/build-image.sh pterodactyl cgroup.1
# deploy per ../t1-node-cgroup-parent/README.md, then commit patches/ changes
```

**Editing the patches** — never edit `.patch` files by hand: change the
commits on the branch (`git rebase -i` on your own machine / amend), then
`scripts/export-patches.sh`.

**Pushing to the fork** (once `FORK_REPO` is set in `stack.conf`):

```bash
cd ../build/wings-pterodactyl
git remote add fork git@github.com:OWNER/wings.git
git push fork cgroup/v1.13.1
```

CI for the fork lives in `../ci/fork-wings-ci.yml` (copy into the fork as
`.github/workflows/cgroup-ci.yml` on the patch branch).

## Notes

- Scripts run all Go tooling inside golang containers with the source
  **tar-piped in** — works on hosts where the checkout isn't bind-mountable
  (like this devcontainer) and pins the toolchain per target.
- `go vet` runs strict except for packages with pre-existing upstream findings
  (`VET_EXCLUDE_RE` in stack.conf): our patches must add zero new warnings.
- Both series were verified end-to-end in this environment: build + vet +
  unit tests + the `dockerintegration` tests against a real systemd/cgroup-v2
  Docker daemon (placement, accepted override, fail-closed rejection).
