# Docker Socket Restart Guide

## Overview

The MDT host-setup now includes robust handling for Docker socket lifecycle events. This guide explains what changed, why, and how to use the new features.

## The Problem

When restarting Docker to activate changes (like the new `/run/docker-api/docker.sock` socket), the restart could fail if:

1. **Stale socket files** — leftover from a previous incomplete restart, which systemd cannot overwrite
2. **Stale tmpfs mounts** — bind mounts or tmpfs entries on the socket paths from failed restarts
3. **Lingering processes** — containers still holding references to old socket inodes

This would leave Docker in a broken state with the error: `Failed to create listening socket: Address already in use`

## What Changed

### 1. Automatic Cleanup in Socket Configuration

**File:** `/etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf`

The socket unit now includes pre-startup cleanup steps:

```bash
ExecStartPre=/bin/sh -c 'umount -l /run/docker.sock 2>/dev/null; rm -f /run/docker.sock; umount -l /run/docker-api/docker.sock 2>/dev/null; exit 0'
```

This atomically:
- Unmounts any stale tmpfs/bind mounts on `/run/docker.sock`
- Removes the stale socket file
- Unmounts any stale mounts on `/run/docker-api/docker.sock`
- Ignores errors (if files don't exist, it continues anyway)

### 2. Optional Automatic Restart in install.sh

**File:** `install.sh`

The installer now accepts an optional `--restart-docker` flag:

```bash
sudo ./install.sh --force --restart-docker
```

This automatically restarts Docker at the end of configuration updates, rather than leaving it as a manual step.

### 3. Safe Restart Helper Script

**File:** `/usr/local/sbin/mdt-docker-safe-restart`

A standalone helper script is now available for manual Docker restarts:

```bash
sudo mdt-docker-safe-restart
```

This script:
- Stops docker.socket cleanly
- Cleans up stale mounts and socket files
- Restarts both docker.socket and docker.service
- Verifies success and shows container count

## Usage

### Option A: Automatic Restart During install.sh

Use the `--restart-docker` flag to restart Docker immediately:

```bash
sudo ./install.sh --force --restart-docker
```

**When to use:** During initial setup or maintenance windows.

### Option B: Manual Restart with Helper Script

Restart Docker anytime using the helper:

```bash
sudo mdt-docker-safe-restart
```

**When to use:** After changes to `/etc/docker/daemon.json` or socket configuration, during planned maintenance.

### Option C: Manual systemctl Restart

The cleanup is now built into systemd configuration, so a manual restart will also work:

```bash
sudo systemctl restart docker.socket docker.service
```

**When to use:** If you prefer native systemd operations.

## What Gets Restarted

**Warning:** Restarting Docker disrupts ALL running containers. Containers do not pause/resume; they are stopped and restarted.

To minimize disruption:
- Restart during a maintenance window
- Ensure no critical work is running in containers
- Use `docker ps` to verify the container count before/after

## Verification

After restart, verify both sockets are available:

```bash
# Check classic socket
ls -la /run/docker.sock

# Check directory-mountable socket
ls -la /run/docker-api/docker.sock

# Verify Docker is running
docker ps -q | wc -l
```

Or use the helper script, which does this automatically:

```bash
sudo mdt-docker-safe-restart
```

## Socket Behavior After Restart

Both sockets are now active:

1. **`/run/docker.sock`** — The traditional Docker socket
   - Used for: `docker` CLI commands on the host
   - Bind-mounted by: Legacy devcontainer configurations (`-v /run/docker.sock:/run/docker.sock`)
   - Issue with direct mount: Pins the inode, breaks when daemon restarts

2. **`/run/docker-api/docker.sock`** — New directory-mountable socket
   - Located in: `/run/docker-api/` (a directory)
   - Used for: Devcontainer `docker-outside-of-docker` with directory bind-mount
   - Mount example: `-v /run/docker-api:/run/docker-api` (or similar)
   - Benefit: Survives daemon restarts because new socket files in already-mounted directories are immediately visible

For devcontainers, update your configuration to use:

```json
{
  "mounts": [
    "source=/run/docker-api,target=/run/docker-api,type=bind,readonly=false"
  ],
  "remoteEnv": {
    "DOCKER_HOST": "unix:///run/docker-api/docker.sock"
  }
}
```

## Edge Cases Handled

The socket cleanup is designed to handle:

- ✓ Stale socket files from incomplete restarts
- ✓ tmpfs mounts on socket paths (from nested restarts or container mounts)
- ✓ Multiple failed restart attempts leaving multiple mount layers
- ✓ Permission issues on stale files (umount -l bypasses these)
- ✓ Sockets or directories that don't exist yet (errors are ignored)

## Troubleshooting

### Docker still won't start after `--restart-docker`

Check the service status:

```bash
systemctl status docker.socket docker.service
journalctl -u docker.socket -u docker.service -n 50
```

Common causes:
- Containers with elevated privileges holding socket references
- SELinux/AppArmor blocking operations
- Filesystem issues on `/run`

### Container access lost after restart

Docker daemon restarts cause ALL containers to stop and restart. This is normal, not an error. The cleanup ensures a clean restart, but applications in containers need to reconnect to services.

If a container is stuck in a restart loop, check its logs:

```bash
docker logs <container-id>
```

### One of the sockets is missing

Both sockets should exist after startup:

```bash
ls -la /run/docker.sock /run/docker-api/docker.sock
```

If one is missing, check systemd status and logs as shown above.

## Technical Details

### Why Both Sockets?

The two-socket setup allows:
- Host-side `docker` CLI to use the traditional socket
- Devcontainers to use a directory-mountable socket that survives restarts
- Gradual migration from socket-file mounting to directory mounting

### Why the Cleanup Step?

systemd's socket unit cannot:
- Overwrite existing socket files
- Unmount already-mounted paths

So cleanup must happen before the socket unit tries to bind. The cleanup is non-fatal (exits 0 on success or error) to ensure socket startup isn't blocked by missing files.

### Cleanup Order

The cleanup runs in this order:

1. Unmount `/run/docker.sock` (lazy unmount, non-fatal)
2. Remove `/run/docker.sock` file (non-fatal)
3. Unmount `/run/docker-api/docker.sock` (lazy unmount, non-fatal)
4. Create `/run/docker-api` directory
5. Fix ownership and permissions

This order ensures maximum cleanup without hard failures.

## See Also

- `README.md` — Overall MDT host-setup overview
- `install.sh --help` — Installer usage and options
- `/etc/mdt/host-setup.env` — Configuration variables
