# P48 - Bounded Journald Inspection Snapshot

## Goal

Extend reviewed P45 content inspection with one bounded, non-following journald
snapshot for an allowlisted systemd unit.

## Dependency And Workflow

- Starts only after reviewed P45 is merged.
- Branch: `feat/groop-p48-inspect-files-journal-snapshot`
- Worktree: `.worktrees/-groop-p48-inspect-files-journal-snapshot`
- Touch only `groop/**`; write P48-LOG.md/P48-REPORT.md; commit, do not merge.

## Requirements

- Support `systemd-journal` through the same `--inspect-files`, `--admin`, root,
  result, rendering, and audit/sensitivity posture as P45.
- Validate a real systemd unit name and reject aliases that parse as options,
  paths, control text, globs, or multiple units.
- Invoke only a fixed absolute `journalctl` argv, `shell=False`, with
  `--unit`, `--no-pager`, a deterministic output mode, and bounded line count.
  No follow mode. Use an injected runner for tests.
- Bound timeout, bytes, lines, stdout/stderr, and errors. A timeout/nonzero exit
  is typed unavailable/error output and never falls back to arbitrary reads.
- Add CLI, runner, gate, target, output-bound, timeout, and structural no-shell/
  no-mutation tests; no live journal dependency in the normal suite.
- Update inspect/operations/readiness/status/measurements docs honestly.

## Out Of Scope

- `--follow`, journal mutation/vacuum, arbitrary journalctl arguments, volume or
  overlay trees, daemon transport, and live-host content certification.

