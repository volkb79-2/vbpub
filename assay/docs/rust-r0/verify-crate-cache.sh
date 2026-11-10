#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    printf 'usage: verify-crate-cache.sh MANIFEST\n' >&2
    exit 2
fi

manifest=$1
if [ ! -f "$manifest" ]; then
    printf 'ERROR: crate cache manifest is missing: %s\n' "$manifest" >&2
    exit 1
fi
if [ ! -s "$manifest" ]; then
    printf 'ERROR: crate cache manifest is empty: %s\n' "$manifest" >&2
    exit 1
fi

failed=0
while IFS= read -r entry || [ -n "$entry" ]; do
    expected=${entry%%  *}
    path=${entry#*  }
    if [ "$expected" = "$entry" ] || [ "$path" = "$entry" ] || [ -z "$path" ]; then
        printf 'ERROR: malformed crate cache manifest entry: %s\n' "$entry" >&2
        failed=1
        continue
    fi

    crate=${path##*/}
    if [ ! -f "$path" ]; then
        printf 'ERROR: cached crate is missing: %s\n' "$crate" >&2
        failed=1
        continue
    fi

    digest=$(sha256sum "$path") || {
        printf 'ERROR: cannot checksum cached crate: %s\n' "$crate" >&2
        failed=1
        continue
    }
    actual=${digest%% *}
    if [ "$actual" != "$expected" ]; then
        printf 'ERROR: cached crate checksum mismatch: %s expected %s got %s\n' \
            "$crate" "$expected" "$actual" >&2
        failed=1
    fi
done < "$manifest"

exit "$failed"
