# MDT-P01: Implementation Log

## Overview

Implemented Codex companion host (`codex-code-mode-host`) installation and added
OpenCode as a first-class AI CLI tool across the modern-debian-tools-python-debug
image build pipeline.

## Commands Executed

### 1. install_ai_cli_tools.py — companion-host + OpenCode

- Added `import tarfile` for archive introspection.
- Added `archive_missing_binaries(archive, *names)` — checks tar archives for
  required executable files without extracting.
- Added `install_binaries_from_archive(archive, *pairs)` — extracts once and
  copies multiple named binaries to their destinations.
- Updated `install_codex()`:
  - Validates both `codex` and `codex-code-mode-host` are present in the archive.
  - Installs both via `install_binaries_from_archive()`.
- Added `install_opencode()` function following the npm-tool pattern (reasonix,
  openclaw, copilot).
- Added `opencode` dispatch in `install_tool()`.

### 2. stage_tool_artifacts.py — staging validation + version resolution

- Added `codex-code-mode-host` validation in `_stage_codex()` using
  `_tar_contains_binary()`.
- Added `"OPENCODE_VER": _resolve_npm_version(...)` for `opencode-ai` package
  in `_resolve_versions()`.

### 3. Dockerfile — build args + version detection

- Added `ARG INSTALL_OPENCODE="true"` and `ARG OPENCODE_VERSION="latest"`.
- Added `opencode` version detection block in the manifest generation RUN
  command (same pattern as other npm AI tools).

### 4. docker-bake.hcl — bake variables

- Added `variable "OPENCODE_VERSION" { default = "latest" }`.
- Added `variable "INSTALL_OPENCODE" { default = "true" }`.
- Added `OPENCODE_VERSION` and `INSTALL_OPENCODE` passthrough in `base` target
  args.

### 5. ai-cli-tools.list

- Added `opencode|root` entry.

### 6. manifest_sections.py

- Added `"opencode"` to `AI_CLI_TOOL_NAMES`.
- Added `"opencode": "https://github.com/opencode-ai/opencode"` to `PROJECT_HOMES`.

### 7. cmru.build.toml

- Added `OPENCODE_VERSION` and `INSTALL_OPENCODE` to both
  `[steps.build-images]` and `[steps.push-images]` bake_set_vars.
- Updated OCI description to list `opencode` in the tool highlights.

### 8. Documentation

- **USAGE.md**: Added `.opencode` state directory path.
- **templates/README.md**: Added `opencode` to AI-CLI agent list and state
  persistence table.
- **templates/initialize_container_environment.py**: Added `.opencode` to
  `FALLBACK` mount directories.

### 9. Tests

Created `scripts/test_install_ai_cli_tools.py` with 20 tests:

| Area | Tests | What it covers |
|---|---|---|
| `archive_missing_binaries` | 4 | both present, one missing, all missing, non-tar fallback |
| `install_binaries_from_archive` | 2 | multi-binary install success, missing binary error |
| `install_codex` | 5 | disabled skip, missing archive, checksum mismatch, missing companion host, two-binary install |
| `install_opencode` | 3 | disabled skip, command construction, version fallback |
| `parse_tool_entries` | 2 | opencode entry parsing, comments/blank lines |
| `is_enabled` / `env_value` | 4 | default true, explicit false, first-name-wins, fallback |

### 10. Validation

```
- py_compile: 4/4 Python files OK
- pytest: 20/20 tests passed
- cmru.build.toml: TOML syntax OK
- docker-bake.hcl: HCL syntax OK (docker buildx bake --print all succeeds)
```

## Decisions

1. **New `install_binaries_from_archive()` helper**: Avoids extracting the same
   archive twice for the two Codex binaries. Single extraction, multiple lookups.
2. **`archive_missing_binaries()` uses `tarfile` directly**: Non-extracting
   validation is faster than extracting-then-checking for pre-flight checks.
   Falls back to empty-list if archive is not a valid tar (callers extract later).
3. **OpenCode default toggle = true**: Matches all other AI CLI tools.
4. **OpenCode state directory pattern**: Follows the existing `.reasonix` /
   `.openclaw` conventions for mount persistence.

## Blockers

None encountered during implementation.

## Files Modified

| File | Change |
|---|---|
| `scripts/install_ai_cli_tools.py` | companion-host + OpenCode |
| `scripts/stage_tool_artifacts.py` | companion-host validation + OPENCODE_VER |
| `scripts/manifest_sections.py` | opencode in AI_CLI_TOOL_NAMES + PROJECT_HOMES |
| `Dockerfile` | INSTALL_OPENCODE, OPENCODE_VERSION, version detection |
| `docker-bake.hcl` | OPENCODE_VERSION, INSTALL_OPENCODE vars + args |
| `ai-cli-tools.list` | opencode\|root |
| `cmru.build.toml` | bake vars + description |
| `USAGE.md` | opencode state directory |
| `templates/README.md` | opencode in agent list + mounts |
| `templates/initialize_container_environment.py` | .opencode fallback dir |

## Files Created

| File | Description |
|---|---|
| `scripts/test_install_ai_cli_tools.py` | 20 focused regression tests |
| `handoff/MDT-P01-LOG.md` | This log |
| `handoff/MDT-P01-REPORT.md` | Summary report |
