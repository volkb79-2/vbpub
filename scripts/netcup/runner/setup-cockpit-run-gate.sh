#!/usr/bin/env bash
# Description: install one pinned Run-Gate wheel into its own cockpit venv ~/.venvs/gate-tools<version without dots>, next to (never over) the default gate-tools venv; idempotent.
# usage: run-job setup-rg-<ver> -- bash ~/runner-scripts/setup-cockpit-run-gate.sh <version> <wheel-sha256>   (e.g. 25.0.2 <sha256>; creates ~/.venvs/gate-tools2502)
# Repos that need a newer Run-Gate than the cockpit default (dstdns: >= 25.0.1) are run by remote-gate.py through
# /home/vscode/.venvs/gate-tools<ver>/bin/run-gate; the profile lists the path (REPO_DEFAULTS in remote-gate.py, `repos` in the hosts file).
# Idempotent: a rerun after success is a no-op (exit 0). A venv cannot be renamed after creation (its scripts carry its path), so instead of
# build-in-temp-and-rename the venv is built at its final path and any failure moves it aside to <venv>.failed.<epoch> (kept, never deleted):
# a failed run never leaves a venv at the final path, and the next run rebuilds clean. An unusable venv found at the final path (for example
# from the previous version of this script) is moved aside the same way.
# Dependencies: the Run-Gate wheel declares none (stdlib-only by design: run-gate-project/pyproject.toml `dependencies = []`), so the install
# uses --no-deps --no-index: nothing is fetched from PyPI, so there is nothing to pin or age-vet. If a future Run-Gate gains dependencies the
# venv's run-gate will not start, the version check fails and the venv is moved aside; at that point this script needs a hashed requirements
# file (`uv pip compile --generate-hashes`) passed with `pip install --require-hashes`.
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
re="(^|[^0-9.])${RG_VERSION//./\\.}\$"
reports_version() { [[ $("$1/bin/run-gate" --version 2>/dev/null | head -n1 || true) =~ $re ]]; }
if [[ -e $v ]]; then
  if reports_version "$v"; then
    echo "$v already provides run-gate $RG_VERSION; nothing to do"
    exit 0
  fi
  mv -- "$v" "$v.failed.$(date +%s)"
  echo "moved the unusable $v aside; rebuilding" >&2
fi
src=${RG_WORKSPACES:-/workspaces}/$COMMIT/tester-unified
[[ -d $src ]] || { echo "$src is missing: the synced checkout must be a recent sync of vbpub" >&2; exit 4; }
mkdir -p "$HOME/wheels"
python3 "$src/fetch-run-gate-wheel.py" --dest "$HOME/wheels" --version "$RG_VERSION"
wheel=$HOME/wheels/run_gate-$RG_VERSION-py3-none-any.whl
[[ -f $wheel ]] || { echo "$wheel was not fetched" >&2; exit 5; }
test "$(sha256sum "$wheel" | cut -d' ' -f1)" = "$RG_SHA"
fail() { [[ ! -e $v ]] || mv -- "$v" "$v.failed.$(date +%s)"; echo "install failed; no venv left at $v" >&2; }
trap fail ERR
python3 -m venv "$v"
"$v/bin/pip" install -q --no-deps --no-index "$wheel"
reports_version "$v"
trap - ERR
"$v/bin/run-gate" --version
INNER
