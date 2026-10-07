#!/usr/bin/env bash
# B105's separately invoked full-source R0-R3 qualification gate.
# run-gate-project owns tester-unified, cgroup placement, and worktree mounts;
# this inner driver builds the selected committed source as a wheel, runs the
# declared lane against its isolated snapshots, and verifies the retained
# verdict before reporting success.
# A full B105 run reuses one persisted campaign deadline across preflight and
# R2. To deliberately restart the same commit after expiry, move that deadline
# and both bound mutation-state directories aside together.
set -euo pipefail

die() { printf 'self-qualification-gate: %s\n' "$*" >&2; exit 2; }

assay_git() {
  command git \
    -c maintenance.auto=false \
    -c maintenance.autoDetach=false \
    -c gc.autoDetach=false \
    "$@"
}

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
source_commit="$(assay_git rev-parse HEAD)"
source_tree="$(assay_git rev-parse 'HEAD^{tree}')"
[[ "$(assay_git rev-parse "${source_commit}^{tree}")" == "$source_tree" ]] \
  || die "captured source commit does not resolve to the captured tree"
ensure_source_unchanged() {
  local worktree_status
  [[ "$(assay_git rev-parse HEAD)" == "$source_commit" ]] \
    || die "HEAD changed during B105 qualification"
  [[ "$(assay_git rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
    || die "source tree changed during B105 qualification"
  worktree_status="$(assay_git status --porcelain --untracked-files=all)" \
    || die "cannot inspect worktree changes during B105 qualification"
  [[ -z "$worktree_status" ]] \
    || die "worktree files changed during B105 qualification"
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
assay_git clone --no-local --no-checkout --quiet "$worktree" "$scratch/source"
assay_git -C "$scratch/source" sparse-checkout init --cone
assay_git -C "$scratch/source" sparse-checkout set assay
assay_git -C "$scratch/source" checkout --quiet --detach "$source_commit"
[[ "$(assay_git -C "$scratch/source" rev-parse HEAD)" == "$source_commit" ]] \
  || die "private clone HEAD differs from selected source commit"
[[ "$(assay_git -C "$scratch/source" rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
  || die "private clone tree differs from selected source tree"
source_epoch="$(assay_git -C "$scratch/source" log -1 --format=%ct "$source_commit")"
[[ "$source_epoch" =~ ^[0-9]+$ ]] \
  || die "cannot derive SOURCE_DATE_EPOCH from selected commit $source_commit"

# S1 (B123): the full lane needs the registered tester-unified gate to have passed
# at this exact commit and tree. `./run-gate.py tester-unified` writes this receipt
# only after a green run; the preflight lane does not require it (CD9).
receipt="$project/.assay/registered-gate/tester-unified.json"
if [[ "$requested_lane" == "self-qualification" ]]; then
  echo "B105_PHASE=require-same-commit-tester-unified-pass"
  "$tester_python" "$scratch/source/assay/tools/b105_report_check.py" \
    --receipt-only \
    --tester-unified-receipt "$receipt" \
    --expected-commit "$source_commit" \
    --expected-tree "$source_tree" \
    || die "no registered tester-unified pass at $source_commit; run ./run-gate.py tester-unified first"
fi

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
SOURCE_DATE_EPOCH="$source_epoch" \
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

wheel_digest="$(sha256sum "$wheel" | cut -d' ' -f1)"
if [[ "$requested_lane" == "self-qualification" ]]; then
  campaign="b105-${source_commit:0:12}"
else
  campaign="b105-pre-${source_commit:0:12}"
fi
deadline=".assay/campaign-deadline-$campaign.json"

check_campaign_wheel_digest() {
  "$tester_python" "$scratch/source/assay/tools/b105_report_check.py" \
    --deadline-wheel-check-only \
    --deadline "$deadline" \
    --expected-wheel-sha256 "$wheel_digest"
}

# Bind the wheel before installing the run closure or invoking plan/preflight/
# R2. A same-OID retry must not spend hours on an artifact that the persisted
# campaign deadline cannot accept.
if [[ -e "$deadline" || -L "$deadline" ]]; then
  check_campaign_wheel_digest \
    || die "existing campaign deadline does not bind this deterministic source wheel"
fi

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

if [[ "$requested_lane" == "self-qualification" ]]; then
  if [[ ! -e "$deadline" ]]; then
    if "$assay_bin" campaign init --file assay.toml \
        --campaign "$campaign" \
        --lane self-qualification \
        --lane self-qualification-preflight \
        --hours 8 \
        --state-dir .assay/mutation-state-self-qualification \
        --state-dir .assay/mutation-state-self-qualification-preflight \
        --wheel-sha256 "$wheel_digest"; then
      :
    else
      init_status=$?
      echo "B105_CAMPAIGN_INIT_REFUSED=1" >&2
      exit "$init_status"
    fi
  fi
else
  if [[ ! -e "$deadline" ]]; then
    if "$assay_bin" campaign init --file assay.toml \
        --campaign "$campaign" \
        --lane self-qualification-preflight \
        --hours 1 \
        --state-dir .assay/mutation-state-self-qualification-preflight \
        --wheel-sha256 "$wheel_digest"; then
      :
    else
      init_status=$?
      echo "B105_CAMPAIGN_INIT_REFUSED=1" >&2
      exit "$init_status"
    fi
  fi
fi
echo "B105_CAMPAIGN_DEADLINE=$deadline"
check_campaign_wheel_digest \
  || die "persisted campaign deadline does not bind this deterministic source wheel"

run_and_verify_lane() {
  local lane="$1" run_status=0 expected_rigor coverage_archive_root coverage_archive_attempt
  local remaining_s
  local verdict_path=".assay/verdict-$lane.json"
  local progress_path=".assay/progress-$lane.jsonl"
  local state_path=".assay/mutation-state-$lane"
  local plan_path=".assay/plan-$lane.json"
  local r2_manifest_path=".assay/r2-manifest-$lane.txt"
  local -a receipt_args=()
  local -a plan_args=()
  local -a r2_manifest_args=()
  local -a lane_flags=()
  [[ "$lane" == "self-qualification" ]] && receipt_args=(--tester-unified-receipt "$receipt")

  case "$lane" in
    self-qualification-preflight)
      expected_rigor="R0,R1"
      export ASSAY_B105_COVERAGE_SOURCE=".assay/coverage-$lane.json"
      coverage_archive_root="$project/.assay/coverage-self-qualification-preflight-snapshots"
      mkdir -p "$coverage_archive_root"
      coverage_archive_attempt="$(mktemp -d "$coverage_archive_root/attempt.XXXXXXXX")"
      export ASSAY_B105_COVERAGE_ARCHIVE_DIR="$coverage_archive_attempt"
      export ASSAY_B105_SOURCE_COMMIT="$source_commit"
      export ASSAY_B105_SOURCE_TREE="$source_tree"
      echo "B105_COVERAGE_ARCHIVE_DIR=$coverage_archive_attempt"
      echo "B105_COVERAGE_ARCHIVE=$coverage_archive_attempt/coverage-self-qualification-preflight-snapshot-$source_commit-$source_tree.json"
      ;;
    self-qualification)
      expected_rigor="R0,R1,R2,R3"
      unset ASSAY_B105_COVERAGE_SOURCE ASSAY_B105_COVERAGE_ARCHIVE_DIR \
        ASSAY_B105_SOURCE_COMMIT ASSAY_B105_SOURCE_TREE
      rm -f -- "$r2_manifest_path"
      lane_flags+=(--cold-witness --r2-manifest "$r2_manifest_path")
      r2_manifest_args=(--r2-manifest "$r2_manifest_path")
      ;;
  esac

  if [[ "$expected_rigor" == *R2* ]]; then
    echo "B105_PHASE=assay-plan-$lane"
    "$assay_bin" plan "$lane" --file assay.toml > "$plan_path" || return 2
    plan_args=(--plan-json "$plan_path")
  fi

  echo "B105_PHASE=assay-run-$lane"
  remaining_s="$("$scratch/run-venv/bin/python" - "$deadline" "$lane" <<'PYEOF'
from datetime import datetime, timezone
import math
import sys
from pathlib import Path

from assay.cli import _parse_campaign_deadline

_, _, expires_at = _parse_campaign_deadline(Path(sys.argv[1]), lane=sys.argv[2])
remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
print(max(0, math.floor(remaining)))
PYEOF
)"
  if timeout --verbose --signal=TERM --kill-after=30s \
      "$((remaining_s + 120))s" "$assay_bin" run "$lane" --file assay.toml \
      --require-judge-provenance \
      --resume \
      --progress "$progress_path" \
      --state-dir "$state_path" \
      --verdict-json "$verdict_path" \
      --campaign-deadline "$deadline" \
      "${lane_flags[@]}"; then
    run_status=0
  else
    run_status=$?
  fi

  if (( run_status >= 124 )); then
    echo "B105_TIMEOUT_FAILSAFE=1" >&2
    return "$run_status"
  fi

  echo "B105_PHASE=assay-verify-$lane"
  "$assay_bin" verify "$verdict_path" || return 2
  if [[ $run_status -eq 0 ]]; then
    echo "B105_PHASE=verify-source-bound-report-$lane"
    "$scratch/run-venv/bin/python" \
      "$scratch/source/assay/tools/b105_report_check.py" \
      --report "$verdict_path" \
      --repo-root "$scratch/source" \
      --expected-commit "$source_commit" \
      --expected-tree "$source_tree" \
      --expected-lane "$lane" \
      --expected-rigor "$expected_rigor" \
      --expected-version "$version" \
      --expected-wheel-sha256 "$wheel_digest" \
      --producer-exit "$run_status" \
      --deadline "$deadline" \
      ${plan_args[@]+"${plan_args[@]}"} \
      ${r2_manifest_args[@]+"${r2_manifest_args[@]}"} \
      ${receipt_args[@]+"${receipt_args[@]}"} || return 2
  else
    return "$run_status"
  fi
  [[ $run_status -eq 0 ]] || return "$run_status"
  echo "B105_VERIFIED_LANE=$lane"
  if [[ "$lane" == "self-qualification" ]]; then
    echo "B105_R2_MANIFEST=$r2_manifest_path"
  fi
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

# R1's whole-source coverage floor must pass before the R2 mutation campaign
# starts. The preflight and the full lane judge the same immutable
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

ensure_source_unchanged

echo "B105_VERIFIED_SOURCE_COMMIT=$source_commit"
echo "B105_VERIFIED_SOURCE_TREE=$source_tree"
echo "B105_VERDICT=.assay/verdict-self-qualification.json"
echo "B105_PROGRESS=.assay/progress-self-qualification.jsonl"
echo "B105_WHEEL_SHA256=$wheel_digest"
echo "ASSAY_SELF_QUALIFICATION_VERIFIED=1"
