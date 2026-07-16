# T0b — daemon-wide default cgroup parent (dedicated Wings nodes only)

Zero-code placement for **dedicated** Wings nodes: the Docker daemon itself
places every container it creates under `wings.slice`. Gives the whole game
tier a real, arithmetically effective `MemoryMin` floor plus ceilings/weights
and per-tier PSI — with stock, unpatched Wings.

**Do NOT use on mixed-use hosts** (like the current 16 GB case-study node):
every non-Wings container on the same daemon would land under `wings.slice`
too. That host should use T1 instead.

## Setup

1. Install and load the slice:

   ```bash
   cp wings.slice /etc/systemd/system/
   systemctl daemon-reload && systemctl start wings.slice
   systemctl show wings.slice -p FragmentPath -p MemoryMin -p MemoryHigh   # pre-flight
   ```

2. Merge `daemon.json.snippet` into `/etc/docker/daemon.json`, then
   `systemctl restart docker` (brief outage for all containers on the node —
   plan it).

3. Recreate existing containers (placement is create-time only):
   stop server in panel → `docker rm <uuid>` → start in panel.

4. Verify:

   ```bash
   docker run --rm --cgroupns=host busybox cat /proc/self/cgroup
   # expect: 0::/wings.slice/docker-<id>.scope
   ```

5. Exempt the Wings management container itself via T0a's per-service
   `cgroup_parent: wings-mgmt.slice` (a per-container `--cgroup-parent` /
   compose key overrides the daemon default).

## Limits of this tier

Per-server placement/floors are not expressible — every container shares one
slice. Per-server guarantees need T2 (patched Wings).
