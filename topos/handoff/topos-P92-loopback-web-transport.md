---
schema_version: 1
id: topos-P92-loopback-web-transport
project: topos
title: "Same-origin loopback web transport and browser fixture"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: [topos-P91-persistent-capped-history]
scope:
  touch: ["topos/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a request with a missing or wrong capability token is rejected"
    negative: "a request with a missing or wrong token is served"
    gate: topos-suite
  - id: O2
    observable: "the capability token never appears in logs, response bodies or any URL"
    negative: "the capability token appears in a log line, response body or URL"
    gate: topos-suite
  - id: O3
    observable: "a spoofed X-Topos-Principal header has no effect on authorization"
    negative: "a spoofed principal header grants elevated access or bypasses the token check"
    gate: topos-suite
  - id: O4
    observable: "responses carry no CORS headers and expose no mutation-verb routes"
    negative: "a CORS header is present, or a mutation-verb route is reachable"
    gate: topos-suite
  - id: O5
    observable: "same-origin static assets and API load correctly from the one loopback origin"
    negative: "assets or API calls require a second origin or fail same-origin loading"
    gate: topos-suite
  - id: O6
    observable: "P88 projections served through the gateway stay within P88's bounded current/raw/summary limits"
    negative: "the gateway returns an unbounded response or a full canonical frame"
    gate: topos-suite
  - id: O7
    observable: "P81 redaction markers on served data cannot be disarmed without the test suite catching it"
    negative: "redaction is silently disabled and the test suite does not catch it"
    gate: topos-suite
  - id: O8
    observable: "stale or oversized requests and responses produce typed errors rather than generic failures"
    negative: "a stale or oversized condition produces an untyped error or a silent truncation"
    gate: topos-suite
  - id: O9
    observable: "the CIU-network-only PWMCP test fixture loads the token-protected page from the Topos-owned PWMCP instance and asserts health with no browser console errors"
    negative: "the PWMCP fixture page fails to load, health is not asserted, or console errors are present"
    gate: topos-suite
gates: [topos-suite, py-compile]
escalate_if: ["a browser test cannot use a capability token without placing it in URL, history or logs", "projected routes cannot stay within P88 bounds"]
advances: []
---

# P92 - Same-origin loopback web transport and browser fixture

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P66, P81, P88, P91
> **Base:** main after dependencies
> **Session-hint:** fresh
> **Serialize-with:** P73, P77 (shared web routes/assets)
> **Escalate-if:** a browser test cannot use a capability token without placing it in URL/history/logs, or projected routes cannot stay within P88 bounds. Do not trust a caller-supplied principal header.

## Goal

Replace P67's provisional proxy-principal boundary with D-002/D-011's trusted-
operator web foundation: Topos serves same-origin API/static assets on loopback,
authenticates with a random per-start capability token, and exposes only bounded
P88 projections. Establish the reproducible PWMCP test path before React pages.

## Required contracts

1. Default bind is loopback. Generate a cryptographically random per-start token,
   print it only to the controlling terminal or an owner-only token file, and
   accept it in an authorization header/cookie that is never a URL parameter.
   Constant-time comparison, no token logging, no `X-Topos-Principal` trust.
2. Serve API and built assets from one origin with a restrictive CSP and
   security headers. CORS is absent. Mutation routes are absent. SSH and client-
   side port forwarding remain system/operator concerns, not Topos features.
3. Routes expose health/status and P88 query results, including projected
   current/raw/summary, coverage/gaps/reset/source/freshness and P81 typed
   redaction markers. Strict request/response limits and timeouts apply before
   serialization.
4. Static assets are package data built during release; Node is never an end-
   user runtime dependency. Pin the future React toolchain/lockfile contract but
   do not build the product pages here.
5. Add a test-only non-loopback fixture binding solely to the CIU consumer
   network, still token-protected and with every production security check.
   Reuse `topos/pwmcp`, image 1.61.0-r6; PWMCP must reach the gateway by container
   DNS. Never publish PWMCP or Topos test ports to the host.

## Acceptance oracles

Drive real daemon/client/gateway sockets. Prove missing/wrong tokens fail, token
does not occur in logs/body/URL, spoofed principal headers do nothing, CORS and
mutation verbs are absent, same-origin assets work, projections stay bounded,
redaction cannot be disarmed unnoticed, and stale/oversized errors are typed.
From the running Topos-owned PWMCP instance, load a token-protected fixture page
and assert health with no console errors. Record exact CIU commands and topology.

## Out of scope

SSH setup, TLS/public exposure, multi-user accounts/RBAC, browser mutations,
live push and the React product routes (P73/P77).

## Gates

Focused security/gateway/query tests, zero-skip full suite, compile checks,
`git diff --check`, PWMCP browser evidence and built-wheel package-data checks.
Write P92-LOG.md, P92-REPORT.md and `docs/WEB-UI.md`.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/topos-p92-loopback-web-transport`
  at `.worktrees/topos-p92-loopback-web-transport` (repo-root-relative, per
  `worktree_root` in `topos/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/topos-p92-loopback-web-transport`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.
