"""Wave-1 §5 (A-260) -- ``judge.mode``/``judge.targets``, and §4 (A-259)'s
``judge.require_branch``, all loaded through the real :func:`assay.config.
load_lane_file`, never a hand-called private helper (this project's own
house convention -- see ``test_config_canary.py``/``test_config_judge_base.py``).

Also covers §5's own canary interaction: an ``uncovered-line`` R3 canary
under ``mode = "whole_target"`` must name one of the lane's own declared
``targets``, or it is refused at LOAD, before any run could produce an
accidental ``CANARY_SURVIVED`` that looks like a real finding.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

from assay.config import JUDGE_MODES, LaneConfigError, load_lane_file

#: A complete whole-target R1 lane: `R1_LANE` with `base` dropped (forbidden
#: without R2 under whole-target mode) and `judge.mode`/`judge.targets`
#: appended before the (order-insensitive) `[…where]` table. `targets` need
#: not exist on disk (§5's own deliberate choice -- a whole-target lane must
#: be judgeable from any commit); `source_roots` still must, and `project`
#: already creates them.
WHOLE_TARGET_LANE = drop_key(R1_LANE, "base").replace(
    "\n[lanes.package.where]",
    '\nmode = "whole_target"\ntargets = ["src/mod.py"]\n\n[lanes.package.where]',
)


def _lane_with_rigor(rigor: str) -> str:
    return set_key(WHOLE_TARGET_LANE, "rigor", rigor)


#: A minimal, standalone R2-only lane (mutation, no R1 declared) -- used to
#: prove `judge.mode`/`judge.require_branch` are refused when R1 is absent,
#: without R1's own unrelated required fields muddying the assertion. Built
#: independent of the `project` fixture's own directories, since its own
#: tests write it to a disposable temp root via `_load`.
R2_ONLY_LANE = (
    set_key(R0_LANE, "rigor", '["R0", "R2"]')
    + '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
    + """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
"""
)


def _load(text: str):
    """Write *text* under a disposable temp project root (with a `src/`
    directory, so `judge.source_roots = ["src"]` always resolves) and load
    it -- used by tests whose lane body is standalone rather than built on
    the `project` fixture's own `R1_SOURCE_ROOTS`."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        path = root / "assay.toml"
        path.write_text(text, encoding="utf-8")
        return load_lane_file(path)


# --- JUDGE_MODES: the closed vocabulary ---------------------------------------


def test_judge_modes_is_the_closed_two_value_vocabulary():
    assert JUDGE_MODES == frozenset({"changed_lines", "whole_target"})


# --- mode: legality, defaulting, and its own closed vocabulary ---------------


def test_mode_declared_without_r1_or_r2_is_refused():
    """B033/A-325 narrowed this from "R1" to "R1 or R2": `mode` is a
    LANE-level judging scope both tiers read, so it is inert only when
    NEITHER is declared."""
    lane = R0_LANE + '\n[lanes.package.judge]\nmode = "whole_target"\n'
    with pytest.raises(LaneConfigError, match="neither R1 nor R2"):
        _load(lane)


def test_mode_declared_on_an_r2_only_lane_is_legal_and_scopes_r2():
    """B033/A-325: an `R0,R2` whole-target lane is exactly dstdns's SQL
    shape. It declares `targets` (required in this mode) and NO `base` --
    whole-target R2 mutates the declared files whole and reads no
    comparison commit."""
    lane = R2_ONLY_LANE.replace(
        'source_roots = ["src"]\nbase = "main"',
        'source_roots = ["src"]\nmode = "whole_target"\ntargets = ["src/mod.py"]',
    )
    judge = _load(lane).lane("package").judge

    assert judge is not None
    assert judge.mode == "whole_target"
    assert judge.targets == ("src/mod.py",)
    assert judge.base is None


def test_a_whole_target_r2_lane_declaring_base_is_refused_as_inert():
    """B033/A-325(c): `judge.base` reaches only `evaluate_r1` (which ignores
    it under whole-target mode), the skipped `check_base_is_head`/`git
    diff`, and the artifact. Under whole-target scope nothing reads it, so
    it is inert config in EVERY language and at EVERY rigor -- the carve-out
    that kept it required for SQL and for R2 is gone."""
    lane = R2_ONLY_LANE.replace(
        'source_roots = ["src"]\nbase = "main"',
        'source_roots = ["src"]\nbase = "main"\nmode = "whole_target"\n'
        'targets = ["src/mod.py"]',
    )
    with pytest.raises(LaneConfigError, match=r"reads none of judge.\{base\}"):
        _load(lane)


def test_a_sql_lane_declaring_targets_without_whole_target_mode_is_refused():
    """B033/A-325(d): the `declared_language != "sql"` carve-out on the
    vacuity guard is deleted. A SQL lane declaring an inert `targets` list
    was the one shape this guard's own error message describes and the one
    shape it stopped refusing."""
    lane = R2_ONLY_LANE.replace(
        'language = "python"\nsource_roots = ["src"]\nbase = "main"',
        'language = "sql"\nsource_roots = ["src"]\nbase = "main"\n'
        'targets = ["src/schema.sql"]',
    ).replace(
        'operators = ["python:compare-swap"]',
        'operators = ["sql:drop-check"]\nequivalence_artifact = ".assay/e.sql"',
    )
    with pytest.raises(LaneConfigError, match="judge.mode is not 'whole_target'"):
        _load(lane)


def test_mode_with_an_unknown_value_is_refused(project: Project):
    lane = WHOLE_TARGET_LANE.replace('mode = "whole_target"', 'mode = "bogus"')
    with pytest.raises(LaneConfigError, match=r"'judge.mode' must be one of"):
        load_lane_file(project.write(lane))


def test_mode_absent_stays_none_on_the_model(project: Project):
    """`JudgeConfig.mode` records the DECLARED value only -- absent stays
    `None`, never silently filled in with `"changed_lines"` (A-260's own
    "declared value or None" contract; the resolved default lives only in
    `assay.runner.evaluate_r1`)."""
    judge = load_lane_file(project.write(R1_LANE)).lane("package").judge
    assert judge is not None
    assert judge.mode is None
    assert judge.targets is None


def test_whole_target_mode_records_targets_and_drops_base(project: Project):
    judge = load_lane_file(project.write(WHOLE_TARGET_LANE)).lane("package").judge
    assert judge is not None
    assert judge.mode == "whole_target"
    assert judge.targets == ("src/mod.py",)
    assert judge.base is None


def test_mode_targets_and_require_branch_round_trip_through_as_declared(
    project: Project,
):
    lane = WHOLE_TARGET_LANE.replace(
        'mode = "whole_target"', 'require_branch = true\nmode = "whole_target"'
    )
    declared = load_lane_file(project.write(lane)).lane("package").as_declared()
    assert declared["judge"]["mode"] == "whole_target"
    assert declared["judge"]["targets"] == ["src/mod.py"]
    assert declared["judge"]["require_branch"] is True


# --- targets: legality under changed_lines mode -------------------------------


def test_targets_declared_under_changed_lines_mode_is_refused(project: Project):
    lane = R1_LANE.replace(
        "\n[lanes.package.where]",
        '\ntargets = ["src/greet.py"]\n\n[lanes.package.where]',
    )
    with pytest.raises(LaneConfigError, match="judge.mode is not 'whole_target'"):
        load_lane_file(project.write(lane))


# --- targets: required and non-empty under whole_target mode -----------------


def test_whole_target_mode_missing_targets_is_refused_with_a_mode_hint(project: Project):
    lane = drop_key(WHOLE_TARGET_LANE, "targets")
    with pytest.raises(LaneConfigError, match=r"judge\.targets.*whole_target"):
        load_lane_file(project.write(lane))


def test_whole_target_mode_with_an_empty_targets_list_is_refused(project: Project):
    lane = WHOLE_TARGET_LANE.replace('targets = ["src/mod.py"]', "targets = []")
    with pytest.raises(LaneConfigError, match="'judge.targets' is empty"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize(
    "bad_toml_literal, why",
    [
        ('"/src/mod.py"', "must not be absolute"),
        # TOML LITERAL string (single quotes): a basic (double-quoted)
        # string would need the backslash itself escaped, which is a fact
        # about TOML syntax, not about this field's own validation.
        ("'src\\mod.py'", "contains a backslash"),
        ('"./src/mod.py"', "invalid path component"),
        ('"src/../mod.py"', "invalid path component"),
        ('"../src/mod.py"', "invalid path component"),
        # A doubled or trailing slash produces an EMPTY path component under
        # `raw.split("/")`, so it is refused by the same "invalid path
        # component" check above -- proven unreachable via any OTHER check
        # (config.py's own `_load_targets` docstring), not merely untested.
        ('"src//mod.py"', "invalid path component"),
        ('"src/mod.py/"', "invalid path component"),
    ],
)
def test_a_non_canonical_target_spelling_is_refused(project: Project, bad_toml_literal: str, why: str):
    lane = WHOLE_TARGET_LANE.replace('targets = ["src/mod.py"]', f"targets = [{bad_toml_literal}]")
    with pytest.raises(LaneConfigError, match=why):
        load_lane_file(project.write(lane))


def test_a_duplicate_target_is_refused(project: Project):
    lane = WHOLE_TARGET_LANE.replace(
        'targets = ["src/mod.py"]', 'targets = ["src/mod.py", "src/mod.py"]'
    )
    with pytest.raises(LaneConfigError, match="more than once"):
        load_lane_file(project.write(lane))


def test_an_empty_string_target_entry_is_refused(project: Project):
    lane = WHOLE_TARGET_LANE.replace(
        'targets = ["src/mod.py"]', 'targets = ["", "src/mod.py"]'
    )
    with pytest.raises(LaneConfigError, match=r"judge\.targets\[0\] must not be empty"):
        load_lane_file(project.write(lane))


def test_a_non_string_target_entry_is_refused(project: Project):
    lane = WHOLE_TARGET_LANE.replace('targets = ["src/mod.py"]', "targets = [1]")
    with pytest.raises(LaneConfigError, match=r"targets\[0\] must be a string"):
        load_lane_file(project.write(lane))


def test_targets_must_be_a_list(project: Project):
    lane = WHOLE_TARGET_LANE.replace('targets = ["src/mod.py"]', 'targets = "src/mod.py"')
    with pytest.raises(LaneConfigError, match=r"'judge\.targets' must be an array"):
        load_lane_file(project.write(lane))


def test_multiple_distinct_targets_all_load(project: Project):
    lane = WHOLE_TARGET_LANE.replace(
        'targets = ["src/mod.py"]', 'targets = ["src/mod.py", "src/other.py"]'
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge
    assert judge is not None
    assert judge.targets == ("src/mod.py", "src/other.py")


# --- base: forbidden without R2, required with R2, under whole_target mode ---


def test_whole_target_mode_with_base_and_no_r2_is_refused_as_inert(project: Project):
    lane = WHOLE_TARGET_LANE.replace(
        'targets = ["src/mod.py"]', 'targets = ["src/mod.py"]\nbase = "main"'
    )
    with pytest.raises(LaneConfigError, match="reads none of"):
        load_lane_file(project.write(lane))


_WHOLE_TARGET_WITH_R2_MUTATION = "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 10\noperators = [\"python:compare-swap\"]\n"


def test_whole_target_mode_with_r2_refuses_base_as_inert(project: Project):
    """INVERTED by B033/A-325. This pair of tests used to assert that an
    `R0,R1,R2` whole-target lane REQUIRED `judge.base` "for R2's own sake".
    That was already false when the assertion was written: `whole_file_r2`
    skips both `check_base_is_head` and the `git diff`, so a whole-target R2
    reads no comparison commit either -- the lane was being forced to
    declare a value nothing consumed, and the verdict then recorded it as
    though a comparison had happened."""
    lane = (
        _lane_with_rigor('["R0", "R1", "R2"]').replace(
            'targets = ["src/mod.py"]', 'targets = ["src/mod.py"]\nbase = "main"'
        )
        + _WHOLE_TARGET_WITH_R2_MUTATION
    )
    with pytest.raises(LaneConfigError, match=r"reads none of judge.\{base\}"):
        load_lane_file(project.write(lane))


def test_whole_target_mode_with_r2_and_no_base_loads_clean(project: Project):
    lane = _lane_with_rigor('["R0", "R1", "R2"]') + _WHOLE_TARGET_WITH_R2_MUTATION
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.base is None
    assert judge.mode == "whole_target"


# --- require_branch: legality and parsing -------------------------------------


def test_require_branch_declared_without_r1_is_refused():
    lane = R2_ONLY_LANE.replace(
        "\n[lanes.package.judge.mutation]",
        "\nrequire_branch = true\n\n[lanes.package.judge.mutation]",
    )
    with pytest.raises(LaneConfigError, match="does not include R1"):
        _load(lane)


@pytest.mark.parametrize("value", [True, False])
def test_require_branch_is_legal_and_parsed_alongside_r1(project: Project, value: bool):
    lane = R1_LANE.replace(
        "\n[lanes.package.where]",
        f"\nrequire_branch = {str(value).lower()}\n\n[lanes.package.where]",
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge
    assert judge is not None
    assert judge.require_branch is value


def test_require_branch_absent_stays_none(project: Project):
    judge = load_lane_file(project.write(R1_LANE)).lane("package").judge
    assert judge is not None
    assert judge.require_branch is None


# --- §5's canary interaction: uncovered-line under whole_target mode ---------


def test_uncovered_line_canary_target_inside_targets_is_accepted(project: Project):
    lane = (
        _lane_with_rigor('["R0", "R1", "R3"]')
        + '\n[lanes.package.judge.canary]\nmechanism = "uncovered-line"\ntarget = "src/mod.py"\n'
    )
    project.file("src/mod.py", "x = 1\n")
    judge = load_lane_file(project.write(lane)).lane("package").judge
    assert judge is not None
    assert judge.canary is not None
    assert judge.canary.target == "src/mod.py"


def test_uncovered_line_canary_target_outside_targets_is_refused(project: Project):
    lane = (
        _lane_with_rigor('["R0", "R1", "R3"]')
        + '\n[lanes.package.judge.canary]\nmechanism = "uncovered-line"\ntarget = "src/other.py"\n'
    )
    project.file("src/mod.py", "x = 1\n")
    project.file("src/other.py", "y = 2\n")
    with pytest.raises(LaneConfigError, match=r"is not\s+one of judge\.targets"):
        load_lane_file(project.write(lane))


def test_uncovered_line_canary_outside_whole_target_mode_ignores_targets_entirely(
    project: Project,
):
    """The interaction check is scoped to `mode == "whole_target"` only --
    a changed-lines-mode lane's canary target need not appear in any
    `targets` list, because there isn't one."""
    lane = (
        set_key(R1_LANE, "rigor", '["R0", "R1", "R3"]')
        + '\n[lanes.package.judge.canary]\nmechanism = "uncovered-line"\ntarget = "src/mod.py"\n'
    )
    project.file("src/mod.py", "x = 1\n")
    judge = load_lane_file(project.write(lane)).lane("package").judge
    assert judge is not None
    assert judge.mode is None
    assert judge.canary is not None
