# P21 — v2 admin action gating skeleton

**Cut:** v2 foundation. **Depends:** P13. Branch:
`feat/groop-p21-admin-action-gating`. Follow `groop/README.md` workflow
protocol.

## Goal

Create the non-mutating skeleton for future Docker/systemd admin actions:
disabled by default, explicit `--admin` opt-in, exact command preview, and audit
logging. This package must not execute Docker/systemd commands or change host
state.

## Scope — in

1. Add an action planning module, preferably under `groop/src/groop/actions/`,
   that can build immutable preview plans for a small catalog:
   - Docker container `restart`, `stop`, `start` by container id/name;
   - systemd unit `restart`, `stop`, `start` by unit name;
   - systemd `set-property` preview for cgroup memory knobs if the shape fits
     cleanly.
2. Add a safe CLI preview surface, for example:
   - `groop action preview --kind docker-restart --target NAME [--admin] [--json]`;
   - without `--admin`, return a clear disabled/gated result and exit `2`;
   - with `--admin`, print the exact command preview and write an audit record
     if `--audit-log PATH` is provided;
   - never call `subprocess.run`, Docker, systemctl, or shell.
3. Tighten TUI reserved action UX:
   - pressing `k` without admin mode should keep the current disabled message;
   - if a top-level `--admin` mode is added for the TUI, pressing `k` should
     still be preview-only and explicitly say execution is not implemented;
   - no mutation hotkey should execute anything.
4. Add audit logging for previews only:
   - JSONL records containing timestamp, user, action kind, target, command
     argv, mode (`preview`), and whether admin mode was enabled;
   - keep it append-only and test with tmpdirs.
5. Add tests:
   - no-admin preview is denied;
   - admin preview emits deterministic command argv;
   - audit record is written only on preview when requested;
   - subprocess/Docker/systemctl execution is not invoked;
   - TUI reserved key still does not mutate.
6. Update docs:
   - `docs/OPERATIONS.md` for current admin-preview limitations;
   - `docs/STATUS.md`, `docs/ROADMAP.md`, and `README.md` work package status
     after implementation.

## Scope — out

- Executing Docker/systemd commands.
- Policy engine, role-based auth, or daemon mutation APIs.
- `systemctl set-property` live writes.
- Confirmation modals for real mutations.
- Any root requirement.

## Design notes

- Treat this as a contract/safety skeleton, not an action implementation.
- Build command previews as argv lists, never shell strings.
- Keep all command construction allowlisted; reject unknown action kinds.
- Audit preview records are useful evidence, but they are not authorization.
- Do not extend the daemon protocol with mutation verbs.

## Acceptance

- `groop action preview --kind docker-restart --target example --admin --json`
  returns a preview containing an argv list and does not execute anything.
- The same command without `--admin` is rejected with a clear message.
- Focused tests cover preview, audit, and no-execution guarantees.
- Full tests pass.
- `py_compile` passes for new/changed Python files.
- Default live/TUI behavior is unchanged unless `--admin` is explicitly passed.

## Resumability

Create and keep current:

- `groop/handoff/reports/P21-LOG.md`
- `groop/handoff/reports/P21-REPORT.md`

Use `groop/handoff/AGENT-LOG-TEMPLATE.md`. Record the actual worktree path,
branch, changed files, commands, validation output, decisions, blockers, and
known gaps.
