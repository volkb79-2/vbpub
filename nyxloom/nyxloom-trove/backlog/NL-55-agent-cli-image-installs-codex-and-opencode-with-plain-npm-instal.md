---
kind: backlog-entry
schema_version: 1
id: NL-55
title: "infra/agent-cli/Dockerfile installs codex and opencode with plain npm install and no verification, pins codex 0.145.0 and the 1.x opencode-ai 1.18.1 package"
status: open
type: "bugfix"
severity: "low"
component: "agent-cli-image"
provenance: "mdt-cockpit codex-image analysis 2026-11-08 (reports/codex-image-ANALYSIS.md action 9) and OpenCode 2 review 2026-11-08 (reports/opencode2-REVIEW.md N6)"
filed_date: "2026-11-08"
---

## Observed mechanism

`nyxloom/infra/agent-cli/Dockerfile:60-62` pins `ARG CODEX_VERSION=0.145.0` and `ARG OPENCODE_VERSION=1.18.1` and runs one
`npm install -g "@openai/codex@${CODEX_VERSION}" "reasonix@..." "opencode-ai@${OPENCODE_VERSION}"`. The README (`infra/agent-cli/README.md:97`) repeats
the pins (`codex 0.145.0`, `opencode-ai 1.18.1`). Nothing after the install runs the tools.

Two problems, both found while fixing the same pattern in the mdt-cockpit image:

1. **No install verification.** Codex ships as an npm launcher plus an optional-dependency platform package carrying the native binary and the
   code-mode companion. An install that misses the platform package (or lands the wrong target) still builds; the failure shows only when an
   agent first runs it. mdt-cockpit now checks the installed package version, the platform package, the executable files and runs the installed
   launcher's `--version`, requiring the pinned version in the output (`verify_codex_install`, mdt-cockpit `scripts/install_ai_cli_tools.py`).
2. **The OpenCode 1.x package.** OpenCode 2 is distributed as `@opencode/cli` with per-platform packages (`@opencode/cli-linux-<arch>[-musl]`);
   `opencode-ai` is the 1.x line (1.19.5 still receives releases; mdt-cockpit moved to `@opencode/cli`). The 1.x config and data layout differ from 2.x
   (`opencode.jsonc` with a `providers` schema, sessions in `opencode.db`). The pin 1.18.1 is also older than the current 1.x.

Not a runtime defect today: the pins keep behaviour constant, which is the stated intent of the Dockerfile comment (an agent CLI silently changing
major behaviour under a running factory is indistinguishable from a model regression).

## Expected

- After the `npm install`, a build step runs `codex --version` and `opencode --version` (each failing the build on a non-zero exit or empty output)
  and requires the pinned version in the output, in the same guarded form the mdt-cockpit image uses.
- The OpenCode pin is decided deliberately: stay on `opencode-ai` 1.x (documented as a held major, with the reason) or move to `@opencode/cli` 2.x with
  the platform-package install and the config notes; the README pin line and the `OPENCODE_VERSION` ARG change together.
- Codex 0.145.0 is re-checked against the current release (the mdt-cockpit image carries 0.167.0); keep the pin only with a recorded reason.

## Acceptance

- A build with a deliberately wrong `CODEX_VERSION` output (a shim that prints another version, or a missing platform package) fails at the new step.
- The README "Current pins" line equals the Dockerfile ARGs (a docs test or the existing image-check, if it covers the file).
- Do it when the image is next rebuilt; no separate release is needed.

## Dependencies and order

None. The mdt-cockpit verification code is the reference implementation (`MDT-079` / `MDT-080` track its remaining OpenCode gaps).
