#!/usr/bin/env bash
# Thin wrapper: soulmask-mempress.sh operating on the PAK slice.
# Usage identical to soulmask-mempress.sh; targets soulmask-paks.slice cgroup.
exec /usr/local/sbin/soulmask-mempress.sh --slice pak "$@"
