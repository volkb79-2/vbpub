# MDT-P01: Implementation Log

## Outcome

The MDT image now installs both required executables from the verified Codex
package and includes OpenCode as a pinned, optional npm-based AI CLI.

## Implementation

- `install_ai_cli_tools.py` validates and extracts `codex` plus
  `codex-code-mode-host` in one pass. Invalid archives and missing executables
  produce explicit `InstallerError` failures.
- Codex SHA256 verification remains ahead of extraction.
- `stage_tool_artifacts.py` requires both Codex executables and resolves
  `OPENCODE_VER` from the `opencode-ai` npm package.
- OpenCode installation uses `npm install -g opencode-ai@<resolved-version>`
  and verifies that npm exposed the `opencode` command.
- Dockerfile, bake, CMRU, AI-tool inventory, and installed-manifest wiring now
  include `OPENCODE_VERSION` and `INSTALL_OPENCODE`.
- README and USAGE inventory and override documentation include OpenCode.
- The vendorable devcontainer template persists `~/.config/opencode` through
  the existing `.config` mount and persists `~/.local/share/opencode` through
  a dedicated `opencode-data` host directory.
- The manifest project link points to `https://github.com/anomalyco/opencode`.

## Controller Review Corrections

- Replaced the agent's incorrect `~/.opencode` state claim with the current
  Linux XDG config and data locations.
- Corrected the upstream GitHub organization.
- Added the missing README and vendorable mount changes.
- Added post-install command verification.
- Added behavioral coverage for npm-based OpenCode version resolution.
- Normalized new files to ASCII.

## Validation

- `/home/vscode/.venv/bin/python -m pytest test_install_ai_cli_tools.py -q`
  from `modern-debian-tools-python-debug/scripts`: 21 passed, one unrelated
  dependency deprecation warning.
- `python3 -m py_compile` passed for the installer, staging, manifest, test,
  and template bootstrap Python files.
- `tomllib` parsed `cmru.build.toml`.
- `docker buildx bake -f docker-bake.hcl --print all` succeeded and emitted
  valid JSON.
- `git diff --check` passed.
- `npm view opencode-ai@latest version bin --json` reported version 1.17.18
  and the `opencode` command mapping during controller review.

## Files

Implementation and tests are confined to `modern-debian-tools-python-debug/**`.
No `topos/**`, staged binary artifacts, historical build logs, or versioned
release manifests were changed.

## Blockers

None.
