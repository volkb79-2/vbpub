<!-- origin: 2026-11-03; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/MDT-COCKPIT-RELEASE-PLAN-2026-11-03.md -->
<!-- relation: MDT release plan -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# mdt-cockpit release before the host reboot — plan (2026-11-03)

Read-only investigation of main = origin/main = `c08b5b89f8a9`. Nothing was run, built or changed.

## 0. Bottom line

- **No prep commit is needed.** Every tool pin is resolved at build time. One plain command, with no special builder environment:
  `cmru release mdt-cockpit`. It takes about 25 minutes nominally; budget 45.
- **The release is not on the critical path for having a working devcontainer after the reboot.** The real hazard is
  the *switch*:
  - This session runs in **`dstdns-devcontainer-vb`**. That is the legacy predecessor image, with the
    split mounts under `~/mdt--mounted-folders/`.
  - The target, the vbpub checkout's `.devcontainer`, mounts a **whole, per-checkout home**,
    `~/mdt-cockpit-vbpub-home/`, which starts **empty**.
  - About 11 GB of working state sits in the old container's writable layer and is not on any mount.
  - Without the migration in §4, the new devcontainer would come up with:
    - no Claude login, memory or transcripts;
    - no Codex sessions;
    - no release-program directory;
    - no console corpus.
- **If the release is not green by the reboot deadline, reboot anyway.** The vbpub devcontainer still works with the
  current `-latest` image (20261027). Cut the release from the new container afterwards.

## 1. Release contents

| Item | Value | Where it is declared |
|---|---|---|
| Release identity | strategy `none`, `git_tag = false`. The label will be `source-<sha12>` (`source-c08b5b89f8a9` if main does not move); CHANGES heading `## [source-…] - 2026-11-03` | `mdt-cockpit/cmru.toml:40-55` |
| Matrix | group `all` = `trixie-py314-php85`, `trixie-py314-php85-vsc` | `docker-bake.hcl:870-873` |
| Tags | `ghcr.io/volkb79-2/mdt-cockpit:trixie-py3.14-php8.5-20261103` and `…-latest`; `ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-20261103` and `…-latest` (BUILD_DATE is the UTC date at build) | `docker-bake.hcl:428-446`, `cmru.toml [build_metadata]` |
| ciu | 7.19.0, auto. `CIU_WHEEL_TAG` is unset, so the resolver uses `ciu-latest/latest.json`, else the highest `ciu-v*` | `scripts/resolve-devcontainers-release.py:715-790` |
| cmru / cli-extended / topos / nyxloom | 7.1.3 / 0.5.0 / 0.4.2 / 0.12.0, auto. `pip/wheels.list` is resolved to the highest `N-v*` release, with no age window for first-party wheels (CHANGES `[Unreleased]`) | `scripts/resolve-devcontainers-release.py:880`, `pip/wheels.list` |
| pwmcp client + get.py | 1.64.0-r4, read from `pwmcp/pwmcp.contract.json` at the candidate HEAD (already `1.64.0-r4` on main). The get.py and contract URLs are pinned to that commit | `build-push.py:288-330` |
| run-gate, assay, tnt, cgprofile | **not baked into the image.** run-gate, assay and tnt need a home install after the switch (§4, step 21). cgprofile is the host daemon at `/run/cgprofile` | `pip/wheels.list` has none of them |
| Third-party tools | resolved at build under the 14-day age window | `MDT_TOOL_MIN_AGE_DAYS` |

- **Embedded `worktree` library.** It is identical in the ciu-v7.19.0 and cmru-v7.1.3 wheels: `git diff ciu-v7.19.0 cmru-v7.1.3 -- libraries/worktree` is empty. Installing both into the image cannot downgrade it.
- **Last published image (20261027).** It carries ciu 7.17.0, cmru 6.2.1 and nyxloom 0.11.0 (`package-manifests-versioned/mdt-cockpit-devcontainer/trixie-py3.14-php8.5-20261027.md:100-101`), so this release is a real tool refresh.
- **What the release carries.** There are 32 mdt-cockpit commits since the last release:
  - iogov and iomon;
  - host-setup fixes;
  - manifest fixes;
  - the per-checkout home template;
  - the age-policy split;
  - the renderer test fix (`32c2aa7fb`, merged as `23789fce7`).

  MDT-033 is docs and backlog only (`7494d68cf`, `aef6747c7`, `c08b5b89f`) and does not block.
- **Builder.** Use the managed builder as the devcontainer already sets it:
  - `BUILDX_BUILDER=mdt-managed`
  - `BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock`
  - The socket is present and `mdt-managed0` is running BuildKit v0.33.1.

  **Do not** copy pwmcp's `BUILDX_BUILDER=default BUILDKIT_HOST=` override. Only mdt-cockpit requires the managed
  endpoint (`cmru.orchestration.toml:89-92`), and the oci-push lane refuses any `BUILDKIT_HOST` that is not
  `unix:///…` (`scripts/run_oci_push_lane.py:require_buildkit_host`).

  The "remote builder cannot see local images" problem does not apply here: the base image is the registry image
  `mcr.microsoft.com/devcontainers/python` (`Dockerfile:8`). Release builds go to OCI layouts and are pushed with
  digest-verified crane.
- **`--allow-uncommitted` is not needed.** The build runs in an isolated candidate taken from origin/main. The
  operator's untracked files (`pwmcp/tools/console-capture/*`, `scripts/netcup/*`, `target-host.jsonc`) were already
  present during today's ciu 7.19.0 and cmru 7.1.3 releases, and cmru's local-main rebase succeeded.
- **Credentials.** Push credentials come from the root `cmru.secret.toml` (`[github] token`, mode 0600), which is
  present. GHCR package visibility is mirrored automatically (`build-push.py:161-190`). The packages already exist,
  so no new-package visibility step is needed.

## 2. Release gate lanes and preconditions

The release runs `[steps.run-tests]` (`cmru.toml:63-68`). The durations below come from the 2026-10-27 release log
(`mdt-cockpit/logs/release-20261027-r2.log`).

| Step / lane | Environment | Budget | Last measured |
|---|---|---|---|
| `prepare`: `build-push.py --build` (both targets, manifest extraction) | managed BuildKit | n/a | 1131 s (about 19 min) |
| `run-gate release` (unittest of release-flow, OCI tool staging and OCI-push driver) | tester-unified | 15 min | 4 s, 68 tests |
| `run-gate oci-push`: runs inside the image just built and starts a `registry:2` container. The 10-27 first-attempt flake ("registry not reachable") is fixed by the poll at `scripts/test_oci_layout_push.py:117-133` | bare-host, needs `CGROUP_PARENT_DEV_GATES` and `BUILDKIT_HOST` | 20 min | 138 s |
| `push`: crane push of the layouts and the GHCR visibility sync | | n/a | 121 s |
| Promotion of the CHANGES and manifest commit to origin/main, then the local rebase | | n/a | about 1 min |

**`smoke` is not part of the release** (backlog MDT-032 at `KNOWN_ISSUES_TODO_BACKLOG.md:471`). Do not use it as a
pre-release check on this host. It is red here for a known, unrelated reason:
`iogov/tests/test_observe.py:328` reads the live filesystem fill. The host root is at 76%, and the second MDT-032 at
`:661` records this (`logs/mdt-smoke-main.log`: 1 failed, 1275 passed). The r1002 run of the same source was green
(1276 passed). `assay-full`, `vm-system` and `image-runtime` are not release lanes either.

Preconditions:

- **tester-unified:local must carry the released run-gate wheel.** It was rebuilt at 2026-11-02 23:23 and proven by
  today's cmru 7.1.3 release (`logs/cmru-release-713c.log`). Re-confirm with `run-gate doctor` (step 4).
- **Run-gate version in the bootstrap doc.** `docs/BOOTSTRAP-FROM-ZERO.md` already says 24.3.0 (lines 146-156 and
  316), and the installed `run-gate` is 24.3.0.
- **KI-79 waiver window:** applies only to cmru. mdt-cockpit has no mutation or R2 lane in its release.
- **Host load:** the host is shared with the production game server, so run one gate container at a time. Do not run
  the home copy (§4) or any other gate while `prepare` builds.

## 3. Dry-run caveat (cmru KI-111)

`cmru release mdt-cockpit --dry-run` takes about 1 minute. It shows the release label and the generated changelog. It
does **not** check:

- the tester-image wheel identity;
- the builder endpoint;
- the registry reachability;
- the oci-push preconditions;
- the push credentials.

It is optional. The real proof is steps 3 and 4, then the release itself. A failure after `prepare` costs a full
rebuild on retry (about 25 minutes). The flow has no resume: **never** use `--resume` without a recorded plan, and
**never** use `--skip-gates`.

## 4. Controller step list

### Before the reboot (inside the current container; about 50-65 minutes)

1. **Freeze pushes to main** for the release window (about 45 minutes). The release pushes a CHANGES and manifest
   commit; a concurrent push makes the promotion fail *after* the images are already pushed.
   - Run `git -C <checkout> fetch origin`.
   - Check that `git rev-parse main origin/main` gives the same value for both.
2. **Check tool versions.**
   - `cmru --version` must be 7.1.3.
   - `run-gate --version` must be 24.3.0.
   - `~/.venv/bin/python -c "import worktree; assert hasattr(worktree, 'finish_workspace_removal')"` must succeed.
3. **Check the builder.**
   - `test -S /run/mdt-buildkitd/buildkitd.sock` must succeed.
   - `docker buildx inspect mdt-managed` must show the remote driver, running.
   - `echo $CGROUP_PARENT_DEV_GATES` must print `dev-gates.slice`.
4. **Run the gate doctor.** `cd <checkout>/mdt-cockpit && run-gate doctor`. It checks docker, the slices and
   the tester-unified image. Check also that no other gate holds the lock: look at the program `gate.lock`, and inspect
   only containers you know by name.
5. **Optional:** `cmru release mdt-cockpit --dry-run` (subject to §3).
6. **Release.**
   - Run from `<checkout>`, detached, with a completion marker:
     `nice -n 10 cmru release mdt-cockpit > mdt-cockpit/logs/release-20261103.log 2>&1; echo $? > mdt-cockpit/logs/release-20261103.exit`
   - The `logs/` path is gitignored and lives on the host bind mount.
   - Read the verdict in a **separate** step: look for `Released: mdt-cockpit (no git tag)` and
     `Local main synced with origin/main`.
   - Expected time: about 25 minutes, 45 at most under load.
7. **Verify the release.**
   - **Manifest.** The new `package-manifests-versioned/mdt-cockpit-devcontainer/trixie-py3.14-php8.5-20261103.md`
     must list CIU 7.19.0, cmru 7.1.3, nyxloom 0.12.0 and the pwmcp client 1.64.0-r4.
   - **Registry.** `docker buildx imagetools inspect ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-latest`
     must give the same digest as `…-20261103`.
   - Time: about 2 minutes.
8. **Pre-pull into the host store.** The devcontainer's docker is the host daemon, and pulled images survive the
   reboot.
   - Run `nice -n 10 docker pull ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-20261103`.
   - Then pull `…-latest`.
   - Time: 3-8 minutes.
9. **Quick image check** (about 1 minute).
   - Run `docker run --rm --pull=never --name mdt-cockpit-check-20261103 --cgroup-parent dev-gates.slice --entrypoint bash ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-20261103 -lc 'ciu --version; cmru --version; nyxloom --version; claude --version; command -v finalize_container_environment.py'`.
   - If it is left behind, remove it by that exact name only.
10. **Save the handoff in two places.**
    - Write the forward-oriented handoff to Claude memory (`~/.claude/...`, persisted).
    - Make a safety copy of the current and previous controller program dirs to
      `<host-scratch-dir>/mdt-carry/`. That host path survives even if the old
      container were lost.
    - Use `ionice -c3 nice -n 19 cp -a`. About 3.3 GB, 1-3 minutes.
11. **Hand back to the operator for the reboot.** Lift the main freeze.

*If the release is not green by the deadline:* skip steps 7-9, do step 10, and reboot. After the switch the vbpub
devcontainer uses the 20261027 `-latest` image, and steps 1-9 are repeated from inside it.

### After the reboot (operator on the host, before opening VS Code; about 25-40 minutes)

12. **Host health** (2 minutes).
    - The runtime sockets must exist: `test -S /run/mdt-buildkitd/buildkitd.sock`, `test -S /run/docker-api/docker.sock`
      and `test -S /run/cgprofile/ctl.sock`.
    - `systemctl show dev-interactive.slice dev-gates.slice dev-background.slice -p LoadState,ActiveState` must show
      every slice loaded and active.
    - The production game server containers must be up; check them by name only.
    - Both devcontainers need all of this, so a failure here blocks the rollback as well. Fix the host first.
13. **COW mount** (new devcontainer only). `findmnt /srv/devcontainers-cow && test -d /srv/devcontainers-cow/$USER`.
    The initializer does **not** create this source: `to_home_dir` skips non-home paths
    (`.devcontainer/initialize_container_environment.py:394-402`), and Docker then refuses the
    `.devcontainer/devcontainer.json:141` bind. If it is missing, see operator decision D4.
14. **Migrate the home.** Use the documented script in `mdt-cockpit/docs/HOME-PERSISTENCE.md:88-111`. Run it as the
    host user (uid 1003 equals the in-container vscode user) with `NEW=~/mdt-cockpit-vbpub-home`.
    - It covers `.claude`, `.claude.json`, `.ssh`, `.gnupg`, `.minisign`, `.claudelink`, `.pi`, `.reasonix`, `.openclaw`,
      `.gitconfig` and `.codex*` (without `packages/standalone`), plus `.config/{gh,git,docker}` and `ai.env`.
    - About 24 GB in total, of which `.codex/sessions` is 12 GB.
    - Prefix it with `ionice -c3 nice -n 19`. About 5-10 minutes.
    - Copy, never move: `~/mdt--mounted-folders` stays the rollback.
15. **Carry the container-layer data.** The old container is stopped and stays untouched.
    - Run `docker cp dstdns-devcontainer-vb:<controller-home>/<item> ~/mdt-cockpit-vbpub-home/` for:
      - the current and previous controller program dirs;
      - `console-corpus` (6.8 GB, the local pwmcp corpus);
      - `gate-evidence`, `iomon-20261027`, `worktree-inventory-20261027.json` and `.md`;
      - `truenas-25.10.4-boot-pool-patch.tar.gz`, `pwmcp-instance-ciu.toml.j2.bak-20261027`;
      - `.zsh_history`, `.agents`, `.copilot`.
    - Skip `.venv`, `.local/bin`, `.oh-my-zsh`, `.cache`, `.npm` and the seeded rc files (HOME-PERSISTENCE.md:150-161).
    - About 11 GB, 3-6 minutes.
16. **Export `DOCKER_GID`** in the environment that launches the VS Code server:
    `export DOCKER_GID=$(getent group docker | cut -d: -f3)`. This is the same requirement as the old container.
17. **Open the new devcontainer.** Open `<host-checkout>` in VS Code and choose "Reopen in Container".
    - The container will be named `mdt-cockpit-vb-vbpub`.
    - The first create runs the UID-update build through the remote builder, then `finalize_container_environment.py`.
    - Time: 3-8 minutes.

### Inside the new devcontainer (controller; about 10 minutes)

18. **Identity and state.**
    - `id` must show uid 1003.
    - `~/.claude/projects/-workspaces-vbpub/memory/MEMORY.md` must be present (the workspace path is unchanged, so the
      project key is the same).
    - The Claude and Codex logins must work.
    - the current controller program directory's report archive must be present.
19. **Image tools.** `ciu --version` must be 7.19.0 and `cmru --version` must be 7.1.3; both come from `/opt/mdt`.
20. **Builder and slices.**
    - `docker buildx inspect mdt-managed` must show it running.
    - `echo $CGROUP_PARENT_DEV_GATES` must print `dev-gates.slice`.
21. **Install the home tools.** These are not in the image.
    - Make a temporary directory holding the four wheels:
      - `run-gate-project/artifacts/run-gate-v24.3.0/dist/run_gate-24.3.0-py3-none-any.whl`
      - `assay/artifacts/assay-v11.0.0/dist/assay-11.0.0-py3-none-any.whl`
      - `truenas/tnt/artifacts/tnt-v0.2.0/dist/tnt-0.2.0-py3-none-any.whl`
      - `cmru/dist/cli_extended-0.5.0-py3-none-any.whl`
    - Install them with `uv tool install --no-index --find-links <dir> <wheel>`, or into a dedicated
      `~/.venv-tools` with its entry points linked into `~/.local/bin`, which is last on PATH.
    - **Do not** reinstall ciu or cmru into the home. That would put a second copy of the shared `worktree` package
      next to the one in the image.
    - Time: about 2 minutes.
22. **Gate readiness.** Run `cd mdt-cockpit && run-gate doctor` (1 minute). If the release was deferred, run steps
    1-9 now.

### Leave untouched throughout

- The `dstdns-devcontainer-vb` container (stopped) and its predecessor image `legacy-predecessor-vsc-devcontainer:trixie-py3.14-php8.5-latest`.
- `~/mdt--mounted-folders/`.
- The production game server containers.
- `tester-unified:local` and `mdt-buildkitd`.
- **No:**
  - `docker system prune`, `docker image prune` or `docker container prune`;
  - rebuild of the dstdns devcontainer;
  - **vendoring of the mdt-cockpit template into dstdns** (see R6);
  - `host-setup/install.sh` on this host. It requests a Docker restart, which on r1002 lost a running container
    despite live-restore (`reports/R1002-MDT-COCKPIT-2026-11-02.md`). Here the daemon is shared with the production
    server. The `[Unreleased]` host-setup fixes do not need a reinstall for this switch.

## 5. Risks that could leave the operator without a devcontainer, and the rollback

| # | Risk | Mitigation / rollback |
|---|---|---|
| R1 | Empty new home: no Claude, Codex or SSH state | Steps 14-15. The old state is copied, never moved. |
| R2 | Working data stranded in the old container's layer (11 GB, including the release-program directory and the corpus) | Step 10 safety copy plus step 15 `docker cp` from the stopped container. Never remove that container. |
| R3 | `/srv/devcontainers-cow/$USER` missing after the reboot, so Docker refuses to create the container | Step 13 and decision D4. |
| R4 | Host runtime sockets or slices not up after the reboot | Step 12. They are shared prerequisites, so fix the host before either container will start. |
| R5 | Release fails late (oci-push, push, or a promotion race) | Freeze main (step 1). A retry is about 25 minutes. Past the deadline, reboot anyway and switch on the 20261027 image. |
| R6 | The template default name `${localWorkspaceFolderBasename}-devcontainer-${localEnv:USER}` (`mdt-cockpit/templates/devcontainer.json:23,203-204`) expands to **`dstdns-devcontainer-vb`**, the old container. Vendoring the template into dstdns and rebuilding would replace it and wipe its writable layer | Do not touch dstdns's `.devcontainer` today. The vbpub copy is already named `mdt-cockpit-…`. |
| R7 | GHCR is unavailable during the post-reboot UID-update build | Step 8 pre-pull. The remote builder still fetches the base itself; if GHCR is down, use the rollback. |
| R8 | Disk: the host root is 76% full with 163 GB free; the carry needs about 35 GB plus the pulls | Acceptable. Do not run the copy while `prepare` builds. |

Rollback paths:

- **(a) The new container will not start, or its state is wrong.** Open `<host-checkout>` and choose
  "Reopen in Container". This restarts the old `dstdns-devcontainer-vb`, which still has its layer data and still
  mounts `<checkout>`. Time: 2 minutes.
- **(b) The new image is broken.** Make a local, uncommitted edit of `.devcontainer/devcontainer.json:41` to
  `…:trixie-py3.14-php8.5-20261027`, then rebuild. The home stays the same. Time: 5-8 minutes.
- **(c) The migration was wrong.** Delete `~/mdt-cockpit-vbpub-home` and redo steps 14-15 from the untouched sources.

## 6. Minimal operator decisions

- **D1 — Target devcontainer.** The target is the vbpub checkout's devcontainer (`mdt-cockpit-vb-vbpub`), and dstdns
  stays on the old container for now. *Recommended: yes.*
- **D2 — Home migration.** Copy the old state into `~/mdt-cockpit-vbpub-home`: about 35 GB in total, including
  `.codex/sessions` (12 GB, needed to resume Codex sessions) and `console-corpus` (6.8 GB). *Recommended: copy
  everything listed in steps 14-15.*
- **D3 — Deadline rule.** If the release is not green by the reboot time, reboot anyway and release from the new
  container. *Recommended: yes.*
- **D4 — Missing COW directory (only if step 13 finds it missing).** Either create `/srv/devcontainers-cow/$USER`
  (with the COW filesystem mounted), or comment out `.devcontainer/devcontainer.json:141` locally for now.

## 7. Side findings (none block the release)

- **P3:** The vendored `.devcontainer/initialize_container_environment.py` lags the template by 49 diff lines; its
  fallback names are the old `~/mdt-cockpit-home`. It is harmless while `devcontainer.json` is readable. Re-vendor it
  after the switch.
- **P3:** The ID MDT-032 is used twice in `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md:471` and `:661`. Renumber one.
- **P3:** The smoke lane is red on this host because of the second MDT-032 (`iogov/tests/test_observe.py:328` depends
  on the live disk fill). It does not affect the release, but it will block any mdt-cockpit merge gate on this host
  until that test is made hermetic.
