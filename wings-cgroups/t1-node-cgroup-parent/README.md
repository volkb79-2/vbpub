# T1 — node-wide `docker.cgroup_parent` (patched Wings)

The Wings code lives in the patch stack:
`../patchstack/patches/pterodactyl-v1.13.1/0001-*.patch` (and the Pelican
port). This folder holds the host-side artifacts and the deployment order.

T1 places **all** server + installer containers on a node under one named
slice. Works on shared nodes (unlike T0b). On a single-game-server node this
already delivers per-server floors — the node slice *is* the server slice.

## Deployment order (footgun-proof; details: proposal Appendix A)

1. Build the patched image: `../patchstack/scripts/build-image.sh pterodactyl`
2. Install the slice unit **first**:

   ```bash
   cp wings.slice /etc/systemd/system/          # or soulmask.slice on our node
   systemctl daemon-reload && systemctl start wings.slice
   ```

3. **Mandatory pre-flight** — a missing/typo'd unit degrades silently to a
   limit-less transient slice (path looks right, guarantees absent):

   ```bash
   systemctl show wings.slice -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh
   # FragmentPath MUST point at your unit file; values MUST match the plan.
   ../test/smoke-placement.sh wings.slice        # path + effective-file check
   ```

4. Add to `/etc/pterodactyl/config.yml` (see `config.yml.snippet`), point the
   compose file at the patched image, `docker compose up -d --force-recreate`.
5. Recreate each game container in a planned window (placement is create-time
   only): panel stop → `docker rm <uuid>` (bind-mounted data is safe) → panel
   start.
6. Verify: `cat /proc/<gamepid>/cgroup` → `/wings.slice/docker-<id>.scope`,
   and effective `/sys/fs/cgroup/wings.slice/memory.min` etc.

Rollback: revert image + config key, recreate Wings. Stock Wings ignores the
unknown YAML key; already-placed containers keep their placement until next
recreation (no second outage).
