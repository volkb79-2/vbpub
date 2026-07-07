#!/usr/bin/env bash
exec python3 "$(dirname "$(readlink -f "$0")")/soulmask-zswap-monitor.py" "$@"
