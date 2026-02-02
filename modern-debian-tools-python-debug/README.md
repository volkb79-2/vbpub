# Modern Debian Python Debug Images

Pre-built Debian + Python tooling images for interactive debugging. Two image families are published:

- `modern-debian-tools-python-debug` (standard Python base image)
- `modern-debian-tools-python-debug-vsc-devcontainer` (VS Code devcontainer base image)

## Images

Variants are built for both image families:
- Bookworm + Python 3.11
- Bookworm + Python 3.13
- Trixie + Python 3.11
- Trixie + Python 3.13
- Trixie + Python 3.14

Base images:
- `modern-debian-tools-python-debug`: `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
- `modern-debian-tools-python-debug-vsc-devcontainer`: `mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}-${DEBIAN_VERSION}`

Each image tag includes Debian version, Python version, and build date:

```
<registry>/<github_username>/modern-debian-tools-python-debug:<debian>-py<python>-<YYYYMMDD>
```

Example:
```
ghcr.io/acme/modern-debian-tools-python-debug:bookworm-py3.13-20260117
```

VS Code devcontainer tag:
```
<registry>/<github_username>/modern-debian-tools-python-debug-vsc-devcontainer:<debian>-py<python>-<YYYYMMDD>
```

## Contents

The image pre-installs:
- Debian System tools from distribution (curl, git, jq, dnsutils, ...)
- Modern tools (latest version) from its own repos (bat, fd, ripgrep, shellcheck, fzf, yq) at system level
- Common CLIs (Consul CLI, Vault, Redis, Postgresql, AWS CLI)
- Python packages installed into a per-user virtualenv at `/home/vscode/.venv`

Python packages are installed directly in the Dockerfile (see the `pip install` list). 

The image sets:
- `VIRTUAL_ENV=/home/vscode/.venv`
- `PATH=/home/vscode/.venv/bin:$PATH`

If CIU wheel inputs are provided at build time, the CIU wheel is downloaded and installed into the same virtualenv (optional SHA256 verification via `CIU_WHEEL_SHA256`).

## Environment Provisioning Flow (To-be)

Chronological steps for a complete development environment vs. GitHub Actions runner. This consolidates what happens in the base image, devcontainer post-create, and CI/CD setup scripts.

| Step (Chronological) | Devcontainer (modern-debian-tools-python-debug-vsc-devcontainer + post-create) | GitHub Actions (runner + env-setup) |
| --- | --- | --- |
| 1. Base OS | `mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}-${DEBIAN_VERSION}` (from [modern-debian-tools-python-debug/Dockerfile](modern-debian-tools-python-debug/Dockerfile)) | GitHub-hosted Ubuntu runner ([about runners](https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners)) |
| 2. Core system packages | Installed in Dockerfile via `apt-get` (curl, git, jq, dnsutils, etc.). | Preinstalled on runner; no Dockerfile step. |
| 3. Modern tools + CLIs | Installed in Dockerfile (bat, fd, ripgrep, shellcheck, fzf, yq, Consul, Vault, PostgreSQL client, Redis tools, AWS CLI). | Not installed by default; add in CI if needed (not done in env-setup). |
| 4. Python runtime + packages | `/home/vscode/.venv` created and populated in Dockerfile with common dev packages; `env-workspace-setup-generate.sh` installs repo `requirements.txt` to keep tooling aligned. | Uses runner Python; `env-workspace-setup-generate.sh` installs requirements from repo `requirements.txt`. |
| 5. CIU install | CIU must already be available (prebaked in image or preinstalled in workspace); post-create does not install CIU. | `.github/actions/env-setup.sh` downloads wheel from `CIU_PKG_URL` and installs it before running env setup. |
| 6. Workspace env generation + bootstrap | `.devcontainer/post-create.sh` calls `env-workspace-setup-generate.sh`, which runs `ciu --generate-env --define-root` to create `.env.ciu` and execute CIU post-bootstrap (network create/attach + TLS access checks). | `.github/actions/env-setup.sh` delegates to `env-workspace-setup-generate.sh` for the same `.env.ciu` generation and CIU bootstrap. |
| 7. Dev UX/CI extras | Post-create sets VS Code settings, aliases, SSH agent, and PATH helpers. | CI sets `MOCK_MODE=true`, optional Docker registry login, and creates `vol-testing-results/`. |

## Version overrides

Defaults are `latest`. Any variable defined in docker-bake.hcl can be overridden via environment variables when invoking the build.

Additional optional build args:
- `CIU_LATEST_TAG`, `CIU_LATEST_ASSET_NAME`: derive the canonical GitHub Releases URL
	- Latest URL scheme: https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-latest/ciu-<version>-py3-none-any.whl

Example:

```
B2_VERSION=4.5.0 ./build-images.py
```

Checksum verification is enabled for HashiCorp binaries, AWS CLI, and B2 CLI downloads during build.

Debian backports are enabled for each Debian release with Pin-Priority 600.

## Manifest

Each image writes a manifest to:

```
/home/vscode/devcontainer-manifest.txt
```

It includes tool versions, Python version, pip package list, and selected Debian package versions.

## Package metadata (GitHub Packages)

The container image publishes OCI labels so the GitHub Packages page shows a useful description and links:

- `org.opencontainers.image.description`
- `org.opencontainers.image.source`
- `org.opencontainers.image.documentation`

This makes the package page readable before download and points to the manifest location inside the image.

## Build

This project uses Buildx Bake. The build date is included in tags via `BUILD_DATE`.

- `docker-bake.hcl` defines base + devcontainer targets and an `all` group.
- `./build-images.py` runs the config-driven build step and builds all variants.

### Shared credentials

`build-images.py` and `push-images.py` will load a shared repo-root env file if present:
- vbpub/.env (preferred)
- modern-debian-tools-python-debug/.env (fallback)

This lets you store GitHub credentials once for multiple vbpub projects.

## Push to GitHub Artifact Registry

Use `./push-images.py` (or run Bake with `--push`) after validation. Configure registry owner via environment variables in the script or your shell.

`push-images.py` also updates the `latest` tag (and per-variant `*-latest` tags) to point at the most recently pushed images.

### GHCR (personal account)

Note: GitHub Packages only supports authentication using a personal access token (classic). See:
https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry


1. Copy `.env.sample` to `.env` and fill in:
	- `GITHUB_USERNAME`
	- `GITHUB_REPO`
	- `GITHUB_PUSH_PAT`
2. Create a GitHub Personal Access Token with:
	- `write:packages` (required for push)
	- `read:packages` (required for pull)
3. Run `./push-images.py` (the script will login if `GITHUB_PUSH_PAT` is set).

## Usage in other repositories

Reference one of the tags in your `.devcontainer/devcontainer.json` under `image` (do not use `build`).

Example:

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

# Check MCR devcontainer released images

run `./check-mcr-devcontainer-tags.py ` to check the availability. As of 2025-02:

```txt
debian    3.12   3.13   3.14   3.15   3.16 
--------  -----  -----  -----  -----  -----
bookworm  1pd    1pd    pd     .      .    
trixie    pd     pd     pd     .      .    
forky     .      .      .      .      .    

Legend: 1 = 1- prefix, p = plain, d = dev- prefix, . = missing

Secondary manifest variants: 10 (https://raw.githubusercontent.com/devcontainers/images/main/src/python/manifest.json)
```