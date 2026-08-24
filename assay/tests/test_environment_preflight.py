from __future__ import annotations

import json
from pathlib import Path

from conftest import GitRepo

from assay.cli import main


_LANE = """\
schema_version = 2

[lanes.real]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false
{probe}
"""


def _run(repo: GitRepo, tmp_path: Path, *, probe: str) -> tuple[int, dict]:
    repo.write(".gitignore", "*.json\n")
    lane = _LANE.format(probe=probe)
    repo.write("assay.toml", lane)
    repo.commit_all("lane")
    target = tmp_path / "verdict.json"
    code = main(["run", "real", "--file", str(repo.path / "assay.toml"), "--verdict-json", str(target)])
    return code, json.loads(target.read_text(encoding="utf-8"))


def test_a_passing_environment_probe_allows_the_lane(git_repo: GitRepo, tmp_path):
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "exit 0"]',
    )

    assert (code, document["outcome"]) == (0, "PASS")


def test_a_failing_environment_probe_refuses_before_the_lane(git_repo: GitRepo, tmp_path):
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "exit 7"]',
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )
