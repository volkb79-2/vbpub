# CIU and CIU Deploy

This directory contains the DST-DNS deployment engine and orchestrator.

## Components

### CIU (ciu)
Single-stack renderer/runner. CIU renders the stack configuration, resolves directives, renders docker-compose.yml, runs hooks, and starts the stack.

**Key responsibilities**:
- Render stack TOML from templates (ciu.defaults.toml.j2 → ciu.toml)
- Merge global and stack config (ciu-global.toml + ciu.toml)
- Resolve secret directives (Vault/local/external)
- Render docker-compose.yml from docker-compose.yml.j2
- Run pre/post compose hooks
- Invoke docker compose for a single stack

**Common usage**:
- Render and run a stack
  - ciu -d infra/db-core
- Render TOML only
  - ciu -d infra/db-core --render-toml
- Dry-run (render without compose)
  - ciu -d infra/db-core --dry-run
- Print merged context (debugging)
  - ciu -d infra/db-core --print-context

**Inputs**:
- ciu-global.toml (rendered global config)
- ciu.toml (rendered stack config)
- docker-compose.yml.j2 (stack template)
- Optional hooks declared in stack config

**Outputs**:
- docker-compose.yml (rendered)
- ciu.toml (rendered)

### CIU Deploy (ciu-deploy)
Multi-stack orchestrator. CIU Deploy sequences multiple stacks using deployment phases/groups defined in ciu-global.toml.

**Key responsibilities**:
- Stop, clean, build, deploy actions
- Phase/group selection and ordering
- Health checks and selftests
- Workspace environment enforcement (.env.ciu)

**Common usage**:
- Default deploy (all enabled phases)
  - ciu-deploy --deploy
- Full restart (stop + clean + deploy)
  - ciu-deploy --stop --clean --deploy
- Build images with Buildx Bake
  - ciu-deploy --build
- List groups
  - ciu-deploy --list-groups
- Deploy selected groups
  - ciu-deploy --groups infra,apps --deploy

**Inputs**:
- ciu-global.toml (rendered global config)
- deployment phases/groups in [deploy.phases] and [deploy.groups]

## Workspace prerequisites

- Run env-workspace-setup-generate.sh and source .env.ciu
- Ensure PYTHON_EXECUTABLE points to the workspace venv
- Build images with docker buildx bake before deploy

## Build & install the CIU package

**Editable install (development)**:
- From repo root (or any location):
  - pip install -e /path/to/vbpub/ciu

**Build a wheel (release/CI)**:
- From the CIU repo root:
  - python -m pip wheel . -w dist
- Output: dist/ciu-*.whl

**Publish a wheel (GitHub Releases)**:
- From the CIU repo root:
  - python3 publish-wheel.py
- Requires `GITHUB_PUSH_PAT`, `GITHUB_USERNAME`, and `GITHUB_REPO`.
- Publishes a versioned release and uploads the versioned wheel asset to the
  `ciu-wheel-latest` tag as an alias.
- Validation step checks the latest release asset exists after publish.

## Running tests

- From the CIU repo root:
  - python3 run-ciu-tests.py

## Where CIU is installed in dstdns

CIU is installed as a Python package (not a repo-local script):

- **Devcontainer**: .devcontainer/post-create.sh installs from `CIU_PKG_URL`
- **CI/GitHub Actions**: .github/actions/env-setup.sh installs from `CIU_PKG_URL`
- **Tools base image**: tools/base/Dockerfile.base installs from `CIU_WHEEL_URL`

Required environment variables:
- `CIU_PKG_URL` (wheel artifact URL for devcontainer/CI)
- `CIU_WHEEL_URL` (wheel artifact URL for base image build)

Optional environment variables:
- `CIU_PKG_SHA256` and `CIU_WHEEL_SHA256` for integrity verification
- `CIU_PKG_CACHE_DIR` to control local caching (defaults to `.ci/ciu-dist` and is gitignored)
- `CIU_PROJECT_ROOT` to publish from a non-default package root
- `CIU_PACKAGE_NAME` to override the project name used for tags
- `CIU_WHEEL_GLOB` to override the wheel filename glob
- `CIU_RELEASE_TAG`, `CIU_LATEST_TAG`, `CIU_LATEST_ASSET_NAME` for custom tag/asset naming

Recommended distribution:
- Publish the CIU wheel to GitHub Releases and set `CIU_PKG_URL` / `CIU_WHEEL_URL` to the release asset URL.
- Latest URL scheme:
  - https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-latest/ciu-<version>-py3-none-any.whl
- Versioned URL scheme:
  - https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-<version>/ciu-<version>-py3-none-any.whl

### Resolve the latest wheel URL dynamically (release.toml)

Use the release config to build the download URL at runtime.

Steps:
1. Read `release.toml` for `github.username`, `github.repo`, and `env.CIU_LATEST_TAG`.
2. Query the release assets for that tag.
3. Pick the `.whl` asset and build the download URL.

```python
#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
from urllib.request import Request, urlopen

import tomllib


def load_release_config(path: Path) -> dict:
  if not path.exists():
    raise FileNotFoundError(f"release config not found: {path}")
  with path.open("rb") as handle:
    return tomllib.load(handle)


def main() -> None:
  repo_root = Path(__file__).resolve().parents[2]
  config = load_release_config(repo_root / "release.toml")

  github = config.get("github") or {}
  env = config.get("env") or {}

  owner = (github.get("username") or "").strip()
  repo = (github.get("repo") or "").strip()
  tag = (env.get("CIU_LATEST_TAG") or "").strip()

  if not owner or not repo or not tag:
    raise ValueError("github.username, github.repo, and env.CIU_LATEST_TAG are required")

  token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_PUSH_PAT")
  if not token:
    raise ValueError("Set GH_TOKEN or GITHUB_PUSH_PAT for GitHub API access")

  api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
  req = Request(api_url, headers={"Authorization": f"Bearer {token}"})
  with urlopen(req) as response:
    payload = response.read().decode("utf-8")

  data = __import__("json").loads(payload)
  assets = data.get("assets") or []
  wheel = next((a for a in assets if a.get("name", "").endswith(".whl")), None)
  if not wheel:
    raise RuntimeError(f"No wheel asset found for tag {tag}")

  asset_name = wheel["name"]
  url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{asset_name}"
  print(url)


if __name__ == "__main__":
  main()
```

All dstdns scripts and docs should invoke `ciu` and `ciu-deploy` from the installed package.

## Separation of concerns

- CIU is stack-scoped: one stack per run, no global orchestration.
- CIU Deploy is orchestration-only: no template rendering outside CIU.

## Troubleshooting

- Missing ciu-global.toml: run ciu --render-toml
- Missing images: run docker buildx bake all-services --load
- Missing workspace env: run env-workspace-setup-generate.sh and source .env.ciu

## Detailed Documentation

- Configuration spec: docs/CONFIG.md
- CIU internals: docs/CIU.md
- CIU Deploy internals: docs/CIU-DEPLOY.md

## Tests and Examples

- Tests: tests/
- Hook examples: src/ciu/hooks/examples
