#!/usr/bin/env bash
# Guest-side: pre-pull the managed BuildKit image under its OWN timeout, then run
# install.sh. A registry/network problem now fails here with a clear message
# instead of as an opaque "mdt-buildkitd.service ... timeout exceeded" inside the
# install, and the unit's own start (which also pulls) then hits a warm cache.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # host-setup/
image=$(sed -n 's/^DEV_BUILDKITD_IMAGE=//p' host-setup.env.example | tr -d '"' | head -n 1)
[ -n "$image" ] || { echo "vm-install: cannot read DEV_BUILDKITD_IMAGE from host-setup.env.example" >&2; exit 1; }
# the installed config may override the example
if [ -f /etc/mdt/host-setup.env ]; then
    override=$(sed -n 's/^DEV_BUILDKITD_IMAGE=//p' /etc/mdt/host-setup.env | tr -d '"' | head -n 1)
    [ -z "$override" ] || image="$override"
fi
echo "vm-install: pre-pulling $image (timeout 900 s)"
if ! timeout 900 docker pull "$image"; then
    echo "vm-install: pre-pull of $image failed or timed out (network, registry, or TCG slowness)" >&2
    exit 1
fi
exec bash ./install.sh   # cwd is host-setup/ (see the cd above)
