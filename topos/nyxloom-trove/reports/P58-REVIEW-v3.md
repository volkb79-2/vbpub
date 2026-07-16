# P58 Frontier Review (pass #2) — v3 attempt — REJECTED, NOT MERGED

Reviewer: Opus 4.8 (frontier review + merge authority, controller-workflow-v2 §6).
Date: 2026-07-13. Branch: `feat/topos-p58-daemon-mcp-frontend-v3` (worktree
`.worktrees/topos-p58-daemon-mcp-frontend-v3`, base `e27ba90`).

## Verdict

**REJECTED — not merged.** This is P58's third attempt. It fixes two of the three
blockers from the v1 rejection: the missing-extra path now exits 2, and the server
correctly consumes the P63 typed `DaemonClient` with no raw socket/envelope path
(the architecture violation is genuinely resolved). But it **re-ships the third
blocker verbatim** (`MAX_RESPONSE_BYTES` declared, documented, and never enforced)
and adds a new class of defect the prior review did not have to name: **the
LLM-facing tool descriptions and CONTRACTS.md document capabilities the code does
not implement.**

For an MCP frontend, the tool descriptions *are* the API — they are the only
thing the calling model reads before choosing arguments. Shipping descriptions
that promise a docker-name selector, a 4 MiB response cap, and a 1000-point
history limit that do not exist is not a documentation nit; it is the product
being wrong.

The good news: the P63 client integration is clean, the error-mapping shape is
right, and the module is small. The re-work is bounded — this is not another
re-carve.

## Blockers (must all be fixed before re-review)

### B1 — `MAX_RESPONSE_BYTES` is still declared, documented, and never enforced

`topos/src/topos/mcp/server.py:45` sets `MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES`.
Verified by grep: **the name appears nowhere else in `topos/src/`.** `_ok()` is:

```python
def _ok(data: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "data": data}
```

No serialization-length check. Meanwhile the cap is asserted as fact in four
places: all four tool descriptions ("Response byte cap: 4 MiB"), the CONTRACTS.md
§11 bounds column, and P58-REPORT.md.

The REPORT's known-gap #4 states this openly ("Response byte cap is checked in
tests but not enforced server-side... a future version could add a
serialization-length check in `_ok()` if needed"). Writing the gap down does not
convert an unenforced bound into an enforced one — and it is the *same* gap the
v1 review rejected. The standing contract is explicit: "Bounds are enforced, then
proven... every bound gets a test that actually violates it and asserts the
observable outcome — verify the mechanism, not its constant."

`test_overview_response_size_under_cap` asserts a three-row fixture serializes to
under 4 MiB. It passes today and would still pass if the cap were deleted,
because there is no cap. That is the definition of a hollow bound test.

**Fix:** enforce in `_ok()` (serialize, measure, return a typed `over-limit` /
`oversized_response` error when over), and add a test that drives a genuinely
oversized payload through a tool and asserts the typed error.

### B2 — Tool descriptions and CONTRACTS.md promise behavior that does not exist

Three separate false claims reach a consumer:

1. **Docker selector.** `topos_entity`'s description says it "Accepts an exact
   EntityKey path ... **or a docker container name/prefix**", and CONTRACTS.md
   §11's parameter column repeats it. `_handle_entity()` passes `selector`
   straight to `request_entity()` — exact key only. REPORT deviation #1 admits
   "V1 uses exact EntityKey only." A model reading the description will pass
   container names and get `invalid-selector` for a documented capability.
2. **Response byte cap** — see B1.
3. **History limit.** `topos_history`'s description says "Limit max: 1000 data
   points." `_handle_history()` hardcodes `limit = 100` and exposes no limit
   parameter at all. The handoff requires "Every tool has an explicit item limit
   (validated, capped maximum — reject over-limit requests with a typed error)";
   `topos_history` has no caller-settable limit to validate.

Additionally, CONTRACTS.md §11 states: "the structural import-boundary test
(`test_textual_boundary.py`) is extended to cover the `mcp` package." The diff
does not touch `test_textual_boundary.py` (verified: `git diff --stat main...HEAD --
topos/tests/test_textual_boundary.py` is empty), and REPORT deviation #2 says the
static boundary test was deferred. CONTRACTS.md is the frozen shared-interface
document; it must not assert tests that do not exist.

### B3 — `_metric_sensitivity()` hand-rolls a mapping P52 already exports, and gets it wrong

`server.py` defines its own sensitivity classifier:

```python
def _metric_sensitivity(name: str) -> Sensitivity:
    """Mirrors ``topos.daemon.api.metric_sensitivity`` without importing api
    module-level internals at MCP startup."""
    sensitive_prefixes = ("pids_", "cgroup_")
    if any(name.startswith(p) for p in sensitive_prefixes):
        return Sensitivity.SENSITIVE
    return Sensitivity.OPERATIONAL
```

Three problems:

- **The stated justification is false.** `server.py:24` already does
  `from topos.daemon.api import DEFAULT_MAX_RESPONSE_BYTES, Sensitivity` at module
  level. The api module is imported regardless; there is nothing to avoid.
  `topos.daemon.api.metric_sensitivity(name)` is a public, exported function.
- **It never returns `PUBLIC`.** CONTRACTS.md §10 defines a three-level closed
  enum whose `public` tier is exactly the `host_*` banner facts.
- **It measurably diverges.** Measured against the canonical function over the
  live registry:

  ```
  46 of 113 metrics classified differently by the P58 hand-rolled map
    host_damon_cold_bytes    registry=public   p58=operational
    host_damon_hot_bytes     registry=public   p58=operational
    host_load1               registry=public   p58=operational
    ...
  ```

  This is a redaction path. `--redact-above public` is supposed to redact
  everything above public; with this map it also redacts the 46 public host
  metrics, because none of them can ever be classified public.

Pass #1 touched this code (it replaced an alphabetical string comparison with an
integer ordering) but never asked the prior question: *why does this mapping exist
at all?* Standing contract: "Shared behavior belongs in `src/topos/` helpers, not
in copied package-local parsers."

**Fix:** delete `_metric_sensitivity` and `_SENSITIVITY_LEVELS`; call
`topos.daemon.api.metric_sensitivity`, and order via the enum, not a private
string table. For `_handle_entity`, prefer the `metrics_meta` the daemon already
returns (P52 attaches sensitivity per metric) over any client-side lookup.

### B4 — `_handle_history` implements the third resolver the handoff forbids

The handoff: "name resolution reuses P57's resolver if merged, else exact
`EntityKey` only (note which in the REPORT; **no third resolver implementation
either way**)." REPORT deviation #1 claims exact-key-only. But `_handle_history`
does this:

```python
for _, frame in result.entries:
    for ek in frame.entities:
        if str(ek) == selector: resolved_key = str(ek); break
        ef = frame.entities[ek]
        if ef.entity.docker and ef.entity.docker.name:
            if ef.entity.docker.name == selector or ef.entity.docker.name.startswith(selector):
                resolved_key = str(ek); break
```

That is a hand-rolled docker name/prefix resolver — the third implementation,
with different semantics from P57's (first-match-wins over frame iteration order,
no ambiguity detection: two containers sharing a prefix silently resolve to
whichever the daemon happened to serialize first). It is also inconsistent with
`_handle_entity`, which has no resolution at all. So the two tools that take the
same `selector` parameter resolve it differently.

**Fix:** pick one and make both tools agree. Either exact-key-only in both (and
correct the descriptions), or reuse P57's resolver in both, with its ambiguity
error mapped to `invalid-selector`.

### B5 — The MCP layer is never exercised; the discovery test is hollow

The handoff's test contract is explicit: "Drive the real MCP server in-process
with an MCP client from the SDK ... tool discovery lists exactly the four tools ...
Assert observable MCP-level results, not internal call counts alone."

No test constructs an MCP client. All 27 tests call private `_handle_*` methods
directly. Everything FastMCP owns — tool registration, JSON-schema generation from
the type hints, argument coercion, error surfacing — is untested. And the
discovery test that was supposed to cover this is:

```python
def test_tool_discovery_lists_four_tools(mock_client: DaemonClient) -> None:
    server = McpServer(mock_client)
    expected = {"topos_health", "topos_overview", "topos_entity", "topos_history"}
    # ... comments explaining why it doesn't check ...
    assert server._client is mock_client
```

`expected` is constructed and never used. The assertion checks constructor
attribute assignment. **Delete all four `@mcp.tool` registrations and this test
still passes.** It is the exact hollow-test shape the standing contract names.

Related, and worse: `test_overview_rejects_bool_as_limit` was required by the
handoff to prove bool-as-int yields a *typed error*. The test instead asserts
`server._handle_overview("ram", True)` returns **`ok is True`**, with a comment
narrating the discovery that `bool` is an `int` subclass so `True` passes. The
test was written to match the code rather than the contract — "never weaken tests
to make new code pass." `isinstance(limit, bool)` must be rejected.

`test_signal_shutdown_via_seam` is likewise hollow: it calls `stop_event.set()`
and asserts `stop_event.is_set()`, which is a tautology about `threading.Event`,
not about the server.

## Non-blocking findings (fix while you are in there)

- **Daemon-absent-at-startup is not handled.** The handoff requires "daemon
  absent/unreachable at startup is exit-code-nonzero with a clear message."
  `run_server()` constructs a `DaemonClient` (which does not connect) and runs;
  a missing daemon surfaces only as per-tool errors later. A `request_hello()`
  probe at startup is the natural fix and also negotiates the protocol version.
- **Redaction silently corrupts overview ranking.** `_handle_overview` redacts
  *before* sorting, and the sort key maps non-numeric values to `0`
  (`r["value"] if isinstance(r["value"], (int, float)) else 0`). With
  `--redact-above`, redacted rows sink to the bottom in value order rather than
  being ranked. Rank first, then redact.
- **Raw exception text can still cross the boundary.** `_try_call`'s
  `DaemonClientError` and `DaemonResponseError` branches pass
  `str(exc).split(": ", 1)[-1]` into the tool result. The leak test only raises a
  bare `RuntimeError`, which lands in the generic `except Exception` branch that
  returns a fixed string — so the test passes while the two typed branches remain
  a leak path. Raise a `DaemonClientError` carrying a secret in the leak test and
  it will fail.
- `until_ts` is computed as `None` and threaded through unused.
- `make_test_signal_registration()` is annotated `-> Callable[[], threading.Event]`
  but returns a `(callable, event)` tuple.
- `_handle_*` use bare `assert isinstance(...)` for control flow; these vanish
  under `python -O`.
- `topos/CONTRACTS.md` loses its trailing newline (`\ No newline at end of file`).

## Pass #1 overlap (trial metric, controller-workflow-v2 §6)

| # | Pass-2 finding | flagged-by-pass-1 |
|---|---|---|
| B1 | `MAX_RESPONSE_BYTES` never enforced | **no** |
| B2 | Tool descriptions / CONTRACTS.md claim unimplemented behavior | **no** |
| B3 | Hand-rolled sensitivity map duplicates + diverges from `metric_sensitivity` | **no** (pass-1 edited this function's comparison logic without questioning its existence) |
| B4 | Third docker resolver in `_handle_history`; REPORT says exact-key-only | **no** |
| B5 | Hollow discovery test; no MCP-client-driven test; bool-as-int test asserts the opposite of the contract | **no** (pass-1 fixed a *different* hollow test — the signal one) |
| N1 | No daemon-absent-at-startup check | **no** |
| N2 | Redaction corrupts overview ranking | **no** |
| N3 | Leak path through typed error branches | **no** |

**0 of 8 substantive findings flagged by pass #1.** Pass #1 did do real
mechanical work here (7 dead-import/dead-assignment fixes, one hollow signal
test), which is consistent with the standing read: high overlap on mechanical
findings, zero on substantive ones.

Notably, pass #1 walked *right past* the REPORT's own known-gap #4 — a
self-declared unenforced bound that had already been rejected once — without
flagging it. Same-session self-review will not catch "I decided this was
acceptable"; that is precisely the correlated blind spot §1 describes.

## Re-dispatch guidance

Do **not** re-carve. The architecture is right this time; the defects are local.

- **Tier: escalate to `sonnet5-high`.** flash-max produced the v1 architecture
  violation; terra-med BLOCKED correctly (good); this v3 leg (terra-med) produced
  correct plumbing with documentation that overclaims and bounds that do not
  exist. The failure mode is now *specification fidelity*, not code generation.
- Keep the branch; the P63 client integration and error taxonomy are worth
  preserving.
- The re-dispatch prompt must state B1–B5 as the acceptance list and require the
  MCP-client-driven test the handoff already asked for.

Handoff updated with a v3 carve note: `topos/handoff/P58-daemon-mcp-frontend.md`.
