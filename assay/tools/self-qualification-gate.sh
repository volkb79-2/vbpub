#!/usr/bin/env bash
# B105's separately invoked full-source R0-R3 qualification gate.
# run-gate-project owns tester-unified, cgroup placement, and worktree mounts;
# this inner driver installs the selected worktree's Assay source, runs the
# declared lane with durable progress/resume state, then verifies its report.
set -euo pipefail

worktree="${1:?usage: self-qualification-gate.sh WORKTREE}"
project="$worktree/assay"
python=/opt/tester-venv/bin/python
assay_bin=/opt/tester-venv/bin/assay

[[ -x "$python" ]] || { echo "B105 requires tester-unified's /opt/tester-venv/bin/python" >&2; exit 2; }
[[ -f "$project/pyproject.toml" ]] || { echo "selected worktree has no assay/pyproject.toml: $project" >&2; exit 2; }

cd "$project"
mkdir -p .assay
source_commit="$(git rev-parse HEAD)"
source_tree="$(git rev-parse 'HEAD^{tree}')"

echo "B105_SOURCE_COMMIT=$source_commit"
echo "B105_SOURCE_TREE=$source_tree"
echo "B105_PHASE=install-selected-source"
"$python" -m pip install --quiet --disable-pip-version-check --no-input \
  --no-deps --no-build-isolation --editable "$project"
[[ -x "$assay_bin" ]] || { echo "editable source install did not provide $assay_bin" >&2; exit 2; }

# The lane passes PATH through to its declared `python -m pytest` command.
# tester-unified's ambient `python` is /usr/local/bin/python, which lacks the
# gate's pytest/coverage packages; the gate interpreter owns those packages.
# Put that interpreter first so the lane cannot silently resolve a different
# Python than the one used to install the selected Assay source.
export PATH="${python%/*}:$PATH"
echo "B105_PYTHON=$python"

echo "B105_PHASE=assay-run-self-qualification"
"$assay_bin" run self-qualification --file assay.toml \
  --resume \
  --progress .assay/progress-self-qualification.jsonl \
  --state-dir .assay/mutation-state \
  --verdict-json .assay/verdict-self-qualification.json

echo "B105_PHASE=assay-verify"
"$assay_bin" verify .assay/verdict-self-qualification.json

[[ "$(git rev-parse HEAD)" == "$source_commit" ]] || {
  echo "HEAD changed during B105 qualification" >&2
  exit 2
}
[[ "$(git rev-parse 'HEAD^{tree}')" == "$source_tree" ]] || {
  echo "source tree changed during B105 qualification" >&2
  exit 2
}

echo "B105_VERIFIED_SOURCE_COMMIT=$source_commit"
echo "B105_VERIFIED_SOURCE_TREE=$source_tree"
echo "B105_VERDICT=.assay/verdict-self-qualification.json"
echo "B105_PROGRESS=.assay/progress-self-qualification.jsonl"
echo "ASSAY_SELF_QUALIFICATION_VERIFIED=1"
