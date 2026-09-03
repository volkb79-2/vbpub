#!/usr/bin/env bash
exec python3 "$(dirname "$(readlink -f "$0")")/exec-soulmask-rcon.py" "$@"
