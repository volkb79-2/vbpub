# P31 - Daemon Client Error Guidance

**Cut:** v1.5/v2 daemon usability. **Depends:** P30. Branch:
`feat/topos-p31-daemon-client-errors`. Follow `topos/README.md` workflow
protocol exactly.

## Goal

Make daemon client failures actionable for non-root users. After P30, users can
run `topos --attach --once --json` and `topos daemon current --json` against the
default socket. If that socket is missing, inaccessible, or speaking the wrong
protocol, the CLI should explain the next diagnostic step instead of only
printing the low-level socket/protocol error.

## Required Context

- `topos/README.md` workflow protocol.
- `topos/handoff/P30-daemon-default-client.md` and
  `topos/handoff/reports/P30-REPORT.md`.
- `topos/docs/DAEMON.md`, `topos/docs/STATUS.md`, `topos/docs/ROADMAP.md`.
- `topos/src/topos/cli.py`, `topos/src/topos/daemon/client.py`,
  `topos/src/topos/daemon/deploy.py`.
- `topos/tests/test_attach_cli.py`, `topos/tests/test_daemon_deploy.py`,
  `topos/tests/test_daemon_client.py`.
- `topos/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Add a small, shared formatter/helper for daemon client errors used by both:
   - attach mode (`topos --attach ...`);
   - `topos daemon current`.
2. Preserve the original daemon error text, then add concise guidance:
   - for the default socket `/run/topos/topos.sock`, suggest
     `topos daemon preflight` and, if deployment is not installed yet,
     `topos daemon install-plan`;
   - for custom sockets, suggest
     `topos daemon preflight --socket <path>`;
   - for protocol/response errors, suggest checking that the process at the
     socket is a compatible topos daemon and reviewing daemon logs.
3. Keep exit codes unchanged: daemon client errors still return `2`.
4. Add focused tests:
   - default-socket attach missing socket prints the original error and
     default preflight/install-plan guidance;
   - custom-socket `daemon current` missing socket suggests preflight with that
     exact custom socket;
   - protocol error guidance mentions compatible topos daemon/logs;
   - existing successful attach/current tests still pass;
   - no live collection fallback is introduced.
5. Update docs:
   - `README.md` P31 row should become Done after implementation;
   - `docs/DAEMON.md` should document troubleshooting commands;
   - `docs/STATUS.md` and `docs/ROADMAP.md` should note improved daemon client
     failure guidance.

## Scope - Out

- No daemon protocol changes.
- No retries, auto-start, service control, or install execution.
- No root operations or host mutation.
- No socket discovery beyond existing default/custom socket paths.
- No changes to `DaemonClient` wire behavior unless a tiny exception-type
  helper is clearly justified.

## Design Notes

- Prefer formatting at the CLI boundary so lower-level client exceptions remain
  reusable and protocol-focused.
- Keep messages short and stable enough to test exact substrings.
- Do not hide the original exception; operators still need the raw path/error.
- Avoid duplicating message construction between attach and `daemon current`.

## Acceptance

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_attach_cli.py topos/tests/test_daemon_deploy.py topos/tests/test_daemon_client.py -q
PYTHONPATH=topos/src python3 -m pytest topos/tests -q
PYTHONPATH=topos/src python3 -m py_compile topos/src/topos/cli.py topos/tests/test_attach_cli.py topos/tests/test_daemon_deploy.py
```

Also run a CLI smoke for a missing custom socket and record stderr.

## Handoff Requirements

- Keep `topos/handoff/reports/P31-LOG.md` current using
  `topos/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P31-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract changes.
- Commit the feature branch with a focused message.
