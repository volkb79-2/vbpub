#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
    printf 'usage: cargo-offline.sh CARGO-ARGS...\n' >&2
    exit 2
fi
if [ ! -f Cargo.toml ] || [ ! -f Cargo.lock ]; then
    printf 'ERROR: cargo-offline.sh must run from a workspace with Cargo.toml and Cargo.lock\n' >&2
    exit 2
fi

/usr/local/bin/verify-crate-cache "$PWD/Cargo.lock"
export CARGO_NET_OFFLINE=true
exec cargo "$@"
