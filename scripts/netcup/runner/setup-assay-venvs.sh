#!/usr/bin/env bash
# Description: create Python 3.13 and 3.14 Assay venvs from the synced repository source.
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
$uv venv --python /usr/bin/python3.13 "$RUNNER_HOME/venvs/assay-3.13"
$uv venv --python 3.14 "$RUNNER_HOME/venvs/assay-3.14"
$uv pip install --python "$RUNNER_HOME/venvs/assay-3.13/bin/python" -e "$repo/assay[test]"
$uv pip install --python "$RUNNER_HOME/venvs/assay-3.14/bin/python" -e "$repo/assay[test]"
for venv in "$RUNNER_HOME/venvs/assay-3.13" "$RUNNER_HOME/venvs/assay-3.14"; do
  "$venv/bin/python" -c 'import sys, pytest, assay; from importlib.metadata import version; print("python="+sys.version.split()[0]+" assay="+version("assay")+" pytest="+pytest.__version__)'
done
