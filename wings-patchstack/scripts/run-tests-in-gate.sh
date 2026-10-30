#!/usr/bin/env bash
# Run the Go and Python workflow contracts inside the registered Wings gate.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR/.git" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }
[[ "$TARGET" == "pterodactyl" ]] || {
    echo "the registered Wings gate currently covers pterodactyl only" >&2
    exit 2
}
[[ ! -S /var/run/docker.sock ]] || {
    echo "Wings test lane must not receive the host Docker socket" >&2
    exit 2
}

export GOTOOLCHAIN=local
export GOFLAGS="${GOFLAGS:--buildvcs=false}"
export GOWORK=off
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

"$TESTER_VENV/bin/python" -m pytest \
    "$STACK_DIR/tests" -q -p no:cacheprovider

cd "$SRC_DIR" || exit 1
echo "=== go build ==="
go build ./...
echo "=== go vet (strict, minus known-dirty upstream packages) ==="
package_list="$(go list ./...)"
mapfile -t packages <<< "$package_list"
vet_packages=()
for package in "${packages[@]}"; do
    [[ "$package" =~ $VET_EXCLUDE_RE ]] || vet_packages+=("$package")
done
(( ${#vet_packages[@]} > 0 )) || {
    echo "no Go packages remain after applying VET_EXCLUDE_RE" >&2
    exit 1
}
go vet "${vet_packages[@]}"
echo "=== go test ==="
go test -count=1 ./config/... ./environment/... ./server/... ./internal/cgroups/... ./sftp/...
echo "=== integration compile check (no daemon or socket) ==="
go vet -tags dockerintegration ./environment/docker/
go vet -tags systemdintegration ./internal/cgroups/
echo "run-tests-in-gate.sh: ALL OK ($TARGET)"
