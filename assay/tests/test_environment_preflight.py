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
    repo.write(".gitignore", "*.json\nciu.global.toml\n")
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


def test_environment_probe_runs_normally_with_a_resolvable_infrastructure_fact(
    git_repo: GitRepo, tmp_path
):
    """(B025 round 2, N-W1 follow-up) Round 2 review found the probe's plan
    resolution never forwarding `infrastructure_source`/`infrastructure_
    environment` at all was fatal even for a RESOLVABLE `derived:` fact --
    not just the unresolvable case the sibling test below covers, which
    would have refused either way and so could not by itself prove the
    forward actually happened. `ciu.global.toml` is gitignored (`_run`'s own
    `.gitignore`), matching real ciu usage, so its presence does not trip
    the pre-run dirty-tree check."""
    (git_repo.path / "ciu.global.toml").write_text(
        "[deploy]\nimage = 'postgres:18'\n", encoding="utf-8"
    )
    code, document = _run(
        git_repo,
        tmp_path,
        probe=(
            'environment_command = ["/bin/sh", "-c", "exit 0"]\n\n'
            "[lanes.real.infrastructure]\n"
            'image = "derived:deploy.image"\n'
        ),
    )

    assert (code, document["outcome"]) == (0, "PASS")


def test_environment_probe_refuses_cleanly_when_infrastructure_is_unresolvable(
    git_repo: GitRepo, tmp_path
):
    """(B025 round 2, N-W1) The probe's own plan resolution never forwarded
    `infrastructure_source`/`infrastructure_environment` at all -- a lane
    pairing `environment_command` with a `derived:` fact crashed
    unconditionally, resolvable or not, unlike every other call site in this
    module. Now forwarded, and wrapped in the same refuse-cleanly pattern."""
    code, document = _run(
        git_repo,
        tmp_path,
        probe=(
            'environment_command = ["/bin/sh", "-c", "exit 0"]\n\n'
            "[lanes.real.infrastructure]\n"
            'image = "derived:deploy.image"\n'
        ),
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )
    assert document["env_effective_incomplete"] is True
