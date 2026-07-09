# P25 - Daemon deployment install plan

**Cut:** v1.5/v2 foundation. **Depends:** P22. Branch:
`feat/groop-p25-daemon-install-plan`. Worktree:
`.worktrees/-groop-p25-daemon-install-plan`.

## Goal

Make the root-daemon deployment path easier to execute deliberately by adding a
safe install-plan command. The command must not mutate the host; it should print
the exact operator steps and paths needed to install the packaged systemd and
tmpfiles templates for a root-owned, group-readable daemon socket.

## Required context

- Read `groop/README.md`, especially "Workflow protocol".
- Read `groop/docs/DAEMON.md`.
- Read `groop/handoff/P22-daemon-deployment-preflight.md` and
  `groop/handoff/reports/P22-REPORT.md`.
- Read existing daemon deployment code/tests:
  - `src/groop/daemon/deploy.py`
  - `src/groop/cli.py` daemon subcommands
  - `src/groop/assets/systemd/groop.service`
  - `src/groop/assets/systemd/groop.tmpfiles`
  - `tests/test_daemon_deploy.py`

## Scope

1. Add a deployment install-plan helper.
   - Suggested location: `src/groop/daemon/deploy.py`.
   - Inputs should include socket path, daemon group, service destination, and
     tmpfiles destination with safe defaults:
     - socket: `/run/groop/groop.sock`
     - group: `groop`
     - service dest: `/etc/systemd/system/groop.service`
     - tmpfiles dest: `/etc/tmpfiles.d/groop.conf`
   - The helper should return structured data: source asset names, destination
     paths, rendered commands/steps, and warnings.
2. Add CLI surface:
   - `groop daemon install-plan [--socket PATH] [--group NAME] [--service-dest PATH] [--tmpfiles-dest PATH] [--json]`
   - Exit `0` for successful rendering, `2` for CLI/rendering errors.
   - Text output should be concise and copy/pasteable but must clearly say it
     is a plan, not an installer.
   - JSON output should be deterministic for tests.
3. Update docs.
   - `docs/DAEMON.md` should tell operators to run `install-plan` before
     applying templates.
   - `README.md`, `docs/STATUS.md`, and `docs/ROADMAP.md` should reflect P25.
4. Add tests.
   - Helper JSON/text rendering is deterministic.
   - CLI text and JSON work.
   - No systemd/subprocess/chown/chmod/group mutation is invoked.
   - Template asset references are correct.

## Out of scope

- No actual installation, group creation, user modification, systemctl calls,
  tmpfiles application, chmod/chown, or writes outside test tmpdirs.
- No daemon protocol changes.
- No privilege escalation, sudo integration, or package-manager integration.
- No distro-specific service manager support beyond systemd template planning.

## Acceptance criteria

- `groop daemon install-plan --json` emits deterministic structured data.
- Text output includes ordered operator steps and the default destination paths.
- Tests prove the command does not mutate host state or invoke systemd.
- Full `groop/tests` passes and py_compile is clean for changed files.

## Handoff artifacts

- Keep `groop/handoff/reports/P25-LOG.md` current using
  `handoff/AGENT-LOG-TEMPLATE.md`.
- Write `groop/handoff/reports/P25-REPORT.md` with implementation summary,
  deviations, test evidence, known gaps, and proposed contract changes.
- Commit the feature branch before handoff.
