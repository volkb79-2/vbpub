"""B108 oracles O15 and O19 (W9): campaigns over evidence a real ``assay run`` produced.

Nothing here installs a plan: the R2 plan is Assay's own (``cli.plan_jobs``) and the
R1 verdict is the one ``runner.run_lane`` evaluated from a coverage artifact the
lane command wrote. Every artifact lives outside the repository under ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from assay.cli import main
from analysis.tests.test_analysis_campaign import _validate


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Campaign Real")
    _git(root, "config", "user.email", "campaign-real@example.invalid")
    (root / ".gitignore").write_text("cov.json\n.assay/\n")
    (root / "src").mkdir()
    return root


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _run(arguments: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _campaign(root: Path, head: str, progress: Path, *extra: str) -> tuple[int, dict]:
    code, out, err = _run([
        "analyze", "campaign", "package",
        "--worktree", str(root), "--file", str(root / "assay.toml"),
        "--expected-commit", head, "--progress", str(progress), "--format", "json", *extra,
    ])
    assert err == ""
    document = json.loads(out)
    _validate(document)
    return code, document


# ---- O19 ------------------------------------------------------------------

_COVERAGE = {
    "meta": {"branch_coverage": True},
    "files": {
        "src/mod.py": {
            "executed_lines": [1, 2, 6, 7, 8, 9],
            "missing_lines": [3],
            "excluded_lines": [],
            "executed_branches": [[7, 8]],
            "missing_branches": [[7, 9]],
        }
    },
}


def _r1_campaign(tmp_path: Path):
    root = _repository(tmp_path)
    (root / "README").write_text("base\n")
    base = _commit(root, "base")
    (root / "src" / "mod.py").write_text(
        "def f(x):\n    return x\n    x += 1\n\n\n"
        "def g(y):\n    if y:\n        y = 1\n    return y\n"
    )
    _commit(root, "add mod.py")
    write_cov = "cat > cov.json <<'EOF'\n" + json.dumps(_COVERAGE) + "\nEOF"
    (root / "assay.toml").write_text(
        "schema_version = 2\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R1"]\n'
        'enforcement = "gate"\n'
        f"argv = [\"/bin/sh\", \"-c\", {json.dumps(write_cov)}]\n"
        "env = {}\n"
        'env_passthrough = ["PATH"]\n'
        'budget = "1m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n\n'
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        "fail_under = 100.0\n"
        "allow_excluded = false\n"
        'coverage = { format = "coverage-py-json", artifact = "cov.json" }\n'
        f'base = "{base}"\n'
    )
    head = _commit(root, "add assay.toml")
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code, _out, err = _run([
        "run", "package", "--file", str(root / "assay.toml"),
        "--verdict-json", str(evidence / "verdict.json"),
        "--progress", str(evidence / "progress.jsonl"),
    ])
    assert code == 1, err
    # The run judged its artifact inside an isolated snapshot; the analysis
    # reverifies the same bytes from the worktree, where the lane declares them.
    (root / "cov.json").write_text(json.dumps(_COVERAGE))
    return root, head, evidence


def test_o19_coverage_reverifies_the_artifact_and_lists_the_gaps_exactly(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path)
    code, document = _campaign(
        root, head, evidence / "progress.jsonl",
        "--verdict", str(evidence / "verdict.json"), "--command-exit", "1",
        "--coverage", str(root / "cov.json"),
    )
    coverage = document["coverage"]
    assert coverage["artifact_status"] == "parsed_and_reverified_r1"
    assert coverage["r1"]["missing_lines"] == {"src/mod.py": [3]}
    assert coverage["missing_branch_arcs"] == [
        {"path": "src/mod.py", "source_line": 7, "destination": 9}
    ]
    assert coverage["branch_arc_detail_status"] == "exact_from_coverage_py_artifact_and_reverified_r1"
    assert (code, document["status"]) == (1, "complete")


def test_o19_without_coverage_the_declared_artifact_is_never_read(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path)
    (root / "cov.json").unlink()
    code, document = _campaign(
        root, head, evidence / "progress.jsonl",
        "--verdict", str(evidence / "verdict.json"), "--command-exit", "1",
    )
    coverage = document["coverage"]
    assert coverage["artifact_status"] == "not_supplied"
    assert coverage["missing_branch_arcs"] is None
    assert coverage["branch_arc_detail_status"] == "unavailable_without_artifact"
    assert document["status"] == "incomplete"
    assert "coverage_not_reverified" in document["complete_blockers"]
    assert code == 3
