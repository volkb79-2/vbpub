# Release Tooling (Config-Driven)

## Overview

vbpub uses a config-driven release workflow:

- **Main config**: vbpub/release.toml (secrets live here)
- **Sample config**: release-manager/release.sample.toml
- **Per-project steps**: build-push.toml in each project
- **Runner**: release-all.py
- **Step runner**: release-manager/src/release_manager/step_runner.py

## Primary entrypoints (Python)

```bash
python3 /workspaces/vbpub/release-all.py --config /workspaces/vbpub/release.toml
python3 /workspaces/vbpub/release-runner.py
```

## Per-project configs

- [ciu/build-push.toml](../ciu/build-push.toml)
- [vsc-devcontainer/build-push.toml](../vsc-devcontainer/build-push.toml)
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
