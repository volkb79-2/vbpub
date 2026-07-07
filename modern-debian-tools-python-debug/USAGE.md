# Devcontainer Image Usage

## Build (local)

Build all variants:

```bash
./build-push.py --build
```

This runs the environment resolver (MCR check, tool version resolution, artifact
staging), saves the resolved state to `.build-env.json`, then runs
`docker buildx bake all --load`.

`RELEASE_IMAGE_FLOW` controls the release path:
- `repack` is the default release mode. It builds with `--load`, repacks at
  `REPACK_TARGET_SIZE` (default `2GB`), and then pushes the repacked OCI layout.
- `load` keeps the daemon-first split for local-only validation.
- `push` pushes the unrepacked BuildKit output directly.

The release config toggle is `RELEASE_IMAGE_FLOW`; `REPACK_TARGET_SIZE`
controls the slice size used by the repack flow.

### Builder governance (`BUILDX_BUILDER`)

Release builds should run through a resource-confined, **named** buildx builder rather than
whatever builder happens to be the current default. Neither `scripts/release-bake.sh` nor
`build-push.py` nor `docker-bake.hcl` hardcode a builder or pass `--builder` — `docker buildx bake`
picks up the `BUILDX_BUILDER` environment variable natively, so selecting a confined builder is
purely a matter of exporting it before invoking `./build-push.py --build` / `--push` / `--rebuild`
(or a raw `docker buildx bake` call):

```bash
export BUILDX_BUILDER=governed
./build-push.py --build
```

Canonical one-time builder creation (resource-confined, `docker-container` driver):

```bash
docker buildx create --name governed --driver docker-container \
    --driver-opt memory=4g --driver-opt cpu-shares=512
```

Caveats:

- The `cgroup-parent` driver-opt is **silently ignored** when the Docker daemon uses the systemd
  cgroup driver (the common case on modern distros) — true slice placement of a buildx builder is
  not possible that way. The `memory` / `cpu-shares` (and `cpu-quota`, `cpuset-cpus`) driver-opts
  above ARE honored unconditionally and apply real limits regardless of cgroup driver.
- I/O caps (e.g. read/write IOPS) are not expressible via buildx driver-opts at all; if you need
  them, apply them host-side against the running `buildx_buildkit_<name>_*` container's cgroup
  (the same mechanism host operators use for any other container — see
  [DEVCONTAINER-LIFECYCLE.md](DEVCONTAINER-LIFECYCLE.md) § "Host resource governance
  (cgroups/slices)" for the underlying primitives).
- Plain `docker build` (the default `docker` driver, no builder object) runs **inside the Docker
  daemon's own cgroup** and bypasses all of the above confinement entirely. Always use the named
  builder (`BUILDX_BUILDER=governed`, or whatever you called it) for anything beyond a quick local
  smoke build.

### Build counter

If you run multiple builds on the same day, `BUILD_DATE` automatically appends a
`-N` suffix starting at `-2` (e.g. `20260604-2`, `20260604-3`). Counter file:
`logs/build-counter-YYYYMMDD.txt`.

Environment configuration:
- Copy `.env.sample` to `.env` and adjust values as needed.

Optional overrides (environment variables):
- `REGISTRY`, `GITHUB_USERNAME`, `BUILD_DATE`, `BACKPORTS_URI`, `CIU_INSTALL_REQUIRED`
- `RELEASE_IMAGE_FLOW` (`repack` is the default, `load` keeps the current daemon-first split, `push` pushes during build)
- `REPACK_TARGET_SIZE` (`2GB` by default for the repack flow)
- `CODEX_VERSION`, `CLAUDE_CODE_VERSION`, `ANTIGRAVITY_VERSION`, `AIDER_VERSION`
- `REASONIX_VERSION`, `OPENCLAW_VERSION`
- `INSTALL_CODEX`, `INSTALL_CLAUDE_CODE`, `INSTALL_ANTIGRAVITY`, `INSTALL_AIDER`
- `INSTALL_REASONIX`, `INSTALL_OPENCLAW`

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

Docker's build cache is checked during push: if the build context and Dockerfile
haven't changed, cached layers are reused and only the image manifest is pushed.
If the context changed (e.g. fresh tool downloads, counter increment), layers
are rebuilt before pushing.

When `RELEASE_IMAGE_FLOW=repack`, the push step becomes a no-op because the
repack happens during the build phase. `docker-repack` changes digests, so the
release flow must always push the repacked OCI layout, not the unrepacked
BuildKit output.

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

## Persisting Agent State

To keep tool state across rebuilds, persist the workspace mount plus these home directories:

- `/home/vscode/.reasonix` for Reasonix user config and session state
- `/home/vscode/.openclaw` for OpenClaw config and gateway state
- `/home/vscode/.config/modern-debian-tools-python-debug` for the central `ai.env`, `aliases.sh`, and their examples

Relevant workspace files that should stay on the host mount:

- `reasonix.toml`
- any repo-local `AGENTS.md` / AI-instruction files
