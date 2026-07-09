# P22 — Daemon deployment preflight and service templates

**Cut:** v1.5/v2 foundation. **Depends:** P16, P20. Branch:
`feat/groop-p22-daemon-deployment`. Follow `groop/README.md` workflow protocol.

## Goal

Make the read-only daemon path practically deployable for non-root users without
mutating the host during tests. This package should add preflight checks and
packaged deployment templates so an operator can set up a root-owned daemon with
a group-readable socket deliberately.

## Scope — in

1. Add a daemon deployment/preflight helper, preferably under
   `groop/src/groop/daemon/deploy.py`, that can inspect:
   - expected socket path, default `/run/groop/groop.sock`;
   - socket parent directory existence, owner/group/mode if present;
   - socket existence/type/mode if present;
   - current user group membership for the expected daemon group (`groop` by
     default);
   - whether the current process can connect to the socket if it exists;
   - whether obvious unsafe states exist, e.g. world-writable runtime directory.
2. Add a safe CLI surface:
   - `groop daemon preflight [--socket PATH] [--group NAME] [--json]`;
   - exit `0` when the state is usable, `1` for failed checks, `2` for CLI or
     unexpected inspection errors;
   - text output should be concise and actionable;
   - JSON output should be deterministic enough for tests.
3. Add packaged deployment templates under a project-owned path such as
   `groop/src/groop/assets/systemd/`:
   - `groop.service` for `groop daemon serve --socket /run/groop/groop.sock`;
   - `groop.tmpfiles` creating `/run/groop` with root-owned, group-readable
     permissions;
   - include comments that the operator must create the `groop` group and add
     approved users.
   If packaging support is needed, update `pyproject.toml` so these assets are
   included in wheels.
4. Add docs:
   - update `docs/DAEMON.md` with a deliberate deployment checklist;
   - update `docs/STATUS.md`, `docs/ROADMAP.md`, and `README.md` work package
     state after implementation.
5. Add tests:
   - fixture directories/sockets for good and bad preflight states;
   - JSON/text CLI behavior;
   - no host mutation, no systemd invocation, no root requirement.

## Scope — out

- Actually installing units, creating groups, modifying `/run`, calling
  `systemctl`, or writing outside test tmpdirs.
- Extra authentication beyond Unix socket permissions.
- Daemon protocol expansion or mutation APIs.
- BPF or DAMON daemon ownership changes.

## Design notes

- Keep this package read-only except tests writing their own tmpdirs.
- Use stdlib APIs (`stat`, `socket`, `grp`, `os`) rather than shelling out for
  preflight checks.
- Treat deployment templates as operator artifacts, not an installer.
- Make failures explain what to do next without pretending to fix the host.

## Acceptance

- `groop daemon preflight --socket TMP/groop.sock --json` is covered by tests.
- Preflight identifies usable socket permissions and obvious unsafe directory
  permissions from fixtures.
- Systemd/tmpfiles templates are present and included in package metadata if
  needed.
- Full tests pass.
- `py_compile` passes for new/changed Python files.
- No live/default behavior changes when daemon commands are not used.

## Resumability

Create and keep current:

- `groop/handoff/reports/P22-LOG.md`
- `groop/handoff/reports/P22-REPORT.md`

Use `groop/handoff/AGENT-LOG-TEMPLATE.md`. Record the actual worktree path,
branch, changed files, commands, validation output, decisions, blockers, and
known gaps.
