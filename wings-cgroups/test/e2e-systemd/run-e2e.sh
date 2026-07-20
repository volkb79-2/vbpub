#!/usr/bin/env bash
# run-e2e.sh — build and run the privileged systemd e2e harness.
# Degrades gracefully: exits 0 with SKIP if privileged containers are refused.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
IMG=wings-cgroups-e2e
NAME=wings-cgroups-e2e-run

# Privileged available?
if ! docker run --rm --privileged busybox true >/dev/null 2>&1; then
    echo "SKIP: this Docker daemon refuses privileged containers; e2e cannot run here."
    exit 0
fi

# Pre-compile the wings internal/cgroups integration test binary (patch 0004)
# from the patched clone; build/ is excluded from the image context, so the
# binary is handed in as an artifact. Missing clone -> section is SKIPped.
ART="$HERE/artifacts"
mkdir -p "$ART" && rm -f "$ART/cgroups.test"
if [[ -d "$PROJECT/build/wings-pterodactyl" ]]; then
    echo "== compiling wings internal/cgroups integration test binary =="
    # shellcheck source=../../patchstack/scripts/common.sh
    source "$PROJECT/patchstack/scripts/common.sh"
    resolve_target pterodactyl
    go_in_container "$SRC_DIR" \
        "CGO_ENABLED=0 go test -c -tags systemdintegration -o /tmp/cgroups.test ./internal/cgroups/ 1>&2 && cat /tmp/cgroups.test" \
        > "$ART/cgroups.test"
    chmod +x "$ART/cgroups.test"
else
    echo "NOTE: build/wings-pterodactyl missing; wings cgroups integration section will be SKIPped"
fi

echo "== building e2e image (context tar-piped: paths here are not daemon-mountable) =="
tar -C "$PROJECT" --exclude='build' --exclude='.git' -cf - . \
    | docker build -q -t "$IMG" -f test/e2e-systemd/Dockerfile - \
    | tail -1

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== booting systemd container =="
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    "$IMG" >/dev/null

echo "== waiting for systemd =="
for _ in $(seq 30); do
    state=$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)
    [[ "$state" == "running" || "$state" == "degraded" ]] && break
    sleep 2
done
echo "systemd state: ${state:-unknown}"
[[ "$state" == "running" || "$state" == "degraded" ]] || {
    echo "FAIL: systemd did not boot in the container"; docker logs "$NAME" | tail -20; exit 1; }

echo "== running inner test =="
if docker exec "$NAME" /usr/local/bin/inner-test.sh; then
    echo "e2e-systemd: PASS"
else
    rc=$?
    echo "e2e-systemd: FAIL (rc=$rc)"
    exit "$rc"
fi
