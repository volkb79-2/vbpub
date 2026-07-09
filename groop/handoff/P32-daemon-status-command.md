# P32 - Daemon Status Command

## Goal

Add a read-only `groop daemon status` command that gives non-root users and
operators one concise answer to: "is the daemon deployment usable from this
account, and is it speaking the expected groop frame protocol?"

This continues the P16/P20/P22/P25/P30/P31 daemon usability stream. Keep the
scope narrow and do not implement service installation, systemd mutation, daemon
supervision, or new daemon protocol verbs.

## Workflow

Follow `groop/README.md` "Workflow protocol" exactly.

- Branch: `feat/groop-p32-daemon-status`
- Worktree: `.worktrees/-groop-p32-daemon-status`
- Branch from local `main`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P32-LOG.md` updated while working
- Finish with `groop/handoff/reports/P32-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `groop/README.md`
- `groop/CONTRACTS.md`
- `groop/docs/DAEMON.md`
- `groop/src/groop/cli.py`
- `groop/src/groop/daemon/client.py`
- `groop/src/groop/daemon/deploy.py`
- `groop/tests/test_daemon_client.py`
- `groop/tests/test_daemon_deploy.py`
- `groop/tests/test_attach_cli.py`

## Functional Requirements

Add:

```bash
groop daemon status [--socket PATH] [--group NAME] [--json] [--pretty-json]
```

Behavior:

- Default `--socket` is `/run/groop/groop.sock`.
- Default `--group` is `groop`.
- The command is read-only:
  - may inspect filesystem metadata and group membership;
  - may attempt a Unix-socket connection;
  - may request one current frame through the existing P16 client protocol;
  - must not run systemd, mutate files, change ownership/modes, or execute host commands.
- It should combine:
  - existing daemon deployment preflight checks;
  - an existing `current` frame protocol check.
- JSON output should be deterministic and scriptable. Include at least:
  - `ok` boolean;
  - `socket` path and group;
  - `preflight` object using existing preflight JSON shape or a clearly nested derivative;
  - `protocol` object with status, message/error, and when successful: schema version, frame timestamp, entity count.
- Text output should be concise and operator-friendly:
  - show socket path;
  - show preflight usable yes/no;
  - show protocol current-frame usable yes/no;
  - include P31-style guidance on daemon client errors.
- Exit codes:
  - `0` when preflight is usable and current-frame protocol check succeeds;
  - `1` when preflight or protocol check fails in a normal deployment/status way;
  - `2` for argument/usage or unexpected validation errors.

Use existing helper functions where practical. If a shared daemon-status helper
keeps `cli.py` cleaner, add it under `groop/src/groop/daemon/`.

## Tests

Add focused tests covering:

- status JSON success against a fixture daemon socket;
- status text success against a fixture daemon socket;
- missing default/custom socket failure includes actionable guidance and exits `1`;
- protocol error failure includes compatible-daemon/log guidance and exits `1`;
- `--pretty-json` emits parseable indented JSON;
- helper purity/no mutation expectations if you add a helper.

Prefer the existing fixture daemon helpers in daemon tests. Avoid sleeps where a
server thread can be started deterministically.

## Documentation

Update:

- `groop/docs/DAEMON.md` with `daemon status` usage and troubleshooting role.
- `groop/docs/OPERATIONS.md` with the normal non-root daemon check workflow.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- No service installation or `systemctl` execution.
- No long-running daemon supervisor.
- No socket permission changes.
- No new daemon protocol verbs.
- No Textual UI changes.
