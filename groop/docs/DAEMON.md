# groop Daemon

P51 introduces a request-independent background producer: the daemon owns one
continuously advancing collector stream and serves non-consuming snapshots and
history to any number of clients. Requests never call `next()` on the collector,
so multiple concurrent clients observe the same sequence without being able to
accelerate, consume, or starve each other.

P52 adds a versioned, bounded, peer-aware read API envelope over the P51
broker. The envelope carries a client `id`, protocol version, typed error
codes, sensitivity metadata, peer credentials, and proven resource bounds.
Legacy clients (requests without a `v` field) continue to be served
unchanged — see "Protocol compatibility" below.

## Socket

Recommended production path: `/run/groop/groop.sock`.

Recommended ownership: `root:groop`, mode `0660`. Users who may read
daemon-approved full telemetry join the `groop` group. The socket directory
should be root-owned and not writable by clients.

## Protocol

One JSON request per connection:

```json
{"op":"current"}
{"op":"stream","limit":3}
{"op":"stream","limit":5,"cursor":7}
```

`current` returns the latest published frame (the most recent frame the
background producer has written). Before the first frame is available it waits
for a bounded startup timeout (default 5 s) and returns a typed
`FrameUnavailableError` on timeout, source exhaustion, or producer failure.

`stream` reads from the published history without advancing the collector.
Without a `cursor` it returns the *limit* most recent frames (the tail of
history).  With a `cursor` it returns frames whose sequence number is strictly
greater than the cursor.  Each frame response includes a `seq` field when
returned from a stream request.

Responses are JSON lines:

```json
{"type":"frame","frame":{...canonical Frame JSON...},"seq":12}
{"type":"end","count":1}
```

Unsupported requests return an error object. The protocol has no arbitrary file
read, command execution, admin, Docker mutation, systemd mutation, BPF, or DAMON
mutation verb.

## Versioned Read API (P52)

P52 adds a versioned envelope for attached TUI and separate frontend
processes (web backend, MCP server — see P58). The envelope is single-line:
one JSON request, one JSON response.

### Envelope

Every request carries `id` (opaque client string, echoed verbatim), `op`
(closed set), and `v` (protocol version integer). Every response carries the
echoed `id`, `ok` boolean, and on failure a typed `error` object (`code` from
a closed enum, safe `message`). A successful response carries `result`.

```json
{"id":"c1","op":"hello","v":1}
{"id":"c1","ok":true,"result":{"protocol_versions":[1],"capabilities":[...]}}
{"id":"c1","ok":false,"error":{"code":"unknown_op","message":"unknown op: exec"}}
```

The error object never carries a raw exception, secret, filesystem path, or
arbitrary exception text. The P47 `sanitize_public_text` helper is reused so
the P51 safety contract persists through the new envelope.

### Ops

- `hello` — protocol version(s) served, capability list, daemon identity, and
  current limits (max request bytes, max response items, history capacity).
- `current` — latest atomic `(sequence, frame)` plus `metrics_meta`.
- `history` — bounded by sequence cursor OR by time window (`since_ts`
  inclusive, `until_ts` exclusive); each form returns explicit
  `gap`/`oldest_seq`/`latest_seq`/`next_cursor` metadata identical to the P51
  legacy `stream` op. The two forms are mutually exclusive.
- `entity` — one entity's frame/model data plus registry metadata. Resolves
  ONLY against daemon-approved in-memory frame data; `key` is validated (no
  absolute path, `..`, NUL, or control chars) and never reaches a filesystem
  path, registry lookup by arbitrary key, command, or sysfs/procfs read.
- `health` — P47 component health through the new envelope.

### Sensitivity metadata

Every metric in a `current`/`history`/`entity` response carries a
sensitivity level from the closed enum `{public, operational, sensitive}` in
`metrics_meta`, alongside registry-derived `unit`/`kind`/`locality`/`glossary`
so a web/MCP consumer can render without duplicating registry prose. See
`CONTRACTS.md` §10 for the mapping.

### Peer identity and authorization

`SO_PEERCRED` (pid/uid/gid) is observed at accept time on every connection
and attached to the connection context; it appears in every audit/rate-limit
record produced for that client. An authorization hook
(`Callable[[PeerCredentials, str], tuple[ErrorCode, str] | None]`) is
injectable for tests. Default policy: socket-group read access enforced by
the OS (mode 0660 root:groop). The hook receives `(peer, op)` and may deny
with a typed error. Mutation-shaped ops are rejected before the hook runs.

**Peer-credential read failure** (platform or race): the connection is served
anonymously (`peer=None`); the daemon never refuses on a best-effort
introspection race. Authorization remains enforced at the socket-group
boundary by the OS.

### Resource bounds

`ApiLimits` validates every bound at construction; out-of-range values raise
and are never silently clamped. Bounds cover: per-request bytes, per-request
read time, aggregate concurrent clients, per-response items, per-response
bytes, and aggregate history capacity. Each bound has a test that violates it
for real and asserts the observable outcome. See `CONTRACTS.md` §10 for the
full table.

### Protocol compatibility (legacy ops)

Pre-P52 clients send requests without a `v` field. The daemon serves these
unchanged through the P51 multi-line protocol (compatibility mode, choice
(a) per the P52 handoff). Each legacy op is documented below; tests cover
both the accepted (legacy) and rejected (envelope-with-bad-version) forms.

| Legacy op | Without `v` (legacy) | With `v` (envelope) |
|---|---|---|
| `current` | Served unchanged (multi-line) | Served as envelope `current` |
| `stream` | Served unchanged (multi-line) | Replaced by envelope `history` |
| `health` | Served unchanged (single-line) | Served as envelope `health` |
| `status` | Not a broker op (CLI composite) | `unknown_op` |

An envelope request with an unsupported `v` is rejected with
`protocol_version`; its message names the supported version(s).

### Typed Versioned Client (P63)

`DaemonClient` (``groop/src/groop/daemon/client.py``) provides typed,
validated Python methods for the versioned envelope ops. These methods are
additive — they do not change the existing legacy-method signatures.

**Transport:** A single private ``_request_envelope`` helper owns the
versioned-envelope round trip: ``AF_UNIX`` connect, ``json.dumps`` with
sorted keys and compact separators, ``SHUT_WR``, single-line response read via
``makefile`` bounded at ``DEFAULT_MAX_RESPONSE_BYTES`` (4 MB), envelope
wrapper decode, and ``id`` echo assertion. A mismatch raises
``DaemonProtocolError``.

**Error codes:** ``DaemonResponseError`` carries a ``.code`` attribute
with the P52 ``ErrorCode`` string (``not_found``, ``invalid_type``,
``out_of_range``, ``unavailable``, etc.) so callers can branch on the
typed code.

**Result types** (all frozen dataclasses):

| Method | Return type | Description |
|---|---|---|
| ``request_hello()`` | ``DaemonHello`` | Protocol versions, capabilities, identity, limits |
| ``request_current()`` | ``DaemonCurrentResult`` | Latest ``(seq, Frame)`` + ``metrics_meta`` |
| ``request_history(*, limit, cursor, since_ts, until_ts)`` | ``DaemonHistoryResult`` | Ordered ``(seq, Frame)`` entries, history bounds, ``metrics_meta``; fast-fail ``ValueError`` if cursor + time window both set |
| ``request_entity(key)`` | ``DaemonEntityResult`` | One entity's frame + ``metrics_meta`` |

``metrics_meta`` is validated as a dict-of-dicts with every entry's
``sensitivity`` belonging to the closed ``Sensitivity`` enum
(``public``/``operational``/``sensitive``), so a frontend can render
``--redact-above`` rules without re-deriving registry prose.

**Import:** add ``from groop.daemon import DaemonClient,
DaemonCurrentResult, DaemonHistoryResult, DaemonEntityResult,
DaemonHello``.

## MCP frontend (P58)

`groop mcp serve` is the stdio-only, read-only MCP frontend for local AI CLI
agents. Install it with `pip install 'groop[mcp]'`, then register for example
with `claude mcp add groop -- groop mcp serve`. It consumes the versioned API
only through P63's typed `DaemonClient`; no MCP code implements daemon socket
or envelope handling.

The server probes `hello` before accepting a stdio session. A daemon absent at
startup exits nonzero; loss after startup is returned as a typed
`daemon-unavailable` tool result. The closed tool set is `groop_health`,
`groop_overview`, `groop_entity`, and `groop_history`; all responses have an
enforced 4 MiB aggregate cap. Overview accepts 1..50 rows, history accepts
1..100 points and a seven-day maximum `last:Ns` window, entity has fixed
128-metric/64-finding limits, and health has a fixed 16-component limit.
Selectors use exact EntityKeys or P57's docker name/prefix resolver. Use
`--redact-above public|operational|sensitive` to replace values above the
chosen P52 sensitivity with a typed `__redacted__` marker.

## Background Producer

On `groop daemon serve` the daemon creates a `FrameBroker` with the live
collector stream and immediately starts a background producer thread. The
producer continuously advances the collector, publishing each frame into a
bounded sequenced history (`--history-size`, default 120).  The producer runs
independently of read requests; it automatically starts on first access (lazy)
and stops deterministically after the Unix server closes.

If the frame source is exhausted or the producer encounters repeated errors, the
broker captures the failure and returns a `FrameUnavailableError` to clients
without crashing the Unix server. The daemon continues to serve cached frames
from history.

## Socket

Recommended production path: `/run/groop/groop.sock`.

Recommended ownership: `root:groop`, mode `0660`. Users who may read
daemon-approved full telemetry join the `groop` group. The socket directory
should be root-owned and not writable by clients.

## Protocol

One JSON request per connection:

```json
{"op":"current"}
{"op":"stream","limit":3}
```

Responses are JSON lines:

```json
{"type":"frame","frame":{...canonical Frame JSON...}}
{"type":"end","count":1}
```

Unsupported requests return an error object. The protocol has no arbitrary file
read, command execution, admin, Docker mutation, systemd mutation, BPF, or DAMON
mutation verb.

## Attach Client

`groop --attach SOCKET` consumes daemon frames through the same UI model used by
live collection. The attach client is read-only and only speaks the P16 broker
protocol.

When no explicit socket path is given, `--attach` defaults to the packaged
default daemon socket (`/run/groop/groop.sock`).

Common forms:

```bash
groop --attach                              # default socket, interactive UI
groop --attach --once --json                # default socket, one canonical frame
groop --attach --ui-smoke                   # default socket, UI smoke test
groop --attach /run/groop/groop.sock        # explicit socket, interactive UI
groop --attach /run/groop/groop.sock --once --json
groop --attach /run/groop/groop.sock --ui-smoke
```

`--attach --once --json` prints one canonical frame JSON payload and is the
preferred shell/test entry point. The interactive attach path polls the daemon
for current frames and feeds them into the existing TUI path.

## Daemon Current Command

`groop daemon current [--json] [--socket PATH] [--pretty-json]` prints one canonical frame
from the daemon socket as JSON. It is a read-only, scriptable one-shot
alternative to `--attach --once --json`.

```bash
groop daemon current --json                       # default socket, compact JSON
groop daemon current --socket /custom/path.sock   # custom socket
groop daemon current --pretty-json                # indented JSON
```

The command returns non-zero with an error message on stderr if the socket is
missing, unreachable, or returns a protocol error. It never falls back to live
collection.

## Daemon Status Command

`groop daemon status --socket PATH --group NAME [--json] [--pretty-json]`
combines deployment preflight checks with a protocol current-frame check to
answer "is the daemon deployment usable from this account, and is it speaking
the expected groop frame protocol?"

```bash
groop daemon status                              # default socket and group
groop daemon status --json                       # JSON output
groop daemon status --pretty-json                # indented JSON
groop daemon status --socket /custom/path.sock --group mygroup
```

Exit codes:
- `0` when preflight is usable and the current-frame protocol check succeeds.
- `1` when preflight or protocol check fails (with guidance in the output).
- `2` for argument/usage errors.

The command is read-only: it inspects filesystem metadata, group membership,
and makes one `current` request over the existing P16 daemon protocol. It
never runs systemd, mutates files, or changes ownership/modes.

Current slice limitations:

- `--attach` is intentionally rejected with `--replay` and `--cgroup-root`.
- `--attach` does not support `--record` in this slice.
- The daemon protocol remains read-only; there is still no file-read, command,
  Docker/systemd mutation, or DAMON mutation verb.

## Daemon-Owned paddr Lifecycle

When `[damon] paddr_enabled = true` is set in the daemon's TOML config, the
root daemon starts and owns one audited whole-host paddr DAMON session at
startup. The session is stopped gracefully on daemon shutdown.

Key characteristics:

1. **Disabled by default.** No DAMON writes occur unless the operator explicitly
   sets `paddr_enabled = true`.

2. **Idempotent restart with verification.** If a groop-owned paddr marker
   already exists (from a prior daemon run), the lifecycle verifies the
   referenced kdamond slot is live (state ``on``, operations ``paddr``) before
   adopting. A stale marker (kdamond is ``off``) is cleaned up; a malformed or
   internally inconsistent marker, a marker pointing at a missing kdamond, or
   a marker whose kdamond runs a different monitoring mode raises a bounded
   startup error.

3. **Foreign-safety.** Non-groop markers and foreign kdamond slots are never
   touched during start, adoption, or stop.

4. **Bounded startup failure.** If the paddr session cannot be started (no free
   kdamond, root required, ownership conflict, stale/malformed marker, or
   kdamond mismatch), the error is logged and the daemon continues without
   paddr status. The read-only daemon is always usable.

5. **Graceful shutdown.** Only a session created by this daemon run is stopped.
   A verified session adopted from an earlier run remains persistent; use
   `groop damon stop --all-mine` for explicit cleanup. The existing
   `stop_owned_sessions` mechanism tears down current-run sessions and removes
   their groop ownership markers.

6. **Audit trail.** Every start and stop produces a JSONL audit event in the
   daemon's state directory (default `~/.local/state/groop/actions.log`).

7. **Config-driven intervals.** The existing `[damon] paddr_sample_us`,
   `paddr_aggr_us`, and `paddr_update_us` settings control the daemon-owned
   session's interval configuration.

## Deployment Checklist

Before deploying, run `groop daemon install-plan` to see the ordered
operator steps, exact commands, and destination paths for the packaged
templates. The plan is read-only — it describes what to do without
changing any host state.

After reviewing the plan, proceed with the checklist below.

The packaged operator templates live under `src/groop/assets/systemd/`:

- `groop.service` starts `groop daemon serve --socket /run/groop/groop.sock`
  as a root daemon with a group-readable socket.
- `groop.tmpfiles` creates `/run/groop` with `0750 root:groop`.

Before enabling the service:

1. Create the `groop` group.
2. Add the approved non-root users who should attach to the daemon socket.
3. Install the service and tmpfiles templates.
4. Start the daemon.
5. Run `groop daemon preflight --socket /run/groop/groop.sock` from the client
   account to confirm that the runtime directory, socket permissions, and
   group membership are usable.

The preflight command is read-only. It inspects the socket path, parent
directory, group membership, and local connectability without mutating host
state or invoking systemd.

## Troubleshooting Daemon Client Errors

When `groop --attach ...` or `groop daemon current ...` fails, the CLI prints
the original error followed by actionable guidance:

### Default socket (`/run/groop/groop.sock`)

```
cannot connect to /run/groop/groop.sock: No such file or directory

Try: groop daemon preflight
If the daemon is not installed: groop daemon install-plan
```

### Custom socket

```
cannot connect to /tmp/custom.sock: Connection refused

Try: groop daemon preflight --socket /tmp/custom.sock
```

### Protocol/response errors

```
daemon at /run/groop/groop.sock returned malformed JSON on line 1

Check that the process at the socket is a compatible groop daemon
and review the daemon logs for errors.
```

All errors preserve the original exception text and exit code 2. No live
collection fallback is introduced.

## Threat Model

The daemon may run with privileges so it can read root-only kernel/debugfs/DAMON
state. The socket therefore exposes sensitive read-only telemetry. The broker
must keep authorization at the socket boundary and must not add request fields
that choose arbitrary paths, commands, process IDs, or Docker/systemd actions.

Docker metadata may include image names and labels before redaction elsewhere;
do not expose Docker socket access to clients. Future mutation APIs require a
separate `--admin` model, exact previews, confirmation, and audit logging.

## Retention

The P16 prototype uses bounded in-memory history, defaulting to 120 frames.
Future production retention should bound both age and bytes and should make any
on-disk store opt-in with explicit permissions.
