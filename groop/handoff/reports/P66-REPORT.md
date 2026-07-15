# P66 REPORT — Daemon Client Versioned Health Method

## What was built

Extended `DaemonClient` (`groop/src/groop/daemon/client.py`) with the one
typed versioned-envelope op P63 left out: `health`. A frontend that
negotiated the versioned envelope via `request_hello()` can now read
component health through the same envelope + `_request_envelope` transport,
without falling back to the legacy, non-versioned `request_health()` socket
path.

### New client surface (additive, no legacy changes)

- **`DaemonVersionedHealthResult`** (frozen dataclass) — wraps a decoded
  `snapshot: HealthSnapshot` plus a computed `overall_ok: bool` property.
  Deliberately a distinct type from the legacy `HealthSnapshot` (per the
  handoff's "do not collide" contract).
- **`request_health_versioned()`** — calls
  `self._request_envelope("health")` (same private transport as
  hello/current/history/entity; no second transport is opened), then decodes
  the envelope's `result` dict through the *existing*
  `DaemonClient._parse_health_payload` — the identical per-field parser the
  legacy `request_health()` path already uses. No new decode implementation
  was written.

### `overall_ok` semantics

`_op_health()` (P52, `api.py`) returns `build_health_response(...)` verbatim
— there is no "overall status" field anywhere in the wire protocol, the CLI,
or the MCP frontend. A repo-wide grep found no existing aggregate-health
concept tied to component health. The closest in-repo precedent is
`groop/src/groop/daemon/status.py`'s `DaemonStatusReport.ok`, a *computed*
(not stored) property on an otherwise-frozen dataclass, rendered as
`"Overall: OK"` / `"Overall: DEGRADED"`.

`DaemonVersionedHealthResult.overall_ok` follows that same pattern:

```python
@property
def overall_ok(self) -> bool:
    return all(
        component.state not in (ComponentState.DEGRADED, ComponentState.FAILED)
        for component in self.snapshot.snapshots
    )
```

The `{DEGRADED, FAILED}` set is not an invented ranking — it is exactly
`ComponentHealthRegistry.set_state`'s own existing `failed_attempt`
classification (`failed_attempt=state in {ComponentState.DEGRADED,
ComponentState.FAILED}`). DISABLED (intentionally off by default),
STARTING, STOPPING, and STOPPED are all treated as "not currently broken",
matching how the registry itself never marks those as failed attempts. This
does not touch `component_health.py` or change any P47 semantics — it only
reads states P47 already decodes, and is computed on every access so it can
never drift from `.snapshot`.

### Updated documentation

- `groop/docs/DAEMON.md` — the "Typed Versioned Client (P63)" section is
  retitled "Typed Versioned Client (P63/P66)"; added the new method's row
  to the result-type table, the new import name, and a paragraph explaining
  the decode reuse and `overall_ok` semantics.

### Updated exports

- `groop/src/groop/daemon/__init__.py` — `DaemonVersionedHealthResult` added
  to the `groop.daemon.client` import block and `__all__`.

## Deviations from handoff

None. All named contracts are met:

| Contract | Status |
|---|---|
| `request_health_versioned()` returns a frozen dataclass, does not collide with legacy `HealthSnapshot` | Done (`DaemonVersionedHealthResult`) |
| Reuses `_request_envelope("health")`, no second transport | Done |
| Decodes into existing `component_health` models where the shape fits, does not re-derive | Done (`_parse_health_payload` reused verbatim) |
| Defensive validation: unknown component names / missing fields raise `DaemonProtocolError` | Done (inherited from `_parse_health_payload`; covered by tests 9-10) |
| Legacy `request_health` and every other legacy/P63 method keep exact current behavior/signatures | Done — zero lines changed in any existing method; full suite green |
| Exported through `groop/src/groop/daemon/__init__.py` | Done |
| Tests drive real `DaemonApi` health envelope over real AF_UNIX transport, not hand-mocked socket | Done (same harness pattern as `test_daemon_client_p63.py`) |
| Coverage: happy path (known component state + overall status), ok:false -> `DaemonResponseError` with `.code`, malformed/oversized/non-object -> `DaemonProtocolError`, id echo | Done (tests 1-3, 4, 5-7, 8) |
| Does not weaken existing P63/P52/health tests | Done — `test_daemon_client_p63.py` and `test_daemon_component_health.py` unmodified and still pass |
| `docs/DAEMON.md` client section updated | Done |

No `Escalate-if` condition fired: the `_op_health` result shape (identical
to the legacy single-line health payload minus the envelope wrapper) is
expressible as a frozen typed result without touching `api.py`, and
implementing it required zero changes to any legacy method.

## Test evidence

Environment: fresh venv built exactly per the handoff's Environment/gates
section, from the worktree root:

```bash
cd /workspaces/vbpub/.worktrees/groop-p66-daemon-client-versioned-health
python3 -m venv .venv
.venv/bin/pip install -e './groop[dev]'
```

Linux x86_64 (container), Python 3.14 (venv-resolved via `python3 -m venv`).

```bash
PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_daemon_client_p66.py -q -W error -p no:schemathesis
# 14 passed in 6.24s

PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_daemon_client_p66.py groop/tests/test_daemon_client_p63.py groop/tests/test_daemon_component_health.py -q -W error -p no:schemathesis
# 83 passed in 18.34s

timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests -q -W error -p no:schemathesis
# 1465 passed in 183.22s (0:03:03)
```

Zero skips on the full suite — the `[dev]` extra (`zstandard` + `mcp`) was
installed, so the P84 gate is satisfied and this is a real full-suite pass,
not a "green with skips" result.

```bash
.venv/bin/python -m py_compile groop/src/groop/daemon/client.py groop/src/groop/daemon/__init__.py groop/tests/test_daemon_client_p66.py
# clean, no output

git diff --check
# clean, no output (exit 0)
```

## Adversarial test coverage

All 14 tests in `groop/tests/test_daemon_client_p66.py` drive the real
`DaemonApi` over a real `AF_UNIX` socket via `serve_versioned_unix_socket`
(the same harness pattern as `test_daemon_client_p63.py`); only the
protocol-level malformed-response tests (5-10) use a raw socket server that
emits a fixed/templated line, matching P63's own pattern for those cases.

| # | Test | Assertion |
|---|---|---|
| 1 | `test_request_health_versioned_all_healthy` | Real registry (collector HEALTHY, others DISABLED) decodes correctly; `overall_ok is True` |
| 2 | `test_request_health_versioned_degraded_component` | DEGRADED component decodes with its `ComponentError` (message + error_code) intact; `overall_ok is False` |
| 3 | `test_request_health_versioned_failed_component` | FAILED component decodes correctly; `overall_ok is False` |
| 4 | `test_request_health_versioned_unavailable_carries_code` | `DaemonApi(health_registry=None)` -> `DaemonResponseError` with `.code == "unavailable"` |
| 5 | `test_request_health_versioned_malformed_json_raises_protocol_error` | Non-JSON line -> `DaemonProtocolError` "malformed JSON" |
| 6 | `test_request_health_versioned_oversized_raises_protocol_error` | Response > `DEFAULT_MAX_RESPONSE_BYTES` -> `DaemonProtocolError` "oversized" |
| 7 | `test_request_health_versioned_non_object_raises_protocol_error` | JSON array response -> `DaemonProtocolError` "non-object" |
| 8 | `test_request_health_versioned_id_echo_mismatch_raises_protocol_error` | Server echoes wrong `id` -> `DaemonProtocolError` "returned id" |
| 9 | `test_request_health_versioned_invalid_component_state_raises_protocol_error` | `result.components[0].state = "future-state"` -> `DaemonProtocolError` "incompatible health-v1" |
| 10 | `test_request_health_versioned_incompatible_schema_version_raises_protocol_error` | `result.schema_version = 999` -> `DaemonProtocolError` "incompatible health-v1" |
| 11 | `test_request_health_versioned_matches_legacy_request_health` | Versioned and legacy methods decode the same live registry to state-for-state, detail-for-detail, error-for-error identical component data |
| 12 | `test_legacy_request_health_still_returns_health_snapshot` | `type(legacy) is HealthSnapshot`, not the new type — legacy path provably untouched |
| 13 | `test_request_health_versioned_result_type_does_not_collide_with_legacy` | `type(result) is DaemonVersionedHealthResult`, `not isinstance(result, HealthSnapshot)` |
| 14 | `test_request_health_versioned_connection_failure_raises_connect_error` | Nonexistent socket -> `DaemonConnectError` "cannot connect" |

## Known gaps / open items

None specific to this package's scope. Out of scope per the handoff and left
untouched: `api.py`/wire protocol/error codes/sensitivity enum, streaming/
subscribe health, the MCP frontend (`groop_health` tool still calls the
legacy `request_health()`; wiring it to the versioned method, if desired, is
a separate decision for a future package), and the P67 HTTP gateway (which
still does not expose a `/v1/health` route — that remains future work, not
part of P66's scope, which was `groop/src/groop/daemon/client.py` only).

## Proposed contract changes

None. Purely additive client-surface work within the existing `DaemonClient`
pattern, following the same shape P63 established.

## Files changed

```
groop/src/groop/daemon/client.py          — DaemonVersionedHealthResult, request_health_versioned()
groop/src/groop/daemon/__init__.py        — new export
groop/tests/test_daemon_client_p66.py     — 14 deterministic tests (new file)
groop/docs/DAEMON.md                      — Typed Versioned Client (P63/P66) section
groop/handoff/reports/P66-LOG.md          — session log
groop/handoff/reports/P66-REPORT.md       — this report
```
