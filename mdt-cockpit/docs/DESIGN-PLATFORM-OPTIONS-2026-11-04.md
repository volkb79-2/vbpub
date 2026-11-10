<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/DESIGN-PLATFORM-OPTIONS-2026-11-04.md -->
<!-- relation: platform options design -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Platform design options, 2026-11-04

**Author:** Opus planner, read-only analysis.

**Sources:** four read-only research passes over main `d9d3bf3ca`, plus the branches `fix-r1002-findings-20261104` and `fix-r1002-gate-ownership-20261104`. Nothing was run.

**Section layout.** Each section gives: how it works today (with file:line citations), why it bites, the options, a recommendation, the backlog entries to file, and the packages in order.

**Backlog ids** are written "next free". In-flight branches already claim MDT-049..052, RG-150, B209 and CP-39/40, so verify each id when filing.

---

## 1. BuildKit `mdt-managed` and local base images

### Today

**What mdt-managed is.** It is the host unit `mdt-buildkitd.service`. It runs `docker run --rm --name mdt-buildkitd` (`units/mdt-buildkitd.service.in` ~85-100) with:
- image `DEV_BUILDKITD_IMAGE`, default `moby/buildkit:buildx-stable-1-rootless`, a floating tag (`host-setup.env.example:398`);
- rootless uid 1000 and an OCI `runc-overlayfs` worker, not containerd (`BUILDKITD-FLOWS.md:23`);
- `--cgroup-parent=dev-buildkitd.slice`. That slice has MemoryHigh 1.5G, MemoryMax 2G, CPUWeight 50, IOWeight 50, an automatic CPU quota, and an IOPS ceiling from reconcile (`host-setup.env.example:382-390`).

**Cache and GC.** The cache is persistent and compressed on btrfs at `/srv/mdt-cockpit-cow/buildkitd-cache` (MDT-024). GC keeps 50 GB and max-parallelism is 4 (`buildkitd.toml.in`).

**Shim.** `scripts/docker-buildx-shim.sh:71-76` forces `mdt-managed` whenever `BUILDX_BUILDER` is unset or `default`. It never falls back to the default builder.

**Guard.** `mdt-buildkit-guard` reports, or terminates, accidental `docker-container` workers (`BUILDX_ACCIDENTAL_CONTAINER_POLICY`).

**`--load`.** It streams through the client into dockerd's store (`BUILDKITD-FLOWS.md:97-114`).

**The limitation.** A remote builder cannot see dockerd's store, so `FROM <local-tag>` fails. `BUILDKITD-FLOWS.md:151` already names `--build-context …=oci-layout://` as the mitigation.

**Bypasses.** These builds use the docker driver instead:
- `cmru/build-initial-standalone.sh:312,320` (tester-base, wheel-builder);
- `tester-unified/build-image.sh` with a local base (`tester-base/docs/CONSUMERS.md:13,23`);
- `wheel-builder/Dockerfile:35`;
- srdm `tools/go-base.sh:39` → `gate.sh:82` / `canary-run.sh:34`. These are plain `docker build`, so through the shim they would reach `mdt-managed` and fail. Needs a live check.
- pwmcp's release "needs `BUILDX_BUILDER=default BUILDKIT_HOST=`" only by environment. There is no committed reason (`build-push.py:115-195` supports both), so investigate.

**Shim caveat** (from the code, not checked live). On the host the shim remaps `BUILDX_BUILDER=default` back to `mdt-managed`. The documented bypass therefore only works inside the devcontainer. On the host it needs `--builder default` or `MDT_BUILDX_PASSTHROUGH=1`.

**What a bypass loses:**
- **Governance:** `dev-buildkitd.slice`. The build runs in dockerd's context. `--cgroup-parent` on RUN steps of the docker driver is unverified.
- **Cache:** the btrfs cache and GC. The cache moves onto the root ext4 journal, which is the contention MDT-024 removed.
- **Safety:** the fail-closed shim and the guard.
- **Provenance:** attestations. The docker-driver `--load` carries none. This matters little for local gate images.

### Why it bites

Every base→derived chain leaves the governed path:
- tester-base → wheel-builder / tester-unified;
- tester-unified-go → srdm gate.

The recipe also differs between host and container, and the base is identified by a mutable local tag (TB-11).

### Options

**A. OCI layout export plus digest-pinned named contexts.**
- The base build exports `--output type=oci,dest=<project>/artifacts/oci/<name>,tar=false`, plus `--load` if it is ever `docker run`.
- Derived builds pass `--build-context <ref>=oci-layout://<dir>@sha256:<digest>`.
- **Pros:** stays fully governed (slice, cache, guard). Digest identity fixes the TB-11 class. No new daemon. The pattern already exists in `release-repack.sh:101,113`.
- **Cons:** each chain passes the context. cmru needs a `build_contexts` option. One extra export per base, kept in gitignored `artifacts/`.

**B. Loopback registry** (`registry:2`).
- **Pros:** uniform `FROM` syntax.
- **Cons:** a new stateful service that needs GC and a slice. The rootless buildkitd container needs a network path plus insecure-registry config. A second store to keep consistent.

**C. Containerd image store shared with BuildKit.**
- **Cons:** needs a dockerd restart and migration on the production-shared host; `DOCKER-IMAGE-STORE.md:11,129` advises against it. The rootless remote buildkitd still cannot use dockerd's containerd. Rejected.

**D. A per-project `docker-container` builder.**
- **Cons:** exactly what the guard flags. Its cache is not shared and it is ungoverned. Rejected.

**E. Formalise the bypass** (`--builder default` plus a mandatory `--cgroup-parent`).
- **Cons:** keeps every loss above.

### Recommendation: A

- The bypass stays only for the **fresh-host bootstrap** (`build-initial-standalone.sh`, before `mdt-managed` exists). It must be spelled `--builder default` and never selected by environment variable.
- **Tester chain:**
  - tester-base exports a layout.
  - `build-image.sh` and wheel-builder take `--base-oci <dir>` and map `tester-base:local` to `oci-layout://…@<digest>`.
  - The base digest enters the source hash, which closes TB-04.
  - tester-unified itself keeps `--load`, because lanes `docker run` it.
- **srdm:** `go-base.sh` exports a layout. The gate and canary builds use `buildx build --build-context`.
- **cmru:** add an optional `build_contexts` to the build handler. Values are limited to `oci-layout://<repo-relative>@sha256:` or `docker-image://<registry>@sha256:`. This is a fix-level change so it can ride a patch.
- **pwmcp:** identify the local base first. If there is none, drop the bypass.
- **Pilot check:** confirm that a context key with a tag (`tester-base:local`) maps a tagged `FROM`.

### Backlog entries

- MDT-next: "Local base images via oci-layout contexts on mdt-managed". Records the shim caveat and the bootstrap-only bypass rule.
- TB-next: tester-base exports an OCI layout, and tester-unified and wheel-builder consume it by digest. TB-04 and TB-11 fold in.
- srdm entry: go-base, gate and canary.
- KI-next (cmru): `build_contexts`.
- PWMCP-next: find the release's local base, or drop the bypass.

### Packages, in order

1. Tester-chain pilot, measured against the bypass.
2. wheel-builder.
3. srdm.
4. cmru `build_contexts`.
5. pwmcp investigation.
6. Docs: BUILDKITD-FLOWS §6 and CONSUMERS. Remove the documented bypasses.

---

## 2. DOCKER_GID

### Today: one gid, three different needs

**1. Socket access.** The relay socket is `/run/docker-api/docker.sock`, `root:docker 0660` (`units/docker-api-socket.conf:56-63`). Consumers:
- devcontainer `--group-add ${localEnv:DOCKER_GID}`. It must be exported in the host shell before launch (`.devcontainer/devcontainer.json:228-231` and both templates).
- the r1002 runner (`start-cockpit-runner.sh:14`);
- the nyxloomd `group_add`, with hard-coded `docker_gid = 994` (`nyxloom/nyxloomd/ciu.defaults.toml.j2:24-27`);
- the ciu TLS helper container `user_uid:docker_gid` (`ciu/src/ciu/workspace_env.py:1021`);
- `tester-unified/run:125-233`, which derives the gid by `stat` of the socket. This is the correct pattern.

**2. File-ownership group.**
- ciu hostdirs are `CONTAINER_UID:DOCKER_GID 0775` (`engine.py:757-788`, `docs/CIU.md:275`).
- Secrets are chowned (`secrets/materialize.py:206,384`).
- TLS readability checks (`workspace_env.py:1007-1095`).
- mattermost hostdirs `70:`/`2000:DOCKER_GID`.
- Source: `_detect_docker_gid` tries env, then `stat /var/run/docker{-host,}.sock`, then `getgrnam` (`workspace_env.py:511-526`). The result is persisted into `ciu.env` (`:2036`).

**3. `/run/cgprofile` directory group** (`root:docker 0770`).
- The cockpit reaches `ctl.sock` through the same `--group-add`.
- run-gate (`run_gate.py:8700-8712`) and assay (`resource-observer-gid.sh:56-75`) use it as a **negative** assertion (observer gid ≠ docker gid). They derive it by `stat`, which is correct. assay also accepts the env override `ASSAY_GATE_HOST_DOCKER_GID`.

**Finalize** stats only `/var/run/docker{-host,}.sock` (`finalize_container_environment.py:143-146`). Under `DOCKER_HOST=/run/docker-api/…` it most likely prints `docker_gid=?` (`:702`).

**The worktree-create warning** with the exact text "DOCKER_GID not found" does not exist on main. The nearest are `workspace_env.py:526` (an error) and `:1009` (a warning).

### Why it bites

- **Chicken-and-egg:** `--group-add` needs the host's gid at `docker run`, before anything in the container can look it up. Only a host-shell export can supply it.
- **Stale fallbacks:** ciu's stat fallbacks probe paths the new layout no longer mounts, and `getgrnam("docker")` inside an image can name another gid.
- **Stale persistence:** a machine fact persisted into `ciu.env` is wrong on every other host.
- **Ownership coupled to a host number:** the docker gid also drives hostdir ownership, so a host move silently breaks existing `vol-*` directories.

### Options

**A. A fixed, estate-reserved group on the relay socket.** host-setup creates it. The cockpit image bakes `vscode` into a group with the same gid, and docker applies the image's `/etc/group` supplementary groups, so no runArg is needed.
- **A1: reuse the image's `mdt` group (gid 2750).** Fewest parts. But "may write /opt/mdt" then also means "is root-equivalent via Docker".
- **A2: a dedicated `mdt-docker` group (gid 2751).** host-setup creates it via sysusers and refuses if the gid is taken.

**B.** The initializer writes the host gid into a file that runArgs reads. Still host-specific, just automated.

**C.** Status quo with better error messages.

### Recommendation: A2

**Host side.** The relay socket, `/run/docker-api` and `/run/cgprofile` become `root:mdt-docker`. The host `/var/run/docker.sock` stays `root:docker`.

**Consumers:**
- **Socket access.** The image adds `vscode` to `mdt-docker`. The templates and the r1002 runner drop `--group-add`. nyxloomd and every other socket user derive the gid by `stat` of the socket they mount (`DOCKER_HOST` when it is `unix://`).
- **ciu.** `docker_gid` becomes a value computed by `stat` of the effective socket. The `DOCKER_GID` env input and the `ciu.env` key are removed with no alias; a stale `ciu.env` key is refused, naming the replacement. Hostdir and secret ownership keep using the derived gid. Decoupling ownership (a `hostdir_group` key) would be a separate change.
- **assay and run-gate.** Keep deriving from `/run/cgprofile`. Drop `ASSAY_GATE_HOST_DOCKER_GID`. Assert the observer gid differs from both `mdt-docker` and the host `docker` gid.
- **finalize** stats the `DOCKER_HOST` socket.

**Migration (one time, documented):**
1. host-setup creates the group and regroups the socket.
2. Restart docker.socket. This is disruptive, so batch it with the next host-setup restart.
3. Rebuild the image and update the templates.
4. Regenerate `ciu env`.
5. `chgrp` the existing hostdirs, following the README recipe.

### Backlog entries

- MDT-next: host group and socket regroup, plus a `check.sh` assertion.
- MDT-next: image and templates (`vscode` in `mdt-docker`, drop `--group-add`), and finalize stats `DOCKER_HOST`.
- CIU-next: derive from the socket; remove the env var and the `ciu.env` key.
- NL-next: nyxloomd derives the gid instead of using 994.
- B-next (assay): drop the env override; assert against both gids.
- NC-next: the runner drops `--group-add`.

### Packages, in order

1. host-setup.
2. Image and templates.
3. ciu.
4. nyxloomd.
5. assay and run-gate assertions.

Then one coordinated host step per host: restart docker.socket, then rebuild the cockpit.

---

## 3. Lane container uid

### Today

**tester-unified bakes uid 1003.** `tester-unified/Dockerfile:405-423` creates `tester:1003:1003`, sets `HOME=/home/tester` and `USER 1003`, and makes `/opt/tester-venv` owned by 1003. The reasons:
- `pwd.getpwuid` in topos (`daemon/deploy.py:311,329`, `procs/sampler.py:316`, `actions/execute.py:122`);
- `getpass.getuser` in nyxloom (`control_auth.py:218`);
- a writable HOME;
- doctrine `nyxloom/reference/DOCTRINE.md:28-37`.

**1003 is asserted** in `tester-unified/run:259,305` and `assay/tools/self-qualification-container.sh:601`.

**run-gate** already supports `[environments.<n>] user = "invoker" | "uid[:gid]"` (`run_gate.py:1056-1066,2422,15672-15681`, RG-106). The tester environment does not use it. RG-118 (refuse a uid with no passwd entry) is open. RG-99 injects `safe.directory=*` via `GIT_CONFIG_*`.

**cmru tester-gate** relies on the baked uid (`tester_gate.py:855-859`).

**Ownership.** `.run-gate/` and `.assay/` live in the bind-mounted checkout. cowtree requires `MDT_COW_ROOT` to be owned by the caller (`libraries/cowtree/src/cowtree/paths.py:89-90`). assay replaces git's config, and setuptools-scm strips `GIT_*`, so `safe.directory` cannot help (RG-150).

**r1002.** uid 1000 owners produced `dubious ownership` ("editable assay install failed"). The fix remapped the host users to 1003 (NC-04, `provision-runner.sh:26-49`).

No nss_wrapper or generated passwd exists anywhere in the estate.

### Why it bites

- Every host must use exactly uid 1003 for checkout, CoW root and cockpit, or lanes fail with an obscure git error.
- A `docker run` cockpit has no automatic remap (MDT-052).

### Options

**A. Run lanes as the checkout owner.**
- uid comes from `stat` of the judged worktree root and is passed as `--user uid:gid`.
- The launcher writes a read-only `passwd`/`group` overlay: the image's file, read once per image id and cached, plus one line for that uid with `HOME=/home/lane`. It is bind-mounted over `/etc/passwd` and `/etc/group`.
- Each lane gets its own writable HOME and XDG directories.
- **Pros:** works for any host uid. `getpwuid`, git, ssh and getpass are satisfied. git ownership is trivially right, and the cowtree check passes. No LD_PRELOAD.
- **Cons:** `/opt/tester-venv` is owned by 1003, so every write into it (for example an editable assay install) must move to a per-lane prefix. That needs an audit.

**B. nss_wrapper (LD_PRELOAD).**
- **Cons:** fails for static or Go binaries and for setuid. More fragile.

**C. Keep 1003 and add the RG-150 preflight**, which refuses with one line when the tree or CoW owner differs from the lane uid.
- **Effect:** better errors, same host constraint.

**D. `"invoker"` uid.**
- **Cons:** inside a cockpit that is `vscode`, which equals the owner only after a remap. "owner" is the real requirement.

### Recommendation: A, with C's preflight kept as the safety net

**Phase 1 (run-gate).**
- Add `user = "owner"`: resolve the uid by `stat` of the worktree root and refuse root.
- Add the overlay and a per-lane HOME.
- Fold RG-118 in (refuse a uid with no passwd entry when there is no overlay).
- Add the RG-150 owner preflight.

**Phase 2 (tester image).**
- The image becomes uid-agnostic. `/opt/tester-venv` is read-only by contract, and every `pip install` or `--editable` in gate scripts moves to a per-lane prefix (`PIP_PREFIX`/`PYTHONUSERBASE` under the lane HOME, or a lane venv created with `--system-site-packages`).
- The `== 1003` assertions become `== owner`.

**Phase 3 (other launchers).** assay `tools/*gate.sh` and cmru tester-gate add the same overlay, each with its own small helper. The contract is documented once in `tester-unified/docs/CONSUMERS.md`.

**Phase 4.** `owner` becomes the default for the tester environment. NC-04 and MDT-052 are relaxed to "any uid, as long as checkout, CoW root and cockpit user share it". Lanes that need Docker add the socket gid derived by `stat` (§2).

### Backlog entries

- RG-next: `user = "owner"` with the overlay and lane HOME. Supersedes RG-118. Includes the RG-150 preflight.
- TB-next: tester-unified uid-agnostic, with a read-only venv.
- B-next (assay): launcher overlay; editable install into a lane prefix.
- KI-next (cmru): tester-gate overlay.
- After phase 4, update NC-04 and MDT-052.

### Packages, in order

1. run-gate phase 1.
2. Tester image audit and change.
3. assay launchers and cmru tester-gate, in parallel.
4. Default flip and docs.

---

## 4. cgprofile host daemon

### Today

**ciu-managed container** `cgprofile-host-daemon` (`scripts/cgroup-profiler/ciu.compose.yml.j2`):
- image `ghcr.io/volkb79-2/cgprofile:1.1.0`, pinned in `ciu.toml`;
- `privileged`, `pid: host`, `network_mode: none`, `cgroup_parent: cgprofile.slice` (a literal), 1g/2g memory, a sessions volume;
- binds: `/run/cgprofile`, `/proc` read-only, `/sys/fs/cgroup`, D-Bus, DAMON.

**Configuration.** The gates slice comes from `[cgprofile.daemon] gates_slice` (default `dev-gates.slice`). Releases ship an OCI image only (`cmru.toml:33`, MDT-048).

**What host-setup already owns:**
- the `/run/cgprofile` directory;
- the `cgprofile-observer` group and its directory (`install.sh:120-210,1026-1029`).

The operator installs `infra/cgprofile.slice` by hand. host-setup's README calls the daemon itself "out of scope" (`README.md:72-75,450-455`).

**Readers:**
- run-gate: `ctl.sock`, with a `docker exec` fallback (`run_gate.py:688,711`), and the observer socket (`:501-507`);
- assay: `ASSAY_RESOURCE_OBSERVER_SOCKET`;
- cockpits: the `/run/cgprofile` bind.

**Upgrade.** Change the tag, run `ciu up --profile host` from a checkout, verify the revision label (`docs/CONSUMERS.md:127-195`). On r1002 that checkout carries **two uncommitted edits** (`RUNBOOK.md:52-90`).

### Why it bites

- A host service depends on a repo checkout, ciu, and hand edits.
- It restarts with dockerd, yet a docker restart is exactly what it should observe (MDT-047).
- Its slice names do not come from host-setup's configuration.

### Options

**A. A host-setup unit running the pinned image** (`docker run` in `ExecStart`, like buildkitd), configured from `host-setup.env`.
- **Pros:** independent of any checkout.
- **Cons:** duplicates the compose spec (privileges and mounts) into a unit. Still needs dockerd.

**B. A native systemd service from a released wheel.** The collector is stdlib-only.
- host-setup installs the sha256-pinned wheel into `/opt/cgprofile`.
- The unit uses `Slice=cgprofile.slice`, `RuntimeDirectory=cgprofile` and host-setup's tier names.
- The unit template ships **inside the wheel** (`cgprofile host-unit --print`), so cgprofile keeps a single source.
- **Pros:** no privileged-container emulation. Survives docker restarts. Same pattern as `mdt-psi-guard`. Upgrading means a wheel swap and a restart.
- **Cons:** needs the wheel artifact first (MDT-048 step 1). The report tier, which uses pandas, stays an image or optional extra.

**C. Keep ciu**, but the gates slice is rendered from `/etc/mdt/host-setup.env` and no hand edits are needed.
- **Effect:** smallest change, still coupled to a checkout and dockerd.

### Recommendation: B, with C as the interim

1. Interim (C): remove r1002's hand edits by rendering them from host config.
2. A cgprofile release ships the collector wheel plus `host-unit`.
3. host-setup adds `CGPROFILE_WHEEL` (pinned version plus sha256, refused when empty if enabled), installs into `/opt/cgprofile`, renders the unit, and restarts only on drift.
4. Socket paths, groups and the observer layout stay unchanged, so run-gate, assay and cockpits need no change.
5. Retire the ciu deployment after one release with a dated migration window. This is not an alias.

### Backlog entries

- CP-next: collector wheel plus `host-unit`.
- MDT-048: rescope to "host service from the wheel", which also covers the client CLI.
- MDT-next: host-setup installs and pins cgprofile, and the README changes its scope statement.
- NC-next: the r1002 migration.

### Packages, in order

1. C.
2. Wheel and `host-unit` (cgprofile 1.2.x or 1.3.0).
3. host-setup install.
4. r1002 first, then the shared host in an idle window.
5. Retire ciu.

---

## 5. Direct access to the r1002 runner container

### Today

`mdt-cockpit-runner` runs the **devcontainer** image, not the plain one (`runner.env.example:30`). It is started by `start-cockpit-runner.sh` (branch `fix-r1002-findings-20261104`, lines 13-39):
- `docker run -d --init --user 0:0 … sleep infinity`;
- `--group-add <docker gid>`;
- `SYS_ADMIN`, `/dev/fuse`, unconfined seccomp and apparmor;
- `--cgroup-parent=dev-interactive.slice`.

**Mounts:**
- checkouts at `/workspaces`;
- `/run/docker-api`, `/run/cgprofile` and `/run/mdt-buildkitd`;
- host `.ssh` read-only;
- a persistent home `<home>/mdt-cockpit-runner-home` → `<controller-home>`;
- `/tmp`;
- the CoW root, rslave.

After start it remaps `vscode` to the runner uid, then runs finalize.

**Entry.** ssh to the host as `runner` (root only for bootstrap), then `docker exec -u vscode …`, often wrapped by `run-job` or `run-lane.sh` (`RUNBOOK.md:7-10,130`). The images have no sshd, no s6 and no EXPOSE (negative grep). `--init` gives a tini PID 1.

### Options

**A. Host ssh plus a restricted second key** with `command="docker exec -it -u vscode -w /workspaces mdt-cockpit-runner zsh -l"`.
- **Pros:** a one-step shell. Only the host's sshd, already hardened and pinned.

**B. `docker context create r1002 --docker host=ssh://runner@r1002`, plus VS Code "Attach to Running Container".**
- **Pros:** a full IDE in the real container with the remapped uid. No new daemon. Uses the existing ssh trust.

**C. sshd inside the container.**
- **Cons:** needs a supervisor (tini alone cannot run sshd beside `sleep`), persistent host keys, key distribution, and a published port (loopback plus ProxyJump), which adds a second auth surface. The container already holds the docker socket, which is host root, so it gains no isolation. Rejected.

**D. Switch to the plain image.** r1002 is the cockpit **parity** runner, so keep the devcontainer image. This choice is unrelated to access.

### Recommendation: B for the IDE, A for the shell; no sshd in containers

- **Security note:** `runner` is in the docker group, so it is host-root equivalent. Its keys must be held only by operators and the controller. B and A add no new exposure.
- **uid:** relies on the single-uid rule (§3). After §3 phase 4 only "same uid everywhere" remains.

### Backlog entries and package

- NC-next: the restricted `cockpit` key, plus a `docker context` recipe in the runner README.
- MDT-next (docs): "attach to a remote runner".
- One small netcup-runner package, after `fix-r1002-findings` merges.

---

## 6. TrueNAS VM features in tnt

### Today

**tnt's VM surface:**
- `vm list|show|create|update|<lifecycle>|delete|console` and only `vm device add` (`truenas/tnt/src/tnt/cli.py:1349-1416`).
- Devices are visible only inside `vm.query` rows (`workflows.py:981-987`).
- The v27 adapter already declares `vm.device.query/create/update/delete` (`adapters/v27.py:73-75`).
- `vm.device.delete` is destructive in `policy.py:43`. Its options are `{force, raw_file, zvol}` (`docs/API-METHODS-V27.md:80`).
- `vm.device.virtual_size` and `vm.device.convert` appear nowhere in the repo.

**Paths.** tnt never uploads anything. `--iso` and `--path` are NAS paths that must start with `/mnt/` (`workflows.py:1063`), and they are not checked for existence.

**Fake server.** Device update and delete are loose (`testing.py:1063-1081`): delete leaves `vm["devices"]` stale.

**Scope wording.** `DESIGN.md:21` says tnt "targets vanilla TrueNAS; it does not rely on any relaxation patch". That conflicts with "vanilla AND patched".

**Backlog.** It ends at TNT-59 (vm-lab placeholder), so TNT-60 and TNT-61 are free.

**debian-install-v2's TrueNAS content** is only `docs/CONSUMERS.md:170-190`: build a NoCloud seed ISO and attach it next to a Debian cloud-image disk on TrueNAS or Proxmox. How the image gets onto the NAS is not covered.

### Assessment

**tnt is the right home.** It is the single API client, with the v27 adapter, the policy, the plan/apply flow, the fake server, and the live lane. Device and image-import calls are VM-management API calls.

Change `DESIGN.md:21` to: "works on vanilla v27; patch-only features are capability-detected (TNT-35 pattern)".

### Addendum: draft backlog text

**TNT-60 — VM devices: list, update, delete.**
- **Read:** `vm device list <vm>` uses `vm.device.query` filtered by vm id.
- **Update:** `vm device update <vm> <id> …` is plan/apply and runs `validate_vm_device_attributes`.
- **Delete:** `vm device delete <vm> <id> [--zvol] [--raw-file] [--force]` is destructive and needs typed confirmation. Deleting a zvol requires a second typed confirmation that names it.
- **TUI:** a "Devices" view using `pending_vm_*` staging.
- **Fake server:** `update` validates keys, and `update`/`delete` keep `vm["devices"]` consistent. Bundle this with TNT-56.
- **Docs and tests:** regenerate `cli-surface.json`, `CLI-SPEC.md` and the action matrix. Run the live lane on vanilla v27.
- **Paths:** NAS paths only (`/mnt/<pool>/…`), and the boot-pool refusal is kept.

**TNT-61 — Disk-image import (cloud image → zvol).**

*Stage 1, optional upload.* `tnt files put <name> <dataset-dir>` through the TrueNAS API upload. This is still API-only, with no SSH.
- The only accepted local source is the repo-level `downloads/<name>`, opened read-only. tnt resolves it from its own location.
- The upload is verified against `<name>.sha256`.
- There is no env path override. An explicit argument may only name a file inside `downloads/`; absolute paths and `..` are refused.

*Stage 2, import.* `vm disk import <dataset-path> --zvol <pool/zvol> [--size auto|N]`.
- It sizes from `vm.device.virtual_size`, then runs the `vm.device.convert` job.
- The plan shows the source, the virtual size, the target, and whether the target is new or will be overwritten. Overwrite is destructive and needs typed confirmation.
- The boot-pool refusal applies. Remote inputs are named only by dataset path.

*Capability.* Probe for `convert` and `virtual_size` on v27; they are not in `API-METHODS-V27.md`. If they are absent on vanilla, hide the feature there with a clear message.

*Local state.* None for this flow. tnt-owned local test VMs (TNT-59) live under `truenas/tnt/artifacts/vm-lab/<vm>/`, with a committed placeholder.

*Tests.*
- fake handlers for `virtual_size`, `convert` (as a job) and the upload;
- refusal of a source outside `downloads/`;
- a sha256 mismatch.

**debian-install-v2 TrueNAS recipe.** debian-install-v2 stays the seed-ISO builder only. Its CONSUMERS section becomes a pointer:
1. Put the cloud image and the seed ISO in `downloads/`.
2. `tnt files put`.
3. `tnt vm disk import`.
4. `tnt vm create --iso <seed>`.
5. `tnt vm device add`.

No TrueNAS API code goes into debian-install-v2.

### Packages, in order

1. TNT-60 together with TNT-56.
2. The TNT-61 capability probe on v27 (vanilla versus patch-only).
3. TNT-61.
4. The debian-install-v2 docs pointer.

---

## Decisions for the operator (only the open ones)

1. **§2 socket group.** A dedicated `mdt-docker` group at fixed gid 2751 (recommended), or reuse the image's `mdt` group (2750)? Reusing it has fewer parts but merges "/opt/mdt writer" with "Docker access". Either way, the migration costs one docker.socket restart per host.
2. **§4 cgprofile.** Approve B: a native systemd service from a released wheel, installed by host-setup, with the ciu deployment retired. The alternative is A: a host-setup unit running the pinned image.
3. **§3 lane uid.** Approve "lanes run as the checkout owner with a generated passwd overlay". After phase 4 this retires the "everything is uid 1003" host rule.
4. **§6 tnt wording and scope.** Approve changing `DESIGN.md:21` to "vanilla v27, patch-only features capability-detected", and allowing the API upload from `downloads/` in TNT-61.

These need no decision and take their defaults:
- §1 option A, keeping the bootstrap-only bypass;
- §5 B plus A, with no sshd in containers.
