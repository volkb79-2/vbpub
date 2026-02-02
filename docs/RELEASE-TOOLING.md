# Release Tooling (Config-Driven)

## Overview

vbpub uses a config-driven release workflow:

- **Main config**: vbpub/release.toml (local copy with secrets; gitignored)
- **Repo defaults**: vbpub/release.sample.toml (committed, no secrets)
- **Syntax reference**: release-manager/release.sample.toml (repo-agnostic spec)
- **Per-project steps**: build-push.toml in each project
- **Runner**: release-all.py
- **Step runner**: release-manager/src/release_manager/step_runner.py

## Primary entrypoints (Python)

```bash
python3 /workspaces/vbpub/release-all.py --config /workspaces/vbpub/release.toml
python3 /workspaces/vbpub/release-runner.py
```

## Config lifecycle

1. Copy the repo defaults into a local config:
  - vbpub/release.sample.toml → vbpub/release.toml
2. Fill in secrets in vbpub/release.toml (GitHub token, usernames).
3. Run the pipeline via release-runner.

The release runner auto-creates release.toml from release.sample.toml when missing
and exits immediately to force you to fill in secrets.

## OCI image description

OCI_DESCRIPTION values are Markdown. Include a short tool manifest summary and
reference the in-image manifest at:

- /usr/local/share/modern-debian-tools-python-debug/manifest.md

## Per-project configs

- [ciu/build-push.toml](../ciu/build-push.toml)
- [modern-debian-tools-python-debug/build-push.toml](../modern-debian-tools-python-debug/build-push.toml)
- [playwright-mcp/build-push.toml](../playwright-mcp/build-push.toml)

## Cleanup by age

```bash
python3 /workspaces/vbpub/release-all.py \
  --config /workspaces/vbpub/release.toml \
  --remove-assets 10min
```

## Notes

- GitHub credentials are loaded from release.toml, not .env.
- The pipeline is config-driven; no project logic is hardcoded in the orchestrator.
