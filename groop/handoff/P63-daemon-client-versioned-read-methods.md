# P63 - Daemon Client Versioned Read Methods

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P52 (merged, reviewed)
> **Base:** main
> **Session-hint:** fresh
> **Escalate-if:** a named result-type contract below cannot be met against the P52 `DaemonApi` envelope as merged; or the existing legacy methods (`current_frame`/`request_frames`/`stream_batch`/`request_health`) would need behavior changes to accommodate the new methods (they must not).

## Goal

Extend P52's typed `DaemonClient` (`groop/src/groop/daemon/client.py`) with
typed, validated client methods for the versioned P52 envelope `entity` and
`history` operations (and, for a coherent versioned surface, `current` and
`hello`), so that a separate frontend process — specifically the P58 MCP
server — can consume the P52 read API **exclusively through the typed client**
without opening its own socket, serializing envelopes, parsing wire JSON, or
duplicating P52 validation/error mapping.

This package exists because P58 BLOCKED twice on exactly this gap: `DaemonApi`
(P52, `groop/src/groop/daemon/api.py`) serves `hello`/`current`/`history`/
`entity`/`health` through the versioned envelope, but `DaemonClient` only has
legacy-protocol methods (`current_frame`/`request_frames`/`request_health`/
`stream_batch`) with no typed versioned-envelope transport at all. See
`groop/handoff/reports/P58-REVIEW.md` (B3 blocker) and the terra-med v2
BLOCKED reasoning in `groop/handoff/reports/P58-REPORT.md` /
`P58-LOG.md` on branch `feat/groop-p58-daemon-mcp-frontend-v2`.

## Dependency And Workflow

- Starts on merged, reviewed P52. This is additive client-surface work
  following P52's established typed-client pattern — do not invent a novel
  transport; mirror the existing methods.
- Branch: `feat/groop-p63-daemon-client-versioned-read-methods`
- Worktree: `.worktrees/-groop-p63-daemon-client-versioned-read-methods`
- Touch only `groop/**`; write P63-LOG.md/P63-REPORT.md; commit, do not merge.

## Context To Read First (bounded — do not survey the whole tree)

`groop/README.md` (Workflow protocol), this handoff, `groop/CONTRACTS.md` §10
(P52 envelope, error-code enum, sensitivity enum), `groop/src/groop/daemon/
client.py` (the existing typed-client pattern you are extending),
`groop/src/groop/daemon/api.py` (the server whose envelope you decode — read
`_op_current`/`_op_history`/`_op_entity`/`_op_hello`, the `_error` envelope
wrapper, `enforce_response_bytes`, `ErrorCode`, `Sensitivity`,
`_metrics_meta_for`), `docs/DAEMON.md` (P52 section), and the existing
`groop/tests/test_daemon_client.py` + P52 tests for the transport/decoding
test shape. Do not read UI, DAMON/BPF, actions, or record/replay code.

## The Envelope You Are Consuming (from P52, do not re-implement server-side)

- **Request:** one JSON object per line: `{"id": <opaque str>, "op": <str>,
  "v": <int>, ...op params}`. `id` is echoed verbatim; `v` is
  `PROTOCOL_VERSION` (currently 1, exported from `api.py`). One request → one
  JSON response line.
- **Response (success):** `{"id": <echoed>, "ok": true, "result": {...}}`.
- **Response (failure):** `{"id": <echoed>, "ok": false, "error":
  {"code": <ErrorCode value>, "message": <safe str>}}`.
- **`current` result:** `{"seq": int, "frame": <frame jsonable>,
  "metrics_meta": {name: {unit, kind, locality, glossary, sensitivity}}}`.
- **`history` result:** `{"frames": [{"seq": int, "frame": <jsonable>}, ...],
  "oldest_seq": int|null, "latest_seq": int|null, "next_cursor": int|null,
  "gap": bool, "metrics_meta": {...}}`.
- **`entity` result:** `{"seq": int, "entity": <entity_frame jsonable>,
  "metrics_meta": {...}}`.
- **`hello` result:** `{"protocol_versions": [int], "capabilities": [str],
  "identity": {name, version}, "limits": {...}}`.

## Required Contracts

### Transport (own it once, mirror the legacy pattern)

- Add a single private versioned-envelope round-trip helper on `DaemonClient`
  (mirroring `request_frames`/`stream_batch`: `AF_UNIX` connect, `timeout_s`,
  `json.dumps(..., sort_keys=True, separators=(",", ":"))` + `\n`,
  `SHUT_WR`, read one response line via `makefile`). It builds the envelope
  (`id`, `op`, `v=PROTOCOL_VERSION`, params), sends, reads exactly one
  response line, and decodes the envelope wrapper. Generate a fresh opaque
  `id` per request and **assert the echoed `id` matches** — a mismatch is a
  `DaemonProtocolError`.
- Reuse the existing exception hierarchy: `DaemonConnectError` on `OSError`;
  `DaemonProtocolError` on malformed/oversized/non-JSON/non-object/`id`
  mismatch/`ok`-missing/unknown-response-shape; `DaemonResponseError` for
  `ok:false` envelopes, carrying the typed `error.code` (map the P52
  `ErrorCode` string onto the raised error so callers can branch on it —
  e.g. an attribute like `.code` on `DaemonResponseError`, or a small
  mapping to distinct subclasses if that reads more consistently with the
  existing file; your call, but the `not_found`/`invalid_type`/`out_of_range`/
  `unavailable` codes P58 needs to distinguish must be recoverable by the
  caller, not flattened into an opaque string). Bound the response read the
  same way `_read_health` bounds it (a max-response-bytes ceiling); do not
  read unboundedly.
- No raw wire detail, socket path beyond the existing `{self.socket_path}`
  framing, or server exception text leaks in a way the legacy methods don't
  already. The P52 server already sanitizes `error.message`; do not undo that.

### Typed methods and result types

Add typed methods returning frozen dataclasses (mirror `DaemonFrameBatch`),
each owning its decoding + validation:

- `request_current() -> DaemonCurrentResult` — `{seq, frame, metrics_meta}`.
  Result carries the decoded `Frame` (via `frame_from_jsonable`), `seq:int`,
  and the metrics_meta mapping.
- `request_history(*, limit: int = 1, cursor: int | None = None, since_ts:
  float | None = None, until_ts: float | None = None) -> DaemonHistoryResult`
  — mirrors the `history` op. Enforce the same client-side shape rule the
  server enforces (cursor XOR time-window; not both) so the client fails fast
  with a clear error rather than round-tripping a guaranteed `bad_request`;
  still surface the server's typed error if one comes back. Result carries the
  ordered `(seq, Frame)` entries plus `oldest_seq`/`latest_seq`/`next_cursor`/
  `gap`/`metrics_meta`, decoded and validated exactly as `stream_batch`
  validates its batch (monotonic seqs, bounds coherence, next_cursor
  agreement).
- `request_entity(key: str) -> DaemonEntityResult` — mirrors the `entity` op.
  Result carries `seq:int`, the decoded entity-frame model (via the model's
  entity-frame-from-jsonable decoder — locate the counterpart to
  `entity_frame_to_jsonable`), and `metrics_meta`. Do NOT re-validate/reject
  the `key` shape client-side beyond basic type — the P52 server owns
  selector validation and returns typed errors (`invalid_type`/`not_found`);
  the client's job is to transport and surface those, not to duplicate the
  injection-rejection logic (that lives in `api.py:_validate_entity_key`).
- `request_hello() -> DaemonHello` (or equivalent) — so a consumer can
  negotiate protocol version/capabilities/limits before issuing ops. Small,
  but it completes the versioned surface and lets P58 fail cleanly against an
  incompatible daemon.

The result types must retain P52 sequence/history metadata and the
registry-derived per-metric sensitivity metadata (`metrics_meta`) — a
web/MCP consumer renders from these without re-deriving registry prose.
`metrics_meta` may be surfaced as a validated typed mapping or as a
lightly-validated dict; if a dict, validate it is a dict-of-dicts with a
`sensitivity` value in the closed `Sensitivity` enum for every entry (P58's
`--redact-above` depends on that being trustworthy).

### Do not disturb the legacy surface

- The existing `current_frame`/`request_frames`/`stream_batch`/`stream_frames`
  /`request_health` methods and the module-level `current_frame`/
  `stream_frames`/`current_frame_stream` helpers keep their exact current
  behavior and signatures. The new methods are additive. Existing
  `test_daemon_client.py` and P52 tests remain green, unchanged.

## Required Deterministic Tests

Events/barriers/bounded polling, not sleeps. Drive the real `DaemonApi`
envelope in-process over a real (or `socketpair`/temp-`AF_UNIX`) transport —
the same harness shape P52's `test_daemon_p52.py` already uses — not a hand-
mocked socket, so the client is proven against the actual server envelope.
Cover at least:

- `request_current`/`request_entity`/`request_history`/`request_hello` happy
  paths decode the correct typed result against a fixture frame with known
  values (assert seq, a known metric value, and a known `sensitivity` in
  `metrics_meta`).
- `id` echo asserted; an injected `id` mismatch raises `DaemonProtocolError`.
- Server `ok:false` envelopes map to the caller-recoverable typed error for
  each of `not_found` (entity), `invalid_type`/injection-shaped key (entity),
  `out_of_range` (over-cap `limit`), `bad_request` (cursor+window both set),
  and `unavailable` — the caller can branch on the P52 code.
- History cursor form and time-window form both round-trip; gap/oldest/latest/
  next_cursor decoded identically to `stream_batch` semantics; client rejects
  cursor+window-both-set before sending (or surfaces the server error — pick
  one and test it).
- Malformed/oversized/truncated/non-object/non-JSON response line each raise
  `DaemonProtocolError`; connection failure raises `DaemonConnectError`.
- Import isolation preserved: no new heavy import lands at module import time.

Do not weaken or rewrite existing P52/client tests to make the new methods
pass; extend. Existing daemon attach/status/health/client tests remain green.

## Gates And Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P63/client tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <all changed/new files>
git diff --check
```

State which environment each result came from. `-p no:schemathesis` and the
`textual`-absent test skips are known environment artifacts in this repo (see
P52-REPORT env notes), not implementation failures — record them as such, do
not claim an unrun gate. Update `docs/DAEMON.md` (client section — the typed
versioned methods now available to frontends), `CONTRACTS.md` (client surface,
if it enumerates client methods), and the P63 LOG/REPORT.

## Patch Discipline

Prefer additive methods + result dataclasses inside the existing `client.py`
and focused edits over rewriting the file. If the versioned transport is
large enough to warrant its own private helper section, keep it in the same
module beside the legacy methods so the pattern is visibly shared. Do not
touch `api.py` (P52 owns the wire); if the server envelope genuinely cannot
satisfy a result-type contract above, escalate per the header rather than
editing the server.

## Out Of Scope

- Any change to the P52 wire protocol, envelope shape, error codes, or
  sensitivity enum — P52 owns the wire; this package only consumes it.
- The MCP frontend itself (P58 consumes this client from a separate process).
- Mutation/write ops, HTTP/WebSocket transport, name/docker selector
  resolution (P57 owns selectors; the client transports an exact key).
- Changing collector metric semantics or registry metadata content.
