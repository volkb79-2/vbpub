#!/usr/bin/env bash
# Description: install source-backed CMRU/cli-extended and the published Run-Gate wheel in the runner's tools venv.
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
cd "$RUNNER_HOME"
identity_check
repo=$(source_repo)
export UV_NO_CONFIG=1
export UV_PYTHON_INSTALL_DIR=$RUNNER_HOME/.local/share/uv/python
uv=$RUNNER_HOME/venvs/tools/bin/uv
python=$RUNNER_HOME/venvs/tools/bin/python
wheel_dir=$RUNNER_HOME/cache/run-gate-wheel-$RUN_GATE_VERSION
install -d -m 0700 "$RUNNER_HOME/cache"
if test ! -e "$wheel_dir"; then
  mkdir -m 0700 "$wheel_dir"
fi
test -d "$wheel_dir"
test -z "$(find "$wheel_dir" -maxdepth 1 -name 'run_gate-*.whl' -print -quit)"
# The checkout's cli-extended sources provide the API required by CMRU, while its last release tag may still derive an
# older version. Mark this explicit source installation with the +bootstrap.source version used by the documented
# zero-release path; install each package separately so PyPI can never resolve the bare cli-extended name.
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=0.3.0+bootstrap.source \
  "$uv" pip install --python "$python" --no-deps -e "$repo/libraries/cli-extended"
"$uv" pip install --python "$python" --no-deps -e "$repo/cmru"
"$python" "$repo/tester-unified/fetch-run-gate-wheel.py" --dest "$wheel_dir" --version "$RUN_GATE_VERSION"
wheel="$(find "$wheel_dir" -maxdepth 1 -type f -name 'run_gate-*.whl' -print -quit)"
test -n "$wheel"
test "$(sha256sum "$wheel" | awk '{print $1}')" = "$RUN_GATE_WHEEL_SHA256"
"$uv" pip install --python "$python" "$wheel"
"$RUNNER_HOME/venvs/tools/bin/cmru" version
"$RUNNER_HOME/venvs/tools/bin/run-gate" --version
sha256sum "$wheel"
