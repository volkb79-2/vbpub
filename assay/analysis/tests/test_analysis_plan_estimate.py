"""``assay analyze plan-estimate``: a measured, advisory projection (CD25).

Every result is asserted through ``assay_analysis.cli.main``; no test reads a
clock, sleeps, or relies on host speed. Timestamps are fixed literals.
"""

from __future__ import annotations

import io
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assay import cli as judge_cli
from assay_analysis import cli as analysis_cli
from assay_analysis import plan_estimate

C = "a" * 40
T = "b" * 40
OTHER = "c" * 40
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _plan(**overrides):
    document = {
        "status": "ok", "commit": C, "tree": T, "candidate_count": 4,
        "candidates": [{"id": str(i)} for i in range(4)],
        "estimated_serial_seconds": 240.0, "estimated_wall_seconds": 240.0,
        "jobs": 1, "budget_per_candidate": None,
    }
    document.update(overrides)
    return document


def _run(commit=C, **extra):
    return {"event": "run", "commit": commit, **extra}


def _finished(phase="baseline", outcome="PASS", seconds=40.0, **extra):
    record = {"event": "command_finished", "phase": phase, "outcome": outcome,
              "started": EPOCH.isoformat(),
              "ended": (EPOCH + timedelta(seconds=seconds)).isoformat()}
    record.update(extra)
    return record


def _lines(records):
    return "".join(json.dumps(record) + "\n" for record in records)


def _invoke(tmp_path, plan, progress, *extra):
    plan_path = tmp_path / "plan.json"
    progress_path = tmp_path / "progress.jsonl"
    plan_path.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
    progress_path.write_text(progress if isinstance(progress, str) else _lines(progress),
                             encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = analysis_cli.main(["plan-estimate", "--plan-json", str(plan_path),
                              "--progress", str(progress_path), *extra], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _ok(tmp_path, plan, progress, *extra):
    code, out, err = _invoke(tmp_path, plan, progress, *extra)
    assert (code, err) == (0, "")
    return json.loads(out)


def _refused(tmp_path, plan, progress, message, *extra):
    code, out, err = _invoke(tmp_path, plan, progress, *extra)
    assert (code, out) == (2, "")
    assert err == f"assay analyze: {message}\n"


def test_op1_a_plan_baseline_gives_exactly_the_documented_projection(tmp_path):
    progress = [_run(lane="l"), {"event": "plan", "baseline_s": 100.0}]
    expected = {"schema_version": 1, "commit": C, "tree": T, "candidates": 4,
                "baseline_s": 100.0, "per_candidate_s": 100.0, "workers": 1,
                "projected_worker_hours": 0.111, "projected_wall_hours": 0.111}
    assert _ok(tmp_path, _plan(), progress) == expected
    wide = _ok(tmp_path, _plan(), progress, "--workers", "4")
    assert wide["projected_wall_hours"] == 0.028
    assert wide["projected_worker_hours"] == 0.111 and wide["workers"] == 4


def test_op2_the_last_segment_with_a_completed_baseline_is_measured(tmp_path):
    progress = [
        _run(), {"event": "plan", "baseline_s": 50},
        _run(),
        _finished("baseline", "FAIL", 25.0),
        _finished("baseline", "PASS", 40.0),
        _run(), {"event": "candidates"},
    ]
    assert _ok(tmp_path, _plan(), progress)["baseline_s"] == 40.0


def test_a_direct_phase_pass_is_a_baseline_and_a_none_plan_value_falls_back(tmp_path):
    progress = [_run(), {"event": "plan", "baseline_s": None},
                _finished("direct", "PASS", 100.0), _finished("other", "PASS", 5.0)]
    result = _ok(tmp_path, _plan(), progress)
    assert result["baseline_s"] == 100.0 and result["projected_worker_hours"] == 0.111


def test_the_last_passing_command_finished_of_the_segment_is_used(tmp_path):
    progress = [_run(), _finished("baseline", "PASS", 10.0), _finished("direct", "PASS", 20.0)]
    assert _ok(tmp_path, _plan(), progress)["baseline_s"] == 20.0


def test_a_run_without_a_baseline_never_hides_an_earlier_measurement(tmp_path):
    progress = [_run(lane=None), {"event": "plan", "baseline_s": 8}, _run(lane="only")]
    assert _ok(tmp_path, _plan(), progress)["baseline_s"] == 8.0


def test_op3_a_later_baseline_at_another_commit_is_a_mismatch_not_a_filter(tmp_path):
    progress = [_run(), {"event": "plan", "baseline_s": 1},
                _run(OTHER), {"event": "plan", "baseline_s": 2}]
    _refused(tmp_path, _plan(), progress,
             f"commit mismatch: progress run at line 3 is at '{OTHER}', plan is at '{C}'")


def test_op3_a_run_with_no_commit_is_a_mismatch(tmp_path):
    progress = [{"event": "run"}, {"event": "plan", "baseline_s": 1}]
    _refused(tmp_path, _plan(), progress,
             f"commit mismatch: progress run at line 1 is at None, plan is at '{C}'")


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ("{", "plan: malformed JSON"),
        ("[]", "plan: top level is not an object"),
        ({"status": "unsupported"}, "plan: status is 'unsupported', not 'ok'"),
        ({**_plan(), "commit": None}, "plan: commit is missing or not a full object id "
                                        "(a plan from before assay 7.2.0?)"),
        ({**_plan(), "commit": "A" * 40}, "plan: commit is missing or not a full object id "
                                           "(a plan from before assay 7.2.0?)"),
        ({**_plan(), "tree": "b" * 39}, "plan: tree is missing or not a full object id "
                                        "(a plan from before assay 7.2.0?)"),
        ({**_plan(), "candidate_count": "4"}, "plan: candidate_count is not a non-negative integer"),
        ({**_plan(), "candidate_count": True}, "plan: candidate_count is not a non-negative integer"),
        ({**_plan(), "candidate_count": -1}, "plan: candidate_count is not a non-negative integer"),
        ({**_plan(), "candidates": None}, "plan: candidate_count does not match candidates"),
        ({**_plan(), "candidates": []}, "plan: candidate_count does not match candidates"),
    ],
)
def test_op4_every_plan_refusal_exits_two_with_one_stderr_line(tmp_path, plan, message):
    _refused(tmp_path, plan, [_run(), {"event": "plan", "baseline_s": 1}], message)


def test_a_sha256_object_id_is_accepted(tmp_path):
    long = "d" * 64
    result = _ok(tmp_path, _plan(commit=long, tree=long),
                 [_run(long), {"event": "plan", "baseline_s": 1}])
    assert result["commit"] == long and result["tree"] == long


def test_a_zero_candidate_plan_projects_zero_hours(tmp_path):
    result = _ok(tmp_path, _plan(candidate_count=0, candidates=[]),
                 [_run(), {"event": "plan", "baseline_s": 3}])
    assert (result["candidates"], result["projected_worker_hours"]) == (0, 0.0)


@pytest.mark.parametrize(
    ("progress", "message"),
    [
        ("[]\n", "progress line 1: not an object"),
        ('{"event": 1}\n', "progress line 1: event is not a string"),
        ('{"x": 1}\n', "progress line 1: event is not a string"),
        ('{"event": "plan"}\n', "progress line 1: event precedes the first run header"),
        ('{"event": "run"}\nnope\n{"event": "plan"}\n', "progress line 2: malformed JSON"),
        ('{"event": "run"}\n\n', "progress line 2: malformed JSON"),
        ('{"event": "run", "commit": "%s"}\n[]' % C, "progress line 2: not an object"),
        ("", "progress: no completed baseline"),
        (_lines([_run(), {"event": "candidates"}]), "progress: no completed baseline"),
        (_lines([_run(), _finished("baseline", "FAIL")]), "progress: no completed baseline"),
        (_lines([_run(lane="a"), _run(lane="b")]),
         "progress: runs of more than one lane (['a', 'b']); pass a single-lane progress file"),
        (_lines([_run(), {"event": "plan", "baseline_s": 1}, {"event": "plan", "baseline_s": 2}]),
         "progress run at line 1: multiple plan events"),
    ],
)
def test_op4_every_progress_refusal_exits_two_with_one_stderr_line(tmp_path, progress, message):
    _refused(tmp_path, _plan(), progress, message)


@pytest.mark.parametrize("raw", ["0", "-1", '"5"', "true", "false", "[]", "1e999", "1" + "0" * 400])
def test_op4_a_plan_baseline_that_is_not_a_finite_positive_number_is_refused(tmp_path, raw):
    text = '{"event": "run", "commit": "%s"}\n' % C
    text += '{"event": "plan", "baseline_s": %s}\n' % raw
    _refused(tmp_path, _plan(), text,
             "progress run at line 1: plan.baseline_s is not a finite positive number")


@pytest.mark.parametrize(
    "record",
    [
        {**_finished(), "started": "garbage"},
        {**_finished(), "ended": None},
        {**_finished(), "started": 5},
        {"event": "command_finished", "phase": "baseline", "outcome": "PASS"},
        {**_finished(), "started": "2026-01-01T00:00:00", "ended": "2026-01-01T00:00:40+00:00"},
        {**_finished(), "started": "2026-01-01T00:00:00+00:00", "ended": "2026-01-01T00:00:40"},
        _finished(seconds=0.0),
        _finished(seconds=-5.0),
    ],
)
def test_op4_an_invalid_command_finished_interval_is_refused(tmp_path, record):
    _refused(tmp_path, _plan(), [_run(), record],
             "progress run at line 1: the selected command_finished record has an invalid interval")


def test_op4_oversize_inputs_and_non_files_are_refused(tmp_path, monkeypatch):
    progress = [_run(), {"event": "plan", "baseline_s": 1}]
    monkeypatch.setattr(plan_estimate, "MAX_PROGRESS_BYTES", 10)
    _refused(tmp_path, _plan(), progress, f"progress {tmp_path / 'progress.jsonl'}: exceeds 10 bytes")
    monkeypatch.setattr(plan_estimate, "MAX_PLAN_BYTES", 10)
    _refused(tmp_path, _plan(), progress, f"plan {tmp_path / 'plan.json'}: exceeds 10 bytes")
    out, err = io.StringIO(), io.StringIO()
    missing = tmp_path / "absent"
    code = analysis_cli.main(["plan-estimate", "--plan-json", str(missing),
                              "--progress", str(tmp_path)], stdout=out, stderr=err)
    assert (code, out.getvalue()) == (2, "")
    assert err.getvalue() == f"assay analyze: plan {missing}: not a regular file\n"


def test_a_directory_progress_is_not_a_regular_file(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan()), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = analysis_cli.main(["plan-estimate", "--plan-json", str(plan),
                              "--progress", str(tmp_path)], stdout=out, stderr=err)
    assert (code, out.getvalue()) == (2, "")
    assert err.getvalue() == f"assay analyze: progress {tmp_path}: not a regular file\n"


def test_op4_a_torn_final_line_is_ignored_and_never_refused(tmp_path):
    text = _lines([_run(), {"event": "plan", "baseline_s": 100}]) + '{"event": "comm'
    assert _ok(tmp_path, _plan(), text)["baseline_s"] == 100.0


def test_a_parseable_tail_run_at_another_commit_after_a_valid_baseline_is_not_selected(tmp_path):
    text = _lines([_run(), {"event": "plan", "baseline_s": 100}])
    text += json.dumps(_run(OTHER))
    assert _ok(tmp_path, _plan(), text)["baseline_s"] == 100.0


def test_a_parseable_tail_is_validated_like_any_other_line(tmp_path):
    text = _lines([_run(), {"event": "plan", "baseline_s": 100}]) + "[]"
    _refused(tmp_path, _plan(), text, "progress line 3: not an object")


@pytest.mark.parametrize("workers", ["0", "65", "-1", "x", "1.5"])
def test_op4_workers_outside_one_to_sixty_four_exit_two(tmp_path, capsys, workers):
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps(_plan()), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        analysis_cli.main(["plan-estimate", "--plan-json", str(plan), "--progress", str(plan),
                           "--workers", workers], stdout=io.StringIO(), stderr=io.StringIO())
    captured = capsys.readouterr()
    assert raised.value.code == 2 and captured.out == ""
    assert "--workers must be an integer from 1 to 64" in captured.err


def test_the_workers_bounds_are_inclusive(tmp_path):
    progress = [_run(), {"event": "plan", "baseline_s": 3600}]
    assert _ok(tmp_path, _plan(candidate_count=64, candidates=[0] * 64), progress,
               "--workers", "64")["projected_wall_hours"] == 1.0


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _real_lane_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "assay-tests@example.com")
    _git(repo, "config", "user.name", "assay tests")
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add mod.py")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "mod.py").write_text("def f(x):\n    return x > 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "introduce a compare-swap site")
    toml = repo / "assay.toml"
    toml.write_text(f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "grep -q 'x > 0' src/mod.py"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
""", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add assay.toml")
    return toml, _git(repo, "rev-parse", "HEAD")


def test_op5_a_real_plan_and_a_real_run_progress_project_through_the_judge_entry_point(tmp_path):
    toml, head = _real_lane_repo(tmp_path)
    plan_out, plan_err = io.StringIO(), io.StringIO()
    assert judge_cli.main(["plan", "package", "--file", str(toml)],
                          stdout=plan_out, stderr=plan_err) == 0
    plan = json.loads(plan_out.getvalue())
    assert plan["commit"] == head and plan["tree"] == _git(toml.parent, "rev-parse", "HEAD^{tree}")
    plan_path, progress = tmp_path / "plan.json", tmp_path / "progress.jsonl"
    plan_path.write_text(plan_out.getvalue(), encoding="utf-8")
    run_out, run_err = io.StringIO(), io.StringIO()
    assert judge_cli.main(["run", "package", "--file", str(toml), "--progress", str(progress)],
                          stdout=run_out, stderr=run_err) == 0, run_err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    code = judge_cli.main(["analyze", "plan-estimate", "--plan-json", str(plan_path),
                           "--progress", str(progress), "--workers", "2"], stdout=out, stderr=err)
    assert (code, err.getvalue()) == (0, "")
    result = json.loads(out.getvalue())
    assert result["candidates"] == plan["candidate_count"] == 1
    assert result["baseline_s"] > 0
    assert (result["commit"], result["tree"]) == (plan["commit"], plan["tree"])
    assert result["workers"] == 2
    direct = _ok(tmp_path, plan_path.read_text(encoding="utf-8"), progress.read_text(encoding="utf-8"))
    assert direct["baseline_s"] == result["baseline_s"]
