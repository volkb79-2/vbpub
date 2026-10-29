#!/usr/bin/env bash
set -euo pipefail

worktree="${1:?usage: parallel-safety-regression.sh WORKTREE}"
repo_root="$(git -C "$worktree" rev-parse --show-toplevel)"
base_commit="$(git -C "$repo_root" merge-base HEAD origin/main)"
scratch_root="$repo_root/assay/.assay"
mkdir -p -- "$scratch_root"
scratch="$(mktemp -d "$scratch_root/parallel-safety-regression.XXXXXXXX")"
baseline="$scratch/baseline"
baseline_created=0

cleanup() {
  local status=$?
  trap - EXIT
  if ((baseline_created == 1)); then
    rm -rf -- "$baseline" || status=1
  fi
  rm -rf -- "$scratch" || status=1
  exit "$status"
}
trap cleanup EXIT

git clone --quiet --shared --no-checkout "$repo_root" "$baseline"
baseline_created=1
git -C "$baseline" checkout --quiet --detach "$base_commit"
test_paths=(
  tests/core/test_canary_p23_isolated_edges.py
  tests/core/test_canary_result.py
  tests/core/test_runner_execute.py
  gate/tests/test_distribution_gate.py
  gate/tests/test_self_qualification_container.py
  gate/tests/test_qualify_sql.py
)
for path in "${test_paths[@]}"; do
  install -D "$worktree/assay/$path" "$baseline/assay/$path"
done
install -D \
  "$worktree/assay/src/assay/contention.py" \
  "$baseline/assay/src/assay/contention.py"

baseline_nodes=(
  tests/core/test_canary_p23_isolated_edges.py::test_canary_contention_distinguishes_limits_from_expected_transform_failure
  tests/core/test_canary_result.py::test_an_incomplete_transformed_run_is_inconclusive
  tests/core/test_runner_execute.py::test_failed_r0_command_with_affected_or_unknown_resources_is_infrastructure
  tests/core/test_runner_execute.py::test_completed_r0_pass_is_withheld_only_for_a_direct_limit_event
  gate/tests/test_distribution_gate.py::test_b145_completed_low_pids_failure_is_exit_one
  tests/core/test_runner_execute.py::test_r1_claim_is_withheld_only_for_unclassifiable_contention_windows
  tests/core/test_runner_execute.py::test_r1_contention_adjustment_is_wired_after_coverage_judgement
  gate/tests/test_distribution_gate.py::test_parallel_gate_feature_is_findable_across_user_docs
  gate/tests/test_distribution_gate.py::test_a_busy_host_does_not_block_the_registered_gate
  gate/tests/test_qualify_sql.py::test_a_running_gate_container_does_not_block_sql_qualification
  gate/tests/test_qualify_sql.py::test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher
  gate/tests/test_qualify_sql.py::test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete
)
baseline_report="$scratch/baseline.xml"
if (
  cd "$baseline/assay"
  PYTHONDONTWRITEBYTECODE=1 /opt/tester-venv/bin/python -m pytest -q \
    --junitxml "$baseline_report" "${baseline_nodes[@]}"
); then
  printf '%s\n' 'parallel-safety-regression: base behavior unexpectedly passed' >&2
  exit 1
else
  baseline_status=$?
fi
[[ "$baseline_status" == 1 ]] || {
  printf 'parallel-safety-regression: base test process exited %s, expected pytest failures\n' \
    "$baseline_status" >&2
  exit 1
}
/opt/tester-venv/bin/python - "$baseline_report" <<'PY'
import sys
import xml.etree.ElementTree as ET

expected = {
    # The merge base's public-marker proof and missing completed-pass
    # telemetry must remain distinguishable from this behavior matrix.
    "test_canary_contention_distinguishes_limits_from_expected_transform_failure",
    "test_completed_r0_pass_is_withheld_only_for_a_direct_limit_event",
}
root = ET.parse(sys.argv[1]).getroot()
from collections import Counter

failures = Counter()
errors = []
for case in root.iter("testcase"):
    name = case.attrib.get("name", "").split("[", 1)[0]
    if case.find("failure") is not None:
        failures[name] += 1
    if case.find("error") is not None:
        errors.append(name)
missing = expected - set(failures)
unexpected = set(failures) - expected
if missing or unexpected or errors:
    raise SystemExit(
        "base proof mismatch: "
        f"missing_expected_failures={sorted(missing)}, "
        f"unexpected_failures={sorted(unexpected)}, "
        f"unexpected_errors={errors}"
    )
print(f"parallel-safety-regression: base tests failed as expected ({sum(failures.values())} cases)")
PY

current_nodes=(
  tests/core/test_canary_p23_isolated_edges.py::test_canary_contention_distinguishes_limits_from_expected_transform_failure
  tests/core/test_contention.py
  tests/core/test_liveness_resources.py::test_cgroup_comparison_ignores_host_psi_and_detects_local_pressure
  tests/core/test_liveness_resources.py::test_cgroup_comparison_marks_incomplete_or_reset_samples_unknown
  tests/core/test_canary_result.py::test_an_incomplete_transformed_run_is_inconclusive
  tests/core/test_canary_p23_isolated_edges.py::test_contention_note_retains_measured_pressure_and_bounds_unknown_details
  tests/core/test_runner_execute.py::test_failed_r0_command_with_affected_or_unknown_resources_is_infrastructure
  tests/core/test_runner_execute.py::test_completed_r0_pass_is_withheld_only_for_a_direct_limit_event
  tests/core/test_runner_execute.py::test_r1_claim_is_withheld_only_for_unclassifiable_contention_windows
  tests/core/test_runner_execute.py::test_r1_contention_adjustment_is_wired_after_coverage_judgement
  tests/core/test_runner_execute.py::test_gate_contention_attribution_requires_the_private_opt_in
  gate/tests/test_distribution_gate.py::test_a_busy_host_does_not_block_the_registered_gate
  gate/tests/test_distribution_gate.py::test_parallel_gate_feature_is_findable_across_user_docs
  gate/tests/test_distribution_gate.py::test_b145_completed_low_pids_failure_is_exit_one
  gate/tests/test_distribution_gate.py::test_incomplete_assay_outcomes_are_infrastructure_not_product_failures
  gate/tests/test_distribution_gate.py::test_a_failing_docker_ps_does_not_block_the_gate
  gate/tests/test_cgroup_parent.py::test_b105_requires_finite_point_in_time_ram_headroom
  gate/tests/test_cgroup_parent.py::test_b105_refuses_insufficient_or_unknown_ram_headroom
  gate/tests/test_qualify_sql.py::test_a_running_gate_container_does_not_block_sql_qualification
  gate/tests/test_qualify_sql.py::test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher
  gate/tests/test_qualify_sql.py::test_legacy_shared_host_flag_does_not_change_sql_qualification
  gate/tests/test_qualify_sql.py::test_sql_name_of_another_gate_does_not_block_qualification
  gate/tests/test_qualify_sql.py::test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete
)
cd "$worktree/assay"
PYTHONDONTWRITEBYTECODE=1 /opt/tester-venv/bin/python -m pytest -q "${current_nodes[@]}"
printf '%s\n' 'parallel-safety-regression: current behavior passed'
