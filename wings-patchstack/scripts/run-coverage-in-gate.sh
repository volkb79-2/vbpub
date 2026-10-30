#!/usr/bin/env bash
# Run Assay's Go R1 lane inside tester-unified; no nested Docker is used.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ "$TARGET" == "pterodactyl" ]] || {
    echo "coverage lane only supports pterodactyl" >&2
    exit 2
}
[[ ! -S /var/run/docker.sock ]] || {
    echo "Wings coverage lane must not receive the host Docker socket" >&2
    exit 2
}
[[ -d "$SRC_DIR/.git" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }
[[ "$(git -C "$SRC_DIR" rev-parse HEAD)" == "$(git -C "$SRC_DIR" rev-parse "$BRANCH")" ]] || {
    echo "checkout HEAD does not match patch branch $BRANCH" >&2
    exit 1
}
[[ -z "$(git -C "$SRC_DIR" status --porcelain)" ]] || {
    echo "patched checkout is dirty; refusing coverage against an unexported tree" >&2
    exit 1
}

export GOTOOLCHAIN=local
export GOFLAGS="${GOFLAGS:--buildvcs=false}"
export GOWORK=off
export GOCACHE="${GOCACHE:-$HOME/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-$HOME/.cache/go-mod}"
mkdir -p "$GOCACHE" "$GOMODCACHE" "$STACK_DIR/.assay/coverage"

"$TESTER_VENV/bin/python" -m pip install --no-build-isolation -e "$VBPUB_ROOT/assay"

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wings-assay.XXXXXX")"
TEMP_REPO="$TEMP_DIR/source"
cleanup() {
    local status=$?
    trap - EXIT
    rm -rf "$TEMP_DIR"
    return "$status"
}
trap cleanup EXIT

git clone --quiet --local --no-hardlinks --branch "$BRANCH" "$SRC_DIR" "$TEMP_REPO"
if [[ -f "$TEMP_REPO/.git/shallow" ]]; then
    git -C "$TEMP_REPO" fetch --unshallow --quiet origin
fi
cp "$STACK_DIR/assay/assay.toml" "$TEMP_REPO/assay.toml"
git -C "$TEMP_REPO" config user.name wings-cgroups
git -C "$TEMP_REPO" config user.email wings-cgroups@localhost
git -C "$TEMP_REPO" -c commit.gpgSign=false add -- assay.toml
git -C "$TEMP_REPO" -c commit.gpgSign=false commit -q -m "chore(assay): temporary coverage lane"

echo "=== go mod download ==="
(cd "$TEMP_REPO" && go mod download)

set +e
(
    cd "$TEMP_REPO"
    "$TESTER_VENV/bin/assay" run coverage \
        --file assay.toml \
        --resume \
        --state-dir "$STACK_DIR/.assay/state-coverage" \
        --progress "$STACK_DIR/.assay/progress-coverage.jsonl" \
        --verdict-json "$STACK_DIR/.assay/coverage/verdict.json"
)
status=$?
set -e
if [[ -f "$TEMP_REPO/.assay/cover.out" ]]; then
    cp "$TEMP_REPO/.assay/cover.out" "$STACK_DIR/.assay/coverage/cover.out"
fi
echo "coverage artifacts: $STACK_DIR/.assay/coverage"
exit "$status"
