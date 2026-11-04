#!/usr/bin/env bash
# Description: install one pinned Run-Gate wheel into its own cockpit venv ~/.venvs/gate-tools<version without dots>, next to (never over) the default gate-tools venv.
# usage: run-job setup-rg-<ver> -- bash setup-cockpit-run-gate.sh <version> <wheel-sha256>   (e.g. 25.0.2 <sha256>; creates ~/.venvs/gate-tools2502)
# Repos that need a newer Run-Gate than the cockpit default (dstdns: >= 25.0.1) are run by remote-gate.py through
# /home/vscode/.venvs/gate-tools<ver>/bin/run-gate; the profile lists the path (REPO_DEFAULTS in remote-gate.py, `repos` in the hosts file).
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
[[ $# -eq 2 ]] || { echo "usage: $0 <run-gate-version> <wheel-sha256>" >&2; exit 2; }
version=$1; sha=$2
[[ $version =~ ^[0-9]+(\.[0-9]+)+$ && $sha =~ ^[0-9a-f]{64}$ ]] || { echo 'version must be dotted digits and the sha256 64 hex digits' >&2; exit 2; }
: "${COCKPIT_NAME:?}"
repo=$(source_repo)
commit=$(basename "$(readlink -f "$repo")")
docker exec -i -u vscode -e COMMIT="$commit" -e RG_VERSION="$version" -e RG_SHA="$sha" "$COCKPIT_NAME" bash -s <<'INNER'
set -Eeuo pipefail
v=$HOME/.venvs/gate-tools${RG_VERSION//./}
[[ ! -e $v ]] || { echo "$v already exists; refusing to modify it" >&2; exit 3; }
mkdir -p "$HOME/wheels"
python3 "/workspaces/$COMMIT/tester-unified/fetch-run-gate-wheel.py" --dest "$HOME/wheels" --version "$RG_VERSION"
wheel=$(ls "$HOME"/wheels/run_gate-"$RG_VERSION"-*.whl)
test "$(sha256sum "$wheel" | cut -d' ' -f1)" = "$RG_SHA"
python3 -m venv "$v"
"$v/bin/pip" install -q "$wheel"
"$v/bin/run-gate" --version
INNER
