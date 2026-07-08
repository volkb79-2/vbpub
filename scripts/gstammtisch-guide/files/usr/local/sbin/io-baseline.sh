#!/usr/bin/env bash
# Keep the legacy entrypoint so existing callers keep working.
exec "$(dirname "$0")/io-baseline.py" "$@"
