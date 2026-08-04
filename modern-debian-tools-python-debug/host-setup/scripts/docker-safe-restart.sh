#!/usr/bin/env bash
# Safe Docker restart helper — cleans up stale sockets before restarting
#
# This script handles the edge cases that can occur when restarting docker:
# - stale socket files from previous restarts
# - lingering tmpfs mounts on socket paths
# - containers that hold references to old socket inodes
#
# Usage:
#   sudo ./docker-safe-restart.sh
#
# The script will:
#   1. Unmount any stale tmpfs/bind mounts on /run/docker.sock
#   2. Remove stale socket files
#   3. Restart docker.socket and docker.service
#   4. Verify the restart succeeded
set -euo pipefail

echo "=== Docker safe restart ==="
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "Stopping docker.socket..."
systemctl stop docker.socket 2>/dev/null || true

echo "Cleaning up stale mounts and socket files..."
# Forcefully unmount any tmpfs or bind mounts on the socket paths
umount -l /run/docker.sock 2>/dev/null || true
umount -l /run/docker-api/docker.sock 2>/dev/null || true

# Remove stale socket files (systemd can't overwrite these)
rm -f /run/docker.sock 2>/dev/null || true
rm -f /run/docker-api/docker.sock 2>/dev/null || true

echo "Restarting docker.socket and docker.service..."
systemctl daemon-reload
systemctl start docker.socket
systemctl restart docker.service

echo "Verifying docker is running..."
sleep 1
if systemctl is-active --quiet docker.service && systemctl is-active --quiet docker.socket; then
  echo "✓ Docker restarted successfully"
  echo "✓ /run/docker.sock (classic socket)"
  ls -la /run/docker.sock 2>/dev/null || echo "  (not found)"
  echo "✓ /run/docker-api/docker.sock (directory-mountable socket)"
  ls -la /run/docker-api/docker.sock 2>/dev/null || echo "  (not found)"
  echo ""
  echo "Running containers:"
  docker ps -q | wc -l
  exit 0
else
  echo "✗ Docker restart failed"
  systemctl status docker.service docker.socket
  exit 1
fi
