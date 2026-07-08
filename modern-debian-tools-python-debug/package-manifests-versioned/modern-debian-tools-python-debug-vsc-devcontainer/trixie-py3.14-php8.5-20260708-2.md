# Modern Debian Tools + Python Debug VS Code Devcontainer

Versioned package manifest for `modern-debian-tools-python-debug-vsc-devcontainer`.

## Release

- Build date: `20260708-2`
- Target: `trixie-py314-php85-vsc`
- Debian: `trixie`
- Python: `3.14`
- PHP: `8.5`
- Immutable image tag: `trixie-py3.14-php8.5-20260708-2`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-20260708-2
```

## Purpose

Full-stack AI-agent cockpit devcontainer for Python/TypeScript development with database, container, and cloud tooling. Built on `mcr.microsoft.com/devcontainers/python:3.14-trixie`. Includes Docker-outside-of-Docker, the full mdt AI CLI tool suite (claude, codex, aider, reasonix, openclaw, antigravity, copilot), PostgreSQL and Redis clients, and extensive container inspection/security tooling.

## Version Selection Policies

### AI CLI Tools
Resolved to the **latest** published version at image build time from the respective registries (npm, PyPI, GitHub Releases). Override any version via build arg (e.g. `--build-arg CLAUDE_CODE_VERSION=2.1.200`).

### Supporting Tools (Rust / Go / system binaries)
Resolved to the **latest** GitHub release tag at build time. Downloads are pre-staged by `stage_tool_artifacts.py` and verified via upstream SHA256 checksums before installation.

### Debian System Packages
Installed from the **Debian Trixie main repos** (preferring backports where configured). These versions are stable snapshots from the Debian release cycle and are not auto-upgraded at container runtime.

### Python Packages
Resolved to **PyPI latest** at build time via pip install. The primary venv (`/home/vscode/.venv`) contains the full `toolkit.txt` closure; secondary venvs (e.g. Python 3.11, 3.9) contain only `uv + debugpy + ruff`.

## First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `ciu` | `4.3.0` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:e93498f1a0f1e2742067…` |
| `cmru` | `1.1.2` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:cf9a9bd956b4f89c3098…` |

## AI CLI Tools

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `aider` | `0.86.2` | latest (PyPI) | https://github.com/Aider-AI/aider | `pypi` |
| `antigravity` | `1.1.0` | latest (GitHub) | https://github.com/antigravity/antigravity-cli | `sha256:7ee512440af5ed0c8190…` |
| `claude` | `2.1.204` | latest (upstream) | https://github.com/anthropics/claude-code | `sha256:c8ee1ea69154533c691a…` |
| `codex` | `0.143.0` | latest (GitHub) | https://github.com/openai/codex | `sha256:bc5e36dbb2caeb34b48d…` |
| `copilot` | `1.0.69` | latest (npm) | https://github.com/github/copilot-cli | `npm` |
| `openclaw` | `2026.6.11` | latest (npm) | https://github.com/openclaw/openclaw | `npm` |
| `reasonix` | `1.17.7` | latest (npm) | https://github.com/reasonix/reasonix | `npm` |

## Container Inspection Tools

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `dive` | `0.13.1` | latest (GitHub) | https://github.com/wagoodman/dive | `sha256:0c20d18f0cc87e6e982a…` |
| `dtop` | `0.7.7` | latest (GitHub) | https://github.com/amir20/dtop | `sha256:945b940632e926ea55d5…` |
| `glances` | `4.5.5` | latest (PyPI) | https://github.com/nicolargo/glances | `sha256:7411c0fc02881fa970a5…` |
| `lazydocker` | `0.25.2` | latest (GitHub) | https://github.com/jesseduffield/lazydocker | `sha256:0d9dbfc26068b218e7ed…` |
| `syft` | `1.46.0` | latest (GitHub) | https://github.com/anchore/syft | `sha256:d654f678b709eb53c39…` |

## Security & Debug Tools

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `cdebug` | `0.0.19` | latest (GitHub) | https://github.com/iximiuz/cdebug | `sha256:10c2dd283ed690f445ac…` |
| `grype` | `0.115.0` | latest (GitHub) | https://github.com/anchore/grype | `sha256:3fad92940650e514c0aa…` |
| `hadolint` | `2.14.0` | latest (GitHub) | https://github.com/hadolint/hadolint | `sha256:6bf226944684f56c84dd…` |

## Custom Tooling

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `awscli` | `2.35.17` | latest (upstream) | https://github.com/aws/aws-cli | `sha256:ba326932b8ca7fef7b6e…` |
| `b2` | `4.7.1` | latest (GitHub) | https://github.com/Backblaze/B2_Command_Line_Tool | `sha256:0f4720858f137cbbdb43…` |
| `bat` | `0.26.1` | latest (GitHub) | https://github.com/sharkdp/bat | `sha256:726f04c8f576a7fd18b7…` |
| `consul` | `2.0.1` | latest (GitHub) | https://github.com/hashicorp/consul | `sha256:f8189736b05e3fe42d27…` |
| `delta` | `0.19.2` | latest (GitHub) | https://github.com/dandavison/delta | `sha256:8e695c5f586a8c53d6c3…` |
| `fd` | `10.4.2` | latest (GitHub) | https://github.com/sharkdp/fd | `sha256:def59805cd14b5651b68…` |
| `fzf` | `0.74.0` | latest (GitHub) | https://github.com/junegunn/fzf | `sha256:cf919f05b7581b4c744d…` |
| `gh` | `2.96.0` | latest (GitHub) | https://github.com/cli/cli | `sha256:83d5c2ccad5498f58bf6…` |
| `grpcurl` | `1.9.3` | latest (GitHub) | https://github.com/fullstorydev/grpcurl | `sha256:a926b62a85787ccf73ef…` |
| `htop` | `3.5.1` | latest (source) | https://github.com/htop-dev/htop | `sha256:dfc4a09845e9bc86f466…` |
| `nvchad` | `2.5` | latest (GitHub) | https://github.com/NvChad/NvChad | `sha256:738b167881a1a0888044…` |
| `nvim` | `0.12.4` | latest (GitHub) | https://github.com/neovim/neovim | `sha256:012bf3fcac5ade43914d…` |
| `rga` | `0.10.10` | latest (GitHub) | https://github.com/phiresky/ripgrep-all | `sha256:a969c25b182ac84aa672…` |
| `ripgrep` | `15.1.0` | latest (GitHub) | https://github.com/BurntSushi/ripgrep | `sha256:1c9297be4a084eea7eca…` |
| `shellcheck` | `0.11.0` | latest (GitHub) | https://github.com/koalaman/shellcheck | `sha256:8c3be12b05d5c177a04c…` |
| `vault` | `2.0.3` | latest (GitHub) | https://github.com/hashicorp/vault | `sha256:1e0ffb7a82491219c724…` |
| `yq` | `4.53.3` | latest (GitHub) | https://github.com/mikefarah/yq | `sha256:fa52a4e758c63d382991…` |

## Python & PHP Runtime

| Component | Version | Policy | Notes |
|---|---|---|---|
| Python (default) | `3.14` | pinned (devcontainer base) | From `mcr.microsoft.com/devcontainers/python:3.14-trixie` |
| Python venv | `/home/vscode/.venv` | build-time pip | Full toolkit: ipython, asyncpg, redis, hvac, httpx, sqlalchemy, dnspython, websockets, requests, boto3, pytest, etc. |
| Secondary Python | `3.11` / `3.9` | pinned (via `uv python install`) | Lean: uv, debugpy, ruff only |
| PHP | `8.5` | sury.org repo | Conditional (INSTALL_PHP=true); includes php-cli, mbstring, xml, curl, pgsql, sqlite3, redis, gd, bcmath, intl |

## System Packages

Installed via apt from Debian Trixie (preferring backports where available).

Key packages include: `bash-completion`, `bind9-dnsutils`, `ca-certificates`, `curl`, `fuse3`, `gdb`, `git`, `git-lfs`, `gnupg`, `gzip`, `httpie`, `jq`, `less`, `locales`, `mc`, `minisign`, `nano`, `ncdu`, `netcat-openbsd`, `openssl`, `postgresql-client-17`, `procps`, `psmisc`, `python3-venv`, `redis-tools`, `rsync`, `skopeo`, `sqlite3`, `sshfs`, `strace`, `sysstat`, `tar`, `tree`, `unzip`, `util-linux`, `vim`, `w3m`, `wget`, `xz-utils`

## In-Image File

- Devcontainer manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`
- Installed-tools inventory: `/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-php8.5-20260708-2.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
