# groop Daemon Spike

P16 defines a small read-only broker, not the final daemon product.

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
