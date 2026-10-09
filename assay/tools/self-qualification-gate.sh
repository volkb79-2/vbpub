#!/usr/bin/env bash
# B105 and B110's separately invoked full-source R0-R3 gate modes.
# run-gate owns admission and artifacts; self-qualification-container.sh owns
# the bounded, cgroup-visible tester-unified container and exact worktree
# mounts. This inner driver builds that selected committed source as a wheel,
# runs the declared lane against isolated snapshots, and verifies the retained
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

pilot_invocation_remaining_s() {
  local started_s="$1" cap_s="$2" now_s="${3:-$SECONDS}"
  local elapsed_s=$((now_s - started_s))
  PILOT_INVOCATION_REMAINING_S=$((elapsed_s >= cap_s ? 0 : cap_s - elapsed_s))
}

pilot_campaign_remaining_s() {
  "$1" -c 'import json,sys,math,datetime as d; e=d.datetime.strptime(json.load(open(sys.argv[1]))["expires_at_utc"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=d.timezone.utc); print(max(0, math.floor((e-d.datetime.now(d.timezone.utc)).total_seconds())))' "$2"
}

run_b110_pilot() {
  pilot_campaign="$campaign"
  pilot_deadline="$deadline"
  pilot_cap_s=$((90 * 60))
  pilot_started_s=$SECONDS
  # These outputs describe this attempt. Preserve only the campaign deadline,
  # mutation state and append-only progress stream needed for a safe resume.
  rm -f -- \
    .assay/b110-pilot-plan.json \
    .assay/b110-pilot-candidates.txt \
    .assay/b110-pilot-selection.json \
    .assay/b110-pilot-summary.json \
    .assay/b110-pilot-run.log
  # P7R2-4: init FIRST, so `assay plan` and the selector run inside the 2 h campaign
  # and the outer failsafe only has to cover the build plus the campaign.
  if [[ ! -f "$pilot_deadline" ]]; then
    pilot_invocation_remaining_s "$pilot_started_s" "$pilot_cap_s"
    pilot_step_budget_s="$PILOT_INVOCATION_REMAINING_S"
    if (( pilot_step_budget_s <= 0 )); then
      echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
      return 124
    fi
    set +e
    timeout --verbose --signal=TERM --kill-after=30s "${pilot_step_budget_s}s" \
      "$assay_bin" campaign init --file assay.toml \
        --campaign "$pilot_campaign" --lane self-qualification --hours 2 \
        --state-dir .assay/b110-pilot-state --wheel-sha256 "$wheel_digest"
    pilot_init_status=$?
    set -e
    if (( pilot_init_status != 0 )); then
      (( pilot_init_status < 124 )) || echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
      echo "B110_PILOT_INIT_REFUSED=1"
      return 0
    fi
  fi
  # The 90-minute per-invocation cap covers campaign init, planning, selection
  # and assay run.
  # The campaign deadline is an independent absolute bound across retries.
  pilot_invocation_remaining_s "$pilot_started_s" "$pilot_cap_s"
  pilot_step_budget_s="$PILOT_INVOCATION_REMAINING_S"
  campaign_budget_s="$(pilot_campaign_remaining_s "$scratch/run-venv/bin/python" "$pilot_deadline")"
  pilot_step_budget_s=$((pilot_step_budget_s < campaign_budget_s ? pilot_step_budget_s : campaign_budget_s))
  if (( pilot_step_budget_s <= 0 )); then
    echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
    return 124
  fi
  set +e
  timeout --verbose --signal=TERM --kill-after=30s "${pilot_step_budget_s}s" \
    "$assay_bin" plan self-qualification --file assay.toml > .assay/b110-pilot-plan.json
  pilot_plan_status=$?
  set -e
  if (( pilot_plan_status != 0 )); then
    (( pilot_plan_status < 124 )) || echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
    return "$pilot_plan_status"
  fi

  pilot_invocation_remaining_s "$pilot_started_s" "$pilot_cap_s"
  pilot_step_budget_s="$PILOT_INVOCATION_REMAINING_S"
  campaign_budget_s="$(pilot_campaign_remaining_s "$scratch/run-venv/bin/python" "$pilot_deadline")"
  pilot_step_budget_s=$((pilot_step_budget_s < campaign_budget_s ? pilot_step_budget_s : campaign_budget_s))
  if (( pilot_step_budget_s <= 0 )); then
    echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
    return 124
  fi
  set +e
  timeout --verbose --signal=TERM --kill-after=30s "${pilot_step_budget_s}s" \
    "$scratch/run-venv/bin/python" "$scratch/source/assay/tools/b110_pilot_select.py" \
    --plan .assay/b110-pilot-plan.json --repo-root "$worktree" \
    --out .assay/b110-pilot-candidates.txt --report .assay/b110-pilot-selection.json
  pilot_selection_status=$?
  set -e
  if (( pilot_selection_status != 0 )); then
    (( pilot_selection_status < 124 )) || echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
    return "$pilot_selection_status"
  fi
  # Exactly P6's rule: max(0, floor(expires_at_utc - now_utc)); never "floored at 1".
  remaining_s="$(pilot_campaign_remaining_s "$scratch/run-venv/bin/python" "$pilot_deadline")"
  pilot_invocation_remaining_s "$pilot_started_s" "$pilot_cap_s"
  pilot_step_budget_s="$PILOT_INVOCATION_REMAINING_S"
  pilot_run_timeout_s=$((remaining_s < pilot_step_budget_s ? remaining_s : pilot_step_budget_s))
  if (( pilot_run_timeout_s <= 0 )); then
    echo "B110_PILOT_TIMEOUT_FAILSAFE=1" >&2
    return 124
  fi
  set +e
  timeout --verbose --signal=TERM --kill-after=30s "${pilot_run_timeout_s}s" \
    "$assay_bin" run self-qualification --file assay.toml \
    --candidates-file .assay/b110-pilot-candidates.txt --pilot-jobs 3 --cold-witness --resume \
    --state-dir .assay/b110-pilot-state --progress .assay/progress-b110-pilot.jsonl \
    --campaign-deadline "$pilot_deadline" \
    > .assay/b110-pilot-summary.json 2> .assay/b110-pilot-run.log
  pilot_status=$?
  set -e
  campaign_remaining_after_run_s="$(pilot_campaign_remaining_s "$scratch/run-venv/bin/python" "$pilot_deadline")"
  pilot_invocation_remaining_s "$pilot_started_s" "$pilot_cap_s"
  if (( PILOT_INVOCATION_REMAINING_S <= 0 || campaign_remaining_after_run_s <= 0 )); then
    pilot_status=124
  fi
  echo "B110_PILOT_EXIT=$pilot_status"
  echo "B110_PILOT_SUMMARY=.assay/b110-pilot-summary.json"
  # Any status >= 124 (124, 125-127, 137 after --kill-after) is the failsafe.
  [[ $pilot_status -ge 124 ]] && echo "B110_PILOT_TIMEOUT_FAILSAFE=1"
  [[ $pilot_status -eq 6 ]] && echo "B110_PILOT_COMPLETED=1"
  return 0
}

run_b110_screen() {
  reuse_args=()
  # A prior commit's screen verdict is passed explicitly for witness replay.
  [[ -f .assay/verdict-b110-screen-prev.json ]] \
    && reuse_args=(--reuse-from .assay/verdict-b110-screen-prev.json)
  rm -f -- .assay/verdict-b110-screen.json \
    || { echo "B110_SCREEN_VERDICT_CLEANUP_FAILED=1" >&2; return 1; }
  "$assay_bin" plan self-qualification --file assay.toml > .assay/b110-screen-plan.json \
    || { plan_status=$?; echo "B110_SCREEN_PLANNING_FAILED=1" >&2; return "$plan_status"; }
  set +e
  timeout --verbose --signal=TERM --kill-after=30s 7h10m \
    "$assay_bin" run self-qualification --file assay.toml --cold-witness --resume \
    "${reuse_args[@]}" \
    --state-dir .assay/b110-screen-state --progress .assay/progress-b110-screen.jsonl \
    --verdict-json .assay/verdict-b110-screen.json \
    > .assay/b110-screen-run.log 2>&1
  screen_status=$?
  set -e
  echo "B110_SCREEN_EXIT=$screen_status" || return 1
  echo "B110_SCREEN_VERDICT=.assay/verdict-b110-screen.json" || return 1
  if [[ $screen_status -ge 124 ]]; then
    echo "B110_SCREEN_TIMEOUT_FAILSAFE=1" || return 1
  fi
  if [[ $screen_status -ge 124 ]]; then
    return 0
  fi
  "$assay_bin" verify .assay/verdict-b110-screen.json \
    || { echo "B110_SCREEN_VERIFICATION_FAILED=1" >&2; return 1; }
  screen_checker_output="$("$scratch/run-venv/bin/python" "$scratch/source/assay/tools/b110_screen_report_check.py" \
    --plan .assay/b110-screen-plan.json \
    --verdict .assay/verdict-b110-screen.json \
    --expected-commit "$source_commit" --expected-tree "$source_tree" \
    --expected-exit-code "$screen_status")" \
    || { echo "B110_SCREEN_VERIFICATION_FAILED=1" >&2; return 1; }
  [[ "$screen_checker_output" == "B110_SCREEN_VERIFIED=1" ]] \
    || { echo "B110_SCREEN_VERIFICATION_FAILED=1" >&2; return 1; }
  echo "$screen_checker_output" || return 1
  if [[ ${#reuse_args[@]} -gt 0 ]]; then
    echo "B110_SCREEN_REUSE_FROM=.assay/verdict-b110-screen-prev.json" || return 1
  fi
  return 0
}

case "$requested_lane" in
  self-qualification|self-qualification-preflight|b110-pilot|b110-screen) ;;
  *) die "unsupported self-qualification lane: $requested_lane" ;;
esac

[[ -x "$tester_python" ]] || die "B105 requires tester-unified's $tester_python"
[[ -f "$project/pyproject.toml" ]] || die "selected worktree has no assay/pyproject.toml: $project"

cd "$project"
mkdir -p .assay
source_commit="$(assay_git rev-parse HEAD)"
source_tree="$(assay_git rev-parse 'HEAD^{tree}')"
[[ "$(assay_git rev-parse "${source_commit}^{tree}")" == "$source_tree" ]] \
  || die "captured source commit does not resolve to the captured tree"
expected_commit="${ASSAY_B105_GATE_EXPECTED_COMMIT:-}"
expected_tree="${ASSAY_B105_GATE_EXPECTED_TREE:-}"
[[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] \
  || die 'outer runner did not provide a full expected source commit'
[[ "$expected_tree" =~ ^[0-9a-f]{40}$ ]] \
  || die 'outer runner did not provide a full expected source tree'
[[ "$source_commit" == "$expected_commit" && "$source_tree" == "$expected_tree" ]] \
  || die 'inner source commit/tree differs from the outer runner launch record'
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
campaign=""
deadline=""
if [[ "$requested_lane" == "self-qualification" ]]; then
  campaign="b105-${source_commit:0:12}"
  deadline=".assay/campaign-deadline-$campaign.json"
elif [[ "$requested_lane" == "self-qualification-preflight" ]]; then
  campaign="b105-pre-${source_commit:0:12}"
  deadline=".assay/campaign-deadline-$campaign.json"
elif [[ "$requested_lane" == "b110-pilot" ]]; then
  campaign="b110-pilot-${source_commit:0:12}"
  deadline=".assay/campaign-deadline-$campaign.json"
fi

check_campaign_wheel_digest() {
  "$tester_python" "$scratch/source/assay/tools/b105_report_check.py" \
    --deadline-wheel-check-only \
    --deadline "$deadline" \
    --expected-wheel-sha256 "$wheel_digest"
}

# Bind the wheel before installing the run closure or invoking plan/preflight/
# R2. A same-OID retry must not spend hours on an artifact that the persisted
# campaign deadline cannot accept.
if [[ -n "$deadline" ]]; then
  if [[ -e "$deadline" || -L "$deadline" ]]; then
    check_campaign_wheel_digest \
      || die "existing campaign deadline does not bind this deterministic source wheel"
  fi
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

case "$requested_lane" in
  b110-pilot) run_b110_pilot; exit 0 ;;
  b110-screen) run_b110_screen || exit $?; exit 0 ;;
esac

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
