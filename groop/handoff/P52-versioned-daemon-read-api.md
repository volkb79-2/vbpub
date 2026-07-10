# P52 - Versioned Daemon Read API

## Goal

Turn the local frame transport into a stable, bounded read API suitable for the
attached TUI and a separate web-backend process.

## Dependency And Workflow

- Starts after reviewed P47 and P51 are merged.
- Branch: `feat/groop-p52-versioned-daemon-read-api`
- Worktree: `.worktrees/-groop-p52-versioned-daemon-read-api`
- Touch only `groop/**`; write P52-LOG.md/P52-REPORT.md; commit, do not merge.

## Requirements

- Add protocol version/capability negotiation while keeping existing current
  clients compatible or clearly rejected with upgrade guidance.
- Define typed request/response/error envelopes with request IDs, version,
  capabilities, sequence/timestamp, and strict byte/item/depth bounds.
- Expose read-only health, current frame, bounded history by sequence/time, and
  one entity detail derived from daemon-approved frame/model data. No arbitrary
  paths, registry keys, commands, or sysfs/procfs reads.
- Include registry-derived source/unit/semantic/sensitivity metadata required by
  a web consumer without duplicating registry prose.
- Add Unix peer-credential observation and authorization hooks. Read access may
  retain socket-group policy, but identity must be available for audit/rate
  limits; mutation remains unsupported.
- Add per-client request/rate/concurrency and response-size bounds plus
  slow-client timeouts. One client cannot block producer or other clients.
- Provide a typed Python adapter for a future HTTP process; do not embed HTTP or
  frontend code here.
- Add compatibility, malformed/fuzz, bounds, peer identity, concurrency, and
  sensitivity tests; update API/daemon/security/readiness docs.

## Out Of Scope

- HTTP/WebSocket transport, browser auth, TLS/CORS/CSRF, mutation APIs,
  persistent history, or frontend framework selection.
