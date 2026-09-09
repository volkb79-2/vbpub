#!/usr/bin/env bash
# test.sh [pterodactyl|pelican] — build + vet + unit tests in a golang
# container. INTEGRATION=1 additionally runs the docker integration tests
# against the daemon on /var/run/docker.sock (requires a systemd-driver daemon
# for meaningful slice placement, but any daemon validates the wiring).
# COVERAGE=1 additionally runs the assay R1 changed-line coverage lane over
# the patch surface (scripts/coverage.sh); off by default because it re-runs
# the suite instrumented in a second container.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR" ]] || { echo "run clone.sh + apply.sh first" >&2; exit 1; }

CMD='
set -e
echo "=== go build ===" && go build ./...
echo "=== go vet (strict, minus known-dirty upstream pkgs) ==="
go vet $(go list ./... | grep -v -E "'"$VET_EXCLUDE_RE"'")
# -count=1 is not optional: without it a green run can be served entirely from
# the shared wingscg-gocache volume, so the gate would report OK having executed
# nothing. A gate that can pass without running is not a gate.
echo "=== go test ===" && go test -count=1 ./config/... ./environment/... ./server/... ./internal/cgroups/...
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
if [[ "${COVERAGE:-0}" == "1" ]]; then
    "$SCRIPT_DIR/coverage.sh" "$TARGET"
fi
echo "test.sh: ALL OK ($TARGET)"
