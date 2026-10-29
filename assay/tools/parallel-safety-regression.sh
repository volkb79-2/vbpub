#!/usr/bin/env bash
set -euo pipefail

worktree="${1:?usage: parallel-safety-regression.sh WORKTREE}"
repo_root="$(git -C "$worktree" rev-parse --show-toplevel)"
base_commit="$(git -C "$repo_root" merge-base HEAD origin/main)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/assay-parallel-regression.XXXXXXXX")"
baseline="$scratch/baseline"
baseline_added=0

cleanup() {
  local status=$?
  trap - EXIT
  if ((baseline_added == 1)); then
    git -C "$repo_root" worktree remove --force "$baseline" >/dev/null \
      || status=1
  fi
  rm -rf -- "$scratch" || status=1
  exit "$status"
}
trap cleanup EXIT

git -C "$repo_root" worktree add --detach "$baseline" "$base_commit"
baseline_added=1
test_paths=(
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
  tests/core/test_canary_result.py::test_an_incomplete_transformed_run_is_inconclusive
  tests/core/test_runner_execute.py::test_failed_r0_command_with_affected_or_unknown_resources_is_infrastructure
  tests/core/test_runner_execute.py::test_completed_r0_pass_is_withheld_for_direct_limit_or_unknown_window
  gate/tests/test_distribution_gate.py::test_b145_completed_low_pids_failure_is_exit_one
  tests/core/test_runner_execute.py::test_r1_claim_is_withheld_only_for_unclassifiable_contention_windows
  tests/core/test_runner_execute.py::test_r1_contention_adjustment_is_wired_after_coverage_judgement
  gate/tests/test_distribution_gate.py::test_parallel_gate_feature_is_findable_across_user_docs
  gate/tests/test_distribution_gate.py::test_a_busy_host_does_not_block_the_registered_gate
  gate/tests/test_self_qualification_container.py::test_runner_launches_beside_an_active_registered_gate
  gate/tests/test_qualify_sql.py::test_a_running_gate_container_does_not_block_sql_qualification
  gate/tests/test_qualify_sql.py::test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher
  gate/tests/test_qualify_sql.py::test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete
  gate/tests/test_self_qualification_container.py::test_created_container_is_reconciled_after_nonzero_docker_run
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
    "test_an_incomplete_transformed_run_is_inconclusive",
    "test_failed_r0_command_with_affected_or_unknown_resources_is_infrastructure",
    "test_completed_r0_pass_is_withheld_for_direct_limit_or_unknown_window",
    "test_b145_completed_low_pids_failure_is_exit_one",
    "test_r1_claim_is_withheld_only_for_unclassifiable_contention_windows",
    "test_r1_contention_adjustment_is_wired_after_coverage_judgement",
    "test_a_busy_host_does_not_block_the_registered_gate",
    "test_runner_launches_beside_an_active_registered_gate",
    "test_a_running_gate_container_does_not_block_sql_qualification",
    "test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher",
    "test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete",
    "test_created_container_is_reconciled_after_nonzero_docker_run",
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
  tests/core/test_contention.py
  tests/core/test_liveness_resources.py::test_cgroup_comparison_ignores_host_psi_and_detects_local_pressure
  tests/core/test_liveness_resources.py::test_cgroup_comparison_marks_incomplete_or_reset_samples_unknown
  tests/core/test_canary_result.py::test_an_incomplete_transformed_run_is_inconclusive
  tests/core/test_canary_p23_isolated_edges.py::test_a_resource_event_in_either_canary_half_withholds_the_verdict
  tests/core/test_canary_p23_isolated_edges.py::test_contention_note_retains_measured_pressure_and_bounds_unknown_details
  tests/core/test_runner_execute.py::test_failed_r0_command_with_affected_or_unknown_resources_is_infrastructure
  tests/core/test_runner_execute.py::test_completed_r0_pass_is_withheld_for_direct_limit_or_unknown_window
  tests/core/test_runner_execute.py::test_r1_claim_is_withheld_only_for_unclassifiable_contention_windows
  tests/core/test_runner_execute.py::test_r1_contention_adjustment_is_wired_after_coverage_judgement
  tests/core/test_runner_execute.py::test_gate_contention_attribution_requires_the_private_opt_in
  gate/tests/test_distribution_gate.py::test_a_busy_host_does_not_block_the_registered_gate
  gate/tests/test_distribution_gate.py::test_parallel_gate_feature_is_findable_across_user_docs
  gate/tests/test_distribution_gate.py::test_b145_completed_low_pids_failure_is_exit_one
  gate/tests/test_distribution_gate.py::test_incomplete_assay_outcomes_are_infrastructure_not_product_failures
  gate/tests/test_distribution_gate.py::test_a_failing_docker_ps_does_not_block_the_gate
  gate/tests/test_self_qualification_container.py::test_runner_launches_beside_an_active_registered_gate
  gate/tests/test_self_qualification_container.py::test_b105_maps_incomplete_container_exit_to_inconclusive_and_removes_it
  gate/tests/test_self_qualification_container.py::test_b105_preserves_a_completed_assay_fail_as_exit_one
  gate/tests/test_self_qualification_container.py::test_created_container_is_reconciled_after_nonzero_docker_run
  gate/tests/test_qualify_sql.py::test_a_running_gate_container_does_not_block_sql_qualification
  gate/tests/test_qualify_sql.py::test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher
  gate/tests/test_qualify_sql.py::test_legacy_shared_host_flag_does_not_change_sql_qualification
  gate/tests/test_qualify_sql.py::test_sql_name_of_another_gate_does_not_block_qualification
  gate/tests/test_qualify_sql.py::test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete
)
cd "$worktree/assay"
PYTHONDONTWRITEBYTECODE=1 /opt/tester-venv/bin/python -m pytest -q "${current_nodes[@]}"
printf '%s\n' 'parallel-safety-regression: current behavior passed'
