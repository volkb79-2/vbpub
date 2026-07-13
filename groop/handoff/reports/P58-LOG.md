# P58 LOG - Daemon MCP Frontend (v4 respin)

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

## Self-review pass #1 - 2026-07-13

Reviewed the committed `HEAD^..HEAD` diff mechanically against the P58
handoff and the v3 rejection, then cross-checked the registered FastMCP tool
descriptions against the actual validators and `daemon/api.py`.

### Required v3 regression probes

- Response bytes: no recurrence. `MAX_RESPONSE_BYTES` is read by `_ok()`,
  which measures the same indented UTF-8 JSON representation FastMCP emits;
  all four successful handlers return through `_ok()`. The oversized entity
  test calls the registered tool through an MCP `ClientSession` and would fail
  if the byte check were removed.
- Description/contract accuracy: no recurrence. Discovery exposes 16 health
  components, 1..50 overview rows, 128 entity metrics/64 findings, and 1..100
  history points, each with the 4 MiB cap. The handlers enforce those same
  values. No description or contract contains the rejected 1000-point claim.
- Docker selectors/history limit: no recurrence. Entity and history happy
  paths resolve docker name/prefix selectors through P57's exported
  `resolve_container_key()`, and history exposes and validates its `limit`
  argument. No MCP-local prefix matcher exists.
- Sensitivity: no recurrence. There is no `_metric_sensitivity` copy. A full
  registry comparison produced `0 mismatches across 113 registry metrics`
  between the MCP fallback and `daemon.api.metric_sensitivity()`.

### Findings and fixes

- Fixed the LOG/REPORT title em dashes; both evidence files are now ASCII as
  required by the standing self-review checklist.
- Removed unused `EntityFrame`/`MetricValue` source imports and an unused
  `Any` test import.
- Added MCP-discovery assertions for all four descriptions and a 113-metric
  canonical-classifier regression test. These make the two exact v3 failure
  classes observable instead of relying on reviewer prose.
- No out-of-scope file: every implementation and self-review change is under
  `groop/**`. No raw socket/envelope parser is present in `groop.mcp`.
- No hollow required test found: deleting tool registration, `_ok()` byte
  enforcement, strict limit checks, P57 resolution, safe error mapping, or
  canonical sensitivity use breaks an observable assertion.

### Gate evidence (agent environment)

```text
PYTHONPATH=groop/src /usr/local/py-utils/venvs/pytest/bin/python -m pytest \
  groop/tests/test_mcp_server.py groop/tests/test_textual_boundary.py \
  groop/tests/test_packaging_metadata.py -q -W error
17 passed in 1.19s

timeout 900 env PYTHONPATH=groop/src \
  /usr/local/py-utils/venvs/pytest/bin/python -m pytest groop/tests -q -W error
1115 passed, 2 skipped in 138.58s

PYTHONPATH=groop/src python3 -m py_compile <all changed/new Python files>
# exit 0
git diff --check
# exit 0
```
