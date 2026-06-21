# Modern Debian Tools + Python Debug VS Code Devcontainer

Versioned package manifest for `modern-debian-tools-python-debug-vsc-devcontainer`.

## Release

- Build date: `20260621-4`
- Target: `trixie-py311-vsc`
- Debian: `trixie`
- Python: `3.11`
- Immutable image tag: `trixie-py3.11-20260621-4`
- Floating image tag: `trixie-py3.11-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-20260621-4
```

## Purpose

No description provided.

## First-Party Wheels

- ciu `4.0.0` — sha256: `5842f58709c7494ba6ea0f26dc7cf54caabd3abfde7489a14e4f728354b10228`
- cmru `1.1.0` — sha256: `a8027aa25956217f88726dbe1f9ef4a97f35426bf1e3624fbae3ec7e9c58c578`

## Staged Tool Artifacts

### AI CLI Tools

- aider: `latest`
- reasonix: `0.53.2`
- deepcode: `1.2.0`
- openclaw: `2026.6.9`
- antigravity: `1.0.10`
- claude: `2.1.185`
- codex: `0.141.0`

### Supporting Tool Versions

- awscli: `2.35.9`
- b2: `4.7.1`
- bat: `0.26.1`
- consul: `2.0.1`
- delta: `0.19.2`
- fd: `10.4.2`
- fzf: `0.73.1`
- gh: `2.95.0`
- rga: `0.10.10`
- ripgrep: `15.1.0`
- shellcheck: `0.11.0`
- vault: `2.0.3`
- yq: `4.53.3`

- GRPCURL_VER: `1.9.3`

Generated from local pre-staging metadata to make release documentation auditable and reproducible.

## Runtime Version Snapshot (Pre-build Probe)

### First-Party Wheels

- cmru: 1.1.0
- CIU: 4.0.0

### AI CLI Tools

- aider: latest (resolved at image build time)
- reasonix: 0.53.2
- deepcode: 1.2.0
- openclaw: 2026.6.9
- codex: 0.141.0
- claude: 2.1.185
- antigravity: 1.0.10

### Custom Tooling

- awscli: 2.35.9
- b2: 4.7.1
- bat: 0.26.1
- consul: 2.0.1
- delta: 0.19.2
- fd: 10.4.2
- fzf: 0.73.1
- gh: 2.95.0
- psql: latest (see apt probe below)
- redis-cli: latest (see apt probe below)
- rga: 0.10.10
- ripgrep: 15.1.0
- shellcheck: 0.11.0
- vault: 2.0.3
- yq: 4.53.3

### System packages

    (candidate versions from apt probe)
    bash-completion=1:2.16.0-7
    ca-certificates=20250419
    curl=8.14.1-2+deb13u3
    bind9-dnsutils=1:9.20.23-1~deb13u1
    fuse3=3.17.2-3
    git=1:2.47.3-0+deb13u1
    git-lfs=3.6.1-1+deb13u1
    gnupg=2.4.7-21+deb13u1
    gzip=1.13-1
    htop=3.4.1-5
    httpie=3.2.4-3
    iputils-ping=3:20240905-3
    jq=1.7.1-6+deb13u2
    less=668-1
    lsb-release=12.1-1
    lsof=4.99.4+dfsg-2
    man-db=2.13.1-1
    mc=3:4.8.33-1+deb13u1
    netcat-openbsd=1.229-1
    ncdu=1.22-1
    openssl=3.5.6-1~deb13u2
    python3-venv=3.13.5-1
    psmisc=23.7-2
    rsync=3.4.1+ds1-5+deb13u3
    sqlite3=3.46.1-7+deb13u1
    strace=6.13+ds-1
    sshfs=3.7.3-1.1+b2
    tar=1.35+dfsg-3.1
    tree=2.2.1-1
    unzip=6.0-29
    vim=2:9.1.1230-2
    w3m=0.5.3+git20230121-2.1
    wget=1.25.0-2
    xz-utils=5.8.1-1
    postgresql-client=17+278
    redis-tools=5:8.0.2-3+deb13u2

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.11-20260621-4.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## In-Image File

- Devcontainer manifest: `/home/vscode/devcontainer-manifest-trixie-py3.11-20260621-4.md`

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.

## Appendix: Artifact Sources and Digests

Full sha256 digests for all staged artifacts. Use these to verify reproducibility.

- codex 0.141.0 (tar.gz): sha256 `091c8a2e27370c41407fa1cb647fe905bd4fd70e4689c13effee0a2dce1b2b07`; source `https://github.com/openai/codex/releases/download/rust-v0.141.0/codex-package-x86_64-unknown-linux-musl.tar.gz`
- codex-sha256sums 0.141.0 (checksum-file): sha256 `94062ac8bd49941fae39e6846a4fcb01b8a1ace2c588ccd5e5ffa8fb74013ab5`; source `https://github.com/openai/codex/releases/download/rust-v0.141.0/codex-package_SHA256SUMS`
- claude 2.1.185 (binary): sha256 `e1246338699f04ee0e627dee3f6d4ed7a0bab48e0514bde69c6dad43bc303952`; source `https://downloads.claude.ai/claude-code-releases/2.1.185/linux-x64/claude`
- claude-manifest 2.1.185 (manifest): sha256 `3dab78d3d9a713b19648610c6b45466cb9324a1c398e227cea59c38f91a262df`; source `https://downloads.claude.ai/claude-code-releases/2.1.185/manifest.json`
- antigravity 1.0.10 (tar.gz): sha256 `6547cf9a37227f26004fa4b805418b1df96f54c57b9723ca7d10864d2610bb0f`; source `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.0.10-6349723456634880/linux-x64/cli_linux_x64.tar.gz`
- antigravity-manifest 1.0.10 (manifest): sha256 `a330c3aa873a475949921301592f5ee967ea06e946698c40052455b2ac823f5c`; source `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json`
- b2 4.7.1 (binary): sha256 `0f4720858f137cbbdb434f13edb5ad8bc5e99a0b83ba8b1f7143831dab937eea`; source `https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v4.7.1/b2-linux`
- b2-hashes 4.7.1 (checksum-file): sha256 `883d61e2ebb4cc922a504b66677f2c32f29c23a1159e3616b6b5f88c2de2e979`; source `https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v4.7.1/b2-linux_hashes.txt`
- bat 0.26.1 (tar.gz): sha256 `726f04c8f576a7fd18b7634f1bbf2f915c43494c1c0f013baa3287edb0d5a2a3`; source `https://github.com/sharkdp/bat/releases/download/v0.26.1/bat-v0.26.1-x86_64-unknown-linux-gnu.tar.gz`
- delta 0.19.2 (tar.gz): sha256 `8e695c5f586a8c53d6c3b01be0b4a422ed218bfed2a56191caebe373a1c18ab2`; source `https://github.com/dandavison/delta/releases/download/0.19.2/delta-0.19.2-x86_64-unknown-linux-gnu.tar.gz`
- fd 10.4.2 (tar.gz): sha256 `def59805cd14b5651b68990855f426ad087f3b96881296d963910431ba3143c8`; source `https://github.com/sharkdp/fd/releases/download/v10.4.2/fd-v10.4.2-x86_64-unknown-linux-gnu.tar.gz`
- rga 0.10.10 (tar.gz): sha256 `a969c25b182ac84aa672518313b5f741091decf7d93d03a020bcfe517b9ff4e8`; source `https://github.com/phiresky/ripgrep-all/releases/download/v0.10.10/ripgrep_all-v0.10.10-x86_64-unknown-linux-musl.tar.gz`
- ripgrep 15.1.0 (tar.gz): sha256 `1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599`; source `https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz`
- shellcheck 0.11.0 (tar.xz): sha256 `8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198`; source `https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz`
- fzf 0.73.1 (tar.gz): sha256 `f3252c2c366bc1700d3c85781ec8c9695998927ac127870eb049ceea2d540f8a`; source `https://github.com/junegunn/fzf/releases/download/v0.73.1/fzf-0.73.1-linux_amd64.tar.gz`
- yq 4.53.3 (binary): sha256 `fa52a4e758c63d38299163fbdd1edfb4c4963247918bf9c1c5d31d84789eded4`; source `https://github.com/mikefarah/yq/releases/download/v4.53.3/yq_linux_amd64`
- gh 2.95.0 (tar.gz): sha256 `25d1e4729e8808c9ed3d613e96ebd3f3e44446f2d368c89d878a71a36ddb3d8c`; source `https://github.com/cli/cli/releases/download/v2.95.0/gh_2.95.0_linux_amd64.tar.gz`
- grpcurl 1.9.3 (tar.gz): sha256 `a926b62a85787ccf73ef8736b3ae554f1242e39d92bb8767a79d6dd23b11d1d5`; source `https://github.com/fullstorydev/grpcurl/releases/download/v1.9.3/grpcurl_1.9.3_linux_x86_64.tar.gz`
- consul 2.0.1 (zip): sha256 `f8189736b05e3fe42d27dd83dfbd3a6d7e44b5669b2e51684362e9c1639babe0`; source `https://releases.hashicorp.com/consul/2.0.1/consul_2.0.1_linux_amd64.zip`
- consul-sha256sums 2.0.1 (checksum-file): sha256 `a75b2cf4d621c98c44d4ffdde85b76e951fe988b7751daf60414d038c2072859`; source `https://releases.hashicorp.com/consul/2.0.1/consul_2.0.1_SHA256SUMS`
- vault 2.0.3 (zip): sha256 `1e0ffb7a82491219c7242da6e05e2d756b05d1097c29799a42228661f229bc2a`; source `https://releases.hashicorp.com/vault/2.0.3/vault_2.0.3_linux_amd64.zip`
- vault-sha256sums 2.0.3 (checksum-file): sha256 `c361d1f6e5ff1f92f0285680b054a3079898f37c134e7d6ac6f30a8ad7bfc5b3`; source `https://releases.hashicorp.com/vault/2.0.3/vault_2.0.3_SHA256SUMS`
- awscli 2.35.9 (zip): sha256 `b331d4822a22612915f22f89cfd0e07895c7b6837999fca8fb9f6c2a370a54c0`; source `https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip`

