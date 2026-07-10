# MDT-P01: Codex companion host and OpenCode integration

## Goal

Make the MDT image install the complete runtime needed by the staged Codex
package and add OpenCode as a first-class, reproducibly pinned AI CLI tool.

## Baseline evidence

- The staged Codex 0.144.0 archive is the upstream verified
  `codex-package-x86_64-unknown-linux-musl.tar.gz`.
- That archive contains both `bin/codex` and `bin/codex-code-mode-host`.
- `scripts/install_ai_cli_tools.py::install_codex` currently extracts and
  installs only `codex`, causing code-mode command execution to fail in a
  newly built container.
- Installing the omitted host from that same archive restored command
  execution in the running container.
- The official OpenCode repository documents the npm package `opencode-ai`,
  exposing the `opencode` command. MDT already uses resolved, pinned npm
  versions for Reasonix, OpenClaw, and GitHub Copilot CLI.

## Required implementation

1. Codex package completeness
   - Require the staged Codex archive to contain both `codex` and
     `codex-code-mode-host` during staging.
   - Extract the verified archive once and install both binaries into
     `/usr/local/bin` with executable permissions.
   - Fail clearly if either required executable is absent.
   - Preserve the existing upstream checksum verification.

2. OpenCode as a first-class AI CLI
   - Add `opencode|root` to `ai-cli-tools.list`.
   - Add `INSTALL_OPENCODE` and `OPENCODE_VERSION` inputs consistently through
     Dockerfile/build-bake configuration.
   - Resolve `OPENCODE_VER` from the npm registry package `opencode-ai` during
     artifact/version staging, following the existing npm-tool convention.
   - Install exactly `opencode-ai@${OPENCODE_VER}` with npm and verify or expose
     the `opencode` command through normal image validation/manifest reporting.
   - Default the optional installation toggle consistently with the other AI
     CLI tools.

3. Usability and documentation
   - Update relevant README, USAGE, templates, customization guidance, and
     installed-tool manifest generation so OpenCode is discoverable and its
     configuration/state persistence expectations are correct.
   - Mention the Codex companion host only where operationally useful; do not
     present it as a user-facing CLI.
   - Keep generated historical logs and versioned release manifests unchanged.

4. Tests and validation
   - Add focused regression tests for the installer and staging/version wiring,
     using mocks/temp paths rather than installing packages or reaching the
     network.
   - Cover the missing-host failure and successful two-binary Codex install.
   - Cover OpenCode version resolution/dispatch/install command construction.
   - Run focused tests, relevant existing tests, `py_compile` on edited Python,
     and syntax/static checks appropriate to edited Docker/HCL/docs files.

## Constraints

- Work only inside `/workspaces/vbpub/.worktrees/-mdt-ai-cli-packaging` on
  branch `feat/mdt-ai-cli-packaging`.
- Do not edit the main checkout.
- Avoid unrelated cleanup and do not modify `groop/**`.
- Do not regenerate release logs, staged binary artifacts, or historical
  package manifests solely to reflect this source change.
- Use ASCII unless an edited file already requires otherwise.
- Prefer small helpers with explicit failure behavior over shell duplication.

## Deliverables

- Production implementation and focused regression tests.
- `modern-debian-tools-python-debug/handoff/MDT-P01-LOG.md` with commands,
  observed results, decisions, and blockers.
- `modern-debian-tools-python-debug/handoff/MDT-P01-REPORT.md` summarizing the
  completed contract, files, and validation evidence.
- One or more commits on `feat/mdt-ai-cli-packaging`; leave the worktree clean.
