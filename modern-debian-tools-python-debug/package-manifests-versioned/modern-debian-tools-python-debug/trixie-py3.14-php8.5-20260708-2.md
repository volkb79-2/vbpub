# Modern Debian Tools + Python Debug

Versioned package manifest for `modern-debian-tools-python-debug`.

## Release

- Build date: `20260708-2`
- Target: `trixie-py314-php85`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20260708-2`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-php8.5-20260708-2
```

## Version Selection Policies

This image pins each component to a specific version policy:

| Policy | Meaning |
|---|---|
| `pinned release` | Version is explicitly pinned to a specific GitHub/GitLab release tag. |
| `rolling` | Version follows the latest stable upstream release at build time. |
| `distro` | Version is determined by the Debian Trixie apt repository. |
| `conditional` | Component is included only when the corresponding build flag is set (e.g., `INSTALL_PHP=true`). |

## Purpose

This image provides a Debian Trixie-based development container with Python 3.14, PHP 8.5, and a comprehensive set of modern CLI tools for AI-assisted development, debugging, and container inspection. It is designed for VS Code Devcontainers and CI/CD pipelines requiring a batteries-included Linux environment with the latest AI coding assistants (Claude Code, Codex, Aider), container debugging tools (dive, dtop, lazydocker, syft, grype), and everyday developer utilities (ripgrep, fd, bat, delta, fzf, yq, gh, nvim).

## First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `ciu` | `4.3.0` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:e93498f1a0f1e2742067…` |
| `cmru` | `1.1.2` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:cf9a9bd956b4f89c3098…` |

## Runtime Version Snapshot (Pre-build Probe)

### First-Party Wheels

- CIU: `4.3.0`
- cmru: `1.1.2`

### AI CLI Tools

- aider: `0.86.2`
- antigravity: `1.1.0`
- claude: `2.1.204`
- codex: `0.143.0`
- openclaw: `2026.6.11`
- reasonix: `1.17.7`

### Container Inspection Tools

- dive: `0.13.1`
- dtop: `0.7.7`
- glances: `4.5.5`
- lazydocker: `0.25.2`
- syft: `1.46.0`

### Security & Debug Tools

- cdebug: `0.0.19`
- grype: `0.115.0`
- hadolint: `2.14.0`

### Custom Tooling

- awscli: `2.35.17`
- b2: `4.7.1`
- bat: `0.26.1`
- consul: `2.0.1`
- delta: `0.19.2`
- fd: `10.4.2`
- fzf: `0.74.0`
- gh: `2.96.0`
- htop: `3.5.1`
- nvchad: `2.5`
- nvim: `0.12.4`
- rga: `0.10.10`
- ripgrep: `15.1.0`
- shellcheck: `0.11.0`
- vault: `2.0.3`
- yq: `4.53.3`

### System packages

- bash-completion: `1:2.16.0-7`
- bind9-dnsutils: `1:9.20.23-1~deb13u1`
- ca-certificates: `20250419`
- curl: `8.14.1-2+deb13u3`
- fuse3: `3.17.2-3`
- gdb: `16.3-1`
- git: `1:2.47.3-0+deb13u1`
- git-lfs: `3.6.1-1+deb13u1`
- gnupg: `2.4.7-21+deb13u1`
- gzip: `1.13-1`
- httpie: `3.2.4-3`
- iotop: `0.6-42-ga14256a-0.3+b1`
- iproute2: `6.15.0-1`
- iputils-ping: `3:20240905-3`
- jq: `1.7.1-6+deb13u2`
- less: `668-1`
- locales: `2.41-12+deb13u3`
- lsb-release: `12.1-1`
- lsof: `4.99.4+dfsg-2`
- man-db: `2.13.1-1`
- mc: `3:4.8.33-1+deb13u1`
- minisign: `0.12-1`
- nano: `8.4-1+deb13u1`
- ncdu: `1.22-1`
- netcat-openbsd: `1.229-1`
- openssl: `3.5.6-1~deb13u2`
- postgresql-client: `17+278`
- procps: `2:4.0.4-9`
- psmisc: `23.7-2`
- python3-venv: `3.13.5-1`
- redis-tools: `5:8.0.2-3+deb13u2`
- rsync: `3.4.1+ds1-5+deb13u3`
- skopeo: `1.18.0+ds1-1+b5`
- sqlite3: `3.46.1-7+deb13u1`
- sshfs: `3.7.3-1.1+b2`
- strace: `6.13+ds-1`
- sysstat: `12.7.5-2`
- tar: `1.35+dfsg-3.1`
- tree: `2.2.1-1`
- unzip: `6.0-29`
- util-linux: `2.41-5`
- vim: `2:9.1.1230-2`
- w3m: `0.5.3+git20230121-2.1`
- wget: `1.25.0-2`
- xz-utils: `5.8.1-1`

## Python & PHP Runtime

| Component | Version | Policy | Notes |
|---|---|---|---|
| Python (default) | `3.14` | pinned (devcontainer base) | From `mcr.microsoft.com/devcontainers/python:3.14-trixie` |
| Python venv | `/home/vscode/.venv` | build-time pip | Full toolkit: ipython, asyncpg, redis, hvac, httpx, sqlalchemy, dnspython, websockets, requests, boto3, pytest, etc. |
| System Python | `3.13` | Debian Trixie | `python3-venv` package is Debian-system Python; independent of base-image Python 3.14 |
| PHP | `8.5` | sury.org repo | Conditional (INSTALL_PHP=true) |

## In-Image File

- Build manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`
- Devcontainer manifest: `/usr/local/share/devcontainer-features/modern-debian-tools-python-debug/manifest.json`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260708-2.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.


