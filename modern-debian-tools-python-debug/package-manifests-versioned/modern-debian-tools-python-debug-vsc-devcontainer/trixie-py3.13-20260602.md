# Modern Debian Tools + Python Debug VS Code Devcontainer

Versioned package manifest for `modern-debian-tools-python-debug-vsc-devcontainer`.

## Release

- Build date: `20260602`
- Target: `trixie-py313-vsc`
- Debian: `trixie`
- Python: `3.13`
- Immutable image tag: `trixie-py3.13-20260602`
- Floating image tag: `trixie-py3.13-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.13-20260602
```

## Purpose

Modern Debian Tools + Python Debug VS Code devcontainer image.
Microsoft devcontainers base plus curated CLI tooling, managed venv helpers, and repository-hosted release manifests for richer per-package documentation.
## Staged Tool Artifacts

### Resolved Tool Versions

- aider: `latest`
- antigravity: `1.0.4`
- awscli: `2.34.59`
- b2: `4.7.0`
- bat: `0.26.1`
- claude: `2.1.161`
- consul: `2.0.0`
- codex: `0.136.0`
- delta: `0.19.2`
- fd: `10.4.2`
- fzf: `0.73.1`
- gh: `2.93.0`
- rga: `0.10.10`
- ripgrep: `15.1.0`
- shellcheck: `0.11.0`
- vault: `2.0.1`
- yq: `4.53.2`

### Artifact Sources and Digests

- codex 0.136.0 (tar.gz): sha256 `5bf661356a68c897d96997e2a65a56d4ad7ffa4f4f85b6dd44506a6e8118f072`; source `https://github.com/openai/codex/releases/download/rust-v0.136.0/codex-package-x86_64-unknown-linux-musl.tar.gz`
- codex-sha256sums 0.136.0 (checksum-file): sha256 `0965bae4bcde686d87dbd3df87a99bc1b1dbc483eb87389bde5039ebecdf6009`; source `https://github.com/openai/codex/releases/download/rust-v0.136.0/codex-package_SHA256SUMS`
- claude 2.1.161 (binary): sha256 `1f6a22f387a3bce496b6d869389a35dffb5a69c97d9831833f3bd6dc0e6c6c28`; source `https://downloads.claude.ai/claude-code-releases/2.1.161/linux-x64/claude`
- claude-manifest 2.1.161 (manifest): sha256 `9872ba39ea64e7fe4e7916eb157da6377f81c5a06d34a5dde80981b52b49350b`; source `https://downloads.claude.ai/claude-code-releases/2.1.161/manifest.json`
- antigravity 1.0.4 (tar.gz): sha256 `e63909ae717aceaa0a482de053c23836e77b5ae57b22ee6d445e9833f1e4a7bd`; source `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.0.4-6410134369468416/linux-x64/cli_linux_x64.tar.gz`
- antigravity-manifest 1.0.4 (manifest): sha256 `063545e9b0fe3063c68375aa69acbd76eccce717df4e35bbaf99a52baf5c3f00`; source `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json`
- b2 4.7.0 (binary): sha256 `f319f50cf3c6cbb7dd8da6837af734ae769c4b117ed07ede9ddf43fcd0d525e2`; source `https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v4.7.0/b2-linux`
- b2-hashes 4.7.0 (checksum-file): sha256 `c60cf974b4b52764b0cce9bcadcf27b29c949c5afdea7320e0dfee96b27ce5ca`; source `https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v4.7.0/b2-linux_hashes.txt`
- bat 0.26.1 (tar.gz): sha256 `726f04c8f576a7fd18b7634f1bbf2f915c43494c1c0f013baa3287edb0d5a2a3`; source `https://github.com/sharkdp/bat/releases/download/v0.26.1/bat-v0.26.1-x86_64-unknown-linux-gnu.tar.gz`
- delta 0.19.2 (tar.gz): sha256 `8e695c5f586a8c53d6c3b01be0b4a422ed218bfed2a56191caebe373a1c18ab2`; source `https://github.com/dandavison/delta/releases/download/0.19.2/delta-0.19.2-x86_64-unknown-linux-gnu.tar.gz`
- fd 10.4.2 (tar.gz): sha256 `def59805cd14b5651b68990855f426ad087f3b96881296d963910431ba3143c8`; source `https://github.com/sharkdp/fd/releases/download/v10.4.2/fd-v10.4.2-x86_64-unknown-linux-gnu.tar.gz`
- rga 0.10.10 (tar.gz): sha256 `a969c25b182ac84aa672518313b5f741091decf7d93d03a020bcfe517b9ff4e8`; source `https://github.com/phiresky/ripgrep-all/releases/download/v0.10.10/ripgrep_all-v0.10.10-x86_64-unknown-linux-musl.tar.gz`
- ripgrep 15.1.0 (tar.gz): sha256 `1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599`; source `https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz`
- shellcheck 0.11.0 (tar.xz): sha256 `8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198`; source `https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz`
- fzf 0.73.1 (tar.gz): sha256 `f3252c2c366bc1700d3c85781ec8c9695998927ac127870eb049ceea2d540f8a`; source `https://github.com/junegunn/fzf/releases/download/v0.73.1/fzf-0.73.1-linux_amd64.tar.gz`
- yq 4.53.2 (binary): sha256 `d56bf5c6819e8e696340c312bd70f849dc1678a7cda9c2ad63eebd906371d56b`; source `https://github.com/mikefarah/yq/releases/download/v4.53.2/yq_linux_amd64`
- gh 2.93.0 (tar.gz): sha256 `02d1290eba130e0b896f3709ffff22e1c75a51475ddb70476a85abc6b5807af0`; source `https://github.com/cli/cli/releases/download/v2.93.0/gh_2.93.0_linux_amd64.tar.gz`
- consul 2.0.0 (zip): sha256 `25fe76d3203529af59834cff4a29a128050b630d62901be7ad850b9991ddf991`; source `https://releases.hashicorp.com/consul/2.0.0/consul_2.0.0_linux_amd64.zip`
- consul-sha256sums 2.0.0 (checksum-file): sha256 `c9c86c574be7374968aef5e05598c24c358f23efa7971bdb1510943deca206c0`; source `https://releases.hashicorp.com/consul/2.0.0/consul_2.0.0_SHA256SUMS`
- vault 2.0.1 (zip): sha256 `c6ed3be36a750875906916716680322719920a102f98c9a0b3105ecff63b9e34`; source `https://releases.hashicorp.com/vault/2.0.1/vault_2.0.1_linux_amd64.zip`
- vault-sha256sums 2.0.1 (checksum-file): sha256 `9043ff123f0b1a3f8686ba0f9189c55e73029e0f5b8490b79d5689d422bb9e7d`; source `https://releases.hashicorp.com/vault/2.0.1/vault_2.0.1_SHA256SUMS`
- awscli 2.34.59 (zip): sha256 `968f9c7096a8a8de090cbd75cea26ea9791c46c7ef6de3b345668a515b43dd24`; source `https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip`

Generated from local pre-staging metadata to make release documentation auditable and reproducible.

## Runtime Version Snapshot (Pre-build Probe)

### Custom Tooling

- CIU: 20260221
- aider: latest (resolved at image build time)
- antigravity: 1.0.4
- awscli: 2.34.59
- b2: 4.7.0
- bat: 0.26.1
- claude: 2.1.161
- consul: 2.0.0
- codex: 0.136.0
- delta: 0.19.2
- fd: 10.4.2
- fzf: 0.73.1
- gh: 2.93.0
- psql: latest (see apt probe below)
- redis-cli: latest (see apt probe below)
- rga: 0.10.10
- ripgrep: 15.1.0
- shellcheck: 0.11.0
- vault: 2.0.1
- yq: 4.53.2

### System packages

    (candidate versions from apt probe)
    probe-unavailable


## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.13-20260602.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## In-Image Files

- Release manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`
- Installed tool inventory: `/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md`

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
