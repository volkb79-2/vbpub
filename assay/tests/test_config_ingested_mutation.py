"""B046 — ``judge.mutation.format``: the LOAD-time half of the ingested R2
lane, and the refusals that are a SECOND layer rather than the only one.

The verdict model independently forbids ``jobs``/``max_mutants``/
``operators``/``equivalence_artifact`` on an ingested ``judgment.r2``
(A-360). Both layers are deliberate and neither substitutes for the other: the
model's refusal protects a document a consumer reads back, and this one is
what a human editing ``assay.toml`` actually sees. A ``ValueError`` out of a
dataclass is not a loader diagnostic, and telling someone "your artifact is
invalid" when what they need to hear is "assay ran none of this, so it
declares none of it" is the weaker of the two messages.

Every test is a load, i.e. before the lane's command can run.
"""

from __future__ import annotations

import pytest
from conftest import Project

from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode
from assay.mutation_parsers import MUTATION_FORMAT_REGISTRY

INGESTED_LANE = """\
schema_version = 2

[lanes.ui_mutation]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
cwd = "app"
argv = ["npx", "stryker", "run"]
env = {}
env_passthrough = ["PATH"]
budget = "45m"
allow_argv_append = false

[lanes.ui_mutation.isolation]
snapshot_selection = "repository"

[lanes.ui_mutation.judge]
language = "javascript"
source_roots = ["app/src"]
base = "main"

[lanes.ui_mutation.judge.mutation]
format = "mutation-report-json"
artifact = "app/reports/mutation.json"
fail_under = 100.0
"""


@pytest.fixture
def app_project(project: Project) -> Project:
    project.dir("app", "src")
    return project


def _load(project: Project, text: str):
    return load_lane_file(project.write(text))


def _mutation_key(text: str, line: str) -> str:
    return text.replace("fail_under = 100.0\n", f"fail_under = 100.0\n{line}\n", 1)


def _refusal(project: Project, text: str) -> LaneConfigError:
    with pytest.raises(LaneConfigError) as excinfo:
        _load(project, text)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    return excinfo.value


# --------------------------------------------------------------------------
# Accepted
# --------------------------------------------------------------------------


def test_an_ingested_lane_loads(app_project: Project):
    lane = _load(app_project, INGESTED_LANE).lane("ui_mutation")
    assert lane.judge is not None and lane.judge.mutation is not None
    config = lane.judge.mutation
    assert config.is_ingested
    assert config.format == "mutation-report-json"
    assert config.artifact == "app/reports/mutation.json"
    assert config.fail_under == 100.0


def test_assays_own_policy_fields_stay_none_never_defaulted(app_project: Project):
    config = _load(app_project, INGESTED_LANE).lane("ui_mutation").judge.mutation
    assert config.jobs is None
    assert config.max_mutants is None
    assert config.operators is None
    assert config.equivalence_artifact is None


def test_a_javascript_r2_lane_is_now_constructible_at_all(app_project: Project):
    """Before B046 this was refused by the foreign-operator guard: a native R2
    lane must declare non-empty ``operators`` and
    ``MUTATION_OPERATORS_BY_LANGUAGE`` has no ``javascript`` entry, so every
    operator such a lane could spell was foreign to it. An INGESTED lane
    declares no operators at all, so it never meets that guard."""
    lane = _load(app_project, INGESTED_LANE).lane("ui_mutation")
    assert lane.judge.language == "javascript"
    assert "R2" in lane.rigor


def test_the_declared_lane_round_trips(app_project: Project):
    from conftest import lane_table

    lane = _load(app_project, INGESTED_LANE).lane("ui_mutation")
    assert lane.as_declared() == lane_table(INGESTED_LANE, "ui_mutation")


def test_the_format_vocabulary_comes_from_the_parser_registry(app_project: Project):
    """A-068 one tier over: the key is closed against the registry's own keys,
    never a second hardcoded list, so the two cannot drift."""
    error = _refusal(
        app_project,
        INGESTED_LANE.replace(
            'format = "mutation-report-json"', 'format = "stryker-html"'
        ),
    )
    for known in MUTATION_FORMAT_REGISTRY:
        assert known in str(error)


# --------------------------------------------------------------------------
# The native-policy refusals — the second layer, with its own message
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "jobs = 4",
        "max_mutants = 200",
        'operators = ["python:compare-swap"]',
    ],
)
def test_assays_own_policy_keys_are_refused_on_an_ingested_lane(
    app_project: Project, line: str
):
    error = _refusal(app_project, _mutation_key(INGESTED_LANE, line))
    message = str(error)
    assert "forbidden on an INGESTED lane" in message
    # The message must say WHY, not merely that the key is not allowed --
    # that is the whole reason this refusal exists beside the model's.
    assert "assay decided none of it" in message
    assert "mutation.*[].operator" in message


def test_equivalence_artifact_is_refused_on_an_ingested_lane(app_project: Project):
    """Refused twice over: by the SQL-only reserved-key guard for a
    ``javascript`` lane, and by the ingested guard for any language. The
    javascript path hits the first, which names the language."""
    error = _refusal(
        app_project, _mutation_key(INGESTED_LANE, 'equivalence_artifact = "d.sql"')
    )
    assert "equivalence_artifact" in str(error)


@pytest.mark.parametrize(
    "line", ['budget_per_candidate = "30s"', "shard_index = 0\nshard_count = 2"]
)
def test_orchestration_keys_are_refused_on_an_ingested_lane(
    app_project: Project, line: str
):
    """They bound or partition an execution ASSAY performs, and on this lane
    assay executes no mutant at all."""
    error = _refusal(app_project, _mutation_key(INGESTED_LANE, line))
    assert "assay executes no mutant at all" in str(error)


# --------------------------------------------------------------------------
# The ingested keys' own grammar
# --------------------------------------------------------------------------


def test_a_missing_artifact_is_refused(app_project: Project):
    error = _refusal(
        app_project,
        INGESTED_LANE.replace('artifact = "app/reports/mutation.json"\n', ""),
    )
    assert "judge.mutation.artifact" in str(error)


def test_a_missing_fail_under_is_refused(app_project: Project):
    error = _refusal(app_project, INGESTED_LANE.replace("fail_under = 100.0\n", ""))
    assert "judge.mutation.fail_under" in str(error)


def test_an_absolute_artifact_is_refused(app_project: Project):
    error = _refusal(
        app_project,
        INGESTED_LANE.replace(
            '"app/reports/mutation.json"', '"/var/tmp/mutation.json"'
        ),
    )
    assert "absolute" in str(error)


def test_an_escaping_artifact_is_refused(app_project: Project):
    error = _refusal(
        app_project,
        INGESTED_LANE.replace('"app/reports/mutation.json"', '"../outside.json"'),
    )
    assert "not contained beneath the project root" in str(error)


def test_a_sub_hundred_fail_under_LOADS_and_is_carried_to_the_judge(
    app_project: Project,
):
    """B050/A-427/DA-R22 — the positive counterpart of the refusal v9 needed.

    Up to v9 this lane was refused at LOAD time with a message naming B050 as
    the wire field a lower floor needs ("can only honour 100.0"). That field
    exists now: ``judgment.r2.fail_under`` is REQUIRED under
    ``producer = "ingested"``, so the verdict records WHICH floor was applied
    and ``verify.py`` reads it back rather than assuming 100. The refusal is
    therefore GONE rather than relaxed, and this test is the same lane text
    asserting the opposite outcome.

    The floor must arrive at the judge, not merely load: this asserts the
    loaded value on the config, and
    ``tests/test_mutation_judge.py`` asserts what ``judge_mutation`` does with
    it. ``config`` is still the only thing that constrains the RANGE (the
    test below), which is the half of the old block that stayed.
    """
    lane = _load(
        app_project, INGESTED_LANE.replace("fail_under = 100.0", "fail_under = 90.0")
    ).lane("ui_mutation")
    assert lane.judge is not None and lane.judge.mutation is not None
    assert lane.judge.mutation.fail_under == 90.0
    # And the value is a float the judge can compare, not the string the file
    # spells it with -- `_as_float` is what the loader is for.
    assert isinstance(lane.judge.mutation.fail_under, float)


def test_a_fail_under_outside_the_percentage_range_is_refused(app_project: Project):
    error = _refusal(
        app_project, INGESTED_LANE.replace("fail_under = 100.0", "fail_under = 101.0")
    )
    assert "0.0..100.0" in str(error)


# --------------------------------------------------------------------------
# The native lane is untouched, and the two shapes cannot be mixed
# --------------------------------------------------------------------------


def test_artifact_without_format_is_refused(app_project: Project):
    """``format``'s PRESENCE is the discriminator, so the other two ingested
    keys are meaningless without it -- and meaningless config that loads is
    config that cannot fail loudly when it is wrong."""
    from conftest import R1_LANE

    text = R1_LANE.replace(
        'rigor = ["R0", "R1"]', 'rigor = ["R0", "R1", "R2"]'
    ).replace(
        "base = \"main\"\n",
        'base = "main"\n\n[lanes.package.judge.mutation]\n'
        'jobs = 1\nmax_mutants = 10\noperators = ["python:compare-swap"]\n'
        'artifact = "m.json"\n',
        1,
    )
    error = _refusal(app_project, text)
    assert "requires 'judge.mutation.format'" in str(error)


def test_a_native_lane_still_requires_its_own_three_keys(app_project: Project):
    from conftest import R1_LANE

    text = R1_LANE.replace(
        'rigor = ["R0", "R1"]', 'rigor = ["R0", "R1", "R2"]'
    ).replace(
        "base = \"main\"\n",
        'base = "main"\n\n[lanes.package.judge.mutation]\njobs = 1\n',
        1,
    )
    error = _refusal(app_project, text)
    assert "missing required field 'judge.mutation." in str(error)


def test_a_sql_lane_cannot_ingest(app_project: Project):
    """``equivalence_artifact`` is REQUIRED on a sql lane (without it a mutant
    that never mutated is recorded ``survived``) and FORBIDDEN on an ingested
    one, so the combination names no real toolchain and is refused rather than
    silently resolved by a precedence rule."""
    error = _refusal(
        app_project,
        INGESTED_LANE.replace('language = "javascript"', 'language = "sql"'),
    )
    assert "cannot ingest a mutation report" in str(error)
