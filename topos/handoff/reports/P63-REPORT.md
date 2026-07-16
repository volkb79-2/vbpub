# P63 REPORT — Daemon Client Versioned Read Methods

## What was built

Extended `DaemonClient` (`topos/src/topos/daemon/client.py`) with typed,
validated client methods for the P52 versioned envelope operations.

### New client surface (additive, no legacy changes)

- **`DaemonResponseError.code`** — carries the P52 `ErrorCode` string so callers
  can branch on `not_found`/`invalid_type`/`out_of_range`/`unavailable` etc.
- **`_request_envelope(op, *, params)`** — private round-trip helper: `AF_UNIX`
  connect, `json.dumps(sorted, compact)` + `\n`, `SHUT_WR`, one response line
  via `makefile` bounded at `DEFAULT_MAX_RESPONSE_BYTES` (4 MB), envelope
  decode, **id echo assertion** (mismatch → `DaemonProtocolError`), `ok:false`
  → `DaemonResponseError` with `.code`.
- **`request_hello() -> DaemonHello`** — protocol versions, capabilities,
  identity, limits.
- **`request_current() -> DaemonCurrentResult`** — latest `(seq, Frame)` +
  validated `metrics_meta`.
- **`request_history(*, limit, cursor, since_ts, until_ts) -> DaemonHistoryResult`**
  — ordered `(seq, Frame)` entries + history bounds + `metrics_meta`.
  Fast-fail `ValueError` if cursor + time window both set.
- **`request_entity(key) -> DaemonEntityResult`** — one entity's frame + `metrics_meta`.

### Result dataclasses (all frozen)

- `DaemonCurrentResult` — `seq`, `frame: Frame`, `metrics_meta`
- `DaemonHistoryResult` — `entries: tuple[(seq, Frame)]`, `oldest_seq`,
  `latest_seq`, `next_cursor`, `gap`, `metrics_meta`; has `frames` property
- `DaemonEntityResult` — `seq`, `entity: EntityFrame`, `metrics_meta`
- `DaemonHello` — `protocol_versions`, `capabilities`, `identity`, `limits`

### Updated documentation

- `topos/docs/DAEMON.md` — added **Typed Versioned Client (P63)** section
  after the protocol compatibility table, documenting the new methods, result
  types, transport pattern, error codes, and metrics_meta validation.

### Updated exports

- `topos/src/topos/daemon/__init__.py` — exports all new result types.

## Deviations from handoff

None. All named contracts are met:

| Contract | Status |
|---|---|
| Transport: single private `_request_envelope`, id echo, SHUT_WR, `makefile` | Done |
| Exception hierarchy: `DaemonConnectError`/`DaemonProtocolError`/`DaemonResponseError` with `.code` | Done |
| Max response bytes ceiling (DEFAULT_MAX_RESPONSE_BYTES = 4 MB) | Done |
| Typed methods + frozen dataclasses for current/history/entity/hello | Done |
| metrics_meta validated as dict-of-dicts with `Sensitivity` enum membership | Done |
| Legacy surface untouched (current_frame/request_frames/stream_batch/request_health) | Done (existing tests unchanged) |
| Client fast-fail on cursor+window both set | Done (ValueError) |
| `not_found`/`invalid_type`/`out_of_range`/`unavailable` recoverable via `.code` | Done |

## Test evidence

All tests pass in the CI/dev environment (Linux x86_64, Python 3.14.6):

```bash
# Focused P63 tests
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_daemon_client_p63.py -q -W error -p no:schemathesis
# 20 passed in 7.32s

# Full suite
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis
# 1023 passed, 2 skipped in 130.93s
```

The 2 skips are known environment artifacts (Textual-dependent tests when
`textual` is not installed — recorded per P52-REPORT env notes), not
implementation failures.

```bash
python3 -m py_compile topos/src/topos/daemon/client.py          # clean
python3 -m py_compile topos/src/topos/daemon/__init__.py         # clean
python3 -m py_compile topos/tests/test_daemon_client_p63.py      # clean
git diff --check                                                  # clean
```

## Adversarial test coverage

| # | Test | Assertion |
|---|---|---|
| 1 | `test_request_current_returns_decoded_frame_and_metrics_meta` | seq, frame.ts, sensitivity in metrics_meta |
| 2 | `test_request_hello_returns_protocol_info` | protocol_version, capabilities, identity, limits |
| 3 | `test_request_entity_returns_decoded_entity_frame` | seq, entity.key, metrics sensitivity |
| 4 | `test_request_history_cursor_form_returns_typed_result` | entries seqs, oldest/latest/next_cursor, gap, metrics sensitivity |
| 5 | `test_request_history_time_window_form_returns_typed_result` | frame.ts within window |
| 6 | `test_request_history_rejects_cursor_and_window_together` | ValueError fast-fail |
| 7 | `test_error_not_found_carries_code` | .code == "not_found" |
| 8 | `test_error_invalid_type_carries_code` | .code == "invalid_type" |
| 9 | `test_error_out_of_range_carries_code` | .code == "out_of_range" |
| 10 | `test_error_bad_request_carries_code` | .code == "bad_request" |
| 11 | `test_error_unavailable_carries_code` | .code == "unavailable" |
| 12 | `test_id_echo_mismatch_raises_protocol_error` | DaemonProtocolError with "returned id" |
| 13 | `test_malformed_json_response_raises_protocol_error` | DaemonProtocolError "malformed JSON" |
| 14 | `test_empty_response_raises_protocol_error` | DaemonProtocolError "malformed JSON" |
| 15 | `test_oversized_response_raises_protocol_error` | DaemonProtocolError "oversized" |
| 16 | `test_non_object_response_raises_protocol_error` | DaemonProtocolError "non-object" |
| 17 | `test_connection_failure_raises_connect_error` | DaemonConnectError "cannot connect" |
| 18 | `test_import_does_not_trigger_heavy_imports` | No heavy framework at module import |
| 19 | `test_history_gap_and_bounds_match_stream_batch_semantics` | Envelope and legacy stream_batch produce identical bounds |
| 20 | `test_non_increasing_sequences_raise_protocol_error` | DaemonProtocolError "non-increasing" |

## Known gaps / open items

None. P58 can now consume the P52 read API exclusively through the typed
client without opening its own socket or serializing envelopes.

## Proposed contract changes

None. This is purely additive client-surface work within the existing
`DaemonClient` pattern.

## Files changed

```
topos/src/topos/daemon/client.py          — DaemonResponseError.code, result dataclasses,
                                            _request_envelope, typed methods, _validate_metrics_meta
topos/src/topos/daemon/__init__.py        — new exports
topos/tests/test_daemon_client_p63.py     — 20 deterministic tests
topos/docs/DAEMON.md                      — Typed Versioned Client (P63) section
topos/handoff/reports/P63-LOG.md          — this session log
topos/handoff/reports/P63-REPORT.md       — this report
```
