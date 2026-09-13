#!/usr/bin/env bash
# Thin wrapper: soulmask-mempress.sh operating on the ZSwapMax0 (pak/incompressible) slice.
# Usage identical to soulmask-mempress.sh; targets soulmask_tmpfs-ZSwapMax0.slice cgroup.
exec /usr/local/sbin/soulmask-mempress.sh --slice soulmask_tmpfs-ZSwapMax0 "$@"
