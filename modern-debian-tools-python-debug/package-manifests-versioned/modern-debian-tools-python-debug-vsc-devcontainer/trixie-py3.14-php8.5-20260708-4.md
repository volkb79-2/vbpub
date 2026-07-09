# Modern Debian Tools + Python Debug VS Code Devcontainer

Versioned package manifest for `modern-debian-tools-python-debug-vsc-devcontainer`.

## Release

- Build date: `20260708-4`
- Target: `trixie-py314-php85-vsc`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20260708-4`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-20260708-4
```

## Version Selection Policies

Tool versions are selected as follows:

- **First-party wheels:** Pinned to specific releases at build time; see table above.
- **AI CLI tools:** Latest npm/GitHub release at build time (override via build arg).
- **Container inspection tools:** Latest GitHub release at build time (override via build arg).
- **Security & debug tools:** Latest GitHub release at build time; verified via upstream SHA256 checksums.
- **Custom tooling:** Latest GitHub release at build time; some tools compiled from source.
- **System packages:** Versions from the Debian trixie repository at image build time.

## Purpose

Provides a Debian trixie-based VS Code devcontainer environment with Python 3.14, PHP 8.5, first-party CI/CD tooling (CIU, cmru), and a comprehensive suite of debugging, security, and container inspection tools. Designed for software development workflows requiring immediate access to modern CLI tooling (AI assistants, database clients, file utilities) without manual installation.

## First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `ciu` | `4.3.0` | latest |  | `e93498f1a0f1e2742067d8ccdd5ffbe95d19bcd4fa6c6e9dbbdca37b4df75e11` |
| `cmru` | `1.1.2` | latest |  | `cf9a9bd956b4f89c3098328fb2c8ef8f7d9b30c5572c500a0af2e506cd274b8d` |


## Runtime Version Snapshot (Pre-build Probe)

### First-Party Wheels

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `CIU` | `4.3.0` | latest |  |  |
| `cmru` | `1.1.2` | latest |  |  |

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

## Python & PHP Runtime

This Devcontainer image is built on **Debian trixie** with the following language runtimes:

- **Python:** `3.14` — installed via the Debian trixie repository (`python3.14`); includes `pip`, `venv`, and common development headers.
- **PHP:** `8.5` — installed via the Debian trixie repository (`php8.5`); includes CLI, common modules, and Composer (where applicable).

Both runtimes are the standard Debian-packaged versions, pinned at image build time. Additional Python and PHP tooling may be installed via `pip` or `composer` at container start.

## In-Image File

- Devcontainer manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-php8.5-20260708-4.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
