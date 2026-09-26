#!/usr/bin/env bash
# B105's separately invoked full-source R0-R3 qualification gate.
# run-gate-project owns tester-unified, cgroup placement, and worktree mounts;
# this inner driver builds the selected committed source as a wheel, runs the
# declared lane against its isolated snapshots, and verifies the retained
# verdict before reporting success.
set -euo pipefail

die() { printf 'self-qualification-gate: %s\n' "$*" >&2; exit 2; }

worktree="${1:?usage: self-qualification-gate.sh WORKTREE}"
requested_lane="${2:-self-qualification}"
project="$worktree/assay"
tester_python=/opt/tester-venv/bin/python

case "$requested_lane" in
  self-qualification|self-qualification-preflight) ;;
  *) die "unsupported B105 lane: $requested_lane" ;;
esac

[[ -x "$tester_python" ]] || die "B105 requires tester-unified's $tester_python"
[[ -f "$project/pyproject.toml" ]] || die "selected worktree has no assay/pyproject.toml: $project"

cd "$project"
mkdir -p .assay
source_commit="$(git rev-parse HEAD)"
source_tree="$(git rev-parse 'HEAD^{tree}')"
ensure_source_unchanged() {
  [[ "$(git rev-parse HEAD)" == "$source_commit" ]] \
    || die "HEAD changed during B105 qualification"
  [[ "$(git rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
    || die "source tree changed during B105 qualification"
}

echo "B105_SOURCE_COMMIT=$source_commit"
echo "B105_SOURCE_TREE=$source_tree"

scratch="$(mktemp -d "${TMPDIR:-/tmp}/assay-b105.XXXXXX")"
cleanup() { rm -rf -- "$scratch"; }
trap cleanup EXIT

# Build from a private exact-OID clone so ignored build residue in the mounted
# worktree cannot enter the judge artifact. The clone remains in the same
# repository history, allowing setuptools-scm to derive the reviewed version.
echo "B105_PHASE=clone-exact-source"
git clone --no-local --no-checkout --quiet "$worktree" "$scratch/source"
git -C "$scratch/source" sparse-checkout init --cone
git -C "$scratch/source" sparse-checkout set assay
git -C "$scratch/source" checkout --quiet --detach "$source_commit"
[[ "$(git -C "$scratch/source" rev-parse HEAD)" == "$source_commit" ]] \
  || die "private clone HEAD differs from selected source commit"
[[ "$(git -C "$scratch/source" rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
  || die "private clone tree differs from selected source tree"

distribution="$project/gate/distribution"
base_prefix="$("$tester_python" -c 'import sys; print(sys.base_prefix)')"
"$base_prefix/bin/python3" -m venv "$scratch/build-venv"
"$base_prefix/bin/python3" -m venv "$scratch/run-venv"

echo "B105_PHASE=install-locked-build-closure"
"$scratch/build-venv/bin/python" -m pip install \
  --no-index \
  --find-links "$distribution/build-wheelhouse" \
  --require-hashes \
  -r "$distribution/build-requirements.txt"
"$scratch/build-venv/bin/python" - <<'PYEOF'
from importlib.metadata import version

expected = {
    "setuptools": "84.0.0",
    "wheel": "0.47.0",
    "setuptools-scm": "10.0.5",
    "packaging": "26.3",
    "vcs-versioning": "2.2.4",
}
for name, wanted in expected.items():
    actual = version(name)
    assert actual == wanted, f"{name}: expected {wanted}, got {actual}"
PYEOF

echo "B105_PHASE=build-selected-wheel"
mkdir -p "$scratch/dist"
"$scratch/build-venv/bin/python" -m pip wheel \
  --no-index \
  --no-build-isolation \
  --no-deps \
  --wheel-dir "$scratch/dist" \
  "$scratch/source/assay"
shopt -s nullglob
wheels=("$scratch"/dist/assay-*.whl)
[[ ${#wheels[@]} -eq 1 ]] || die "expected one Assay wheel, found ${#wheels[@]}"
wheel="${wheels[0]}"
version="$("$scratch/build-venv/bin/python" - "$wheel" <<'PYEOF'
import email
import re
import sys
import zipfile

wheel = sys.argv[1]
match = re.fullmatch(r"assay-(.+)-py3-none-any\.whl", wheel.rsplit("/", 1)[-1])
assert match, f"unexpected wheel filename: {wheel}"
with zipfile.ZipFile(wheel) as archive:
    metadata_path = next(
        name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
    )
    metadata = email.message_from_bytes(archive.read(metadata_path))
metadata_version = metadata["Version"]
assert metadata_version == match.group(1), (
    f"wheel filename version {match.group(1)!r} != METADATA {metadata_version!r}"
)
assert metadata_version not in {"0.0.0", "0+unknown"}, (
    f"wheel has placeholder version {metadata_version!r}"
)
print(metadata_version)
PYEOF
)"

echo "B105_PHASE=install-wheel-and-tester-test-closure"
"$scratch/run-venv/bin/python" -m pip install --no-index --no-deps "$wheel"
tester_site="$("$tester_python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
run_venv_site="$("$scratch/run-venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$tester_site" > "$run_venv_site/tester_unified_site.pth"
"$scratch/run-venv/bin/python" - "$version" "$scratch/run-venv" <<'PYEOF'
import sys
from importlib.metadata import version as installed_version

import assay
import coverage
import pytest

expected_version, run_venv = sys.argv[1:]
actual_version = installed_version("assay")
assert actual_version == expected_version, (
    f"installed Assay version {actual_version!r} != wheel {expected_version!r}"
)
assert assay.__version__ == expected_version
assert assay.__file__.startswith(run_venv + "/"), (
    f"Assay imported from outside run-venv: {assay.__file__}"
)
assert pytest.__version__ and coverage.__version__
print(f"B105_TEST_CLOSURE=pytest-{pytest.__version__},coverage-{coverage.__version__}")
PYEOF

export PATH="$scratch/run-venv/bin:$PATH"
assay_bin="$scratch/run-venv/bin/assay"

run_and_verify_lane() {
  local lane="$1" run_status=0
  local verdict_path=".assay/verdict-$lane.json"
  local progress_path=".assay/progress-$lane.jsonl"
  local state_path=".assay/mutation-state-$lane"

  echo "B105_PHASE=assay-run-$lane"
  if "$assay_bin" run "$lane" --file assay.toml \
      --require-judge-provenance \
      --resume \
      --progress "$progress_path" \
      --state-dir "$state_path" \
      --verdict-json "$verdict_path"; then
    run_status=0
  else
    run_status=$?
  fi

  echo "B105_PHASE=assay-verify-$lane"
  "$assay_bin" verify "$verdict_path" || return 2
  [[ $run_status -eq 0 ]] || return "$run_status"
  echo "B105_VERIFIED_LANE=$lane"
}

if [[ "$requested_lane" == "self-qualification-preflight" ]]; then
  run_and_verify_lane "$requested_lane"
  ensure_source_unchanged
  echo "B105_VERIFIED_SOURCE_COMMIT=$source_commit"
  echo "B105_VERIFIED_SOURCE_TREE=$source_tree"
  echo "B105_VERDICT=.assay/verdict-$requested_lane.json"
  echo "B105_PROGRESS=.assay/progress-$requested_lane.jsonl"
  echo "ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1"
  exit 0
fi

# R1's whole-source coverage floor must pass before the multi-day mutation
# sweep starts. The preflight and the full lane judge the same immutable
# worktree revision; if either R0 or R1 is red, preserve its verified report
# and stop before R2.
if run_and_verify_lane self-qualification-preflight; then
  echo "ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1"
else
  run_status=$?
  echo "B105_STOPPED_BEFORE_R2=preflight-failed" >&2
  exit "$run_status"
fi

run_and_verify_lane self-qualification

# Independently bind the emitted judge provenance to the wheel bytes that this
# gate built and installed. A matching version string alone is not provenance.
wheel_digest="$(sha256sum "$wheel" | cut -d' ' -f1)"
"$scratch/run-venv/bin/python" - \
  .assay/verdict-self-qualification.json "$version" "$wheel_digest" <<'PYEOF'
import json
import sys

path, expected_version, expected_digest = sys.argv[1:]
document = json.load(open(path, encoding="utf-8"))
identity = document.get("judge_provenance")
assert isinstance(identity, dict), "verdict has no judge_provenance"
assert identity.get("artifact") == "wheel", identity
assert identity.get("digest_algorithm") == "sha256", identity
assert identity.get("digest") == expected_digest, identity
assert identity.get("version") == expected_version, identity
assert document.get("assay_version") == expected_version, document.get("assay_version")
PYEOF

ensure_source_unchanged

echo "B105_VERIFIED_SOURCE_COMMIT=$source_commit"
echo "B105_VERIFIED_SOURCE_TREE=$source_tree"
echo "B105_VERDICT=.assay/verdict-self-qualification.json"
echo "B105_PROGRESS=.assay/progress-self-qualification.jsonl"
echo "B105_WHEEL_SHA256=$wheel_digest"
echo "ASSAY_SELF_QUALIFICATION_VERIFIED=1"
