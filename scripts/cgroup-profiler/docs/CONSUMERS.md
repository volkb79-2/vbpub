# Consuming the cgroup-profiler daemon

This is the adoption guide: commands here are intended to be copied by an
operator or a run-gate integration. The daemon control contract is major
version 1; summaries returned by `ctl stop` use schema 1. For the rationale,
see [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md#daemon-safety-and-placement).

## Start the host daemon

From this directory, build the image and start the one host-scoped daemon:

```bash
python3 build-push.py --build
ciu up --dir .
docker exec cgprofile-host-daemon cgprofile ctl version --json
```

The daemon must have the host cgroup and PID view supplied by the shipped
stack. It has no Docker socket and does not accept `--cap`; it observes the
target and writes session data under `/var/lib/cgprofile/sessions`.

The version response has the current wire shape:

```json
{
  "ok": true,
  "contract": 1,
  "cgprofile": "1.0.0",
  "daemon": {
    "name": "cgprofile-host-daemon",
    "started_at": "2026-09-12T10:15:00Z",
    "damon": "available",
    "damon_default": "on",
    "sessions_live": 0,
    "max_sessions": 16
  }
}
```

## Attach a run-gate lane

Run-gate must start the lane first, inspect its immutable 64-hex container ID,
then ask the daemon to profile it. Generate a fresh token per lane attempt and
pass it through the lane as `RUN_GATE_PROFILE_SESSION`; the token makes a
retry-safe `(container_id, token)` start idempotent. The `meta` object is
required and its `kind` is either `command` or `assay`.

The following is a minimal shell-shaped integration. Replace the image,
command, and metadata values for the consuming project:

```bash
set -euo pipefail
: "${CGROUP_PARENT_DEV_BACKGROUND:?set the real host dev-background slice}"

PROFILE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
LANE_NAME="rg55-example-lane"
docker run -d --name "$LANE_NAME" \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e RUN_GATE_PROFILE_SESSION="$PROFILE_TOKEN" \
  example-lane-image:local sleep 3600

CONTAINER_ID="$(docker inspect --format '{{.Id}}' "$LANE_NAME")"
META="$(python3 -c 'import json; print(json.dumps({
  "lane": "rg55-example-lane", "project": "example-project",
  "worktree": "/workspaces/example", "commit": None,
  "run_gate_revision": 1, "kind": "command", "expected": None}))')"

START_JSON="$(docker exec cgprofile-host-daemon cgprofile ctl start \
  --target "containerid:${CONTAINER_ID}" \
  --scope container --token "$PROFILE_TOKEN" --damon on --interval 1.0 \
  --meta "$META" --json)"
SESSION_ID="$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["session"])' <<<"$START_JSON")"

docker exec "$LANE_NAME" ./run-lane.sh
LANE_STATUS=$?

STOP_JSON="$(docker exec cgprofile-host-daemon cgprofile ctl stop "$SESSION_ID" --json)"
printf '%s\n' "$STOP_JSON"
exit "$LANE_STATUS"
```

In production code, put the stop call in the lane's `finally`/cleanup path so
it runs when the lane fails. Read the daemon command's exit status directly:
`0` is a valid response, `2` is a daemon-declared contract error, and `3` is
unreachable or malformed-daemon state. Do not pipe the command through a
pager when deciding whether the stop succeeded.

Use `--scope container-shared` when the target cgroup is shared with unrelated
work and the summary must report sampled-max memory plus deltas. Use
`--scope container` for a lane-owned cgroup; its `memory.peak` and absolute
counters have the schema-1 semantics documented in the contract.

## Closed values and response evidence

Consumers may type these public values:

| field | values |
|---|---|
| `scope` | `container`, `container-shared` |
| start `damon` request | `on`, `off` |
| start response `damon` status | `on`, `off`, `unavailable:<reason>` |
| `meta.kind` | `command`, `assay` |
| response `contract` | `1` |
| summary `schema` | `1` |

Only a non-null token enables idempotent reuse. Omitting it deliberately starts
an independent session, so two no-token starts count separately against
`max_sessions`.
