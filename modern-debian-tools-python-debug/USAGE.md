# Devcontainer Image Usage

## Build (local)

Build all variants:

```bash
./build-push.py --build
```

This runs the environment resolver (MCR check, tool version resolution, artifact
staging), saves the resolved state to `.build-env.json`, then builds and
publishes the release targets through the governed builder.

For the end-to-end data path, cgroup and slice boundaries, resource defaults,
and guidance for attributing load in `top`, Docker stats, and systemd, read
[MDT build and release architecture](docs/BUILD-ARCHITECTURE.md).

`RELEASE_IMAGE_FLOW` controls the release path:

- `push` is the default release mode. It publishes the governed BuildKit output
  directly without loading it into dockerd's image store.
- `repack` is an optional compression experiment. It builds to OCI tar streams,
  extracts those into disk-backed layouts, repacks at `REPACK_TARGET_SIZE`
  (default `2GB`), validates the candidate by importing it through BuildKit,
  and only then publishes it. It does not load the original image into dockerd
  and does not use `skopeo`. The affected image currently fails this gate due
  to the repacker defect recorded in the architecture guide.
- `load` keeps the daemon-first split for local-only validation.

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
- `RELEASE_IMAGE_FLOW` (`push` is the default, `repack` is the validated optional compression lane, `load` is daemon-first local compatibility mode)
- `REPACK_TARGET_SIZE` (`2GB` by default for the repack flow)
- `CODEX_VERSION`, `CLAUDE_CODE_VERSION`, `ANTIGRAVITY_VERSION`, `AIDER_VERSION`
- `REASONIX_VERSION`, `OPENCLAW_VERSION`, `OPENCODE_VERSION`
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

### AIDER_VERSION modes (three-way)

`AIDER_VERSION` accepts three kinds of values:

| Value | Behavior | Use case |
|-------|----------|----------|
| `main` (default) | `pip install` from git `@main` branch | Python 3.13/3.14 support (PR #4899 merged but not yet released) |
| `latest` | Resolve the current PyPI release during staging, then install that exact version | Stable release tracking with a concrete manifest version |
| `<version>` e.g. `0.86.2` | `pip install aider-chat==<version>` | Pinned reproducible builds |

**Background**: The default `AIDER_VERSION=main` still exists for the Python 3.13/3.14 support case while the
upstream release cadence catches up. If you use `latest`, staging resolves the current PyPI release version
up front so the manifest records a concrete version instead of a symbolic placeholder.

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

When `RELEASE_IMAGE_FLOW=push` or `repack`, the push step becomes a no-op because
publication happens during the build phase. The canonical in-image manifest is
exported through the governed builder; it is not loaded into Docker's local
image store. `docker-repack` changes digests, so evidence from an optional
repack run must refer to the artifact that was actually published. If its
validation fails, retain the default `push` lane rather than copying an invalid
OCI layout to the registry.

In the non-release `load` mode, the later push is a second Bake invocation.
BuildKit checks its cache, but changed inputs can rebuild layers and the result
is unrepacked. In the default `push` mode, publication already happens during
the build phase.

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
  "image": "ghcr.io/acme/modern-debian-tools-python-debug-vsc-devcontainer:bookworm-py3.13-20260117",
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

## Manifest

The canonical user-facing manifest lives at:

```
/usr/local/share/modern-debian-tools-python-debug/manifest.md
```

That is the same manifest content published on the repo-hosted release page.

`/home/vscode/mdt-manifest.md` is kept as a compatibility symlink.

`/etc/os-release` includes a custom `IMAGE_MANIFEST=/usr/local/share/modern-debian-tools-python-debug/manifest.md` entry so tooling can discover the file without a repo-specific path convention.

An internal inventory snapshot is also written at:

```
/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md
```

That snapshot includes tool versions, pip package list, and selected Debian package versions.

An example repository instruction file is shipped at:

```
/usr/local/share/modern-debian-tools-python-debug/AGENTS.md.example
```

Copy and adapt it as the consumer repo's `AGENTS.md`; for Claude Code, use a
small `CLAUDE.md` containing `@AGENTS.md`. See
[AI agent tool discovery](docs/AI-AGENT-TOOL-DISCOVERY.md). For the distinction
between this human inventory and registry OCI manifests/digests, see
[OCI image tooling and repack design](docs/OCI-IMAGE-TOOLING.md).

## Persisting Agent State

To keep tool state across rebuilds, persist the workspace mount plus these home directories:

- `/home/vscode/.reasonix` for Reasonix user config and session state
- `/home/vscode/.openclaw` for OpenClaw config and gateway state
- `/home/vscode/.local/share/opencode` for OpenCode auth, sessions, logs, and runtime state
- `/home/vscode/.config/opencode` for OpenCode user configuration (covered by the persisted `.config` mount)
- `/home/vscode/.config/modern-debian-tools-python-debug` for the central `ai.env`, `aliases.sh`, and their examples

Relevant workspace files that should stay on the host mount:

- `reasonix.toml`
- any repo-local `AGENTS.md` / AI-instruction files
