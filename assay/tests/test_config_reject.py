"""O1, REJECT direction — a lane missing a required field fails, by name.

Every test here mutates the *same* canonical template the ACCEPT module loads,
and the central test asserts BOTH directions in one body: the untouched
template loads, and the one-field-lighter mutant does not. That pairing is what
stops the suite being satisfied by a loader that rejects everything, which is
O1's stated negative.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

from assay.config import REQUIRED_LANE_FIELDS, load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode


def test_the_eight_required_fields_are_exactly_what_the_oracle_names():
    # O1 names them; if the tuple drifts, the parametrisation below would
    # silently stop covering one, so pin it.
    assert REQUIRED_LANE_FIELDS == (
        "scope",
        "rigor",
        "enforcement",
        "argv",
        "env",
        "env_passthrough",
        "budget",
        "allow_argv_append",
    )


@pytest.mark.parametrize("field", REQUIRED_LANE_FIELDS)
def test_omitting_a_required_field_fails_and_names_it(field: str, project: Project):
    # Direction 1 — the complete template loads. Without this half, a loader
    # that rejects every input passes this test.
    complete = project.write(R0_LANE)
    assert load_lane_file(complete).lane("package").name == "package"

    # Direction 2 — the same file minus one field does not.
    mutant = project.write(drop_key(R0_LANE, field), name="mutant.toml")
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(mutant)

    message = str(excinfo.value)
    assert field in message, f"message does not name the missing field: {message}"
    assert "package" in message, f"message does not name the lane: {message}"
    assert str(mutant) in message, f"message does not name the file: {message}"
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert excinfo.value.exit_code == 2


def test_dropping_env_does_not_also_drop_env_passthrough():
    # Guards the mutation helper itself: a prefix match would delete both, and
    # the parametrised test above would then be asserting the wrong thing.
    mutated = drop_key(R0_LANE, "env")
    assert "env_passthrough" in mutated
    assert "\nenv = " not in mutated


def test_a_key_the_template_lacks_cannot_be_silently_dropped():
    with pytest.raises(AssertionError, match="no top-of-line key"):
        drop_key(R0_LANE, "not_a_field")


# --- O3 (P15): a name in both 'env' and 'env_passthrough' is refused ---------
#
# assay.runner.resolve_command_plan builds env_effective from lane.env FIRST,
# then overwrites any name env_passthrough also finds present in the ambient
# environment -- so a name declared in BOTH tables is not actually fixed, it
# silently tracks whoever's environment ran the lane. Refusing the collision
# here removes that lane shape before runner.py ever sees it.


def test_a_name_declared_in_both_env_and_env_passthrough_is_rejected(
    project: Project,
):
    # R0_LANE declares env = { MOCK_MODE = "true" } and
    # env_passthrough = ["HOME", "TMPDIR", "PATH"] -- no overlap. Add
    # MOCK_MODE to env_passthrough too.
    mutated = set_key(
        R0_LANE, "env_passthrough", '["MOCK_MODE", "HOME", "TMPDIR", "PATH"]'
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(project.write(mutated))

    message = str(excinfo.value)
    assert "MOCK_MODE" in message
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_several_names_declared_in_both_tables_are_all_named(project: Project):
    mutated = set_key(R0_LANE, "env", '{ MOCK_MODE = "true", HOME = "/tmp" }')
    mutated = set_key(
        mutated, "env_passthrough", '["MOCK_MODE", "HOME", "TMPDIR", "PATH"]'
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(project.write(mutated))

    message = str(excinfo.value)
    assert "HOME" in message
    assert "MOCK_MODE" in message


def test_disjoint_env_and_env_passthrough_still_load(project: Project):
    # The ACCEPT half: R0_LANE's own env/env_passthrough are disjoint, and
    # this check must not reject them.
    lane = load_lane_file(project.write(R0_LANE)).lane("package")
    assert set(lane.env) & set(lane.env_passthrough) == set()


def test_missing_schema_version_is_rejected(project: Project):
    path = project.write(drop_key(R0_LANE, "schema_version"))
    with pytest.raises(LaneConfigError, match="schema_version"):
        load_lane_file(path)


def test_unknown_schema_version_is_rejected(project: Project):
    # B006a/A-269: LANE_SCHEMA_VERSION bumped 1 -> 2, so the "unknown version"
    # probe moves to the next integer the loader still refuses.
    path = project.write(set_key(R0_LANE, "schema_version", "3"))
    with pytest.raises(LaneConfigError, match="schema_version = 3"):
        load_lane_file(path)


def test_non_integer_schema_version_is_rejected(project: Project):
    path = project.write(set_key(R0_LANE, "schema_version", '"1"'))
    with pytest.raises(LaneConfigError, match="must be an integer"):
        load_lane_file(path)


def test_file_with_no_lanes_table_is_rejected(project: Project):
    path = project.write("schema_version = 2\n")
    with pytest.raises(LaneConfigError, match="no .lanes. table"):
        load_lane_file(path)


def test_file_with_an_empty_lanes_table_is_rejected(project: Project):
    path = project.write("schema_version = 2\n\n[lanes]\n")
    with pytest.raises(LaneConfigError, match="declares no lanes"):
        load_lane_file(path)


def test_unknown_top_level_key_is_rejected(project: Project):
    text = R0_LANE.replace("schema_version = 2", 'schema_version = 2\nprofile = "fast"')
    assert 'profile = "fast"' in text
    path = project.write(text)
    with pytest.raises(LaneConfigError, match="unknown top-level key"):
        load_lane_file(path)


def test_unknown_lane_key_is_rejected(project: Project):
    # A key assay does not understand is a claim assay cannot honour. Silently
    # ignoring it is how `allow_argv_appended = true` reads as enforced.
    path = project.write(R0_LANE + 'retries = 3\n')
    with pytest.raises(LaneConfigError, match="unknown key"):
        load_lane_file(path)


def test_unknown_judge_key_is_rejected(project: Project):
    text = R1_LANE.replace(
        'language = "python"', 'language = "python"\nbranches = 1'
    )
    assert "branches = 1" in text
    path = project.write(text)
    with pytest.raises(LaneConfigError, match="unknown judge key"):
        load_lane_file(path)


def test_malformed_toml_is_rejected(project: Project):
    path = project.write("schema_version = 1\n[lanes.package\n")
    with pytest.raises(LaneConfigError, match="not valid TOML"):
        load_lane_file(path)


def test_missing_file_is_rejected(project: Project):
    with pytest.raises(LaneConfigError, match="cannot be read"):
        load_lane_file(project.root / "absent.toml")


def test_lanes_that_is_not_a_table_is_rejected(project: Project):
    path = project.write('schema_version = 2\nlanes = "package"\n')
    with pytest.raises(LaneConfigError, match="must be a table of lanes"):
        load_lane_file(path)


def test_lane_that_is_not_a_table_is_rejected(project: Project):
    path = project.write('schema_version = 2\n\n[lanes]\npackage = "yes"\n')
    with pytest.raises(LaneConfigError, match="must be a table"):
        load_lane_file(path)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("scope", "1", "must be a string"),
        ("rigor", '"R0"', "must be an array"),
        ("rigor", "[1]", "must be a string"),
        ("argv", '"pytest"', "must be an array"),
        ("argv", "[]", "must declare the command"),
        ("argv", "[1, 2]", "must be a string"),
        ("env", '"MOCK_MODE=true"', "must be a table"),
        ("env", "{ MOCK_MODE = true }", "quote it"),
        ("env", "{ RETRIES = 3 }", "quote it"),
        ("env_passthrough", '"HOME"', "must be an array"),
        ("budget", "300", "must be a string"),
        ("allow_argv_append", '"false"', "must be a boolean"),
    ],
)
def test_wrongly_typed_field_is_rejected(
    field: str, value: str, expected: str, project: Project
):
    path = project.write(set_key(R0_LANE, field, value))
    with pytest.raises(LaneConfigError, match=expected):
        load_lane_file(path)


def test_where_that_is_not_a_table_is_rejected(project: Project):
    path = project.write(R0_LANE + 'where = "worktree"\n')
    with pytest.raises(LaneConfigError, match="'where' must be a table"):
        load_lane_file(path)


def test_bare_executable_without_declared_path_is_rejected(project: Project):
    text = R0_LANE.replace(
        'env_passthrough = ["HOME", "TMPDIR", "PATH"]',
        'env_passthrough = ["HOME", "TMPDIR"]',
    )
    assert 'argv = ["pytest"' in text

    with pytest.raises(LaneConfigError, match="bare executable.*PATH"):
        load_lane_file(project.write(text))


def test_bare_executable_accepts_path_declared_in_env(project: Project):
    text = R0_LANE.replace(
        'env = { MOCK_MODE = "true" }',
        'env = { MOCK_MODE = "true", PATH = "/opt/test/bin" }',
    ).replace(
        'env_passthrough = ["HOME", "TMPDIR", "PATH"]',
        'env_passthrough = ["HOME", "TMPDIR"]',
    )

    lane = load_lane_file(project.write(text)).lane("package")

    assert dict(lane.env)["PATH"] == "/opt/test/bin"


def test_executable_path_needs_no_path_environment(project: Project):
    text = R0_LANE.replace('argv = ["pytest"', 'argv = ["./tools/pytest"').replace(
        'env_passthrough = ["HOME", "TMPDIR", "PATH"]',
        'env_passthrough = ["HOME", "TMPDIR"]',
    )

    lane = load_lane_file(project.write(text)).lane("package")

    assert lane.argv[0] == "./tools/pytest"


def test_every_rejection_is_error_bad_lane_config(project: Project):
    # One reason code, one exit code, whatever the defect: a consumer switching
    # on reason_code never has to special-case the config layer.
    defects = [
        drop_key(R0_LANE, "budget"),
        set_key(R0_LANE, "scope", '"S9"'),
        set_key(R0_LANE, "budget", '"soon"'),
        "schema_version = 2\n",
        "not toml at all\n[",
    ]
    for index, text in enumerate(defects):
        path = project.write(text, name=f"defect{index}.toml")
        with pytest.raises(LaneConfigError) as excinfo:
            load_lane_file(path)
        assert excinfo.value.outcome is Outcome.ERROR
        assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
