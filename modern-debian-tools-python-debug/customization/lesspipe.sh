#!/bin/sh
set -e

if [ $# -lt 1 ]; then
    exec cat
fi

file="$1"
if [ ! -r "$file" ] || [ -d "$file" ]; then
    exec cat -- "$file"
fi

BAT_BIN="$(command -v batcat 2>/dev/null || command -v bat 2>/dev/null || true)"
if [ -n "$BAT_BIN" ]; then
    exec "$BAT_BIN" --color=always --style=plain --paging=never -- "$file"
fi

exec cat -- "$file"
