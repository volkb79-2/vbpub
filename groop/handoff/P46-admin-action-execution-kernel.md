# P46 - Admin Action Execution Kernel

## Goal

Add the first production-safe execution path behind P21's preview model for
allowlisted Docker/systemd start, stop, and restart actions only.

## Workflow

- Branch: `feat/groop-p46-admin-action-execution-kernel`
- Worktree: `.worktrees/-groop-p46-admin-action-execution-kernel`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P46-LOG.md` current
- Finish with `groop/handoff/reports/P46-REPORT.md` and focused commits

## Requirements

- Add an execution module and `groop action execute` CLI path. Preserve
  `action preview` behavior.
- Execution requires `--admin`, root in production, and exact typed
  `--confirm EXECUTE`. Missing gates perform zero subprocess calls.
- Initially allow only Docker/systemd start, stop, and restart catalog kinds.
  Reject update, kill, raw process signals, set-property, unknown kinds, and
  any future catalog entry unless separately opted into the execution allowlist.
- Validate targets before preview and again before execution. Reject option-like
  targets, whitespace/control characters, path syntax, shell metacharacters,
  invalid container identifiers/names, and invalid systemd unit forms.
- Execute the exact previewed argv with `shell=False`, a clean/minimal
  environment, bounded timeout and captured output, and no PATH ambiguity for
  the allowlisted executable. Never accept arbitrary argv from the client.
- Return a typed result distinguishing success, nonzero exit, timeout, refusal,
  and runner failure. Bound/redact stdout and stderr in JSON/text rendering.
- Append an audit JSONL record for every attempted execution after gates pass,
  including identity, kind, target, argv, outcome, timestamps, and exit status;
  never include secrets. Make audit location injectable for fixtures and fail
  closed if durable audit cannot be written before execution.
- Add tests with injected runners/clocks/identity proving exact argv, no-shell,
  gate ordering, validation, audit failure closure, result bounds, and that no
  real Docker/systemd mutation occurs in tests.
- Update README, ROADMAP, STATUS, OPERATIONS, RELEASE-READINESS, and
  MEASUREMENTS. Keep the TUI hotkey disabled in this package.

## Acceptance

- Ungated, non-root, invalid, and unauditable requests execute nothing.
- A fully gated fixture request invokes exactly one allowlisted argv and records
  an audit result for success, failure, and timeout.
- Preview and execute use the same validated plan contract.
- Focused tests, full suite, CLI fixture smoke, and full-source `py_compile` pass.

## Out Of Scope

- Kill, update/pull/recreate, CIU/Compose orchestration, batch actions.
- `systemctl set-property` and memory governance.
- TUI bindings/modals and daemon mutation RPCs.
- Live destructive acceptance on this development host.

