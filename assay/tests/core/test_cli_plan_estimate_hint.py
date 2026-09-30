"""``assay plan`` names the commit and tree it planned and points at the measured estimate (H, CD36)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from conftest import GitRepo

from assay import cli, mutation
from assay.cli import PLAN_ESTIMATE_HINT, main


def _seed(repo: GitRepo) -> Path:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    base = repo.commit_all("seed")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    repo.commit_all("add one compare-swap site")
    path = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["{sys.executable}", "-c", "pass"]
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
max_mutants = 10
operators = ["python:compare-swap"]
""",
    )
    repo.commit_all("add assay.toml")
    return path


def _plan(toml: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["plan", "package", "--file", str(toml)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_o_h1_the_plan_carries_commit_and_tree_and_stderr_is_exactly_the_hint(git_repo: GitRepo):
    toml = _seed(git_repo)
    code, out, err = _plan(toml)
    assert code == 0
    payload = json.loads(out)  # stdout is one JSON document
    assert payload["status"] == "ok"
    assert payload["commit"] == git_repo.git("rev-parse", "HEAD").strip()
    assert payload["tree"] == git_repo.git("rev-parse", "HEAD^{tree}").strip()
    assert payload["tree"] != payload["commit"]
    assert err == PLAN_ESTIMATE_HINT + "\n"
    assert PLAN_ESTIMATE_HINT not in out


def test_o_h1_an_unsupported_plan_has_no_commit_tree_or_hint(git_repo: GitRepo, monkeypatch):
    toml = _seed(git_repo)
    monkeypatch.setattr(mutation, "collect_mutation_sites", lambda *_a, **_k: mutation.UNSUPPORTED)
    code, out, err = _plan(toml)
    payload = json.loads(out)
    assert code == 0 and payload["status"] == "unsupported"
    assert "commit" not in payload and "tree" not in payload
    assert err == ""


def test_the_hint_names_the_analyze_subcommand_and_the_placeholder():
    assert "assay analyze plan-estimate --plan-json PLAN --progress PROGRESS [--workers N]" in PLAN_ESTIMATE_HINT
    assert "60 s placeholder" in PLAN_ESTIMATE_HINT
    assert "\n" not in PLAN_ESTIMATE_HINT


def test_without_an_error_stream_no_hint_is_printed(git_repo: GitRepo):
    toml = _seed(git_repo)
    out = io.StringIO()
    args = cli.build_parser().parse_args(["plan", "package", "--file", str(toml)])
    assert cli._cmd_plan(args, out) == 0
    assert json.loads(out.getvalue())["status"] == "ok"
