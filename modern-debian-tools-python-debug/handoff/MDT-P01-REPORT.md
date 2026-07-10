# MDT-P01: Codex companion host and OpenCode integration — Completion Report

## Contract summary

**Goal:** Make the MDT image install the complete runtime needed by the staged
Codex package and add OpenCode as a first-class, reproducibly pinned AI CLI tool.

**All required deliverables have been implemented and validated.**

---

## 1. Codex package completeness

| Requirement | Status | Implementation |
|---|---|---|
| Require both `codex` and `codex-code-mode-host` in staged archive | ✅ | `archive_missing_binaries()` checks archive contents pre-install; `_tar_contains_binary()` validates during staging |
| Extract once, install both to `/usr/local/bin` | ✅ | `install_binaries_from_archive()` extracts once, copies both |
| Clear failure if either binary absent | ✅ | `InstallerError` with descriptive message listing missing names |
| Preserve existing checksum verification | ✅ | SHA256 checksums verified before archive content check, unchanged |

## 2. OpenCode integration

| Requirement | Status | Implementation |
|---|---|---|
| Add `opencode\|root` to `ai-cli-tools.list` | ✅ | `opencode\|root` added |
| Add `INSTALL_OPENCODE` and `OPENCODE_VERSION` inputs | ✅ | Dockerfile ARGs + docker-bake.hcl variables |
| Resolve `OPENCODE_VER` from npm registry | ✅ | `_resolve_npm_version("opencode-ai")` in `stage_tool_artifacts.py` |
| Install `opencode-ai@${OPENCODE_VER}` with npm | ✅ | `install_opencode()` follows existing npm-tool pattern |
| Default toggle consistent with other AI CLI tools | ✅ | Default `true` like reasonix, openclaw, copilot |

## 3. Usability and documentation

| Requirement | Status | Implementation |
|---|---|---|
| OpenCode discoverable in README, USAGE, templates | ✅ | USAGE.md, templates/README.md, cmru.build.toml description updated |
| Host companion mentioned only where useful | ✅ | Not user-facing; no separate documentation section |
| Installed-tool manifest generation | ✅ | `manifest_sections.py` updated; version detection block in Dockerfile |
| Historical logs unchanged | ✅ | No `groop/**` or `package-manifests-versioned/**` files modified |

## 4. Tests and validation

| Test area | Count | Status |
|---|---|---|
| archive_missing_binaries | 4 | ✅ All pass |
| install_binaries_from_archive | 2 | ✅ All pass |
| install_codex (two-binary, companion-host, checksum, skip) | 5 | ✅ All pass |
| install_opencode (command construction, skip, version fallback) | 3 | ✅ All pass |
| parse_tool_entries, env helpers | 6 | ✅ All pass |
| py_compile on all edited Python | 4 files | ✅ All pass |
| TOML syntax on cmru.build.toml | 1 file | ✅ Pass |
| HCL syntax on docker-bake.hcl | 1 file | ✅ Pass |

**Total: 20 tests, all passing.**

## Files changed

**13 files modified, 3 files created** (excluding this report and LOG.md).

### Modified
- `scripts/install_ai_cli_tools.py` — companion-host extraction, OpenCode install
- `scripts/stage_tool_artifacts.py` — companion-host validation, OPENCODE_VER
- `scripts/manifest_sections.py` — opencode in AI_CLI_TOOL_NAMES, PROJECT_HOMES
- `Dockerfile` — ARGs for OpenCode version install toggle + version detection
- `docker-bake.hcl` — OPENCODE_VERSION + INSTALL_OPENCODE variables and target args
- `ai-cli-tools.list` — opencode|root entry
- `cmru.build.toml` — bake vars and OCI description
- `USAGE.md` — opencode state directory
- `templates/README.md` — opencode agent list + mount table
- `templates/initialize_container_environment.py` — .opencode fallback

### Created
- `scripts/test_install_ai_cli_tools.py` — 20 focused regression tests
- `handoff/MDT-P01-LOG.md` — implementation log
- `handoff/MDT-P01-REPORT.md` — this report

## Commit

One commit on `feat/mdt-ai-cli-packaging` containing all changes.
Worktree is clean.
