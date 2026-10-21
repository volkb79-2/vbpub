# Devcontainer Image Usage

## Build (local)

Build all variants:

```bash
./build-push.py --build
```

This runs the environment resolver (MCR check, tool version resolution, artifact
staging), saves the resolved state to `.build-env.json`, then builds the release
targets through the governed builder.

For the configured CMRU release (`RELEASE_IMAGE_FLOW=load`), `--build` resolves
and builds the release targets into OCI layouts; it does not publish them. The
CMRU lifecycle is build and manifest extraction, the release gate, source
promotion, then a post-gate `--push` that publishes those exact layouts. The
named BuildKit builder is `mdt-managed`, selected and checked by
`scripts/ensure-release-builder.sh` from the release configuration.

For the end-to-end data path, cgroup and slice boundaries, resource defaults,
and guidance for attributing load in `top`, Docker stats, and systemd, read
[MDT build and release architecture](docs/BUILD-ARCHITECTURE.md).

`RELEASE_IMAGE_FLOW` controls the release path:

- `load` is the configured canonical source-first release mode. The governed
  `mdt-managed` BuildKit worker writes unrepacked OCI layouts without loading
  images into dockerd. After the CMRU gate and source promotion,
  `build-push.py --push` copies the exact layouts with `regctl` and verifies
  every remote digest with `crane`. For example, an immutable published
  coordinate may be `trixie-py3.14-php8.5-20260913`.
- `push` is an explicit alternate mode. The governed BuildKit worker exports
  directly to the registry during the build, before the CMRU gate and source
  promotion. Its later CMRU push step reports an already-published terminal
  state; it does not publish a second time.
- `repack` is an optional compression experiment. It builds OCI tar streams,
  extracts those into disk-backed layouts, repacks at `REPACK_TARGET_SIZE`
  (default `2GB`), validates the candidate structurally and by importing it
  through BuildKit, and publishes only after those checks. It does not load the
  original image into dockerd and does not use `skopeo`. Its publication occurs
  during the build, so its later CMRU push step also reports an already-published
  terminal state. The affected image currently fails closed in this lane
  because of the repacker defect recorded in the architecture guide.
- `load` is the configured canonical source-first release mode. The governed
  BuildKit worker writes unrepacked OCI layouts without loading images into
  dockerd. After the CMRU gate and source promotion, `build-push.py --push`
  copies the exact layouts with `regctl` and verifies every remote digest with
  `crane`. For example, an immutable published coordinate may be
  `trixie-py3.14-php8.5-20260913`.
- `push` is an explicit alternate mode. The governed BuildKit worker exports
  directly to the registry during the build, before the CMRU gate and source
  promotion. Its later CMRU push step is an explicit already-published terminal
  state; it does not publish a second time.
- `repack` is an optional compression experiment. It builds OCI tar streams,
  extracts them into disk-backed layouts, repacks at `REPACK_TARGET_SIZE`
  (default `2GB`), validates the candidate structurally and by importing it
  through BuildKit, and publishes only after those checks. It does not load the
  original image into dockerd and does not use `skopeo`. Its publication occurs
  during the build, so its later CMRU push step is also an explicit
  already-published terminal state. The affected image currently fails closed
  in this lane because of the repacker defect recorded in the architecture
  guide.

The value in `cmru.toml` is the project default; the current value is
`RELEASE_IMAGE_FLOW = "load"`. CMRU runs the configured build, gate, promotion,
and push steps in that order. The `push` and `repack` alternatives deliberately
publish during the build and therefore have different release ordering.

The release config toggle is `RELEASE_IMAGE_FLOW`; `REPACK_TARGET_SIZE`
controls the slice size used by the repack flow.

### Builder governance (`BUILDX_BUILDER`)

Every MDT devcontainer build and release build requires
`BUILDX_BUILDER=mdt-managed` with
`BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock`. The host installer
maintains the rootless `mdt-buildkitd.service` in `dev-buildkitd.slice` and
creates the durable Buildx `remote` node after the service socket is ready.
`scripts/ensure-release-builder.sh` only verifies that contract; it cannot
create or repair an ungoverned worker.

Do not copy a generated `docker buildx create` command. Run host setup first,
then the normal build entry point. The in-container finalizer safely reuses the
same named remote and fails clearly if either environment variable, endpoint,
or builder identity is inconsistent. `docker build` delegates to Buildx with
the explicit `BUILDX_BUILDER` selection, and `docker buildx build` uses the same
selection unless an operator explicitly overrides it (which MDT release hooks
reject).

Caveats:

- The service is a plain `docker run --cgroup-parent=dev-buildkitd.slice`.
  The design does not rely on Buildx's container-driver `cgroup-parent` option.
- I/O caps (for example read/write IOPS) are not expressible via Buildx driver
  options. The canonical worker receives its host-side caps from
  `dev-buildkitd.slice` and its `mdt-buildkitd.service` placement; no generated
  Buildx worker cgroup is part of this path. A `buildx_buildkit_*` container is
  an accidental/non-canonical worker covered by the host guard and watcher, not
  the worker to inspect for an MDT build. See
  [DEVCONTAINER-LIFECYCLE.md](DEVCONTAINER-LIFECYCLE.md) § "Host resource
  governance (cgroups/slices)" for the underlying primitives.
- Seeing `dockerd` in `system.slice/docker.service` is normal. The managed
  BuildKit worker is the separately-labelled `mdt-buildkitd` container in
  `dev-buildkitd.slice`; Dockerfile execution is performed by that remote.
- Repacking runs outside BuildKit, so it has separate controls: disk-backed
  `REPACK_WORK_DIR`, one target worker, two compression threads, low CPU/I/O
  priority, and the caller's cgroup. The default does not impose a virtual
  address-space ceiling because the merged filesystem is much larger than the
  process's resident memory; `REPACK_VMEM_KB` remains available as an explicit
  diagnostic override. All values live in `cmru.toml`.

This section is the operator quick reference. The architecture document covers
the important distinction between the builder's Docker cgroup leaf, the
caller's inherited slice, and `dockerd` in `system.slice`.

### Build counter

If you run multiple builds on the same day, `BUILD_DATE` automatically appends a
`-N` suffix starting at `-2` (e.g. `20260604-2`, `20260604-3`). Counter file:
`logs/build-counter-YYYYMMDD.txt`.

An explicitly exported `BUILD_DATE` is authoritative and bypasses the local
counter. Use that to retry a failed release under the same immutable coordinate.

Environment configuration:

- Copy `.env.sample` to `.env` and adjust values as needed.

Optional overrides (environment variables):

- `REGISTRY`, `GITHUB_USERNAME`, `BUILD_DATE`, `BACKPORTS_URI`, `CIU_INSTALL_REQUIRED`
- `RELEASE_IMAGE_FLOW` (`load` is the configured source-first OCI-layout lane, `push` is a direct-registry export alternative, and `repack` is the validated optional compression lane)
- `REPACK_TARGET_SIZE` (`2GB` by default for the repack flow)
- `CODEX_VERSION`, `CLAUDE_CODE_VERSION`, `ANTIGRAVITY_VERSION`
- `REASONIX_VERSION`, `OPENCODE_VERSION`
- `INSTALL_CODEX`, `INSTALL_CLAUDE_CODE`, `INSTALL_ANTIGRAVITY`, `INSTALL_AIDER`
- `INSTALL_REASONIX`, `INSTALL_OPENCLAW`, `INSTALL_OPENCODE`

Resolver-emitted build coordinates (normally not set manually):
- `CIU_WHEEL_TAG`, `CIU_WHEEL_ASSET_NAME`, `CIU_WHEEL_VERSION`

Latest CIU wheel asset scheme:
- https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-latest/ciu-<version>-py3-none-any.whl

Any variable in docker-bake.hcl can be overridden for a build.

Example:

```bash
B2_VERSION=4.5.0 ./build-push.py --build
```

Disable optional AI tooling for a minimal image:

```bash
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_ANTIGRAVITY=false INSTALL_AIDER=false INSTALL_REASONIX=false INSTALL_OPENCLAW=false ./build-push.py --build
```

### aider, copilot, openclaw and claudelink versions

There is no `AIDER_VERSION`, `COPILOT_VERSION`, `OPENCLAW_VERSION` or `CLAUDELINK_VERSION` input any more
(staging refuses them). copilot, openclaw and claudelink are the newest releases at least 14 days old,
recorded by `cmru versions resolve` in `cmru.toml`; aider is an exact commit of upstream `main` recorded in
`aider-main-pin.toml` (its newest release needs Python <3.13; exit condition: until an aider release
supports Python 3.14). See [docs/CONSUMERS.md](docs/CONSUMERS.md#ai-cli-versions-copilot-openclaw-claudelink).

`ANTIGRAVITY_VERSION` currently uses upstream latest-manifest resolution during artifact staging.

## Push (registry)

After validation, push all variants to the registry:

```bash
./build-push.py --push
```

**Optimization**: Unlike the old split scripts, `--push` does **not** re-run the
environment resolver. It loads the resolved state from `.build-env.json` (saved
during `--build`) and injects those values directly into the bake command. This
saves 20–60 seconds of resolver overhead (MCR check, tool version resolution,
artifact staging).

To do both in one command:

```bash
./build-push.py --rebuild
```

For the configured `load` flow, `--push` loads the saved `.build-env.json` and
publishes the OCI layouts produced by the preceding `--build`. It does not run
the resolver or a second Bake/build. `regctl` copies each tagged descriptor and
`crane` compares the remote digest with the local layout; a missing layout,
failed copy, missing remote manifest, or digest mismatch returns nonzero.

When `RELEASE_IMAGE_FLOW=push` or `repack`, publication happens during the
build phase and the later CMRU push step reports an already-published terminal
state. `push` publishes the unrepacked BuildKit output. `repack` publishes the
validated repacked artifact, whose digests differ from the source layout. If
repack validation fails, retain the canonical `load` lane rather than copying
an invalid OCI layout to the registry.

For a full direct build-and-publish cycle, `--rebuild` runs `--build` followed
by `--push`. In the canonical `load` flow this is still a build-then-verified-
layout-copy sequence; it is not the same as a CMRU release, because it does not
provide CMRU's gate and source-promotion ordering. Use `./cmru.release.sh` for
the release lifecycle.

Ensure you are logged in to the registry (e.g., `docker login ghcr.io`) and that
`GITHUB_USERNAME` matches your org/user. If `GITHUB_PUSH_PAT` and
`GITHUB_USERNAME` are set in `.env`, the push script will log in automatically.

### Startup output delay

Build mode (`--build`) runs the environment resolver script
(`resolve-devcontainers-release.py`) which performs these steps silently before
emitting the first `[INFO]` line:

1. MCR registry check for newer devcontainer releases (MCR API call)
2. Tool version resolution (GitHub API calls to determine latest releases)
3. Downloading/verifying tool artifacts (bat, delta, fd, gh, etc. — hundreds of MB)
4. CIU wheel coordinate resolution (GitHub API)
5. Pulling base devcontainer images to inspect labels (`docker pull`)
6. Writing versioned package documentation files

**This can take 20-60 seconds before the first `[INFO]` output appears.**

Push mode (`--push`) skips the resolver entirely, so it starts immediately with
the Docker login and bake push.

## Use in devcontainer.json

Reference the desired tag under `image` (no `build` section):

```
{
  "image": "ghcr.io/acme/mdt-cockpit-devcontainer:bookworm-py3.13-20260117",
  "remoteUser": "vscode"
}
```

Counterexample (do NOT do this):

```
{
  "build": {
    "dockerfile": "Dockerfile"
  }
}
```

## Installing a package from Debian testing

Debian `testing` (the next release) is a permanent apt source in the image, pinned to priority 100
together with `oldstable` and `oldstable-backports`: `apt install <pkg>` and `apt upgrade` never take
a version from it. Refresh the lists and ask for it explicitly:

```bash
sudo apt-get update
apt-cache policy <pkg>                     # shows the testing candidate at priority 100
sudo apt-get install -t testing <pkg>      # or: sudo apt-get install <pkg>/testing
```

`-t testing` raises testing to 990 for that one command, so the package's new dependencies may also
come from testing. Afterwards, on plain `apt upgrade`:

* a package that stable (trixie, 500) also offers stays on stable: apt never picks testing for it;
* a package installed with `-t testing` that stable does **not** offer at a higher version (testing-only
  packages, libraries with a new soname, a locally installed `.deb`) keeps **following testing**: the
  installed version also sits at priority 100, ties with testing at 100, and the newer version wins.

Priority 99 for the three archives would stop those upgrades while `-t testing` and `pkg/testing`
would still work; the image keeps the operator's 100. Image-level check: `tests/runtime/test_apt_preview.py`.

## Manifest

The canonical user-facing manifest lives at:

```
/usr/local/share/mdt-cockpit/manifest.md
```

That is the same manifest content published on the repo-hosted release page. There is no copy or link
in the home.

`/etc/os-release` includes a custom `IMAGE_MANIFEST=/usr/local/share/mdt-cockpit/manifest.md` entry so tooling can discover the file without a repo-specific path convention.

An internal inventory snapshot is also written at:

```
/usr/local/share/mdt-cockpit/installed-tools-manifest.md
```

That snapshot includes tool versions, pip package list, and selected Debian package versions.

An example repository instruction file is shipped at:

```
/usr/local/share/mdt-cockpit/AGENTS.md.example
```

Copy and adapt it as the consumer repo's `AGENTS.md`; for Claude Code, use a
small `CLAUDE.md` containing `@AGENTS.md`. See
[AI agent tool discovery](docs/AI-AGENT-TOOL-DISCOVERY.md). For the distinction
between this human inventory and registry OCI manifests/digests, see
[OCI image tooling and repack design](docs/OCI-IMAGE-TOOLING.md).

## Persisting Agent State

The vendored template mounts one host directory, `${localEnv:HOME}/mdt-cockpit-home/`, over
`/home/vscode` - the complete home. It preserves all agent and tool state, including Claude Code and ClaudeLink
databases, Codex profiles and shared SQLite data, Pi sessions, OpenCode auth and sessions,
`.config`, `.local`, SSH/GPG/minisign keys, shell startup files, caches, and user-created content.

The image leaves `/home/vscode` empty and a **new home starts empty**. Image-owned executables and
runtimes live under `/opt/mdt` and `/srv` (primary and secondary Python environments, npm CLIs,
standalone CLIs, uv-managed Pythons, Oh My Zsh, PWMCP), group-writable for the `mdt` group your user
belongs to. Defaults for bash, zsh, nano, htop, mc, Neovim and `less` are wired system-wide under
`/etc` and `/usr/local/share/mdt-cockpit`; you override one by creating your own file, and your
`~/.config/mdt-cockpit/{ai.env,shell.env,aliases.sh}` are sourced after the image's. The only home
file the finalizer writes is a comment-only `~/.zshrc` stub, and only when you have no zsh startup
file. The home mount's host source is mode `0700` when created. Host SSH keys remain a separate
readonly mount at `/run/mdt/host-ssh`; `/tmp` remains a separate host-backed source at
`~/mdt-cockpit-tmp` (mode `1777`).

To adopt the template copy `devcontainer.json` and `initialize_container_environment.py` from
`templates/` into the consumer's `.devcontainer` and rebuild. **There is no migration.** If you used
the previous split-mount template the initializer prints one informational line while
`~/mdt--mounted-folders/` still holds files and the new home is empty; copy what you want by hand -
[Moving from modern-debian-tools-python-debug](docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
has the `cp -a` recipe and the paths that must not be copied.

The generated manifest is at `/usr/local/share/mdt-cockpit/manifest.md`; there is no home symlink.

Workspace files that should stay on the workspace mount include:

- `reasonix.toml`
- repo-local `AGENTS.md` and other AI instruction files
