# Modern Debian Tools + Python Debug

Versioned package manifest for `modern-debian-tools-python-debug`.

## Release

- Build date: `20260708-4`
- Target: `trixie-py314-php85`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20260708-4`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-php8.5-20260708-4
```

## Purpose

Full-stack AI-agent cockpit image for Python/TypeScript development with database, container, and cloud tooling. Built on `mcr.microsoft.com/devcontainers/python:3.14-trixie`. Includes the full mdt AI CLI tool suite (claude, codex, aider, reasonix, openclaw, antigravity), PostgreSQL and Redis clients, and extensive container inspection/security tooling.

## First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `ciu` | `4.3.0` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:e93498f1a0f1e2742067d8ccdd5ffbe95d19bcd4fa6c6e9dbbdca37b4df75e11` |
| `cmru` | `1.1.2` | pinned release | https://github.com/volkb79-2/vbpub/releases | `sha256:cf9a9bd956b4f89c3098328fb2c8ef8f7d9b30c5572c500a0af2e506cd274b8d` |

## AI CLI Tools

**Version policy:** latest npm/GitHub release at build time (override via build arg). AI CLI tool versions are resolved dynamically during `stage_tool_artifacts` from the respective package registries (npm, PyPI, GitHub Releases).

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `aider` | `0.86.2` | latest (PyPI) | https://github.com/Aider-AI/aider | `pypi` |
| `antigravity` | `1.1.0` | latest (GitHub) | https://github.com/antigravity/antigravity-cli | `sha256:7ee512440af5ed0c819065cd7cc14eec90699214df4be32280ac346f0100577e` |
| `claude` | `2.1.205` | latest (upstream) | https://github.com/anthropics/claude-code | `sha256:dd8734c0b6a503fe1d17425184e57b397c30bb0337a33f1470d9985febfe5b09` |
| `codex` | `0.143.0` | latest (GitHub) | https://github.com/openai/codex | `sha256:bc5e36dbb2caeb34b48dbfbab92e7593a4fa3b47dc0a39b9f30403e2c18e25bd` |
| `openclaw` | `2026.6.11` | latest (npm) | https://github.com/openclaw/openclaw | `npm` |
| `reasonix` | `1.17.8` | latest (npm) | https://github.com/reasonix/reasonix | `npm` |

## Container Inspection Tools

**Version policy:** latest GitHub release at build time (override via build arg). All tools in this category are downloaded as pre-built binaries from their upstream releases.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `dive` | `0.13.1` | latest (GitHub) | https://github.com/wagoodman/dive | `sha256:0c20d18f0cc87e6e982a3289712ac3aa9fc364ba973109d1da3a473232640571` |
| `dtop` | `0.7.8` | latest (GitHub) | https://github.com/amir20/dtop | `sha256:65e3aeaa044d4d50ca157b6d303ed335f8a305246b22beb154ce003fe0698457` |
| `glances` | `4.5.5` | latest (PyPI) | https://github.com/nicolargo/glances | `sha256:7411c0fc02881fa970a5c1b0af5953ffe211fe553ea2eab012d211e76c5bbc46` |
| `lazydocker` | `0.25.2` | latest (GitHub) | https://github.com/jesseduffield/lazydocker | `sha256:0d9dbfc26068b218e7ed84b104748cadc6e3cf733c0afd35465306fb39b9523c` |
| `syft` | `1.46.0` | latest (GitHub) | https://github.com/anchore/syft | `sha256:d654f678b709eb53c393d38519d5ed7d2e57205529404018614cfefa0fb2b5ca` |

## Security & Debug Tools

**Version policy:** latest GitHub release at build time (override via build arg). Binaries are verified via upstream SHA256 checksums before installation.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `cdebug` | `0.0.19` | latest (GitHub) | https://github.com/iximiuz/cdebug | `sha256:10c2dd283ed690f445ac41d7b4846101abe92d1a61c725d7c6cfe81f86c48024` |
| `grype` | `0.115.0` | latest (GitHub) | https://github.com/anchore/grype | `sha256:3fad92940650e514c0aa2dad83526942a055e210cec09a8a59d9c024adc2b90e` |
| `hadolint` | `2.14.0` | latest (GitHub) | https://github.com/hadolint/hadolint | `sha256:6bf226944684f56c84dd014e8b979d27425c0148f61b3bd99bcc6f39e9dc5a47` |

## Custom Tooling

**Version policy:** latest GitHub release at build time (override via build arg). Some tools are compiled from source (nvim, htop); the rest are pre-built binaries.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `awscli` | `2.35.18` | latest (upstream) | https://github.com/aws/aws-cli | `sha256:815fd90211c88f2ad8d137d293935f0bb8ef6dca895b8460a8778dae825f1d9f` |
| `b2` | `4.7.1` | latest (GitHub) | https://github.com/Backblaze/B2_Command_Line_Tool | `sha256:0f4720858f137cbbdb434f13edb5ad8bc5e99a0b83ba8b1f7143831dab937eea` |
| `bat` | `0.26.1` | latest (GitHub) | https://github.com/sharkdp/bat | `sha256:726f04c8f576a7fd18b7634f1bbf2f915c43494c1c0f013baa3287edb0d5a2a3` |
| `consul` | `2.0.2` | latest (GitHub) | https://github.com/hashicorp/consul | `sha256:96e56c9d06b4a15bfa316afa39af926c1b67d189f66388dc1eecbb7c26faeed4` |
| `delta` | `0.19.2` | latest (GitHub) | https://github.com/dandavison/delta | `sha256:8e695c5f586a8c53d6c3b01be0b4a422ed218bfed2a56191caebe373a1c18ab2` |
| `fd` | `10.4.2` | latest (GitHub) | https://github.com/sharkdp/fd | `sha256:def59805cd14b5651b68990855f426ad087f3b96881296d963910431ba3143c8` |
| `fzf` | `0.74.0` | latest (GitHub) | https://github.com/junegunn/fzf | `sha256:cf919f05b7581b4c744d764eaa704665d61dd6d3ca785f0df2351281dff60cda` |
| `gh` | `2.96.0` | latest (GitHub) | https://github.com/cli/cli | `sha256:83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60` |
| `htop` | `3.5.1` | latest (source) | https://github.com/htop-dev/htop | `sha256:dfc4a09845e9bc86f466a722e62b8f87d59028ff39689077ff2257a6a605061d` |
| `nvchad` | `2.5` | latest (GitHub) | https://github.com/NvChad/NvChad | `sha256:738b167881a1a088804420403e4120bbb9896f61c6d7ef3a09187abb8a4ea91d` |
| `nvim` | `0.12.4` | latest (GitHub) | https://github.com/neovim/neovim | `sha256:012bf3fcac5ade43914df3f174668bf64d05e049a4f032a388c027b1ebd78628` |
| `rga` | `0.10.10` | latest (GitHub) | https://github.com/phiresky/ripgrep-all | `sha256:a969c25b182ac84aa672518313b5f741091decf7d93d03a020bcfe517b9ff4e8` |
| `ripgrep` | `15.1.0` | latest (GitHub) | https://github.com/BurntSushi/ripgrep | `sha256:1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599` |
| `shellcheck` | `0.11.0` | latest (GitHub) | https://github.com/koalaman/shellcheck | `sha256:8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198` |
| `vault` | `2.0.3` | latest (GitHub) | https://github.com/hashicorp/vault | `sha256:1e0ffb7a82491219c7242da6e05e2d756b05d1097c29799a42228661f229bc2a` |
| `yq` | `4.53.3` | latest (GitHub) | https://github.com/mikefarah/yq | `sha256:fa52a4e758c63d38299163fbdd1edfb4c4963247918bf9c1c5d31d84789eded4` |

## Version Selection Policies

This image uses the following version-selection strategies, applied consistently across all tool categories:

| Category | Policy | Details |
|---|---|---|
| First-Party Wheels | Pinned release | Versions are pinned in the build configuration and updated intentionally per release |
| AI CLI Tools | Latest npm/GitHub release | Resolved dynamically from package registries (npm, PyPI, GitHub Releases) at build time |
| Container Inspection Tools | Latest GitHub release | Downloaded as pre-built binaries from upstream releases |
| Security & Debug Tools | Latest GitHub release | Binaries verified via upstream SHA256 checksums before installation |
| Custom Tooling | Latest GitHub release | Some tools compiled from source (nvim, htop); remainder are pre-built binaries |
| System Packages | Debian Trixie (preferring backports) | Installed via apt with standard Debian pinning |

## Python & PHP Runtime

| Component | Version | Policy | Notes |
|---|---|---|---|
| Python (default) | `3.14` | pinned (devcontainer base) | From `mcr.microsoft.com/devcontainers/python:3.14-trixie` |
| Python venv | `/home/vscode/.venv` | build-time pip | Full toolkit: ipython, asyncpg, redis, hvac, httpx, sqlalchemy, dnspython, websockets, requests, boto3, pytest, etc. |
| System Python | `3.13` | Debian Trixie | `python3-venv` package (`3.13.5-1`) is the Debian-system Python; independent of the devcontainer base Python 3.14 |
| Secondary Python | `3.11` / `3.9` | pinned (via `uv python install`) | Lean: uv, debugpy, ruff only |
| PHP | `8.5` | sury.org repo | Conditional (INSTALL_PHP=true); includes php-cli, mbstring, xml, curl, pgsql, sqlite3, redis, gd, bcmath, intl |

## System Packages

Installed via apt from Debian Trixie (preferring backports where available).

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

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260708-4.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
