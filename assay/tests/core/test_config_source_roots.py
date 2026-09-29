"""O3 — ``source_roots`` resolve against the directory containing assay.toml.

A-049. The layout every test here uses puts the project root *below* the repo
root, and gives each of them a directory the other does not have:

    repo/            <- "repo root"
      pkgs/          <- exists ONLY relative to the repo root
      proj/          <- project root: contains assay.toml
        src/         <- exists ONLY relative to the project root
        src/deep/

So ``source_roots = ["src"]`` must load and ``source_roots = ["pkgs"]`` must
not. Getting that backwards is O3's negative: a root that resolves against the
repo root (or the process cwd) matches no changed file, and the gate then
returns 0/0 PASS forever — a laundering gate none of the four existing copies
guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from assay.config import load_lane_file
from assay.errors import LaneConfigError

LANE = """\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = {}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = %s
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }
base = "main"
"""


@dataclass(frozen=True)
class Layout:
    repo_root: Path
    project_root: Path

    def write(self, *roots: str) -> Path:
        rendered = "[" + ", ".join(f'"{r}"' for r in roots) + "]"
        path = self.project_root / "assay.toml"
        path.write_text(LANE % rendered, encoding="utf-8")
        return path


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    repo_root = tmp_path / "repo"
    project_root = repo_root / "proj"
    (repo_root / "pkgs").mkdir(parents=True)
    (project_root / "src" / "deep").mkdir(parents=True)
    return Layout(repo_root=repo_root, project_root=project_root)


def test_the_layout_really_distinguishes_the_two_roots(layout: Layout):
    # Without this, "pkgs is rejected" could be true merely because nothing
    # called pkgs exists anywhere, and the test would prove nothing.
    assert (layout.repo_root / "pkgs").is_dir()
    assert not (layout.project_root / "pkgs").exists()
    assert (layout.project_root / "src").is_dir()
    assert not (layout.repo_root / "src").exists()


def test_root_relative_to_the_project_loads(layout: Layout):
    judge = load_lane_file(layout.write("src")).lane("package").judge

    assert judge is not None
    assert judge.source_roots == ("src",)
    assert judge.source_root_paths == ((layout.project_root / "src").resolve(),)


def test_root_that_exists_only_relative_to_the_repo_root_is_rejected(
    layout: Layout,
):
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(layout.write("pkgs"))

    message = str(excinfo.value)
    assert "pkgs" in message
    assert str(layout.project_root) in message


def test_nonexistent_root_is_rejected(layout: Layout):
    with pytest.raises(LaneConfigError, match="does not exist"):
        load_lane_file(layout.write("nope"))


def test_nested_root_resolves_below_the_project_root(layout: Layout):
    judge = load_lane_file(layout.write("src/deep")).lane("package").judge

    assert judge is not None
    assert judge.source_root_paths == (
        (layout.project_root / "src" / "deep").resolve(),
    )


def test_several_roots_resolve_in_declaration_order(layout: Layout):
    (layout.project_root / "scripts").mkdir()
    judge = load_lane_file(layout.write("scripts", "src")).lane("package").judge

    assert judge is not None
    assert judge.source_roots == ("scripts", "src")
    assert judge.source_root_paths == (
        (layout.project_root / "scripts").resolve(),
        (layout.project_root / "src").resolve(),
    )


@pytest.mark.parametrize("where", ["repo_root", "project_root", "elsewhere"])
def test_resolution_does_not_depend_on_the_process_cwd(
    where: str, layout: Layout, tmp_path: Path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cwd = {
        "repo_root": layout.repo_root,
        "project_root": layout.project_root,
        "elsewhere": elsewhere,
    }[where]
    monkeypatch.chdir(cwd)

    judge = load_lane_file(layout.write("src")).lane("package").judge

    assert judge is not None
    assert judge.source_root_paths == ((layout.project_root / "src").resolve(),)


def test_repo_relative_root_is_rejected_even_when_cwd_is_the_repo_root(
    layout: Layout, monkeypatch
):
    # The sharpest form of the negative: standing in the directory where the
    # wrong answer would look right.
    monkeypatch.chdir(layout.repo_root)

    with pytest.raises(LaneConfigError, match="does not exist"):
        load_lane_file(layout.write("pkgs"))


def test_absolute_source_root_is_rejected(layout: Layout):
    absolute = str((layout.project_root / "src").resolve())
    with pytest.raises(LaneConfigError, match="is absolute"):
        load_lane_file(layout.write(absolute))


def test_source_root_pointing_at_a_file_is_rejected(layout: Layout):
    (layout.project_root / "README.md").write_text("hi", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="does not exist"):
        load_lane_file(layout.write("README.md"))


def test_empty_source_root_string_is_rejected(layout: Layout):
    with pytest.raises(LaneConfigError, match="empty path"):
        load_lane_file(layout.write(""))


def test_project_root_is_the_directory_containing_assay_toml(layout: Layout):
    lane_file = load_lane_file(layout.write("src"))

    assert lane_file.project_root == layout.project_root.resolve()
    assert lane_file.project_root != layout.repo_root.resolve()


# --- O3 (P15): containment holds even for a RESOLVED-directory escape ---------
#
# A source root that IS a real, existing directory (clearing the old checks
# unmodified) but resolves outside the project root -- via '..' or a symlink
# -- must still be rejected. Finding 9's own words: "a lane can measure a
# sibling project despite the diagnostic claiming containment."


def test_dotdot_source_root_that_resolves_to_a_real_sibling_directory_is_rejected(
    layout: Layout,
):
    # repo_root/pkgs is a REAL directory (the fixture layout guarantees it),
    # and project_root/".." resolves EXACTLY to repo_root -- so a naive
    # "is it an existing directory" check alone would accept this.
    with pytest.raises(LaneConfigError, match="not contained beneath"):
        load_lane_file(layout.write("../pkgs"))


def test_symlink_source_root_escaping_the_project_root_is_rejected(layout: Layout):
    outside = layout.repo_root / "outside_target"
    outside.mkdir()
    link = layout.project_root / "escape_link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LaneConfigError, match="not contained beneath"):
        load_lane_file(layout.write("escape_link"))


def test_symlink_source_root_pointing_within_the_project_root_is_accepted(
    layout: Layout,
):
    real = layout.project_root / "src"
    link = layout.project_root / "src_link"
    link.symlink_to(real, target_is_directory=True)

    judge = load_lane_file(layout.write("src_link")).lane("package").judge

    assert judge is not None
    assert judge.source_root_paths == (real.resolve(),)
