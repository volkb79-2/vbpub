#!/bin/sh
# Run cargo from thrashguard's own directory, offline and locked, after proving
# that the lock still names only this workspace's own crate.
#
# Why this exists instead of reusing hostobs/tools/cargo-offline.sh: the Rust
# gate image's crate cache identity is the registry set of ITS lock
# (`hostobs/Cargo.lock`, baked as the image label
# `org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256`). The image's
# `/usr/local/bin/verify-crate-cache` is a BIDIRECTIONAL comparison: it refuses
# a lock with an archive the cache does not hold (right, that needs a rebuild)
# AND a lock without an archive the cache does hold. Pointed at thrashguard's
# lock it therefore reports a false mismatch for a perfectly consistent lock,
# because thrashguard declares no third-party dependency and its registry set is
# empty by design (design §5: every dependency needs its own justification).
#
# So the check lives here, is narrower on purpose and is still a closed check:
# it refuses a lock that names any package with a `source` (a registry or git
# package), because such a package needs the image rebuilt before it can build
# offline. If thrashguard ever needs a crate, the correct change is to add that
# crate to the image's lock-keyed cache, not to relax this check.
set -eu

if [ "$#" -eq 0 ]; then
    printf 'usage: cargo-offline.sh CARGO-ARGS...\n' >&2
    exit 2
fi
if [ ! -f Cargo.toml ] || [ ! -f Cargo.lock ]; then
    printf 'ERROR: cargo-offline.sh must run from a workspace with Cargo.toml and Cargo.lock\n' >&2
    exit 2
fi

here=$(cd "$(dirname "$0")" && pwd -P)
python3 -I "$here/verify-lock.py" "$PWD/Cargo.lock"
export CARGO_NET_OFFLINE=true
exec cargo "$@"
