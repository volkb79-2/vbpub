# `wingsctl.py` — a minimal CLI for the Wings node API

Incidental to the cgroups work: this is a general-purpose wrapper around the
Wings HTTP API, kept in this repository only because it is convenient to have on
the same nodes. It does not read, write or know anything about slices, cgroup
parents or the patch series.

What it is for: controlling servers on a node **directly**, without the panel —
useful when the panel is unreachable, when you want a scriptable stop before a
container recreation (SETUP.md §5), or when you simply want the node's own view
of server state. Wings has no per-server power CLI, but its HTTP API — the same
one the panel drives — accepts the node token, which is all this script needs.

## Install

Single file, stdlib only, no pip. Place it next to the `docker-compose.yml` of
the Wings container (i.e. on the node, as root) and mark it executable:

```bash
install -m 0755 wingsctl.py /root/<wings-compose-dir>/wingsctl.py
```

Root is required for one reason only: reading Wings' `config.yml`.

## Authentication

The node token is read at runtime from the Wings config (`--config`, default
`/etc/pterodactyl/config.yml`) and is never printed — not in output, not in
errors. The parser takes the **top-level** `token:` and `token_id:` keys only.

Modern Wings authorizes the node API with the **bare** `token` value. Sending the
panel-style `token_id.token` form returns **403**, not 401 — Wings distinguishing
"header present, wrong value" from "no header at all", which is why a 403 here is
an auth-*format* symptom rather than a permissions one. The script therefore
sends the bare token first and retries with the combined `token_id.token` form
(older builds) only on 401/403. Any other status is treated as a real answer and
returned as-is.

## Pointing it at a node

```bash
export WINGS_URL=https://<node-hostname>          # or pass --url per invocation
./wingsctl.py --url https://<node-hostname> list
```

Resolution order is `--url` → `$WINGS_URL` → a hardcoded default baked into the
script. The default is one specific deployment's reverse-proxy route, so **set
`WINGS_URL` on every node** rather than relying on it. The proxied route normally
carries a valid certificate; `--insecure` skips TLS verification and exists only
for bypassing the proxy or a broken chain.

All three options (`--url`, `--config`, `--insecure`) are global and must appear
**before** the subcommand.

## Subcommands

```bash
./wingsctl.py list                        # every server on the node: uuid, state, suspended, memory
./wingsctl.py status <server-uuid>        # the same summary for one server
./wingsctl.py power  <server-uuid> stop   # start | stop | restart | kill
./wingsctl.py logs   <server-uuid> 100    # recent console output; line count optional (default 50)
```

| Command | Endpoint | Notes |
|---|---|---|
| `list` | `GET /api/servers` | Prints a count plus one line per server. Accepts either a bare array or a `data:` envelope. |
| `status` | `GET /api/servers/<uuid>` | Same one-line summary. |
| `power` | `POST /api/servers/<uuid>/power` | Body `{"action": …}`. Treats 200/202/204 as accepted and tells you to poll. |
| `logs` | `GET /api/servers/<uuid>/logs?size=<n>` | Line count defaults to 50. If the response is not a `data:` array of lines it is printed raw — the response shape of this endpoint varies by Wings version, so treat it as best-effort. |

`list` and `status` deliberately print only non-sensitive fields: a server's
configuration object embeds egg environment variables (RCON passwords, API keys),
so the summary is restricted to uuid, state, suspension and memory use.

## Power actions are asynchronous

`power` returns **HTTP 202 immediately**. The response only means Wings accepted
the request; the egg's configured stop sequence then runs asynchronously and can
take as long as the egg's stop command and timeout allow. Never treat the 202 as
confirmation of a stop. To confirm one, either poll:

```bash
./wingsctl.py status <server-uuid>        # until state=offline
```

or wait on the container itself from the node, which also gives you the exit
code:

```bash
docker wait <server-uuid>
```

## Stopping containers the right way

Wings runs a crash-detection watchdog: when a container exits while Wings'
**desired** state for that server is still "running", Wings classifies the exit
as a crash and restarts the server.

That is exactly what happens if you shut a game down from the inside — e.g. an
RCON `SaveAndExit`. The game process exits, and in the deployments observed here
it exits nonzero (a Soulmask `SaveAndExit` reports `World is closing (110)`, and
the resulting container exit has also been observed as `130`); but Wings was
never told, its desired state is unchanged, and the watchdog brings the server
straight back up. The exit codes above are observed values, not a contract — do
not build logic on them.

The fix is not to make the exit look clean. It is to flip the desired state
first, so there is nothing for the watchdog to restore:

```bash
./wingsctl.py power <server-uuid> stop     # desired state -> offline
docker wait <server-uuid>                  # confirm the actual exit
```

After this the server stays down: Wings reports `state=offline` and performs no
watchdog restart, because offline is now what it wants. Use in-game RCON
shutdowns for *graceful save-and-quit inside* a stop you have already asked Wings
for — never as the stop itself.
