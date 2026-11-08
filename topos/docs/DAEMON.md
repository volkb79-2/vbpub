# topos Daemon

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

The production listener is owned by the systemd socket unit
`topos-observe.socket` at `/run/topos/observe/topos.sock`. The unit creates the socket
node as `root:topos-observe`, mode `0660` by default, and its parent directory as
`root:root` mode `0755`. It passes its
listening descriptor to `topos.service` with the name `observe`. Daemon state
is kept under the configured `state_dir`, created as `root:root` mode
`0700` by systemd's `RuntimeDirectory=`.

`/etc/topos/toposd.toml` configures `state_dir` and the observe socket's path,
group, and mode. The state directory is the base for daemon BPF snapshots
(`state_dir/bpf`) and squeeze recovery records (`state_dir/squeeze`). The
`toposd run --bpf-state-dir PATH` option overrides the BPF snapshot directory.
The BPF `state_dir` in the collector's config does not select the daemon's
snapshot directory. `topos squeeze` and `topos squeeze --recover` read this
service config as well. If the default config file is absent, built-in defaults
apply; an invalid file stops the command with a config error. Clients need
membership in the configured observe group to read daemon-approved telemetry.

## Service entry point

`toposd run` adopts the named socket passed by systemd and refuses to start
without it. For tests and development, use
`toposd run --self-bind --socket PATH`; `--socket` is valid only with
`--self-bind`. The self-bind mode uses the configured mode, removes only a
stale socket node, and unlinks the node it owns at shutdown. It never changes
the parent directory's owner or mode. `--self-bind` is refused when this
process also has a systemd-passed listener.

`toposd unit print [--config PATH] [--unit NAME]` renders the socket, service,
slice, and sysusers files from the config. `toposd unit check [--config PATH]
[--root DIR] [--diff]` compares them with installed files. `toposd config check
[--config PATH] [--json]` validates the service config; `toposd -t [--config
PATH]` is the same check in the `nginx -t` style (exit 0, or 2 with the error on
stderr); `topos.service` runs it as its `ExecStartPre`, so a config that fails the
check (including a group with GID 0 or the docker GID) never starts the daemon.
`RestartPreventExitStatus=2` covers only the main process, so a failing check is
retried every 5 s until the unit's start limit (5 starts in 300 s) ends it; read
the cause with `journalctl -u topos.service`. `toposd install`,
`uninstall`, and `doctor` manage and inspect the host service. The client-side
`topos daemon status` command remains available for socket and protocol checks.
The old path `/run/topos/topos.sock` is no longer served, and `topos daemon
serve` has been removed. There is no migration or in-place repair: an old
installation must be removed manually before installing this layout.

## Install and uninstall

Every asset is pinned by its release version and lowercase SHA-256 digest:
`<version>@sha256:<64 hex>`. The daemon lock pin must use the same version as
the topos wheel. The lock itself contains the fully hashed third-party runtime
dependency wheels. Release assets use tag `topos-v<version>`; a local
`--wheel-source` directory must contain the two wheels, the daemon lock, and
the dependency wheels used by pip's `--find-links` resolver.

Preview the exact inputs and resources first:

```bash
sudo toposd install --dry-run \
  --wheel 0.7.3@sha256:<topos-wheel-sha256> \
  --cli-extended-wheel 0.5.2@sha256:<cli-extended-wheel-sha256> \
  --daemon-lock 0.7.3@sha256:<daemon-lock-sha256> \
  --wheel-source https://github.com/volkb79-2/vbpub/releases/download \
  --add-user observe=alice --add-user observe=bob --enable
```

After checking the preview, run the same command with `--yes` and without
`--dry-run`. On an interactive TTY, the observe-group prompt offers
`topos-observe` as its default. A non-interactive run must pass
`--observe-group` (unless an existing config or install record supplies it).
A mapped group must already exist. The observe group is refused, with exit 2 and
no override, when it is named `docker`, has GID 0, or has the docker group's GID;
the flag, the install record, `toposd.toml` and the prompt answer are all
checked before anything changes (its membership would be root-equivalent). Put
operator accounts into `topos-observe` with `--add-user observe=USER`, or map
another dedicated group. A `toposd.toml` that names `docker` is refused even when
`--observe-group` is given, so change that line by hand first; one that names a
group with GID 0 or the docker GID is replaced by the flag's group. `toposd config
check` and `toposd -t` refuse such a group on the host too.
`--add-user observe=USER` (repeatable; `observe` is topos's only level) adds an
existing user to the observe group with `gpasswd -a` after the group exists.
Members are only ever added: a rerun without the flag keeps every recorded user,
a further flag adds to them, and neither install nor uninstall removes a member.
A recorded user who no longer exists is dropped from the record and reported
instead of blocking the install; a user named on the command line must exist.
The result names the users added; a new membership applies to the user's next
login session or after a service restart, not to running sessions.
`--no-enable` writes the service files without enabling or starting the
socket/service.

`toposd install` supports topos 0.7.0 and later; older releases have no
`toposd` or daemon lock. Installing a version older than the current one needs
`--allow-downgrade`.

`/etc/topos/toposd.toml` is written only when it is absent. With
`--observe-group`, the installer updates only `sockets.observe.group`, preserves
the other lines and comments, and reports the change in the install result and
dry-run summary; a rewrite keeps the file's mode. Without the flag, a group the
existing config names wins over the install record and is saved back into the
record; a config without a `group` key names none, so the record or a prompt
decides, and a pipe run with neither errors. A mapped group must already
exist; `topos-observe` may be created by the installer. If the config uses a
form the installer cannot safely edit, it refuses and tells you what to change
by hand. The config's `state_dir` and socket `path` must keep the fixed layout
(`/run/topos/state`, `/run/topos/observe/topos.sock`); only the group and mode
are host choices.

The installer refuses an entry in `/usr/local/bin/topos` or `toposd` that is not
its own symlink into `/opt/topos`. Move such a file away before installing.

The installer resolves missing pins in this order: command-line flag, saved
`/etc/topos/install.toml`, interactive TTY prompt, then an error naming the
missing flag. Dry runs never prompt and default to `enabled = false` when no
enable choice is supplied. The install record stores the three pins, release
source, observe group and users, enable choice, and groups created by the
installer. Version trees under `/opt/topos/<version>` are immutable once their
`MANIFEST` exists. The active version is `/opt/topos/current`.

Install checks socket nodes, old paths, tmpfiles rules, service drop-ins,
runtime directory ownership and modes, systemd files without an install
record, version manifests, and interrupted-install markers before changing
anything. It refuses an old or unknown installation and prints the manual
removal commands. It never migrates, repairs, or chmods old state.

`/opt/topos/PENDING` marks an install in progress. A failed download, pin check
or venv build removes its version tree and the marker, so a retry starts clean.
A failure after that point, while units, links and services change, leaves
`PENDING` in place (see below for how a rerun treats it).

`toposd doctor [--check NAME] [--json]` runs `config-valid`, `units-current`,
`groups`, `socket-units`, `socket-nodes`, `socket-dirs`, `state-dir`,
`service`, `connect`, `foreign-rules`, and `caller-access`. The `groups` check
fails when the observe group is missing, has GID 0 or the docker GID, and lists
the members; it warns when a user recorded in `install.toml` is no longer a
member. A non-root caller
without observe-group access gets `skip` for `connect`; checks that cannot
inspect a root-owned resource report `skip` with `run as root`.
The `service` check compares the running hello version with the installed
version and requires `socket_activation.mode = "activated"` with exactly one
listener. If the daemon is self-bound or is an older daemon without this hello
field, the remedy is `systemctl restart topos-observe.socket`.

```bash
toposd doctor --json
toposd doctor --check connect
toposd uninstall --dry-run
sudo toposd uninstall --yes
sudo toposd uninstall --remove-groups --yes
```

Uninstall keeps groups and memberships by default. `--remove-groups` removes
only groups that the install record says `toposd install` created. If
`groupdel` refuses a group, uninstall finishes the service, config, and version
cleanup, keeps that group and the install record, prints the failure, and exits
2. The `toposd` that ran is removed with its version tree, so it cannot be
rerun: run `groupdel` for the kept group once it is free, then delete
`/etc/topos/install.toml`. If removing the version tree itself fails, remove
`/opt/topos` and the install record by hand.

Uninstall acts only on a host where `toposd install` left its record
(`/etc/topos/install.toml`) or its `PENDING` marker; otherwise it reports that
nothing is installed. It checks everything before the first change and refuses,
with the manual removal steps, when it finds old-layout or unknown paths under
`/run/topos` or `/opt/topos`. It removes the socket and service units (socket
first), the slice, the sysusers file, its own `/usr/local/bin` links, the runtime
tree, `/opt/topos`, and the leftover host-setup drop-in
`topos.service.d/50-mdt-socket-group.conf`. Other files in `/usr/local/bin` or
`/etc/topos` are kept and listed; `/etc/topos/toposd.toml` is removed even when
it predates the install. When the uninstall process runs from a version tree
under `/opt/topos`, it keeps that tree and the `current` symlink while printing
the result. It then removes the running tree, `current`, `PENDING`, `/opt/topos`,
the install record, and `/etc/topos` if empty, in that order. Otherwise the
version tree and `current` are removed in the main pass before `PENDING`, the
`/opt/topos` directory, the install record, and the empty `/etc/topos` directory.
The ownership markers are last, so an uninstall interrupted before the version
tree is removed can be rerun; after that point finish by hand as described above.
`--dry-run` lists the paths and groups it would remove.

A failed upgrade of a finished install leaves `PENDING` next to the install
record. Rerunning `toposd install` after fixing the cause re-applies the
requested version: it rewrites every resource, reloads systemd and restarts the
running service, because the failed attempt may already have written the files.
After the sysusers step, install writes the complete record, including any
created observe group, before readiness can fail. A first install interrupted
after that point can be rerun to re-apply, or uninstalled with
`--remove-groups` to remove the group. A `PENDING` without a record means the
install stopped before writing that ownership record; uninstall can clear the
partial files, while install refuses the unfinished state. A host where an
interrupted uninstall left the record can likewise be reinstalled over (and then
uninstalled again).

### Manual removal of an old installation

When install refuses an old layout, stop and disable its service, remove the
host-setup rules and old units, reload systemd, then remove the old runtime
tree. Recreate containers that bind-mount `/run/topos` after removing it.

```bash
sudo systemctl stop topos.service
sudo systemctl disable topos.service
sudo rm -f /etc/tmpfiles.d/mdt-topos.conf
sudo rm -f /etc/systemd/system/topos.service.d/50-mdt-socket-group.conf
sudo rm -f /etc/systemd/system/topos.service /etc/systemd/system/topos.slice
sudo systemctl daemon-reload
sudo rm -rf /run/topos
# Recreate containers that bind-mount /run/topos.
```

## Differences from cgprofiled

- topos has one read-only `observe` socket. It has no control level, slices, or
  `allow_uids` access list.
- There is no live-session drain or exit code 3; topos clients only read
  telemetry, and there is no mutating daemon session to drain.
- topos has no `/var/lib` daemon data, so uninstall has no `--purge-data` mode.
- Like cgprofile, topos refuses `docker` as the observe group; an observe group
  may map to any other existing group.
- Readiness uses a bounded hello request on the configured observe socket and
  requires the reply to report the expected topos version.
- There is no downgrade below the socket-unit release: topos before 0.7.0 has
  no `toposd` or daemon lock, so `toposd install` refuses those versions.
  `--allow-downgrade` covers rollbacks between 0.7.0 and later releases, which
  all use the same socket units.

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
the OS (mode 0660 root:topos-observe). The hook receives `(peer, op)` and may deny
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

### Typed Versioned Client (P63/P66)

`DaemonClient` (``topos/src/topos/daemon/client.py``) provides typed,
validated Python methods for the versioned envelope ops. These methods are
additive — they do not change the existing legacy-method signatures.

**Transport:** A single private ``_request_envelope`` helper owns the
versioned-envelope round trip: ``AF_UNIX`` connect, ``json.dumps`` with
sorted keys and compact separators, ``SHUT_WR``, single-line response read via
``makefile`` bounded at ``DEFAULT_MAX_RESPONSE_BYTES`` (4 MB), envelope
wrapper decode, and ``id`` echo assertion. A mismatch raises
``DaemonProtocolError``. Every typed method below, including P66's
``request_health_versioned()``, goes through this one helper — there is no
per-op transport.

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
| ``request_health_versioned()`` | ``DaemonVersionedHealthResult`` | P47 ``HealthSnapshot`` decoded through the versioned envelope, plus a derived ``overall_ok`` property |

``metrics_meta`` is validated as a dict-of-dicts with every entry's
``sensitivity`` belonging to the closed ``Sensitivity`` enum
(``public``/``operational``/``sensitive``), so a frontend can render
``--redact-above`` rules without re-deriving registry prose.

**Import:** add ``from topos.daemon import DaemonClient,
DaemonCurrentResult, DaemonHistoryResult, DaemonEntityResult,
DaemonHello, DaemonVersionedHealthResult``.

**``request_health_versioned()`` (P66):** completes the versioned read
surface with the one op P63 left out (``health``) so a frontend that
negotiated the envelope via ``request_hello()`` never has to fall back to
the legacy, non-versioned ``request_health()`` socket path to read
component health. It decodes the ``health`` envelope's ``result`` dict
through the exact same per-field parsing the legacy path uses
(``DaemonClient._parse_health_payload``) — unknown component names/order,
missing/invalid fields, and an incompatible ``schema_version``/
``capability`` all raise ``DaemonProtocolError`` identically for both
methods, and both methods stay independently callable (there is no
transport or decode change to the legacy method). ``DaemonVersionedHealthResult``
wraps the decoded ``HealthSnapshot`` under ``.snapshot`` and exposes a
computed ``.overall_ok`` property: ``True`` iff no component reports
``DEGRADED`` or ``FAILED`` — the same two states
``ComponentHealthRegistry.set_state`` itself classifies as a failed
attempt. ``.overall_ok`` is derived on read, never stored, so it cannot
drift from ``.snapshot``. The legacy ``request_health()`` (single-line,
non-versioned protocol) and ``HealthSnapshot`` are unchanged; the two
result types are deliberately distinct so callers can't confuse a
versioned-envelope health read with a legacy one.

## MCP frontend (P58)

`topos mcp serve` is the stdio-only, read-only MCP frontend for local AI CLI
agents. Install it with `pip install 'topos[mcp]'`, then register for example
with `claude mcp add topos -- topos mcp serve`. It consumes the versioned API
only through P63's typed `DaemonClient`; no MCP code implements daemon socket
or envelope handling.

The server probes `hello` before accepting a stdio session. A daemon absent at
startup exits nonzero; loss after startup is returned as a typed
`daemon-unavailable` tool result. The closed tool set is `topos_health`,
`topos_overview`, `topos_entity`, and `topos_history`; all responses have an
enforced 4 MiB aggregate cap. Overview accepts 1..50 rows, history accepts
1..100 points, a registry-validated `metric` name, and a seven-day maximum
`last:Ns` window; entity has fixed 128-metric/64-finding limits, and health has
a fixed 16-component limit. A window containing no frames yields an empty
series, not a selector error.
Selectors use exact EntityKeys or P57's docker name/prefix resolver. Use
`--redact-above public|operational|sensitive` to replace values above the
chosen P52 sensitivity with a typed `__redacted__` marker.

### Acceptance leg (P75)

Run the live-daemon MCP acceptance harness:

```bash
python -m topos.acceptance mcp-smoke [--socket PATH] [--timeout-s FLOAT] [--json] [--pretty-json]
```

The leg starts a real daemon on a temp socket, connects `topos mcp serve` via
the MCP SDK's stdio client, drives all four tools, records the largest response
size, verifies daemon-loss yields a typed error, and verifies a bogus selector
yields `invalid-selector`. Rootless, self-contained lifecycle, skip when the
`topos[mcp]` extra is absent.

## Hardened Versioned Read HTTP Gateway (P67)

Implementation note (2026-07-15): P67 is the current code, but its trusted-
proxy principal header is **not** the accepted browser deployment boundary.
P92 replaces it with D-002's random per-start capability token, same-origin
assets and bounded P88 projections. Do not launch the future React product by
copying the provisional header example below.

`topos gateway serve` is a separate, read-only HTTP process over the typed P63
client. It never opens or speaks the Unix socket protocol itself; every route
uses exactly one of `DaemonClient.request_hello`, `request_current`,
`request_history`, or `request_entity`. It does not expose `health` on this
build because the typed versioned health method belongs to P66; it never falls
back to legacy `request_health`.

### Bind and deployment posture

The default listener is `127.0.0.1:8080`. It never defaults to a wildcard or
LAN address, and it is not advertised or auto-bound by any daemon socket
option. A non-loopback IP is refused unless the operator supplies both
`--allow-non-loopback` and at least one `--principal NAME:CEILING` setting.
That refusal is a startup error, not a warning.

The supported v1 authentication shape is a **trusted local reverse proxy**.
The proxy authenticates the browser user, removes any client-supplied
`X-Topos-Principal`, and supplies its verified principal on its private
loopback hop to the gateway. The gateway trusts this header only when the TCP
peer is loopback; a forwarded identity from any non-loopback peer receives no
principal and no telemetry. Keep the gateway port private to that proxy (for
example with a dedicated local network namespace/firewall policy); loopback
alone is not a substitute for a proxy that overwrites client headers.

The proxy owns TLS termination. Do not expose a direct gateway listener to a
browser or use this header as a browser-controlled credential. A non-loopback
listener is useful only when direct clients remain unauthenticated and a local
TLS/auth proxy performs the private-hop connection.

Example local deployment, with the daemon already listening at its normal
group-protected socket:

```bash
topos gateway serve \
  --daemon-socket /run/topos/observe/topos.sock \
  --principal dashboard:operational
```

`public`, `operational`, and `sensitive` are the only accepted ceilings. Start
with `operational`; grant `sensitive` only where the authenticated viewer is
explicitly allowed to see process-identifying/count telemetry.

### HTTP contract

Only these same-origin `GET` routes exist. Query fields are closed and
duplicate/unknown fields are rejected before reaching the daemon client.

| Route | Typed call | Accepted query fields |
|---|---|---|
| `/v1/hello` | `request_hello()` | none |
| `/v1/current` | `request_current()` | none |
| `/v1/history` | `request_history()` | `limit`, `cursor`, `since_ts`, `until_ts` |
| `/v1/entity` | `request_entity(key)` | exactly `key` (an empty value selects the root entity) |

`POST`, `PUT`, `PATCH`, and `DELETE` (as well as other unsupported methods)
return `405 Allow: GET`; there are no write routes. The gateway emits no CORS
headers, no JSONP, and no cookies, so cross-origin browser reads are not
enabled. If a future authentication mode uses cookies, it must add `Secure`,
`HttpOnly`, and `SameSite` cookie attributes plus Origin checking before it is
enabled.

Responses are compact, sorted JSON. `metrics_meta`, including the closed
`sensitivity` value and its name/unit/glossary metadata, is retained intact. Before
serialization, a metric above the principal's ceiling is replaced (never
dropped) with this typed marker; the metric key and `metrics_meta` remain:

```json
{"pids_max":{"redacted":true,"sensitivity":"sensitive"}}
```

No raw value or raw counter for a redacted metric is present in the HTTP body.
The marker is server-side authorization output, not browser masking.

Typed daemon errors map deterministically: `not_found` to `404`,
`invalid_type`/`out_of_range` (and other malformed request codes) to `400`,
`denied` to `403`, and `unavailable`/`server_busy`/`request_timeout` to `503`.
A daemon connection or protocol failure maps to `502`. Error bodies contain a
short code only; they never include socket paths, exception text, or stack
traces.

## Background Producer

On `toposd run` the daemon creates a `FrameBroker` with the live
collector stream and immediately starts a background producer thread. The
producer continuously advances the collector, publishing each frame into a
bounded sequenced history (`--history-size`, default 120).  The producer runs
independently of read requests; it automatically starts on first access (lazy)
and stops deterministically after the Unix server closes.

If the frame source is exhausted or the producer encounters repeated errors, the
broker captures the failure and returns a `FrameUnavailableError` to clients
without crashing the Unix server. The daemon continues to serve cached frames
from history.

## Attach Client

`topos --attach SOCKET` consumes daemon frames through the same UI model used by
live collection. The attach client is read-only and only speaks the P16 broker
protocol.

When no explicit socket path is given, `--attach` defaults to the packaged
default daemon socket (`/run/topos/observe/topos.sock`).

Common forms:

```bash
topos --attach                              # default socket, interactive UI
topos --attach --once --json                # default socket, one canonical frame
topos --attach --ui-smoke                   # default socket, UI smoke test
topos --attach /run/topos/observe/topos.sock        # explicit socket, interactive UI
topos --attach /run/topos/observe/topos.sock --once --json
topos --attach /run/topos/observe/topos.sock --ui-smoke
```

`--attach --once --json` prints one canonical frame JSON payload and is the
preferred shell/test entry point. The interactive attach path polls the daemon
for current frames and feeds them into the existing TUI path.

## Daemon Current Command

`topos daemon current [--json] [--socket PATH] [--pretty-json]` prints one canonical frame
from the daemon socket as JSON. It is a read-only, scriptable one-shot
alternative to `--attach --once --json`.

```bash
topos daemon current --json                       # default socket, compact JSON
topos daemon current --socket /custom/path.sock   # custom socket
topos daemon current --pretty-json                # indented JSON
```

The command returns non-zero with an error message on stderr if the socket is
missing, unreachable, or returns a protocol error. It never falls back to live
collection.

## Daemon Status Command

`topos daemon status [--socket PATH] [--group NAME] [--json] [--pretty-json]`
checks that the socket node is a socket owned by root with mode `0660`, then
sends hello and current requests to verify client access and the topos frame
protocol. Any socket group is accepted unless `--group` names one: a container
usually cannot resolve the host's group names. Use `toposd doctor` for host-wide
install checks.

```bash
topos daemon status                              # default socket, any group
topos daemon status --json                       # JSON output
topos daemon status --pretty-json                # indented JSON
topos daemon status --group topos-observe        # also require the socket group
topos daemon status --socket /custom/path.sock
```

Exit codes:
- `0` when socket metadata and the current-frame protocol check succeed.
- `1` when socket metadata or protocol check fails (with guidance in the output).
- `2` for argument/usage errors.

The command is read-only: it inspects filesystem metadata and makes one
`hello` and one `current` request over the daemon protocol. It
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

2. **Idempotent restart with verification.** If a topos-owned paddr marker
   already exists (from a prior daemon run), the lifecycle verifies the
   referenced kdamond slot is live (state ``on``, operations ``paddr``) before
   adopting. A stale marker (kdamond is ``off``) is cleaned up; a malformed or
   internally inconsistent marker, a marker pointing at a missing kdamond, or
   a marker whose kdamond runs a different monitoring mode raises a bounded
   startup error.

3. **Foreign-safety.** Non-topos markers and foreign kdamond slots are never
   touched during start, adoption, or stop.

4. **Bounded startup failure.** If the paddr session cannot be started (no free
   kdamond, root required, ownership conflict, stale/malformed marker, or
   kdamond mismatch), the error is logged and the daemon continues without
   paddr status. The read-only daemon is always usable.

5. **Graceful shutdown.** Only a session created by this daemon run is stopped.
   A verified session adopted from an earlier run remains persistent; use
   `topos damon stop --all-mine` for explicit cleanup. The existing
   `stop_owned_sessions` mechanism tears down current-run sessions and removes
   their topos ownership markers.

6. **Audit trail.** Every start and stop produces a JSONL audit event in the
   daemon's state directory (default `~/.local/state/topos/actions.log`).

7. **Config-driven intervals.** The existing `[damon] paddr_sample_us`,
   `paddr_aggr_us`, and `paddr_update_us` settings control the daemon-owned
   session's interval configuration.

## Deployment Checklist

The packaged default rendering lives under `src/topos/assets/systemd/`:

- `topos-observe.socket` creates `/run/topos/observe/topos.sock` as
  `root:topos-observe`, mode `0660`, and passes it to the service as `observe`.
- `topos.service` starts `toposd run --config /etc/topos/toposd.toml` and
  creates `/run/topos/state` as `0700 root:root`.
- `topos.slice` bounds the host daemon, and `topos.sysusers` declares the
  default observe group.

Use `toposd install` with pinned topos, cli-extended, and daemon-lock assets to
install these files and the isolated runtime. Then run `toposd doctor --json`
as root for the host checks and `topos daemon status` from a client account for
socket and protocol access. `toposd install --dry-run` prints the planned
version and service resources without fetching assets or changing host state.

## Troubleshooting Daemon Client Errors

When `topos --attach ...` or `topos daemon current ...` fails, the CLI prints
the original error followed by actionable guidance:

### Default socket (`/run/topos/observe/topos.sock`)

```
cannot connect to /run/topos/observe/topos.sock: No such file or directory

Try: toposd doctor --check connect
If the daemon is not installed: see toposd install --help (docs/DAEMON.md)
```

### Custom socket

```
cannot connect to /tmp/custom.sock: Connection refused

Try: topos daemon status --socket /tmp/custom.sock
```

### Protocol/response errors

```
daemon at /run/topos/observe/topos.sock returned malformed JSON on line 1

Check that the process at the socket is a compatible topos daemon
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

The broker uses bounded in-memory history (`--history-size`, defaulting to 120
frames). `HistoryConfig.full_resolution_seconds` defaults to 300 (five minutes
at the default 5s interval), reconciling the prior four-hour default per D-005.

P91 (`topos.daemon.persist.PersistentHistoryStore`) adds an on-disk,
age-and-byte-capped persistent tier, configured under `[history.daemon]`
(`PersistConfig`) and disabled by default pending the O10 resource-cost
measurement (`nyxloom-trove/archive/P91-REPORT.md`, `MEASUREMENTS.md`). When enabled,
canonical frames are batched into small segment files (JSON-lines, optionally
zstd-compressed) and published atomically: written to a temp path, fsynced,
then renamed into `<dir>/segments/`, so a crash mid-write can never leave a
torn segment visible. The index (`<dir>/index.json`) is republished the same
way. Age (`max_age_days`) and byte (`max_size_mb`) caps are enforced
simultaneously, oldest-segment-first, both at write time and again at startup
recovery. Recovery re-hashes every segment against its recorded checksum;
a mismatch, missing, or unparseable segment is quarantined under
`<dir>/quarantine/` (never deleted) and reported as an explicit gap between
its still-valid neighbours rather than silently returning wrong data. A
disk-full or read-only `<dir>` degrades the store (visible via `stats()`
as `degraded`/`degraded_reason`) without raising, so collection and the RAM
tier are unaffected. `PersistentHistoryStore.stats()` reports byte/frame
counts, oldest/newest timestamps, evictions, quarantine count, gaps, write
errors, recovery state and compression ratio; a `PersistentHistoryFrameSource`
(`topos.query`) hands the same store to P88's query engine without a second
aggregation path. Lifecycle facts are intended to share this store and these
caps (D-008) rather than create a second persistence engine, which remains
future work.
