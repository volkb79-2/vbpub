# Modern Debian Tools + Python Debug VS Code Devcontainer

Versioned package manifest for `modern-debian-tools-python-debug-vsc-devcontainer`.

## Release

- Build date: `20260708-3`
- Target: `trixie-py314-php85-vsc`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20260708-3`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-20260708-3
```

## Purpose

This Devcontainer image provides a Debian trixie-based environment with Python 3.14 and PHP 8.5, pre-loaded with AI-assisted development CLI tools (aider, claude, codex, antigravity, openclaw, reasonix), container inspection utilities (dive, dtop, lazydocker, syft, glances), security debugging tools (grype, hadolint, cdebug), and general-purpose system utilities (awscli, bat, fd, fzf, gh, nvim, ripgrep, shellcheck, yq, and more). All tool versions are pinned at build time for reproducible Devcontainer environments.

## First-Party Wheels

The following first-party Python wheels are included in the image.

| Wheel | Version | SHA256 |
|---|---|---|
| `ciu` | `4.3.0` | `e93498f1a0f1e2742067d8ccdd5ffbe95d19bcd4fa6c6e9dbbdca37b4df75e11` |
| `cmru` | `1.1.2` | `cf9a9bd956b4f89c3098328fb2c8ef8f7d9b30c5572c500a0af2e506cd274b8d` |

## Python & PHP Runtime

| Component | Version | Policy | Notes |
|---|---|---|---|
| Python (default) | `3.14` | pinned (devcontainer base) | From `mcr.microsoft.com/devcontainers/python:3.14-trixie` |
| Python venv | `/home/vscode/.venv` | build-time pip | Full toolkit: ipython, asyncpg, redis, hvac, httpx, sqlalchemy, dnspython, websockets, requests, boto3, pytest, etc. |
| System Python | `3.13` | Debian Trixie | `python3-venv` package is Debian-system Python; independent of base-image Python 3.14 |
| PHP | `8.5` | sury.org repo | Conditional (INSTALL_PHP=true) |

## Version Selection Policies

### AI CLI Tools
Resolved to the latest published version at build time from registries (npm, PyPI, GitHub Releases). Override any via build arg.

### Supporting Tools
Resolved to the latest GitHub release tag at build time. Pre-staged and verified via upstream SHA256 checksums.

### Debian System Packages
Installed from Debian Trixie main repos (preferring backports). Stable snapshots, not auto-upgraded at runtime.

### Python Packages
Resolved to PyPI latest at build time via pip install.

## Runtime Version Snapshot (Pre-build Probe)

### First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `ciu` | `4.3.0` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:e93498f1a0f1e2742067…` |
| `cmru` | `1.1.2` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:cf9a9bd956b4f89c3098…` |

### AI CLI Tools

**Version policy:** latest npm/GitHub release at build time (override via build arg). AI CLI tool versions are resolved dynamically during `stage_tool_artifacts` from the respective package registries (npm, PyPI, GitHub Releases).

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `aider` | `0.86.2` | latest | https://github.com/Aider-AI/aider |  |
| `antigravity` | `1.1.0` | latest | https://github.com/antigravity/antigravity-cli |  |
| `claude` | `2.1.205` | latest | https://github.com/anthropics/claude-code |  |
| `codex` | `0.143.0` | latest | https://github.com/openai/codex |  |
| `openclaw` | `2026.6.11` | latest | https://github.com/openclaw/openclaw |  |
| `reasonix` | `1.17.8` | latest | https://github.com/reasonix/reasonix |  |

### Container Inspection Tools

**Version policy:** latest GitHub release at build time (override via build arg). All tools in this category are downloaded as pre-built binaries from their upstream releases.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `dive` | `0.13.1` | latest | https://github.com/wagoodman/dive |  |
| `dtop` | `0.7.8` | latest | https://github.com/amir20/dtop |  |
| `glances` | `4.5.5` | latest | https://github.com/nicolargo/glances |  |
| `lazydocker` | `0.25.2` | latest | https://github.com/jesseduffield/lazydocker |  |
| `syft` | `1.46.0` | latest | https://github.com/anchore/syft |  |

### Security & Debug Tools

**Version policy:** latest GitHub release at build time (override via build arg). Binaries are verified via upstream SHA256 checksums before installation.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `cdebug` | `0.0.19` | latest | https://github.com/iximiuz/cdebug |  |
| `grype` | `0.115.0` | latest | https://github.com/anchore/grype |  |
| `hadolint` | `2.14.0` | latest | https://github.com/hadolint/hadolint |  |

### Custom Tooling

**Version policy:** latest GitHub release at build time (override via build arg). Some tools are compiled from source (nvim, htop); the rest are pre-built binaries.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `awscli` | `2.35.18` | latest | https://github.com/aws/aws-cli |  |
| `b2` | `4.7.1` | latest | https://github.com/Backblaze/B2_Command_Line_Tool |  |
| `bat` | `0.26.1` | latest | https://github.com/sharkdp/bat |  |
| `consul` | `2.0.2` | latest | https://github.com/hashicorp/consul |  |
| `delta` | `0.19.2` | latest | https://github.com/dandavison/delta |  |
| `fd` | `10.4.2` | latest | https://github.com/sharkdp/fd |  |
| `fzf` | `0.74.0` | latest | https://github.com/junegunn/fzf |  |
| `gh` | `2.96.0` | latest | https://github.com/cli/cli |  |
| `htop` | `3.5.1` | latest | https://github.com/htop-dev/htop |  |
| `nvchad` | `2.5` | latest | https://github.com/NvChad/NvChad |  |
| `nvim` | `0.12.4` | latest | https://github.com/neovim/neovim |  |
| `rga` | `0.10.10` | latest | https://github.com/phiresky/ripgrep-all |  |
| `ripgrep` | `15.1.0` | latest | https://github.com/BurntSushi/ripgrep |  |
| `shellcheck` | `0.11.0` | latest | https://github.com/koalaman/shellcheck |  |
| `vault` | `2.0.3` | latest | https://github.com/hashicorp/vault |  |
| `yq` | `4.53.3` | latest | https://github.com/mikefarah/yq |  |

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

## In-Image File

- Devcontainer manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-php8.5-20260708-3.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
