"""B148: native R2 needs a visible cgroup hierarchy; preserve independent R0."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from conftest import GitRepo

from assay import cli, mutation, resource_limits
from assay.cli import main
from assay.config import load_lane_file
from assay.verify import verify_document


def _native_lane(
    repo: GitRepo,
    marker: Path,
    *,
    max_mutants: int = 50,
    include_r3: bool = False,
) -> Path:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write(
        "tests/conftest.py",
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    repo.write(
        "tests/test_mod.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n"
        "from mod import f, g\n\ndef test_f():\n    assert f(1)\n"
        "\ndef test_g():\n    assert g(1)\n",
    )
    repo.write("src/mod.py", "def f(x):\n    return 0\n\ndef g(y):\n    return 0\n")
    base = repo.commit_all("seed source")
    repo.write(
        "src/mod.py",
        "def f(x):\n    return x > 0\n\ndef g(y):\n    return y > 0\n",
    )
    repo.commit_all("add native mutation candidates")
    argv = json.dumps([sys.executable, "-m", "pytest", "tests", "-q"])
    rigor = ["R0", "R2", "R3"] if include_r3 else ["R0", "R2"]
    canary = (
        "\n[lanes.package.judge.canary]\n"
        'mechanism = "import-break"\n'
        'target = "src/mod.py"\n'
        if include_r3
        else ""
    )
    path = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = {json.dumps(rigor)}
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
{canary}
""",
    )
    repo.commit_all("declare native R2 lane")
    return path


def _unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        lambda **_kwargs: resource_limits.ResourceObservationCapability(
            available=False, reason="fixture hides the cgroup ancestor"
        ),
    )

    def refuse_resource_sample(**_kwargs):
        raise resource_limits.ResourceLimitObservationError(
            "fixture hides the cgroup ancestor"
        )

    monkeypatch.setattr(mutation, "read_candidate_resource_counters", refuse_resource_sample)


def test_preflight_passes_explicit_observer_setting(monkeypatch):
    observer_path = "/run/cgprofile-observer/resource-observer.sock"
    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", observer_path)
    calls = []
    capability = resource_limits.ResourceObservationCapability(available=True)
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        lambda **kwargs: calls.append(kwargs) or capability,
    )

    assert cli._current_cgroup_observation_capability() is capability
    assert calls == [{"observer_socket_path": observer_path}]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_selected_native_candidates_preserve_r0_when_cgroup_is_hidden(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    marker = tmp_path / "r0-ran"
    lane = _native_lane(git_repo, marker, include_r3=True)
    _unavailable(monkeypatch)

    code, out, err = _run(
        ["run", "package", "--file", str(lane), "--verdict-json", "-"]
    )

    document = json.loads(out)
    claims = {claim["rigor"]: claim for claim in document["claims"]}
    assert code == 3, (
        f"reason={document.get('reason_code')} claims={claims!r} stderr={err}"
    )
    assert marker.exists()
    assert claims["R0"]["status"] == "PASS"
    assert claims["R2"]["status"] == "NO_MEASUREMENT"
    assert claims["R2"]["reason_code"] == "CGROUP_OBSERVATION_UNAVAILABLE"
    assert claims["R3"]["status"] == "PASS"
    assert verify_document(document) == []
    assert "CGROUP_OBSERVATION_UNAVAILABLE" in err


def test_plan_reports_selected_candidate_visibility_without_refusing(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    lane = _native_lane(git_repo, tmp_path / "r0-ran")
    _unavailable(monkeypatch)
    out, err = io.StringIO(), io.StringIO()

    code = main(["plan", "--json", "package", "--file", str(lane)], stdout=out, stderr=err)

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
            "--json",
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
        ["plan", "--json", "package", "--file", str(lane)],
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
        ["plan", "--json", "package", "--file", str(lane)],
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
