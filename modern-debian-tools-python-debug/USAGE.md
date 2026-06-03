# Devcontainer Image Usage

## Build (local)

Build all four variants:

- `./build-images.py`

Environment configuration:
- Copy `.env.sample` to `.env` and adjust values as needed.

Optional overrides (environment variables):
- `REGISTRY`, `GITHUB_USERNAME`, `BUILD_DATE`, `BACKPORTS_URI`, `CIU_INSTALL_REQUIRED`
- `CODEX_VERSION`, `CLAUDE_CODE_VERSION`, `ANTIGRAVITY_VERSION`, `AIDER_VERSION`
- `INSTALL_CODEX`, `INSTALL_CLAUDE_CODE`, `INSTALL_ANTIGRAVITY`, `INSTALL_AIDER`

Resolver-emitted build coordinates (normally not set manually):
- `CIU_WHEEL_TAG`, `CIU_WHEEL_ASSET_NAME`, `CIU_WHEEL_VERSION`

Latest CIU wheel asset scheme:
- https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-latest/ciu-<version>-py3-none-any.whl

Any variable in docker-bake.hcl can be overridden for a build.

Example:

```
B2_VERSION=4.5.0 ./build-images.py
```

Disable optional AI tooling for a minimal image:

```
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_ANTIGRAVITY=false INSTALL_AIDER=false ./build-images.py
```

### AIDER_VERSION modes (three-way)

`AIDER_VERSION` accepts three kinds of values:

| Value | Behavior | Use case |
|-------|----------|----------|
| `main` (default) | `pip install` from git `@main` branch | Python 3.13/3.14 support (PR #4899 merged but not yet released) |
| `latest` | `pip install aider-chat` (latest PyPI release) | Stable release, Python < 3.13 only |
| `<version>` e.g. `0.86.2` | `pip install aider-chat==<version>` | Pinned reproducible builds |

**Background**: The latest PyPI release `aider-chat 0.86.2` (Feb 2026) declares `Requires: Python <3.13, >=3.10`.
PR [Aider-AI/aider#4899](https://github.com/Aider-AI/aider/pull/4899) added Python 3.13 and 3.14 support to `main`
(Mar 9, 2026) but no new release was cut. The default `AIDER_VERSION=main` works around this by installing
from the upstream `main` branch directly. Switch back to `latest` or a pinned version once a PyPI release
with 3.14 support is available.

`ANTIGRAVITY_VERSION` currently uses upstream latest-manifest resolution during artifact staging.

## Push (registry)

After validation, push all variants:

- `./push-images.py`

Ensure you are logged in to the registry (e.g., `docker login ghcr.io`) and that `GITHUB_USERNAME` matches your org/user.
If `GITHUB_PUSH_PAT` and `GITHUB_USERNAME` are set in `.env`, the push script will log in automatically.

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

Each image writes a manifest file at:

```
/usr/local/share/modern-debian-tools-python-debug/manifest.md
```

Installed tooling/program/package inventory is written at:

```
/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md
```

This includes tool versions, pip package list, and selected Debian package versions.
