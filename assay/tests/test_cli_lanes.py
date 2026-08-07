"""``assay lanes`` — lists and validates, runs nothing, writes nothing.

Two contracts under test that are easy to state and easy to break:

* **It must not execute a lane.** The fixtures below declare an argv that would
  leave a marker file behind, and assert the marker never appears. A subcommand
  that quietly ran the argv while claiming to list it would be exactly the
  "implies capability it does not have" failure assay exists to remove.
* **It renders no verdict artifact** (A-054). The tests snapshot the whole
  project directory before and after and require it unchanged, so an artifact
  written anywhere under it fails the test rather than going unnoticed.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

from assay.cli import main


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_lanes_lists_a_valid_file_and_exits_zero(project: Project):
    path = project.write(R0_LANE)

    code, out, err = run(["lanes", "--file", str(path)])

    assert code == 0
    assert err == ""
    assert "package" in out
    assert "scope=S1" in out
    assert "rigor=R0" in out
    assert "enforcement=gate" in out
    assert "budget=5m (300s)" in out
    assert "pytest tests/unit -q" in out


def test_lanes_shows_the_judge_language_and_source_roots(project: Project):
    path = project.write(R1_LANE)

    code, out, _ = run(["lanes", "--file", str(path)])

    assert code == 0
    assert "judge=python" in out
    assert "source_roots: src, scripts" in out
    assert str(project.root.resolve()) in out


def test_lanes_reports_an_r0_lane_as_unjudged(project: Project):
    path = project.write(R0_LANE)
    _, out, _ = run(["lanes", "--file", str(path)])
    assert "judge=none" in out


def test_lanes_lists_every_declared_lane(project: Project):
    text = R0_LANE + """
[lanes.acceptance]
scope = "S3"
rigor = ["R0"]
enforcement = "advisory"
argv = ["pytest", "tests/acceptance"]
env = {}
env_passthrough = ["PATH"]
budget = "45m"
allow_argv_append = false
"""
    code, out, _ = run(["lanes", "--file", str(project.write(text))])

    assert code == 0
    assert "2 lanes" in out
    assert "package" in out
    assert "acceptance" in out


def test_lanes_maps_a_bad_config_to_exit_two_with_the_reason_code(
    project: Project,
):
    path = project.write(drop_key(R0_LANE, "budget"))

    code, out, err = run(["lanes", "--file", str(path)])

    assert code == 2, "ERROR is exit 2 (A-021)"
    assert "ERROR/BAD_LANE_CONFIG" in err
    assert "budget" in err
    assert out == "", "stdout is for humans reporting success, not for errors"


def test_lanes_does_not_execute_the_lane_argv(project: Project, tmp_path: Path):
    marker = tmp_path / "EXECUTED"
    text = set_key(
        R0_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]'
    )
    path = project.write(text)

    code, out, _ = run(["lanes", "--file", str(path)])

    assert code == 0
    assert "/bin/sh" in out, "the argv was read..."
    assert not marker.exists(), "...but must never have been run"


def test_lanes_writes_no_verdict_artifact(project: Project):
    # A-054: `assay lanes` does not run a lane, so A-027's "emitted on every
    # outcome" does not apply, and A-028 makes emission conditional on an
    # explicit path this subcommand has no flag for.
    path = project.write(R1_LANE)
    before = snapshot(project.root)

    run(["lanes", "--file", str(path)])

    assert snapshot(project.root) == before


def test_lanes_writes_nothing_on_the_error_path_either(project: Project):
    project.write(set_key(R0_LANE, "scope", '"S9"'))
    before = snapshot(project.root)

    code, _, _ = run(["lanes", "--file", str(project.root / "assay.toml")])

    assert code == 2
    assert snapshot(project.root) == before


def test_lanes_discovers_assay_toml_by_searching_upward(
    project: Project, monkeypatch
):
    project.write(R0_LANE)
    deep = project.dir("src", "pkg", "inner")
    monkeypatch.chdir(deep)

    code, out, _ = run(["lanes"])

    assert code == 0
    assert "package" in out


def test_lanes_fails_loudly_when_there_is_no_lane_file(tmp_path: Path, monkeypatch):
    # DERIVE, then FAIL — never a default path (AGENTS.md §4.2a).
    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.chdir(empty)

    code, out, err = run(["lanes"])

    assert code == 2
    assert "assay.toml" in err
    assert "--file" in err
    assert out == ""


def test_lanes_reports_a_missing_named_file(project: Project):
    code, _, err = run(["lanes", "--file", str(project.root / "absent.toml")])

    assert code == 2
    assert "absent.toml" in err


def test_main_returns_an_int_rather_than_exiting(project: Project):
    code = main(
        ["lanes", "--file", str(project.write(R0_LANE))],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert isinstance(code, int)


def test_version_flag_reports_a_version():
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code != 0


def test_cli_still_rejects_subcommands_not_yet_shipped():
    # P04 adds `run` (see tests/test_cli_run.py); `verify` and `mutate` remain
    # later packages' work. A parser that already accepted them would be the
    # lane-table-implies-capability failure in the CLI's own help, one level
    # up from the artifact assay itself is built to remove.
    with pytest.raises(SystemExit) as excinfo:
        main(["verify"])
    assert excinfo.value.code == 2
