"""P19 work item 1 — ``judge.canary`` is a CLOSED table, validated at LOAD
time rather than passed through opaque (A-106's own deliberate gap, closed
here): exactly ``mechanism`` (one of :data:`assay.canary.CANARY_MECHANISMS`)
and ``target`` (a normalized, project-relative path to a real, ordinary
source file beneath one of the lane's own declared ``source_roots``).

Mirrors ``tests/test_config_mutation.py``'s own house pattern one field
over: the vocabulary is cross-checked against its own owner
(:mod:`assay.canary`), never duplicated, and both directions of every
closed check are proven — every accepted shape loads, every rejected shape
is refused and names the offending field.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, Project, set_key

from assay.canary import CANARY_MECHANISMS
from assay.config import CanaryConfig, load_lane_file
from assay.errors import LaneConfigError

R3_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
"""


def _lane_with_canary(canary_table: str) -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R3"]')
    return body + R3_JUDGE + canary_table


def _canary_table(mechanism: str = "import-break", target: str = "src/mod.py") -> str:
    return f'\n[lanes.package.judge.canary]\nmechanism = "{mechanism}"\ntarget = "{target}"\n'


# --- accept -------------------------------------------------------------------


def test_a_minimal_valid_canary_table_loads(project: Project):
    project.file("src/mod.py", "x = 1\n")
    lane = _lane_with_canary(_canary_table())
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.canary == CanaryConfig(mechanism="import-break", target="src/mod.py")


def test_every_declared_mechanism_is_accepted(project: Project):
    """Direction 1 of the closed-vocabulary pair: every member of
    :data:`CANARY_MECHANISMS` loads on its own."""
    project.file("src/mod.py", "x = 1\n")
    for mechanism in sorted(CANARY_MECHANISMS):
        lane = _lane_with_canary(_canary_table(mechanism=mechanism))
        judge = load_lane_file(
            project.write(lane, name=f"{mechanism}.toml")
        ).lane("package").judge
        assert judge is not None and judge.canary is not None
        assert judge.canary.mechanism == mechanism


def test_a_target_in_a_nested_source_directory_loads(project: Project):
    project.file("src/pkg/mod.py", "x = 1\n")
    lane = _lane_with_canary(_canary_table(target="src/pkg/mod.py"))
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.canary is not None
    assert judge.canary.target == "src/pkg/mod.py"


# --- reject: closed vocabulary --------------------------------------------


def test_an_unknown_mechanism_is_rejected(project: Project):
    """Direction 2: a name outside the closed vocabulary is refused, and
    the message names both the offender and the known set."""
    project.file("src/mod.py", "x = 1\n")
    lane = _lane_with_canary(_canary_table(mechanism="delete-everything"))
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(project.write(lane))
    message = str(excinfo.value)
    assert "delete-everything" in message
    for mechanism in CANARY_MECHANISMS:
        assert mechanism in message


# --- reject: table shape ---------------------------------------------------


def test_canary_missing_mechanism_is_rejected(project: Project):
    project.file("src/mod.py", "x = 1\n")
    lane = _lane_with_canary('\n[lanes.package.judge.canary]\ntarget = "src/mod.py"\n')
    with pytest.raises(LaneConfigError, match="judge.canary.mechanism"):
        load_lane_file(project.write(lane))


def test_canary_missing_target_is_rejected(project: Project):
    lane = _lane_with_canary('\n[lanes.package.judge.canary]\nmechanism = "import-break"\n')
    with pytest.raises(LaneConfigError, match="judge.canary.target"):
        load_lane_file(project.write(lane))


def test_canary_with_an_unknown_key_is_rejected(project: Project):
    project.file("src/mod.py", "x = 1\n")
    lane = _lane_with_canary(
        '\n[lanes.package.judge.canary]\n'
        'mechanism = "import-break"\ntarget = "src/mod.py"\nsite = "src/mod.py"\n'
    )
    with pytest.raises(LaneConfigError, match="unknown judge.canary key"):
        load_lane_file(project.write(lane))


# --- reject: target path shape ---------------------------------------------


def test_an_empty_target_is_rejected(project: Project):
    lane = _lane_with_canary(_canary_table(target=""))
    with pytest.raises(LaneConfigError, match="'judge.canary.target' is empty"):
        load_lane_file(project.write(lane))


def test_an_absolute_target_is_rejected(project: Project):
    lane = _lane_with_canary(_canary_table(target="/etc/passwd"))
    with pytest.raises(LaneConfigError, match="is absolute"):
        load_lane_file(project.write(lane))


def test_a_target_escaping_every_source_root_via_traversal_is_rejected(project: Project):
    # A real file exists OUTSIDE every declared source root -- the target
    # must be refused by CONTAINMENT, not merely because nothing is there.
    project.file("outside.py", "x = 1\n")
    lane = _lane_with_canary(_canary_table(target="../outside.py"))
    with pytest.raises(LaneConfigError, match="not contained beneath any declared"):
        load_lane_file(project.write(lane))


def test_a_target_escaping_via_a_symlinked_source_root_is_rejected(project: Project):
    # `source_roots` resolution (`_resolve_source_root`) already follows
    # `src` itself if it is a symlink, but a symlink INSIDE `src` pointing
    # outside the project root must be caught by the containment check
    # (`resolved.is_relative_to`) even though it is never checked for
    # `is_symlink()` itself (only the LEAF is).
    outside = project.root.parent / "outside_dir"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n", encoding="utf-8")
    (project.root / "src" / "escape").symlink_to(outside)
    lane = _lane_with_canary(_canary_table(target="src/escape/secret.py"))
    with pytest.raises(LaneConfigError, match="not contained beneath any declared"):
        load_lane_file(project.write(lane))


def test_a_symlinked_target_itself_is_rejected(project: Project):
    real = project.file("src/real.py", "x = 1\n")
    link = project.root / "src" / "link.py"
    link.symlink_to(real)
    lane = _lane_with_canary(_canary_table(target="src/link.py"))
    with pytest.raises(LaneConfigError, match="is a symlink"):
        load_lane_file(project.write(lane))


def test_a_nonexistent_target_is_rejected(project: Project):
    lane = _lane_with_canary(_canary_table(target="src/does_not_exist.py"))
    with pytest.raises(LaneConfigError, match="does not exist as"):
        load_lane_file(project.write(lane))


def test_a_target_that_is_a_directory_is_rejected(project: Project):
    project.dir("src/subdir")
    lane = _lane_with_canary(_canary_table(target="src/subdir"))
    with pytest.raises(LaneConfigError, match="does not exist as"):
        load_lane_file(project.write(lane))


def test_canary_that_is_not_a_string_is_rejected(project: Project):
    project.file("src/mod.py", "x = 1\n")
    lane = _lane_with_canary(
        '\n[lanes.package.judge.canary]\nmechanism = 4\ntarget = "src/mod.py"\n'
    )
    with pytest.raises(LaneConfigError, match="'judge.canary.mechanism' must be a string"):
        load_lane_file(project.write(lane))


# --- round trip --------------------------------------------------------------


def test_canary_config_as_declared_round_trips():
    canary = CanaryConfig(mechanism="uncovered-line", target="src/mod.py")
    assert canary.as_declared() == {"mechanism": "uncovered-line", "target": "src/mod.py"}
