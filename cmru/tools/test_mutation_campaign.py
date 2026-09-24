from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mutation_campaign


def _job(path: str = "cmru/src/cmru/thing.py", line: int = 7):
    return SimpleNamespace(
        path=path,
        site=SimpleNamespace(
            lineno=line,
            operator="python:compare-swap",
            description="Eq->NotEq",
        ),
    )


def test_mutation_subprocess_has_a_hard_timeout(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        mutation_campaign._run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=0.1,
        )


def test_timeout_is_recorded_as_a_killed_candidate():
    result = mutation_campaign._candidate_result(
        _job(), returncode=None, elapsed_seconds=120, timed_out=True,
    )
    assert result.outcome == "killed"
    assert result.termination == "timeout"
    assert result.exit_code == 124


def test_resume_reuses_killed_candidates_and_retries_other_outcomes(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    jobs = [_job(line=7), _job(line=8)]
    previous = {
        "schema_version": 1,
        "base": head,
        "head": head,
        "project_prefix": "cmru",
        "assay_source_commit": "assay-commit",
        "max_mutants": 10,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "candidate_count": 2,
        "results": [
            {
                "path": jobs[0].path,
                "line": jobs[0].site.lineno,
                "operator": jobs[0].site.operator,
                "description": jobs[0].site.description,
                "outcome": "killed",
            },
            {
                "path": jobs[1].path,
                "line": jobs[1].site.lineno,
                "operator": jobs[1].site.operator,
                "description": jobs[1].site.description,
                "outcome": "crashed",
            },
        ],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(previous), encoding="utf-8")

    resumed = mutation_campaign._resume_results(
        evidence_path=evidence,
        repo_root=repo,
        resume=True,
        base=head,
        head=head,
        project_prefix=Path("cmru"),
        assay_source_commit="assay-commit",
        test_argv=["pytest", "tests"],
        jobs=jobs,
        max_mutants=10,
    )
    assert resumed[0] == previous["results"][0]
    assert resumed[1] is None


def test_resume_refuses_changed_source(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    source = project / "src" / "cmru" / "thing.py"
    source.write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    previous_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "cmru/src"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "source change"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    job = _job()
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps({
        "base": previous_head,
        "head": previous_head,
        "project_prefix": "cmru",
        "assay_source_commit": "assay-commit",
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "candidate_count": 1,
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "outcome": "killed",
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="predates a source or test change"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=head,
            project_prefix=Path("cmru"),
            assay_source_commit="assay-commit",
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
        )
