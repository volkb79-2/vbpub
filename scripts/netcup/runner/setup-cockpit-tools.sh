#!/usr/bin/env bash
# Description: install the published Run-Gate wheel and the checkout's Assay (editable) into the cockpit user's ~/.venvs/gate-tools and link run-gate/assay into ~/.local/bin.
# start-cockpit-runner.sh leaves the cockpit without run-gate and assay; run-lane.sh calls /home/vscode/.local/bin/run-gate.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
repo=$(source_repo)
commit=$(basename "$(readlink -f "$repo")")
: "${COCKPIT_NAME:?}" "${RUN_GATE_VERSION:?}" "${RUN_GATE_WHEEL_SHA256:?}"
docker exec -i -u vscode "$COCKPIT_NAME" env COMMIT="$commit" RG_VERSION="$RUN_GATE_VERSION" RG_SHA="$RUN_GATE_WHEEL_SHA256" bash -s <<'INNER'
set -Eeuo pipefail
repo=/workspaces/$COMMIT
test -d "$repo/assay"
v=$HOME/.venvs/gate-tools
python3 -m venv "$v"
mkdir -p "$HOME/wheels" "$HOME/.local/bin"
python3 "$repo/tester-unified/fetch-run-gate-wheel.py" --dest "$HOME/wheels" --version "$RG_VERSION"
wheel=$(ls "$HOME"/wheels/run_gate-"$RG_VERSION"-*.whl)
test "$(sha256sum "$wheel" | cut -d' ' -f1)" = "$RG_SHA"
"$v/bin/pip" install -q "$wheel"
"$v/bin/pip" install -q -e "$repo/assay"
for t in run-gate assay; do ln -sfn "$v/bin/$t" "$HOME/.local/bin/$t"; done
"$HOME/.local/bin/run-gate" --version
"$HOME/.local/bin/assay" --version
INNER
