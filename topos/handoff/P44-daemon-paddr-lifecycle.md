# P44 - Daemon-Owned paddr Lifecycle

## Goal

Move the explicitly enabled, whole-host DAMON paddr session into the root daemon
lifecycle without changing the default: no paddr session starts unless the
operator sets `[damon] paddr_enabled = true`.

## Workflow

- Branch: `feat/topos-p44-daemon-paddr-lifecycle`
- Worktree: `.worktrees/-topos-p44-daemon-paddr-lifecycle`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P44-LOG.md` current
- Finish with `topos/handoff/reports/P44-REPORT.md` and focused commits

## Requirements

- Add `DamonConfig.paddr_enabled: bool = false`, parse and serialize it, and
  document the disabled default and existing interval settings.
- Add a small daemon lifecycle owner around the existing `damon/paddr.py` and
  `damon/control.py` sources of truth. Do not duplicate sysfs write lists.
- When enabled, daemon startup plans and starts exactly one topos-owned paddr
  session as root. Treat explicit configuration as operator authorization, but
  retain ownership markers and audit evidence.
- Be idempotent across an already-live topos-owned marker/session: adopt or
  report it rather than allocating a duplicate. Never adopt, stop, or overwrite
  a foreign session.
- On graceful daemon shutdown, stop only the session owned by this daemon run
  and restore any topos-managed DAMON conflict state using existing controls.
- Startup failure must be bounded and explicit. The read-only daemon must remain
  usable with paddr status unavailable; never hide the error as success.
- Keep fixture injection seams for DAMON root, state dir, root check, and clock;
  production defaults remain root-owned locations.
- Add focused config, lifecycle, ownership/recovery, foreign-session, failure,
  and daemon integration tests. No live DAMON mutation in the normal suite.
- Update README, ROADMAP, STATUS, OPERATIONS, DAEMON, RELEASE-READINESS, and
  MEASUREMENTS with honest fixture-vs-live claims.

## Acceptance

- Disabled/default daemon startup performs zero DAMON writes.
- Explicit enablement starts one owned paddr session and graceful shutdown
  stops only that run's owned session.
- Duplicate/restart and foreign-session cases are deterministic and safe.
- Focused tests, full suite, daemon smoke, and full-source `py_compile` pass.

## Out Of Scope

- Enabling paddr by default.
- Per-entity paddr attribution.
- Live-host overhead certification.
- Generic daemon mutation RPCs or BPF lifecycle work.

