# P58 LOG — Daemon MCP Frontend (v4 respin)

## Scope and prior-review intake

Read the P58 handoff in full, P52/P63/P57 contracts, daemon client/API,
registry, packaging pattern, and the available prior rejection record
`handoff/reports/P58-REVIEW-v3.md`. The requested unversioned
`P58-REVIEW.md` and `P58-REPORT.md` are not present on this base; the v3
review and the prior report recovered from commit `6314f60` supplied the
required failure history.

The respin specifically closes all v3 blockers:

- B1: `_ok()` serializes every successful payload and returns typed
  `over-limit` when it exceeds the enforced 4 MiB cap. An MCP-client test
  drives an oversized entity response through this branch.
- B2: registered tool descriptions and CONTRACTS state the implemented limits:
  overview 1..50, history 1..100, entity 128 metrics/64 findings, health 16
  components, all with the 4 MiB aggregate cap. History now exposes `limit`;
  the textual boundary test now also structurally guards the MCP import
  boundary.
- B3: deleted the copied sensitivity classifier. P52's
  `daemon.api.metric_sensitivity()` is the fallback, while validated
  `metrics_meta` is preferred when the daemon returned it.
- B4: both entity and history selectors use P57's
  `resolve_container_key()`; no MCP-local docker prefix matching exists.
- B5: the test suite creates the real registered FastMCP server and calls it
  through the SDK's in-memory `ClientSession`. Discovery, each happy path,
  invalid arguments, output cap, leak behavior, and daemon loss are observable
  MCP calls.

## Implementation decisions

- The optional `mcp>=1.28.0` dependency is imported only inside
  `McpServer.run()`; CLI dispatch imports the frontend only for
  `groop mcp serve`.
- Startup probes P63's `request_hello()`. The frontend has no socket,
  daemon-envelope, or raw JSON code.
- Values include sensitivity in overview/entity/history results. Redaction is
  applied after overview ranking, so redacted values do not change rank.
- The client opens per-request connections, so no persistent frontend socket
  exists to close; stdio EOF returns from FastMCP normally and the injectable
  signal seam turns the first signal into a clean interrupt.

## Environment note

Installed MCP SDK for deterministic local testing: `mcp 1.28.1` on Python
3.14.6. The project requirement remains the compatible minimum
`mcp>=1.28.0`.
