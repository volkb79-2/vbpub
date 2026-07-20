# Build the patched Wings image

Everything here runs in the **devcontainer** — no local Go toolchain needed; the
scripts compile inside `golang:*` containers and build against the host Docker
daemon. The result is an image on that daemon, ready for [`SETUP.md`](SETUP.md),
which is where all host/node deployment lives.

## 1. Clone and apply the stack

```bash
cd /workspaces/vbpub/wings-cgroups
patchstack/scripts/clone.sh                # -> build/wings-pterodactyl, build/wings-pelican (gitignored)
patchstack/scripts/apply.sh pterodactyl    # applies 0001..0007 onto v1.13.1
```

`build/` is disposable — the `patchstack/patches/` series is the canonical
artifact. Re-run `clone.sh` + `apply.sh` any time to get a clean tree.

## 2. Test before building

```bash
patchstack/scripts/test.sh pterodactyl              # build + vet + unit tests
INTEGRATION=1 patchstack/scripts/test.sh pterodactyl   # + Environment.Create() vs the real daemon
```

`go vet` runs strict on our packages; upstream packages with pre-existing
findings are excluded via `VET_EXCLUDE_RE` in `stack.conf` so our patches stay at
zero new warnings.

## 3. Build the image

```bash
patchstack/scripts/build-image.sh pterodactyl cgroup.5    # -> wings-local:1.13.1-cgroup.5
patchstack/scripts/build-image.sh pelican               # -> wings-local-pelican:<ver>-cgroup.1
```

Second argument is the tag suffix (default `cgroup.1`); the tag is
`<IMAGE_PREFIX>:<upstream-version>-<suffix>`. The build tar-pipes the tree into
`docker build`, so the image lands directly on the host daemon — no registry, no
push. Idempotent: re-running rebuilds the same tag.

**The registry-less name is deliberate.** A stray `docker compose pull` on the
node fails loudly instead of silently reverting the node to stock upstream Wings.
Bump the suffix (`cgroup.5` → `cgroup.6`) for each deployable change so compose
`--force-recreate` and rollback stay unambiguous.

## 4. Verify the image before deploying

```bash
docker run --rm wings-local:1.13.1-cgroup.5 version      # prints the version
test/smoke-placement.sh                                  # placement vs the real host daemon
test/e2e-systemd/run-e2e.sh                              # privileged systemd-in-Docker: effective floors
```

`smoke-placement.sh` places a throwaway container under `wings-smoke.slice`
(override with its first argument), which systemd materializes as a limit-less
transient unit. It is harmless, but stop it before installing real units on a
node — `systemctl stop wings-smoke.slice` — or the `FragmentPath` pre-flight in
`SETUP.md` §1a will catch it for you.

## 5. Deploy

→ [`SETUP.md`](SETUP.md). Nothing in this file touches a node.

## Rebasing onto a new upstream release

```bash
patchstack/scripts/rebase.sh pterodactyl v1.13.2   # rebase the series
patchstack/scripts/test.sh pterodactyl             # must be green
patchstack/scripts/export-patches.sh pterodactyl   # refresh patchstack/patches/
patchstack/scripts/build-image.sh pterodactyl cgroup.5
```

Then bump `PTERODACTYL_REF` in `stack.conf`. Details and the systemd gotchas the
patches encode: `patchstack/README.md`.

## `stack.conf` — what needs setting

Nothing, to build. The build scripts read `*_REMOTE`, `*_REF`, `*_GO_IMAGE`,
`IMAGE_PREFIX` and `VET_EXCLUDE_RE`, all already set. `FORK_REPO` is referenced
only by the fork/CI/PR flow (`patchstack/README.md`, `pr/README.md`) and is still
the `OWNER/wings` placeholder — set it to your fork when you create one.

## Optional: the fork (needed for CI and PRs, not for deploying)

```bash
gh repo fork pterodactyl/wings --clone=false        # -> <you>/wings
# set FORK_REPO in patchstack/stack.conf, then:
cd build/wings-pterodactyl
git remote add fork https://github.com/<you>/wings.git
git push fork cgroup/v1.13.1                        # auth trouble: gh auth setup-git
```

Notes:

- Pushing makes the patches public. This is not a PR — submission stays deferred
  (`pr/README.md` holds the drafts).
- The Pelican fork cannot also be named `wings` under the same account:
  `gh repo fork pelican-dev/wings --fork-name wings-pelican --clone=false`.
- For fork CI, put `ci/fork-wings-ci.yml` on a **separate** branch (e.g.
  `cgroup/v1.13.1-ci`) so the patch series stays clean for `export-patches.sh`.
- Commits carry `Co-Authored-By` trailers — strip them before PR if unwanted.
