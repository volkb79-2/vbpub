"""O14: a real ``assay plan`` and a real ``assay run`` agree, and a shard does not.

The checker's scope refusals are unit-tested against hand-built plans; this file
drives ``check_campaign_scope`` with data produced by the judge itself, so a
planner/runner disagreement about candidate identity cannot hide behind
fixtures that were written to agree. Nothing here asserts a timing.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT, GitRepo

from assay.cli import main
from assay import mutation
from assay.resource_limits import ResourceLimitCounters

CHECKER_PATH = PROJECT_ROOT / "tools" / "b105_report_check.py"


@pytest.fixture(autouse=True)
def _cgroup_reads_are_isolated_from_the_devcontainer(monkeypatch):
    """This test exercises planner/runner identity, not host cgroup discovery."""
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(mutation, "read_current_cgroup_counters", lambda: counters)


def _checker():
    spec = importlib.util.spec_from_file_location("b105_report_check", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assay(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _seeded_lane(repo: GitRepo) -> Path:
    """A committed pytest project with two compare-swap sites and one R2 lane."""
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n.assay/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n"
        "    assert flag(9)\n"
        "    assert not flag(0)\n"
        "    assert not flag(10)\n",
    )
    base = repo.commit_all("seed test project")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0 and value < 10\n")
    repo.commit_all("add two compare-swap mutation sites")
    argv = json.dumps([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"])
    toml = repo.write(
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
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
""",
    )
    repo.commit_all("add assay.toml")
    return toml


def test_o14_the_real_plan_agrees_with_the_real_unsharded_run_and_not_with_a_shard(
    git_repo: GitRepo, tmp_path: Path
):
    toml = _seeded_lane(git_repo)
    head = git_repo.head()
    tree = git_repo.git("rev-parse", "HEAD^{tree}").strip()

    code, out, err = _assay(["plan", "package", "--file", str(toml)])
    assert code == 0, err
    plan = json.loads(out)
    assert plan["status"] == "ok" and plan["candidate_count"] == 2
    assert (plan["commit"], plan["tree"]) == (head, tree)

    whole = tmp_path / "v.json"
    code, _, err = _assay(["run", "package", "--file", str(toml), "--verdict-json", str(whole)])
    assert code == 0, err
    shard = tmp_path / "s.json"
    code, _, err = _assay(
        ["run", "package", "--file", str(toml), "--shard", "0/2", "--verdict-json", str(shard)]
    )
    assert code == 0, err

    checker = _checker()
    verdict = json.loads(whole.read_text(encoding="utf-8"))
    checker.check_campaign_scope(verdict, plan)
    checker.check_campaign_scope(verdict, plan, expected_commit=head, expected_tree=tree)
    with pytest.raises(ValueError, match=r"judgment\.r2\.shard_(index|count) is"):
        checker.check_campaign_scope(json.loads(shard.read_text(encoding="utf-8")), plan)
    with pytest.raises(ValueError, match="plan commit/tree differ from the expected source"):
        checker.check_campaign_scope(verdict, plan, expected_tree="0" * 40)
