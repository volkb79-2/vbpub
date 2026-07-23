#!/usr/bin/env bash
# test.sh [pterodactyl|pelican] — build + vet + unit tests in a golang
# container. INTEGRATION=1 additionally runs the docker integration tests
# against the daemon on /var/run/docker.sock (requires a systemd-driver daemon
# for meaningful slice placement, but any daemon validates the wiring).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }

CMD='
set -e
echo "=== go build ===" && go build ./...
echo "=== go vet (strict, minus known-dirty upstream pkgs) ==="
go vet $(go list ./... | grep -v -E "'"$VET_EXCLUDE_RE"'")
echo "=== go test ===" && go test ./config/... ./environment/... ./server/... ./internal/cgroups/...
echo "=== integration compile check ===" && go vet -tags dockerintegration ./environment/docker/
go vet -tags systemdintegration ./internal/cgroups/
'
if [[ "${INTEGRATION:-0}" == "1" ]]; then
    CMD+='
echo "=== docker integration tests ===" && go test -tags dockerintegration -count=1 -v ./environment/docker/
'
    go_in_container "$SRC_DIR" --docker "$CMD"
else
    go_in_container "$SRC_DIR" "$CMD"
fi
echo "test.sh: ALL OK ($TARGET)"
