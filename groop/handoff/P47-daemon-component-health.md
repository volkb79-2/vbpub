# P47 - Daemon Component Health

## Goal

Replace startup-print-only visibility for daemon-owned background components
with a typed, bounded health snapshot available through the local read-only
daemon protocol and CLI.

## Dependency And Workflow

- Starts only after reviewed P44 is merged; also builds on P42.
- Branch: `feat/groop-p47-daemon-component-health`
- Worktree: `.worktrees/-groop-p47-daemon-component-health`
- Touch only `groop/**`; write P47-LOG.md/P47-REPORT.md; commit, do not merge.

## Requirements

- Add a thread-safe component-health registry owned by daemon serve. Model at
  least collector, BPF snapshot bridge, and paddr lifecycle with stable states:
  disabled, starting, healthy, degraded, failed, stopping, stopped.
- Record bounded public detail, attempt/success timestamps, consecutive failure
  count, and last bounded error. Never expose tracebacks, environment, arbitrary
  paths, command output, or secrets.
- Wire P42 refresh and P44 lifecycle transitions into this registry without
  duplicating their implementation logic. Disabled defaults must be explicit.
- Add a read-only protocol request and `groop daemon health [--json]`; version
  or capability-gate it so older daemons fail with existing compatible-daemon
  guidance rather than corrupting current/stream behavior.
- Make snapshots deterministic and safe during concurrent updates/shutdown.
- Add unit, protocol, CLI, error-bound, concurrency, and daemon integration
  tests. Update daemon/operations/readiness/status/measurements docs.

## Out Of Scope

- Mutating daemon RPCs, remote/TCP API, metrics exporter, live BPF load/attach,
  or automatic restart loops beyond existing component behavior.
