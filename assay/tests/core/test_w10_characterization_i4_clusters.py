"""W10 step 1: the I4 same-side clusters (E1, E2, E6-E10), pinned before they move.

Each section names the cluster's brief row. Behaviour is characterized as-is
against the unchanged source and this file is never edited afterwards (W10
brief, step 1). E3/E4/E5 are cited in the LOG (existing tests pin them).
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import FakeAdapter, make_deadline, make_lane, make_plan

from assay import canary, git, liveness
from assay.coverage import derive_branch_capability, derive_exclusion_capability
from assay.coverage_parsers.model import BranchCoverage, CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import evaluate_targets
from assay.liveness_resources import (
    RESOURCE_SNAPSHOT_SCHEMA_VERSION,
    compare_resource_snapshots,
)
from assay.mutation_witness import replay_witness_from_receipt, witness_from_receipt
from assay.runner import CommandResult, SnapshotUnitResult
from assay.verdict import JudgmentR1, JudgmentR2

# --- E1: git returncode 0 / 1 / other ----------------------------------------

_REMAINING = lambda: 60.0  # noqa: E731 - a fixed budget, never a clock read

_E1_CALLS = (
    (
        "path_is_ignored",
        lambda: git.path_is_ignored(Path("."), "some/rel.txt", remaining=_REMAINING),
        "git check-ignore some/rel.txt",
    ),
    (
        "is_ancestor",
        lambda: git.is_ancestor(Path("."), "aaa", "bbb", remaining=_REMAINING),
        "git merge-base --is-ancestor",
    ),
    (
        "path_is_current",
        lambda: git.path_is_current(Path("."), "aaa", "bbb", "p.txt", remaining=_REMAINING),
        "git diff --quiet",
    ),
)


def _patch_raw(monkeypatch, returncode: int, stderr: bytes = b"") -> None:
    monkeypatch.setattr(
        git, "_run_raw", lambda *args, **kwargs: (returncode, b"", stderr)
    )


@pytest.mark.parametrize(("name", "call", "label"), _E1_CALLS)
def test_git_returncode_zero_is_true_and_one_is_false(monkeypatch, name, call, label):
    _patch_raw(monkeypatch, 0)
    assert call() is True
    _patch_raw(monkeypatch, 1, b"ignored stderr")
    assert call() is False


@pytest.mark.parametrize("returncode", [2, 128, -1, 255])
@pytest.mark.parametrize(("name", "call", "label"), _E1_CALLS)
def test_any_other_git_returncode_is_a_typed_failure_with_the_exact_text(
    monkeypatch, name, call, label, returncode
):
    _patch_raw(monkeypatch, returncode, b"  fatal: bad thing \n")
    with pytest.raises(AssayError) as caught:
        call()
    assert str(caught.value) == f"{label} failed ({returncode}): fatal: bad thing"
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert caught.value.outcome is Outcome.ERROR


@pytest.mark.parametrize(("name", "call", "label"), _E1_CALLS)
def test_git_stderr_is_decoded_with_replacement_stripped_then_cut_at_200(
    monkeypatch, name, call, label
):
    _patch_raw(monkeypatch, 2, b" \xffbad" + b"e" * 300)
    with pytest.raises(AssayError) as caught:
        call()
    expected = (b" \xffbad" + b"e" * 300).decode("utf-8", errors="replace").strip()[:200]
    assert str(caught.value) == f"{label} failed (2): {expected}"
    assert expected.startswith("�bad")
    assert len(expected) == 200


# --- E2: liveness_resources counter deltas -----------------------------------

_PRESSURE = (
    ("host_psi", "cpu", "some"),
    ("host_psi", "memory", "some"),
    ("host_psi", "memory", "full"),
    ("host_psi", "io", "some"),
    ("host_psi", "io", "full"),
    ("cgroup_psi", "cpu", "some"),
    ("cgroup_psi", "memory", "some"),
    ("cgroup_psi", "memory", "full"),
    ("cgroup_psi", "io", "some"),
    ("cgroup_psi", "io", "full"),
)
_COUNTERS = [
    (("psi", scope, name, row), f"{scope}.{name}.{row}_us") for scope, name, row in _PRESSURE
] + [
    (("cpu", "cgroup_cpu", key), f"cgroup_cpu.{key}")
    for key in ("nr_throttled", "throttled_usec")
]


def _snapshot() -> dict:
    return {
        "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION,
        "status": "available",
        "cgroup_identity": "id",
        "host_psi": {
            "cpu": {"some": 5},
            "memory": {"some": 5, "full": 5},
            "io": {"some": 5, "full": 5},
        },
        "cgroup_psi": {
            "cpu": {"some": 5},
            "memory": {"some": 5, "full": 5},
            "io": {"some": 5, "full": 5},
        },
        "cgroup_cpu": {"nr_throttled": 5, "throttled_usec": 5},
    }


def _set(snapshot: dict, path: tuple, value) -> None:
    if path[0] == "psi":
        _, scope, name, row = path
        snapshot[scope][name][row] = value
    else:
        _, scope, key = path
        snapshot[scope][key] = value


def _compare(path: tuple, old, new):
    previous, current = _snapshot(), _snapshot()
    _set(previous, path, old)
    _set(current, path, new)
    return compare_resource_snapshots(previous, current)


@pytest.mark.parametrize(("path", "key"), _COUNTERS)
def test_a_zero_delta_on_every_counter_is_clear_and_a_positive_one_is_named(path, key):
    assert compare_resource_snapshots(_snapshot(), _snapshot()) == ("clear", {})
    assert _compare(path, 0, 0) == ("clear", {})
    assert _compare(path, 5, 5) == ("clear", {})
    assert _compare(path, 5, 8) == ("stalled", {key: 3})


@pytest.mark.parametrize(("path", "key"), _COUNTERS)
@pytest.mark.parametrize(
    ("old", "new"),
    [
        (5, 4),
        (-1, 0),
        (0, -1),
        (True, 6),
        (5, True),
        (True, True),
        (False, False),
        (5.0, 6),
        (5, 6.0),
        ("5", 6),
        (5, None),
        (None, 5),
    ],
)
def test_a_non_int_bool_negative_or_backwards_counter_is_unknown(path, key, old, new):
    assert _compare(path, old, new) == ("unknown", {})


# --- E6: liveness _EventProgressReader init/_reset ----------------------------

_READER_DEFAULTS = {
    "_path": None,
    "_identity": None,
    "_offset": 0,
    "_pending": b"",
    "_count": 0,
    "_has_finish": False,
    "_all_finish_pids_valid": True,
    "_candidate_finish_seen": False,
}


def test_a_fresh_event_progress_reader_holds_exactly_these_eight_defaults_and_the_pid():
    reader = liveness._EventProgressReader(4242)
    assert vars(reader) == {"_candidate_pid": 4242, **_READER_DEFAULTS}
    assert vars(liveness._EventProgressReader(None)) == {
        "_candidate_pid": None,
        **_READER_DEFAULTS,
    }
    assert vars(liveness._EventProgressReader(-3)) == {
        "_candidate_pid": None,
        **_READER_DEFAULTS,
    }


def test_reset_restores_the_eight_defaults_and_keeps_the_pid():
    reader = liveness._EventProgressReader(7)
    reader._offset = 9
    reader._pending = b"x"
    reader._count = 3
    reader._has_finish = True
    reader._all_finish_pids_valid = False
    reader._candidate_finish_seen = True
    reader._reset()
    assert vars(reader) == {"_candidate_pid": 7, **_READER_DEFAULTS}
    reader._reset(Path("/tmp/events"), (1, 2))
    assert vars(reader) == {
        "_candidate_pid": 7,
        **_READER_DEFAULTS,
        "_path": Path("/tmp/events"),
        "_identity": (1, 2),
    }


# --- E7: mutation_witness ``is False`` on receipt keys ------------------------


def _receipt(**overrides):
    value = {
        "unsupported": False,
        "unsupported_pytest_cov_only": False,
        "replay_supported": False,
        "target_node_id": None,
        "target_count": None,
        "earlier_failure": False,
        "auxiliary_failure": False,
        "witness_node_id": "tests/test_example.py::test_case",
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": False,
        "cold_requested": False,
        "stopped_cold": False,
        "collection_error": False,
        "manifest_supported": True,
        "collection_count": 1,
        "collection_sha256": "a" * 64,
        "collection_duplicates": 0,
        "started_count": 1,
        "started_prefix_ok": True,
        "failed_call_index": None,
        "hook_fingerprint_sha256": "b" * 64,
        "hook_count": 10,
        "runtime_fingerprint_sha256": "c" * 64,
        "config_sha256": None,
        "archive_hook_exception_used": False,
    }
    value.update(overrides)
    return value


_FALSY_NOT_FALSE = [0, 0.0, None, "", "false", [], {}, 1, True]


@pytest.mark.parametrize("value", _FALSY_NOT_FALSE)
@pytest.mark.parametrize("key", ["unsupported", "auxiliary_failure"])
def test_a_projected_witness_needs_the_literal_false_on_each_key(key, value):
    assert witness_from_receipt(_receipt(), process_exit_status=1) is not None
    assert witness_from_receipt(_receipt(**{key: value}), process_exit_status=1) is None


@pytest.mark.parametrize("value", _FALSY_NOT_FALSE)
def test_a_replayed_witness_needs_the_literal_false_on_earlier_failure(value):
    node = "tests/test_example.py::test_case"

    def replay(**overrides):
        receipt = _receipt(
            target_node_id=node,
            target_count=1,
            witness_node_id=node,
            stopped_at_target=True,
        )
        receipt.update(overrides)
        return replay_witness_from_receipt(
            receipt, process_exit_status=1, target_node_id=node
        )

    assert replay() is not None
    assert replay(earlier_failure=value) is None


# --- E8: canary `_judge_unit` R0-then-R1 shortcut ------------------------------


def _unit(outcome: Outcome, reason_code: ReasonCode | None, lane) -> SnapshotUnitResult:
    return SnapshotUnitResult(
        result=CommandResult(
            plan=make_plan(lane),
            outcome=outcome,
            reason_code=reason_code,
            returncode=0,
            started="2026-09-01T00:00:00+00:00",
            ended="2026-09-01T00:00:01+00:00",
        ),
        post_reason=None,
        profile=None,
        profile_error=None,
    )


def _judge(lane, unit):
    return canary._judge_unit(
        lane,
        unit,
        repo=Path("/repo"),
        project_root=Path("/repo"),
        scratch_project_root=Path("/tmp/snapshot"),
        base=None,
        adapter=object(),
        deadline=make_deadline(),
    )


def test_judge_unit_returns_the_r0_verdict_without_r1_when_r0_did_not_pass(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("R1 must not be evaluated")

    monkeypatch.setattr(canary, "evaluate_r1", boom)
    lane = make_lane(rigor=("R0", "R1"))
    unit = _unit(Outcome.FAIL, ReasonCode.COMMAND_FAILED, lane)
    assert _judge(lane, unit) == (Outcome.FAIL, ReasonCode.COMMAND_FAILED)


def test_judge_unit_returns_the_r0_verdict_without_r1_when_the_lane_declares_no_r1(
    monkeypatch,
):
    def boom(*args, **kwargs):
        raise AssertionError("R1 must not be evaluated")

    monkeypatch.setattr(canary, "evaluate_r1", boom)
    lane = make_lane(rigor=("R0",))
    assert _judge(lane, _unit(Outcome.PASS, None, lane)) == (Outcome.PASS, None)


def test_judge_unit_returns_the_r1_claim_when_r0_passed_and_r1_is_declared(monkeypatch):
    class _Claim:
        status = Outcome.FAIL
        reason_code = ReasonCode.UNCOVERED_LINES

    monkeypatch.setattr(canary, "evaluate_r1", lambda *args, **kwargs: _Claim())
    monkeypatch.setattr(canary, "_relocate_source_roots", lambda lane, **kwargs: lane)
    lane = make_lane(rigor=("R0", "R1"))
    assert _judge(lane, _unit(Outcome.PASS, None, lane)) == (
        Outcome.FAIL,
        ReasonCode.UNCOVERED_LINES,
    )


# `canary._run_pipeline` (the twin) is pinned end to end by
# tests/adapters/python/test_canary_python_pipeline.py, cited in the LOG.

# --- E9: coverage capability derivation (exact refusal texts) ------------------


def _files(**files: FileCoverage) -> CoverageProfile:
    return CoverageProfile(files=dict(files))


def test_the_mixed_exclusion_refusal_text_is_exact():
    reported = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset())
    silent = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=None)
    with pytest.raises(AssayError) as caught:
        derive_exclusion_capability(_files(a=reported, b=silent))
    assert str(caught.value) == (
        "coverage artifact mixes files that report exclusions with files "
        "that cannot report them at all; one artifact is one tool's "
        "output, so this cannot be read without inventing a precedence "
        "rule the lane never declared"
    )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert derive_exclusion_capability(_files(a=reported)) == "reported"
    assert derive_exclusion_capability(_files(a=silent)) == "unavailable"


def test_the_mixed_branch_refusal_text_is_exact():
    reported = FileCoverage(
        executed=frozenset({1}),
        missing=frozenset(),
        excluded=None,
        branches=BranchCoverage(by_line={}),
    )
    silent = FileCoverage(
        executed=frozenset({1}), missing=frozenset(), excluded=None, branches=None
    )
    with pytest.raises(AssayError) as caught:
        derive_branch_capability(_files(a=silent, b=reported))
    assert str(caught.value) == (
        "coverage artifact mixes files that report branch arcs with "
        "files that cannot report them at all; one artifact is one "
        "tool's output, so this cannot be read without inventing a "
        "precedence rule the lane never declared"
    )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert derive_branch_capability(_files(a=reported)) == "reported"
    assert derive_branch_capability(_files(a=silent)) == "unavailable"


# --- E10: evaluate branch-only shortfall (whole-target path) -------------------
# The changed-lines twin is pinned by tests/core/test_evaluate_branch_coverage.py
# (branch deficit alone -> UNCOVERED_BRANCHES; missing line takes precedence).


@pytest.fixture()
def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.zzz").write_text("code\n")
    return tmp_path


def _whole_target(repo: Path, record: FileCoverage, *, fail_under: float = 100.0):
    return evaluate_targets(
        profile=CoverageProfile(files=MappingProxyType({"pkg/mod.zzz": record})),
        adapter=FakeAdapter(),
        repo_top=repo,
        project_root=repo,
        targets=("pkg/mod.zzz",),
        source_root_paths=(repo / "pkg",),
        fail_under=fail_under,
        allow_excluded=False,
    )


def test_a_missing_line_beside_a_branch_deficit_is_uncovered_lines_on_the_whole_target_path(
    _repo,
):
    result = _whole_target(
        _repo,
        FileCoverage(
            executed=frozenset({1}),
            missing=frozenset({2}),
            excluded=frozenset(),
            branches=BranchCoverage(by_line={1: (1, 2)}),
        ),
    )
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.missing_lines == {"pkg/mod.zzz": frozenset({2})}


def test_a_branch_deficit_under_a_lower_floor_that_still_passes_has_no_reason(_repo):
    result = _whole_target(
        _repo,
        FileCoverage(
            executed=frozenset({1}),
            missing=frozenset(),
            excluded=frozenset(),
            branches=BranchCoverage(by_line={1: (1, 2)}),
        ),
        fail_under=50.0,
    )
    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.branches_covered == 1


# --- E5: cli verdict delivery tail (write -> verdict_written -> summary -> exit) --
# The two timed-out sites are exercised (exit code, artifact) by
# tests/core/test_lane_timeout_writes_a_verdict.py, cited in the LOG; the
# ordering below is pinned on the normal tail, the body all three share.


def _cli(argv):
    import io

    from assay.cli import main

    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out, err


def _lane_file(git_repo, exit_code: int) -> Path:
    from conftest import R0_LANE, set_key

    path = git_repo.write(
        "assay.toml", set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "exit {exit_code}"]')
    )
    git_repo.commit_all("add assay.toml")
    return path


def test_the_summary_follows_the_write_and_carries_the_exit_code(
    git_repo, tmp_path, monkeypatch
):
    from assay import runner

    path = _lane_file(git_repo, 7)
    target = tmp_path / "verdict.json"
    real = runner.write_verdict
    seen = {}

    def spy(verdict, destination):
        seen["stdout_before_write"] = seen["out"].getvalue()
        return real(verdict, destination)

    import io

    from assay.cli import main

    out, err = io.StringIO(), io.StringIO()
    seen["out"] = out
    monkeypatch.setattr(runner, "write_verdict", spy)
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)],
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert seen["stdout_before_write"] == ""
    assert target.is_file()
    assert out.getvalue().splitlines()[0] == "package: FAIL/COMMAND_FAILED (exit 1)"
    assert out.getvalue().splitlines()[1].startswith("  commit: ")


def test_a_dash_destination_prints_no_summary_after_the_verdict(git_repo):
    path = _lane_file(git_repo, 7)
    code, out, _err = _cli(["run", "package", "--file", str(path), "--verdict-json", "-"])
    assert code == 1
    import json

    assert json.loads(out.getvalue())["reason_code"] == "COMMAND_FAILED"


def test_a_failed_write_prints_no_summary_and_returns_the_write_error(
    git_repo, tmp_path, monkeypatch
):
    from assay import runner

    def refuse(verdict, destination):
        raise AssayError(
            "cannot deliver", outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
        )

    monkeypatch.setattr(runner, "write_verdict", refuse)
    path = _lane_file(git_repo, 0)
    code, out, err = _cli(
        ["run", "package", "--file", str(path), "--verdict-json", str(tmp_path / "v.json")]
    )
    assert code == Outcome.ERROR.exit_code
    assert "package: PASS" not in out.getvalue()
    assert out.getvalue() == ""
    assert "cannot deliver" in err.getvalue()


# --- E4: verdict whole-target ``targets`` pairing (R1 and R2 exact texts) ------


def _r1_with(mode, targets):
    return JudgmentR1(
        coverage_format="coverage-py-json",
        coverage_artifact="coverage.json",
        fail_under=100.0,
        allow_excluded=False,
        mode=mode,
        targets=targets,
    )


def _r2_with(mode, targets):
    return JudgmentR2(
        jobs=1,
        max_mutants=50,
        operators=("python:compare-swap",),
        mode=mode,
        targets=targets,
        kill_attribution="unattributed",
        cold_witness_kills=False,
    )


_E4_TIERS = (("judgment.r1", _r1_with), ("judgment.r2", _r2_with))


def _refused(build, mode, targets) -> str:
    with pytest.raises(ValueError) as caught:
        build(mode, targets)
    return str(caught.value)


@pytest.mark.parametrize(("label", "build"), _E4_TIERS)
def test_whole_target_targets_accept_a_sorted_unique_relative_tuple(label, build):
    assert build("whole_target", ("a.py", "b.py")).targets == ("a.py", "b.py")
    assert build("changed_lines", None).targets is None


@pytest.mark.parametrize(("label", "build"), _E4_TIERS)
@pytest.mark.parametrize("value", [None, (), ["a.py"]])
def test_whole_target_targets_must_be_a_non_empty_tuple(label, build, value):
    assert _refused(build, "whole_target", value) == (
        f"{label}.targets must be a non-empty tuple when mode is "
        f"'whole_target', got {value!r}"
    )


@pytest.mark.parametrize(("label", "build"), _E4_TIERS)
def test_whole_target_targets_refuse_a_bad_entry_a_duplicate_and_an_unsorted_tuple(
    label, build
):
    assert _refused(build, "whole_target", ("/abs",)) == (
        f"{label}.targets entry '/abs' must be relative and must not end in a separator"
    )
    assert _refused(build, "whole_target", ("a", "a")) == (
        f"{label}.targets contains a duplicate: ['a', 'a']"
    )
    assert _refused(build, "whole_target", ("b", "a")) == (
        f"{label}.targets must be sorted, got ['b', 'a']"
    )


@pytest.mark.parametrize(("label", "build"), _E4_TIERS)
@pytest.mark.parametrize("targets", [("a",), ()])
def test_targets_outside_whole_target_mode_are_refused(label, build, targets):
    assert _refused(build, "changed_lines", targets) == (
        f"{label}.targets is present ({list(targets)}) but mode is "
        f"'changed_lines', not 'whole_target' -- targets describes nothing "
        f"outside whole-target mode"
    )
