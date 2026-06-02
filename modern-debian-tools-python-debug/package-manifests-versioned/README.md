# Versioned Package Manifests

Repository-hosted Markdown targets for GHCR package metadata.

## Package Families

- [modern-debian-tools-python-debug](https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/README.md) - stable current docs: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/latest.md
- [modern-debian-tools-python-debug-vsc-devcontainer](https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md) - stable current docs: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/latest.md

Each family directory contains a landing page plus versioned release manifests that can also be copied into the image build output.

Runtime snapshot sections include optional AI tooling (`aider`, `codex`, `claude`, `antigravity`) when enabled.
If a corresponding `INSTALL_*` toggle is disabled for a build, the generated manifest records that tool as `not-installed`.
