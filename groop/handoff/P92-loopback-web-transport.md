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
operator web foundation: Groop serves same-origin API/static assets on loopback,
authenticates with a random per-start capability token, and exposes only bounded
P88 projections. Establish the reproducible PWMCP test path before React pages.

## Required contracts

1. Default bind is loopback. Generate a cryptographically random per-start token,
   print it only to the controlling terminal or an owner-only token file, and
   accept it in an authorization header/cookie that is never a URL parameter.
   Constant-time comparison, no token logging, no `X-Groop-Principal` trust.
2. Serve API and built assets from one origin with a restrictive CSP and
   security headers. CORS is absent. Mutation routes are absent. SSH and client-
   side port forwarding remain system/operator concerns, not Groop features.
3. Routes expose health/status and P88 query results, including projected
   current/raw/summary, coverage/gaps/reset/source/freshness and P81 typed
   redaction markers. Strict request/response limits and timeouts apply before
   serialization.
4. Static assets are package data built during release; Node is never an end-
   user runtime dependency. Pin the future React toolchain/lockfile contract but
   do not build the product pages here.
5. Add a test-only non-loopback fixture binding solely to the CIU consumer
   network, still token-protected and with every production security check.
   Reuse `groop/pwmcp`, image 1.61.0-r6; PWMCP must reach the gateway by container
   DNS. Never publish PWMCP or Groop test ports to the host.

## Acceptance oracles

Drive real daemon/client/gateway sockets. Prove missing/wrong tokens fail, token
does not occur in logs/body/URL, spoofed principal headers do nothing, CORS and
mutation verbs are absent, same-origin assets work, projections stay bounded,
redaction cannot be disarmed unnoticed, and stale/oversized errors are typed.
From the running Groop-owned PWMCP instance, load a token-protected fixture page
and assert health with no console errors. Record exact CIU commands and topology.

## Out of scope

SSH setup, TLS/public exposure, multi-user accounts/RBAC, browser mutations,
live push and the React product routes (P73/P77).

## Gates

Focused security/gateway/query tests, zero-skip full suite, compile checks,
`git diff --check`, PWMCP browser evidence and built-wheel package-data checks.
Write P92-LOG.md, P92-REPORT.md and `docs/WEB-UI.md`.
