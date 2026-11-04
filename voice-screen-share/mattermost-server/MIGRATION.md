# Migrating the live Mattermost state to voice-screen-share/mattermost-server

Run once, by the controller, in the MAIN checkout (`/workspaces/vbpub`), right after
the branch that moves `mattermost-server/` into `voice-screen-share/` is merged.
Nothing here is run by the branch itself.

## What moves and why

Git carries the tracked files. The merge deletes the tracked files of the old
`mattermost-server/` and adds them under `voice-screen-share/mattermost-server/`, but
the gitignored state stays behind in the old directory:

| State | Kind | Handling |
|---|---|---|
| `.ciu/` (secrets incl. `mattermost/postgres_password`, webhook URLs, PATs, overlay) | bind-mounted secret store | `mv` (the DB password lives in the postgres data; it must stay paired with it) |
| `vol-postgres-data/` (uid 70, mode 0700) | postgres bind dir | `mv` only, never copied; if the plain rename is refused (a directory changing parent needs write permission on itself) the script repeats the same-filesystem rename as root through `host-escape` |
| `vol-mattermost-config/`, `vol-mattermost-logs/` | bind dirs | `mv` |
| `ciu.env`, `ciu.compose.yml`, `ciu.global.toml`, `ciu.instance.generated.toml` | generated, hold the old path and instance id | set aside as `*.pre-vss-move`; `ciu env generate` writes fresh ones |
| docker named volumes `mattermost-3fnqqi-mattermost-server_{mattermost-data,mattermost-plugins,mattermost-client-plugins}` | project-prefixed | phase 2: copied to the new prefix (optional but recommended: uploads live in `mattermost-data`) |

`ciu` derives the instance id from the physical checkout path
(`/home/vb/volkb79-2/vbpub/mattermost-server` -> `3fnqqi`, the new
`/home/vb/volkb79-2/vbpub/voice-screen-share/mattermost-server` -> `e0nixp`, computed
with ciu's own `workspace_id_for_path`). Container names (`mattermost-<id>-mattermost`,
`...-db`), the compose project (`mattermost-<id>-mattermost-server`) and the instance
network therefore change with the move. The database content, secrets, webhook URLs and
PATs do not, because they are in the moved directories.

## Steps

```bash
cd /workspaces/vbpub
git status --short voice-screen-share mattermost-server      # merge is in; stack tracked files present
S=voice-screen-share/mattermost-server
# 0. the old stack is already down (exited); the script refuses if a container of the old id runs.
bash $S/migrate-to-voice-screen-share.sh --dry-run            # read the plan
bash $S/migrate-to-voice-screen-share.sh --apply              # phase 1: mv state, verify metadata
ciu env generate --root-folder $S                             # new instance id, network, fresh generated files
bash $S/migrate-to-voice-screen-share.sh --copy-named-volumes --dry-run
bash $S/migrate-to-voice-screen-share.sh --copy-named-volumes # phase 2: named volumes -> new prefix
ciu up --dir $S --yes --dry-run --root-folder $S                 # review, then the real up
```

Verify: `ls -ld $S/vol-postgres-data` (owner uid 70, mode 700, unchanged), `ls -l $S/.ciu/secrets`,
the new container names with `docker ps --filter name=mattermost-e0nixp`, the
`/api/v4/system/ping` through the edge, and that the existing channels/users are there.
Calls: `mmctl plugin list` shows `com.mattermost.calls` enabled; edge 8443 TCP+UDP is
already published on the live host.

Consumers that use the container name or secrets paths: `nyxloom` secrets examples now
read `voice-screen-share/mattermost-server/.ciu/secrets/...`; the nyxloom
`[intake_bridge] container` is deliberately unset (resolve from `docker ps` for the new id).

## Rollback

Before `ciu up`: `bash $S/migrate-to-voice-screen-share.sh --rollback` moves everything back
(same renames reversed) and restores the `*.pre-vss-move` files; then check out the
pre-merge commit so the tracked files are back at the old path. The old containers, the old
named volumes and the old network were never touched, so the old stack still starts from
the old path. After phase 2 the old named volumes are still intact (copied, not moved).

## Cleanup of the old instance (later, by EXACT name only)

When the new stack has been used for a while:
`docker rm mattermost-3fnqqi-mattermost mattermost-3fnqqi-mattermost-db`,
`docker volume rm mattermost-3fnqqi-mattermost-server_mattermost-data` (and `_plugins`,
`_client-plugins`), `docker network rm mattermost-server-cmqemn-3fnqqi-network`. Never by
filter, never `ancestor=`.
