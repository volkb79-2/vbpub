"""B148: native R2 needs a visible cgroup hierarchy before R0 starts."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from conftest import GitRepo

from assay import cli, mutation, resource_limits
from assay.cli import main
from assay.config import load_lane_file
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verify import verify_document


def _native_lane(repo: GitRepo, marker: Path, *, max_mutants: int = 50) -> Path:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def f(x):\n    return 0\n\ndef g(y):\n    return 0\n")
    base = repo.commit_all("seed source")
    repo.write(
        "src/mod.py",
        "def f(x):\n    return x > 0\n\ndef g(y):\n    return y > 0\n",
    )
    repo.commit_all("add native mutation candidates")
    argv = json.dumps(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ]
    )
    path = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = {argv}
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
max_mutants = {max_mutants}
operators = ["python:compare-swap"]
""",
    )
    repo.commit_all("declare native R2 lane")
    return path


def _unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        lambda: resource_limits.ResourceObservationCapability(
            available=False, reason="fixture hides the cgroup ancestor"
        ),
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_selected_native_candidates_refuse_before_r0_when_cgroup_is_hidden(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker)
    _unavailable(monkeypatch)

    code, out, err = _run(
        ["run", "package", "--file", str(lane), "--verdict-json", "-"]
    )

    document = json.loads(out)
    assert code == 3
    assert document["outcome"] == "NO_MEASUREMENT"
    assert document["reason_code"] == "CGROUP_OBSERVATION_UNAVAILABLE"
    assert all(claim["status"] == "NO_MEASUREMENT" for claim in document["claims"])
    assert verify_document(document) == []
    assert not marker.exists()
    assert "complete cgroup v2 ancestor hierarchy" in err
    assert "configure the runner" in err


def test_plan_reports_selected_candidate_visibility_without_refusing(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    lane = _native_lane(git_repo, tmp_path / "r0-ran")
    _unavailable(monkeypatch)
    out, err = io.StringIO(), io.StringIO()

    code = main(["plan", "package", "--file", str(lane)], stdout=out, stderr=err)

    payload = json.loads(out.getvalue())
    assert code == 0
    assert "estimated_serial_seconds" in err.getvalue()
    assert payload["resource_observation"] == {
        "applies_to": "selected_candidates",
        "available": False,
        "reason": "fixture hides the cgroup ancestor",
    }


def test_empty_selected_shard_does_not_trigger_cgroup_refusal(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker)
    _unavailable(monkeypatch)
    lane_file = load_lane_file(lane)
    candidates = cli.plan_jobs(lane_file, lane_file.lane("package"))
    assert candidates != mutation.UNSUPPORTED and candidates
    empty_shard = next(
        index
        for index in range(len(candidates) + 1)
        if not mutation.select_mutation_shard(
            [row["id"] for row in candidates], index=index, count=len(candidates) + 1
        )
    )
    plan_out, plan_err = io.StringIO(), io.StringIO()
    plan_code = main(
        [
            "plan",
            "package",
            "--file",
            str(lane),
            "--shard",
            f"{empty_shard}/{len(candidates) + 1}",
        ],
        stdout=plan_out,
        stderr=plan_err,
    )
    assert plan_code == 0
    assert json.loads(plan_out.getvalue())["resource_observation"] == {
        "applies_to": "none",
        "available": False,
        "reason": "fixture hides the cgroup ancestor",
    }

    code, out, err = _run(
        [
            "run",
            "package",
            "--file",
            str(lane),
            "--shard",
            f"{empty_shard}/{len(candidates) + 1}",
        ]
    )

    assert code == 5
    assert marker.exists()
    assert "CGROUP_OBSERVATION_UNAVAILABLE" not in err
    assert "CGROUP_OBSERVATION_UNAVAILABLE" not in out


def test_over_limit_inventory_keeps_mutant_limit_refusal_after_r0(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker, max_mutants=1)
    _unavailable(monkeypatch)
    plan_out, plan_err = io.StringIO(), io.StringIO()
    plan_code = main(
        ["plan", "package", "--file", str(lane)],
        stdout=plan_out,
        stderr=plan_err,
    )
    assert plan_code == 0
    assert json.loads(plan_out.getvalue())["resource_observation"] == {
        "applies_to": "none",
        "available": False,
        "reason": "fixture hides the cgroup ancestor",
    }

    code, out, err = _run(
        ["run", "package", "--file", str(lane), "--verdict-json", "-"]
    )

    document = json.loads(out)
    assert code == 4
    assert marker.exists()
    assert document["reason_code"] == "MUTANT_LIMIT_EXCEEDED"
    assert "CGROUP_OBSERVATION_UNAVAILABLE" not in err


def test_unsupported_native_discovery_does_not_claim_candidates(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker)
    _unavailable(monkeypatch)
    monkeypatch.setattr(
        mutation,
        "collect_mutation_sites",
        lambda *_args, **_kwargs: mutation.UNSUPPORTED,
    )
    plan_out, plan_err = io.StringIO(), io.StringIO()
    plan_code = main(
        ["plan", "package", "--file", str(lane)],
        stdout=plan_out,
        stderr=plan_err,
    )
    assert plan_code == 0
    assert json.loads(plan_out.getvalue())["resource_observation"] == {
        "applies_to": "none",
        "available": False,
        "reason": "fixture hides the cgroup ancestor",
    }

    code, out, err = _run(
        ["run", "package", "--file", str(lane), "--verdict-json", "-"]
    )

    document = json.loads(out)
    assert marker.exists()
    assert document["reason_code"] == "MUTATION_UNSUPPORTED"
    assert "CGROUP_OBSERVATION_UNAVAILABLE" not in err
    assert "CGROUP_OBSERVATION_UNAVAILABLE" not in out


@pytest.mark.parametrize(
    ("discovery_outcome", "discovery_reason", "expected_outcome", "expected_reason"),
    [
        (
            Outcome.ERROR,
            ReasonCode.GIT_FAILED,
            Outcome.ERROR,
            ReasonCode.GIT_FAILED,
        ),
        (
            Outcome.ERROR,
            ReasonCode.MUTATION_DISCOVERY_FAILED,
            Outcome.NO_MEASUREMENT,
            ReasonCode.CGROUP_OBSERVATION_UNAVAILABLE,
        ),
    ],
)
def test_failed_discovery_stops_before_r0_without_a_false_empty_plan(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch,
    discovery_outcome: Outcome,
    discovery_reason: ReasonCode,
    expected_outcome: Outcome,
    expected_reason: ReasonCode,
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker)
    _unavailable(monkeypatch)

    def fail_discovery(*_args, **_kwargs):
        raise AssayError(
            "fixture could not inspect candidate source",
            outcome=discovery_outcome,
            reason_code=discovery_reason,
        )

    monkeypatch.setattr(cli, "_discover_plan_jobs", fail_discovery)
    code, out, err = _run(
        ["run", "package", "--file", str(lane), "--verdict-json", "-"]
    )

    document = json.loads(out)
    assert code == expected_outcome.exit_code
    assert document["outcome"] == expected_outcome.value
    assert document["reason_code"] == expected_reason.value
    assert all(
        claim["reason_code"] == expected_reason.value for claim in document["claims"]
    )
    assert all(
        "fixture could not inspect candidate source" in claim["detail"]
        for claim in document["claims"]
    )
    assert not marker.exists()
    assert expected_reason.value in err
