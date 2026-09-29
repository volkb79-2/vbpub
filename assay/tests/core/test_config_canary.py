"""P19 work item 1 — ``judge.canary`` is a CLOSED table, validated at LOAD
time rather than passed through opaque (A-106's own deliberate gap, closed
here): ``mechanism`` (one of :data:`assay.canary.CANARY_MECHANISMS`) and a
normalized, project-relative path to a real, ordinary source file beneath
one of the lane's own declared ``source_roots`` — the singular ``target``,
or (B007/A-432) the ordered plural ``targets`` with its ``aggregation``.

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
from assay.verdict import CANARY_AGGREGATIONS, MAX_CANARY_TARGETS

R3_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
"""


#: B006a/A-269, schema v2: every R1+ lane requires an explicit [isolation]
#: table; this module's own subject is `judge.canary`, so every case here
#: picks the plain "repository" selection.
_ISOLATION_TABLE = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'


def _lane_with_canary(canary_table: str) -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R3"]')
    return body + _ISOLATION_TABLE + R3_JUDGE + canary_table


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
    :data:`CANARY_MECHANISMS` loads on its own.

    P23/A-192: ``uncovered-line``'s expected cause is R1's own
    ``UNCOVERED_LINES``, so its own load-time grammar now requires R1
    declared alongside R3 -- exercised here with the full R1 judge table,
    never the bare R3-only shape ``import-break`` still uses.
    """
    project.file("src/mod.py", "x = 1\n")
    for mechanism in sorted(CANARY_MECHANISMS):
        if mechanism == "uncovered-line":
            body = set_key(R0_LANE, "rigor", '["R0", "R1", "R3"]')
            lane = (
                body
                + _ISOLATION_TABLE
                + "\n[lanes.package.judge]\n"
                + 'language = "python"\n'
                + 'source_roots = ["src"]\n'
                + "fail_under = 100.0\n"
                + "allow_excluded = false\n"
                + 'coverage = { format = "coverage-py-json", artifact = "cov.json" }\n'
                + 'base = "main"\n'
                + _canary_table(mechanism=mechanism)
            )
        else:
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
    """B007/A-432 changed what "missing" MEANS here: the table must name the
    source it transforms, in one of the two spellings, so the diagnostic
    names both rather than only the singular one it used to require."""
    lane = _lane_with_canary('\n[lanes.package.judge.canary]\nmechanism = "import-break"\n')
    with pytest.raises(
        LaneConfigError, match="declares neither 'target' nor 'targets'"
    ):
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


# --- B007/A-432: the plural spelling ------------------------------------------


def _plural_canary_table(
    *,
    mechanism: str = "import-break",
    targets: str = '["src/a.py", "src/b.py"]',
    aggregation: str | None = "any",
) -> str:
    table = (
        f"\n[lanes.package.judge.canary]\n"
        f'mechanism = "{mechanism}"\n'
        f"targets = {targets}\n"
    )
    if aggregation is not None:
        table += f'aggregation = "{aggregation}"\n'
    return table


def _two_targets(project: Project) -> None:
    project.file("src/a.py", "a = 1\n")
    project.file("src/b.py", "b = 2\n")


def test_the_plural_spelling_loads_as_an_ordered_list(project: Project):
    """A-432's lane surface, additive: `LANE_SCHEMA_VERSION` stays 2 and the
    ORDER the file declares is the order the loader keeps -- attempts are
    made in declared order, so a sorted or de-ordered list would silently
    change which probe answers first."""
    _two_targets(project)
    lane = _lane_with_canary(_plural_canary_table(targets='["src/b.py", "src/a.py"]'))
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.canary is not None
    assert judge.canary == CanaryConfig(
        mechanism="import-break",
        targets=("src/b.py", "src/a.py"),
        aggregation="any",
    )
    assert judge.canary.declared_targets == ("src/b.py", "src/a.py")
    assert judge.canary.target is None


def test_the_singular_spelling_is_the_one_probe_form_of_the_plural_one(
    project: Project,
):
    """The compatibility statement the whole additive design rests on: no
    producer downstream branches on which spelling the lane used."""
    project.file("src/mod.py", "x = 1\n")
    judge = (
        load_lane_file(project.write(_lane_with_canary(_canary_table())))
        .lane("package")
        .judge
    )

    assert judge is not None and judge.canary is not None
    assert judge.canary.declared_targets == ("src/mod.py",)
    assert judge.canary.aggregation is None


def test_both_spellings_at_once_are_rejected(project: Project):
    _two_targets(project)
    lane = _lane_with_canary(
        '\n[lanes.package.judge.canary]\n'
        'mechanism = "import-break"\n'
        'target = "src/a.py"\n'
        'targets = ["src/a.py", "src/b.py"]\n'
        'aggregation = "any"\n'
    )
    with pytest.raises(LaneConfigError, match="declares BOTH 'target' and 'targets'"):
        load_lane_file(project.write(lane))


def test_every_declared_aggregation_is_accepted(project: Project):
    """Direction 1 of the closed-vocabulary pair, the house pattern this
    module already applies to `mechanism`: the vocabulary is cross-checked
    against its own owner, never duplicated here."""
    _two_targets(project)
    for aggregation in CANARY_AGGREGATIONS:
        lane = _lane_with_canary(_plural_canary_table(aggregation=aggregation))
        judge = load_lane_file(project.write(lane)).lane("package").judge
        assert judge is not None and judge.canary is not None
        assert judge.canary.aggregation == aggregation


def test_an_unknown_aggregation_is_rejected(project: Project):
    _two_targets(project)
    lane = _lane_with_canary(_plural_canary_table(aggregation="most"))
    with pytest.raises(LaneConfigError, match="'judge.canary.aggregation' 'most'"):
        load_lane_file(project.write(lane))


def test_several_targets_without_an_aggregation_are_rejected(project: Project):
    """A-432: with more than one probe the claim's status is not defined
    without one, and assay does not invent a policy the lane never stated."""
    _two_targets(project)
    lane = _lane_with_canary(_plural_canary_table(aggregation=None))
    with pytest.raises(LaneConfigError, match="targets but no 'aggregation'"):
        load_lane_file(project.write(lane))


def test_an_aggregation_over_one_probe_is_rejected(project: Project):
    """Its ABSENCE is the checkable statement 'one declared target, no
    aggregation policy' -- so recording one there would record a policy the
    lane never stated, in either spelling."""
    project.file("src/a.py", "a = 1\n")
    plural = _lane_with_canary(_plural_canary_table(targets='["src/a.py"]'))
    with pytest.raises(LaneConfigError, match="declared beside a single target"):
        load_lane_file(project.write(plural))

    singular = _lane_with_canary(
        '\n[lanes.package.judge.canary]\n'
        'mechanism = "import-break"\n'
        'target = "src/a.py"\n'
        'aggregation = "all"\n'
    )
    with pytest.raises(LaneConfigError, match="declared beside a single target"):
        load_lane_file(project.write(singular))


def test_one_target_in_the_plural_spelling_needs_no_aggregation(project: Project):
    project.file("src/a.py", "a = 1\n")
    lane = _lane_with_canary(
        _plural_canary_table(targets='["src/a.py"]', aggregation=None)
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.canary is not None
    assert judge.canary.declared_targets == ("src/a.py",)
    assert judge.canary.aggregation is None


def test_a_repeated_probe_is_rejected(project: Project):
    _two_targets(project)
    lane = _lane_with_canary(
        _plural_canary_table(targets='["src/a.py", "src/b.py", "src/a.py"]')
    )
    with pytest.raises(LaneConfigError, match="more than once"):
        load_lane_file(project.write(lane))


def test_more_than_the_measured_bound_is_rejected(project: Project):
    """The bound is MEASURED (~2.76 s of materialisation per target against
    the smallest documented lane budget), and it is imported from its one
    owner rather than restated here."""
    for index in range(MAX_CANARY_TARGETS + 1):
        project.file(f"src/t{index}.py", f"t{index} = {index}\n")
    listed = ", ".join(
        f'"src/t{index}.py"' for index in range(MAX_CANARY_TARGETS + 1)
    )
    lane = _lane_with_canary(_plural_canary_table(targets=f"[{listed}]"))
    with pytest.raises(LaneConfigError, match="declares 9 target"):
        load_lane_file(project.write(lane))


def test_an_empty_target_list_is_rejected(project: Project):
    lane = _lane_with_canary(_plural_canary_table(targets="[]", aggregation=None))
    with pytest.raises(LaneConfigError, match="declares 0 target"):
        load_lane_file(project.write(lane))


def test_a_target_list_that_is_not_an_array_is_rejected(project: Project):
    lane = _lane_with_canary(
        _plural_canary_table(targets='"src/a.py"', aggregation=None)
    )
    with pytest.raises(LaneConfigError, match="'judge.canary.targets' must be an array"):
        load_lane_file(project.write(lane))


def test_a_bad_entry_names_its_own_index_not_the_singular_key(project: Project):
    """Every per-target diagnostic points at the declaration it came from:
    the plural form never inherits a message about a key the lane never
    wrote."""
    _two_targets(project)
    lane = _lane_with_canary(
        _plural_canary_table(targets='["src/a.py", "src/missing.py"]')
    )
    with pytest.raises(LaneConfigError, match=r"judge\.canary\.targets\[1\]"):
        load_lane_file(project.write(lane))


def test_plural_as_declared_reproduces_exactly_what_was_written():
    canary = CanaryConfig(
        mechanism="import-break", targets=("src/a.py", "src/b.py"), aggregation="all"
    )
    assert canary.as_declared() == {
        "mechanism": "import-break",
        "targets": ["src/a.py", "src/b.py"],
        "aggregation": "all",
    }


def test_a_canary_config_cannot_be_built_in_a_shape_the_loader_refuses():
    """The structural half of the same rule: a hand-built `CanaryConfig` --
    every test that constructs one, and every future caller -- cannot reach
    a shape `assay.toml` could not have declared."""
    with pytest.raises(ValueError, match="exactly one of target/targets"):
        CanaryConfig(mechanism="import-break")
    with pytest.raises(ValueError, match="exactly one of target/targets"):
        CanaryConfig(
            mechanism="import-break", target="src/a.py", targets=("src/a.py",)
        )
    with pytest.raises(ValueError, match="aggregation is present iff"):
        CanaryConfig(mechanism="import-break", targets=("src/a.py", "src/b.py"))
    with pytest.raises(ValueError, match="aggregation is present iff"):
        CanaryConfig(
            mechanism="import-break", target="src/a.py", aggregation="any"
        )
