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
