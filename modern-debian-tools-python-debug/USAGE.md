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
named BuildKit builder is `mdt-governed-v1`, selected and checked by
`scripts/ensure-release-builder.sh` from the release configuration.

For the end-to-end data path, cgroup and slice boundaries, resource defaults,
and guidance for attributing load in `top`, Docker stats, and systemd, read
[MDT build and release architecture](docs/BUILD-ARCHITECTURE.md).

`RELEASE_IMAGE_FLOW` controls the release path:

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

Release builds use a resource-confined, **named** buildx builder rather than
whatever builder happens to be the current default. `cmru.toml` owns the
limits and `scripts/ensure-release-builder.sh` creates the builder on first use,
automatically recreates a project-owned builder whose driver or limits drifted,
and fails closed if Docker does not apply the configured values.

Do not create or update the release builder by copying a `docker buildx create`
command from this guide. Run the normal build entry point; it reads the current
name and limits from `cmru.toml`, creates the builder when absent, repairs
configuration drift, and verifies Docker's applied limits before building.

Caveats:

- The `cgroup-parent` Buildx driver option is not relied upon with Docker's
  systemd cgroup driver. The release instead verifies the container leaf's
  memory, memory+swap, CPU shares, and CPU quota. True placement in a specific
  host slice requires a host-managed BuildKit service and the Buildx `remote`
  driver.
- I/O caps (e.g. read/write IOPS) are not expressible via buildx driver-opts at all; if you need
  them, apply them host-side against the running `buildx_buildkit_<name>_*` container's cgroup
  (the same mechanism host operators use for any other container — see
  [DEVCONTAINER-LIFECYCLE.md](DEVCONTAINER-LIFECYCLE.md) § "Host resource governance
  (cgroups/slices)" for the underlying primitives).
- Seeing `dockerd` in `system.slice/docker.service` is normal. The governed
  `docker-container` builder puts the CPU- and memory-heavy BuildKit worker in
  a separate generated container scope, where Docker enforces the values from
  `cmru.toml`. Plain `docker build` does not select this named builder and
  therefore bypasses that project-owned confinement.
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
- `RELEASE_IMAGE_FLOW` (`load` is the configured source-first release lane, `push` is a direct registry-export alternative, and `repack` is the validated optional compression lane)
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
