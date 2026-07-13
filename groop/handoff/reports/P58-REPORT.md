# P58 REPORT — Daemon MCP Frontend (v4 respin)

## Delivered

`groop mcp serve` is a stdio-only, read-only MCP server in the optional
`groop[mcp]` extra (`mcp>=1.28.0`; tested with 1.28.1). It exposes exactly:

| Tool | Enforced bounds |
|---|---|
| `groop_health` | <=16 component summaries; 4 MiB aggregate response |
| `groop_overview(sort_by, limit)` | closed sort keys; integer limit 1..50; 4 MiB aggregate response |
| `groop_entity(selector)` | exact EntityKey or P57 docker name/prefix; <=128 metrics and <=64 findings; 4 MiB aggregate response |
| `groop_history(selector, metric, window, limit)` | exact EntityKey or P57 docker name/prefix; 1..100 points; `last:Ns` max seven days; 4 MiB aggregate response |

The server consumes only P63 `DaemonClient` methods. It contains no socket
transport, daemon-envelope, or raw daemon JSON implementation. It probes
`request_hello()` before serving, reports startup daemon absence nonzero,
and maps later adapter failures to safe closed tool errors:
`daemon-unavailable`, `invalid-selector`, `over-limit`, or `internal`.

Successful output is serialized in `_ok()` before return; an output larger
than `DEFAULT_MAX_RESPONSE_BYTES` (4 MiB) becomes `over-limit`. This is an
actual server-side cap, not a fixture-size assertion. Metric sensitivity uses
returned P52 metadata when available and P52's canonical
`metric_sensitivity()` fallback otherwise. `--redact-above LEVEL` preserves
each key and replaces only its value with `__redacted__`; overview ranks first
and redacts second.

## Review-history closure

- The v3 byte-cap blocker is fixed and exercised by an oversized MCP tool call.
- Tool descriptions/CONTRACTS now describe only implemented limits and
  selector behavior; history has its documented `limit` parameter.
- The local sensitivity classifier and history-local docker resolver are gone.
  Both selector tools use P57's canonical resolver.
- Tests use the MCP SDK's in-memory client session. Discovery asserts exactly
  four registered tools; handler-only tests are not used as the contract oracle.
- The MCP import boundary has both dynamic non-MCP-process coverage and a
  structural `test_textual_boundary.py` extension.

## Evidence

Run in this agent environment (Linux Debian 13, Python 3.14.6, MCP 1.28.1):

```text
PYTHONPATH=groop/src /usr/local/py-utils/bin/pytest \
  groop/tests/test_mcp_server.py groop/tests/test_textual_boundary.py \
  groop/tests/test_packaging_metadata.py -q -W error
15 passed in 1.52s
```

This includes MCP-client discovery, all tool happy paths, unknown/zero/negative/
over-cap/bool argument rejection, leak protection with a typed daemon error,
daemon loss in one live MCP session, maximal-overview size, oversize-response
rejection, redaction/rank ordering, startup probe, signal seam, missing-extra
exit 2, and import isolation.

```text
PYTHONPATH=groop/src python3 -m py_compile \
  groop/src/groop/cli.py groop/src/groop/mcp/__init__.py \
  groop/src/groop/mcp/server.py groop/tests/test_mcp_server.py \
  groop/tests/test_textual_boundary.py
# exit 0
git diff --check
# exit 0
```

A live daemon end-to-end session was not claimed; it remains controller-side
evidence as required.

```text
timeout 900 env PYTHONPATH=groop/src /usr/local/py-utils/bin/pytest groop/tests -q -W error
1113 passed, 2 skipped in 141.74s
```
