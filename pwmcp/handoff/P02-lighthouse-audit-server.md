# pwmcp P02 - Lighthouse Audit MCP Server

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-max
> **Depends-on:** P01 (reviewed)
> **Base:** main after P01 merge
> **Session-hint:** resume P01 implementer session (same container/supervisord area)
> **Serialize-with:** P03 (shared files: Dockerfile, supervisord.conf)
> **Escalate-if:** a named contract cannot be met as specified; Lighthouse cannot run against the hardened container profile without cap changes

## Goal

Add one-shot Lighthouse audits (performance / accessibility / SEO /
best-practices category scores plus top opportunities) as MCP tools on
container port **8933**, so AI agents can ask "is `http://webapp-ui/` fast
and accessible, and what are the top fixes" and get bounded, structured JSON
— a complement to P01's interactive DevTools tracing and often the better
first tool for that question class.

## Access model (answering "how is it reached inside pwmcp")

Same shape as P01: a Node MCP server speaking stdio, fronted by the SAME
pinned stdio→streamable-HTTP proxy package P01 selects, serving
`http://pwmcp:8933/mcp` on the shared Docker network (internal mode, no
auth — network boundary is the trust line, identical to 8931/8932).
Lighthouse itself runs in-process per audit: the server calls Lighthouse's
Node API, which launches a fresh headless Chromium via `chrome-launcher`
using the image's binary (`CHROME_PATH` from `/etc/pwmcp-chromium-path.txt`)
and tears it down when the audit ends — per-audit browser, zero idle cost,
no interaction with the 8931/8932 servers' sessions.

## Workflow

- Branch: `feat/pwmcp-p02-lighthouse-audit-server`
- Worktree: `git worktree add -b feat/pwmcp-p02-lighthouse-audit-server
  .worktrees/-pwmcp-p02-lighthouse-audit-server main`
- Starts after reviewed P01 merges (reuses its proxy choice, allowed-hosts
  pattern, smoke script, and templated-pin plumbing).
- Touch only `pwmcp/**`; write `pwmcp/handoff/reports/P02-LOG.md` and
  `P02-REPORT.md`; commit the feature branch, do not merge.

## Context To Read First (bounded)

This handoff; P01's handoff + REPORT (proxy choice, pin mechanism);
`containers/pwmcp/{Dockerfile,supervisord.conf,entrypoint.sh}`;
`ciu.defaults.toml.j2`; `ciu.compose.yml.j2`; `scripts/smoke-endpoints.sh`;
`docs/{USAGE,SECURITY,ARCHITECTURE}.md`.

## Required Contracts

### Server selection

Evaluate existing maintained Lighthouse MCP npm packages first; adopt one
ONLY if it is pinnable, stdio-transport, allows executable-path/chrome-flags
injection, and returns bounded results. Otherwise write a minimal in-repo
server (~150 lines, vendored under `containers/pwmcp/lighthouse-mcp/`) using
the official `lighthouse` Node API — record the decision and rationale in
the LOG. Pin `lighthouse` (and the chosen server package if external)
exactly, via the P01 templated-ARG mechanism.

### Tools (closed v1 set)

1. `lighthouse_audit(url, categories?, form_factor?)` — categories is a
   closed subset enum of {performance, accessibility, seo, best-practices}
   (default: all four); `form_factor` ∈ {mobile, desktop} (default mobile,
   Lighthouse's own default). Returns: per-category score (0-100), the
   top-N opportunities/diagnostics (N ≤ 10) each with id, title, estimated
   savings where Lighthouse provides one, and the audited final URL (after
   redirects).
2. `lighthouse_metrics(url, form_factor?)` — just the core web vitals /
   timing metrics (LCP, CLS, TBT, FCP, SI, TTI) with values and scores; the
   cheap call for "did the page get slower".

### Response bounds (token budget — same posture as topos P58)

A raw Lighthouse LHR is hundreds of KB and must NEVER be returned. Cap every
tool result (items AND total bytes; state both caps in the tool
descriptions); reject unknown categories/form factors with typed errors, no
silent defaults for misspellings.

### URL validation

Accept `http://`/`https://` only — reject `file://`, `data:`, `chrome://`,
and non-URL strings with a typed error. Internal Docker-network hostnames
are explicitly allowed (auditing `http://webapp-ui/` is the point); document
in SECURITY.md that the server can reach anything the container's network
reaches, and that this is the existing pwmcp trust model, not a new surface.

### Failure safety

Audit failures (unreachable URL, Chromium launch failure, Lighthouse
runtime error, audit timeout) map to a closed set of typed tool errors with
safe messages — no raw stack traces, no container filesystem paths. Bound
audit wall-time (configurable, default ≤ 120 s) so an abandoned audit cannot
pin a Chromium instance indefinitely; the mechanism test kills/hangs a
target and asserts the Chromium process is gone afterwards.

### Container/template integration

Mirror P01 exactly: new supervisord `[program:lighthouse-mcp]`;
`PWMCP_LIGHTHOUSE_ALLOWED_HOSTS` host-header parity; `ciu.defaults.toml.j2`
`lighthouse_port = 8933` + `host_lighthouse_port`; third mapping in the
compose template's expose block; own Traefik router in external mode; no
hardening relaxation (UID 1000, cap_drop ALL, no-new-privileges); version
bump per the P01 templated mechanism.

## Required Validation

Extend `scripts/smoke-endpoints.sh` (from P01) with 8933: initialize
handshake (correct + forged Host), then one real `lighthouse_audit` against
a reachable in-network HTTP URL asserting all requested category scores are
present and numeric, and one rejection case (`file:///etc/passwd` → typed
error). Assert supervisord shows all four programs RUNNING and that killing
the lighthouse program leaves 3000/8931/8932 unaffected. Same evidence
rules as P01: `timeout`-wrap everything, never claim an unrun check, record
environment limitations separately.

## Documentation

`README.md` + `docs/USAGE.md` endpoint tables (now four endpoints), a short
"which browser tool when" paragraph (Playwright = drive/assert, DevTools =
trace/throttle, Lighthouse = score/audit), `docs/SECURITY.md`,
`docs/ARCHITECTURE.md` (four-program diagram).

## Out Of Scope

- Attaching Lighthouse to a shared persistent browser (P03's concern if it
  merges; per-audit launch is correct v1).
- Historical score storage/trending, CI budgets/assertions
  (lighthouse-ci), user-flow (multi-step) audits.
- Auth, non-HTTP schemes, consumer-repo changes.
