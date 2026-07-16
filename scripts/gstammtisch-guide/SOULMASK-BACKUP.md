# Soulmask — offline backup of world.db and game config

Scope: cold (offline) backup of the world database and gameplay config from the
Pterodactyl server volume to the native host, while the game server is
**stopped**. Companion to `SOULMASK.md` (RCON-based *online* flush:
`BackupDataBase world` / `SaveWorld`) — this document is for the stopped-server
case, e.g. before maintenance, migrations, or the T1 cgroup rollout's container
recreation.

## Facts

- Wings stores server data as a **host bind mount** — the files live directly
  on the host at `/var/lib/pterodactyl/volumes/<uuid>/`; no container needs to
  run to read them.
- Server UUID (prod Soulmask): `b87c0a5b-2387-4a1c-8863-ff23e6800a1d`
- Inside the volume:
  - world database: `WS/Saved/Worlds/…/world.db` (level-dependent subfolder —
    discover it, don't hardcode; the game's rotating backups live nearby)
  - cluster accounts (main server only, if clustered): `WS/Saved/Accounts/account.db`
  - gameplay config: `WS/Saved/GameplaySettings/GameXishu.json`
- Files are owned by uid/gid **988** (the container user). Preserve ownership
  (`cp -a`) so restores don't need fixing.

## Preconditions

The server must be truly stopped (panel "Stop", graceful-stop path flushes the
in-memory DB to `world.db` on the way down). Verify on the node:

```bash
UUID=b87c0a5b-2387-4a1c-8863-ff23e6800a1d
docker ps --filter "name=$UUID" --format '{{.Names}} {{.Status}}'
# must print nothing (a stopped/created container is fine; "Up …" is NOT)
```

## Backup (run on the node, as root)

```bash
UUID=b87c0a5b-2387-4a1c-8863-ff23e6800a1d
VOL=/var/lib/pterodactyl/volumes/$UUID
DEST=/root/backups/soulmask/$(date +%F-%H%M%S)

# 1. Locate the database files (level folder name varies per map/DLC)
find "$VOL/WS/Saved" -xdev \( -name world.db -o -name account.db \) -ls

# 2. Copy: world DBs (incl. the game's own rotating backups next to them),
#    accounts if present, and the gameplay config — ownership/mtimes preserved
mkdir -p "$DEST"
( cd "$VOL" && cp -a --parents WS/Saved/Worlds "$DEST"/ )
( cd "$VOL" && cp -a --parents WS/Saved/GameplaySettings/GameXishu.json "$DEST"/ )
( cd "$VOL" && [ -d WS/Saved/Accounts ] && cp -a --parents WS/Saved/Accounts "$DEST"/ || true )

# 3. Manifest + checksums (restore-time verification)
( cd "$DEST" && find . -type f -exec sha256sum {} + > SHA256SUMS )
ls -lhR "$DEST" | head -30
```

Optional single-archive form instead of step 2 (same content, one file):

```bash
tar -C "$VOL" -czf "$DEST/soulmask-saved-$(date +%F).tar.gz" \
    WS/Saved/Worlds WS/Saved/GameplaySettings/GameXishu.json \
    $( [ -d "$VOL/WS/Saved/Accounts" ] && echo WS/Saved/Accounts )
```

Optional integrity check of the copied DB (host usually has no sqlite3; use a
throwaway container — checks the **copy**, never the live file):

```bash
docker run --rm -v "$DEST":/b:ro alpine:3 sh -c \
  'apk add -q sqlite && find /b -name world.db -exec sqlite3 {} "PRAGMA integrity_check;" \;'
# expect: ok
```

### Fallback: extraction via docker (if the host path is ever inaccessible)

The stopped Wings container still exists in the "Exited/Created" state, and
`docker cp` works on stopped containers:

```bash
docker cp "$UUID:/home/container/WS/Saved/Worlds" "$DEST/WS-Saved-Worlds"
docker cp "$UUID:/home/container/WS/Saved/GameplaySettings/GameXishu.json" "$DEST/"
```

Not needed on this node (bind mount is authoritative); documented for
completeness / other deployments.

## Restore (outline)

1. Stop the server (panel), verify as above.
2. Copy the tree back into `$VOL` at the same relative paths
   (`cp -a` / `tar -xzf … -C "$VOL"`).
3. Ensure ownership: `chown -R 988:988 "$VOL/WS/Saved"`.
4. Verify against `SHA256SUMS`, then start from the panel.

Cluster caveat (see `SOULMASK.md` §cluster): if the setup has become a
cluster, `world.db` and the main's `account.db` are a **matched pair** —
back up and restore them together, main server stops last / starts first.

## Notes

- Offline copies taken while the process is stopped are consistent by
  construction; never copy `world.db` while the server runs (use the RCON
  flush sequence in `SOULMASK.md` for online backups instead).
- `WS/Content/Paks` (game assets, possibly on the tmpfs ramdisk overlay) is
  deliberately excluded: reinstallable, not save data.
- Keep at least the last few `$DEST` snapshots; world.db size grows with the
  world, so prune by count, not blindly by age.
