"""P18 work item 1 — ``judge.mutation`` is a CLOSED table, validated at
LOAD time rather than passed through opaque (P11/P12's own contract,
before this package): a required positive-integer ``jobs`` and a
non-empty, duplicate-free ``operators`` list drawn from
:data:`assay.mutation.MUTATION_OPERATORS`'s own closed vocabulary
(A-068's "cross-checked against the vocabulary's own owner" discipline,
one field over from ``judge.coverage.format``/``FORMAT_REGISTRY``).
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, Project, set_key

from assay import config as config_module
from assay import verdict as verdict_module
from assay.config import load_lane_file
from assay.errors import LaneConfigError
from assay.mutation import MUTATION_OPERATORS
from assay.vocabulary import (
    MUTATION_OPERATORS_BY_LANGUAGE,
    WITHDRAWN_MUTATION_OPERATORS,
)

R2_JUDGE = """
[lanes.package.judge]
language = "{language}"
source_roots = ["src"]
base = "main"
"""


#: B006a/A-269, schema v2: every R1+ lane requires an explicit [isolation]
#: table; this module's own subject is `judge.mutation`, so every case here
#: picks the plain "repository" selection.
_ISOLATION_TABLE = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'


def _lane_with_mutation(mutation_table: str, *, language: str = "python") -> str:
    """(P33/V5-2) *language* is now a parameter, because the operator
    vocabulary is closed PER LANGUAGE: which operators a lane may declare is
    a function of what it says it is."""
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    return body + _ISOLATION_TABLE + R2_JUDGE.format(language=language) + mutation_table


def test_a_minimal_valid_mutation_table_loads(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        "operators = [\"python:compare-swap\"]\n"
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None
    assert judge.mutation.jobs == 1
    assert judge.mutation.operators == ("python:compare-swap",)


def test_every_declared_operator_is_accepted(project: Project):
    """Direction 1 of the closed-vocabulary pair: every member of
    :data:`MUTATION_OPERATORS` loads on its own -- **under the language that
    owns it** (P33/V5-2). Iterating the per-language map rather than the flat
    tuple is what makes this a real sweep now: the flat tuple would have
    declared `sql:drop-check` on a Python lane, which is precisely the
    combination v5 exists to refuse.

    (P34/W4) A ``sql`` lane also needs ``equivalence_artifact`` -- it is
    REQUIRED on that language alone, so the extra line is conditional and
    this sweep still proves nothing about Python/Go's own table shape."""
    for language, operators in MUTATION_OPERATORS_BY_LANGUAGE.items():
        # (A-331) The B034/A-326-era `if operator in
        # WITHDRAWN_MUTATION_OPERATORS: continue` skip that used to guard this
        # loop is GONE, because it can no longer fire: the withdrawn names left
        # `MUTATION_OPERATORS_BY_LANGUAGE` at the v8 cut, so nothing this loop
        # iterates is withdrawn. Keeping it would be a filter that reads like a
        # live guard and can never fire -- the exact defect class A-331 invoked
        # to delete the sibling filters in `config.py`, applied here too.
        for operator in sorted(operators):
            extra = (
                'equivalence_artifact = ".assay/schema-dump.sql"\n'
                if language == "sql"
                else ""
            )
            lane = _lane_with_mutation(
                f"\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
                f'operators = ["{operator}"]\n{extra}',
                language=language,
            )
            judge = load_lane_file(project.write(lane, name=f"{operator}.toml")).lane(
                "package"
            ).judge
            assert judge is not None and judge.mutation is not None
            assert judge.mutation.operators == (operator,)
    # The flat tuple is exactly the union, so the sweep above really is total.
    assert set(MUTATION_OPERATORS) == {
        operator
        for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()
        for operator in operators
    }


def test_an_operator_belonging_to_another_language_is_refused(project: Project):
    """Direction 3 (P33/V5-2/O2): `sql:drop-check` IS a known operator, so
    this is not the unknown-operator rejection one field over -- it is the
    cross-language one, and the message has to say so or an operator name a
    consumer typed correctly reads as a typo. A rejected value per language,
    not one rejection standing for all three."""
    for language, foreign in (
        ("python", "sql:drop-check"),
        ("go", "python:falsy-swap"),
        ("sql", "go:compare-swap"),
    ):
        lane = _lane_with_mutation(
            f"\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
            f'operators = ["{foreign}"]\n',
            language=language,
        )
        with pytest.raises(LaneConfigError) as excinfo:
            load_lane_file(project.write(lane, name=f"{language}-{foreign}.toml"))
        message = str(excinfo.value)
        assert foreign in message, "the refusal must name the offending operator"
        assert "another language" in message
        assert f"{language!r}" in message


def test_an_unknown_operator_is_rejected(project: Project):
    """Direction 2: a name outside the closed vocabulary is refused, and
    the message names both the offender and the DECLARABLE set.

    Narrowed from "the known set" by the B034 round-2 review: a withdrawn
    operator is still spellable in a v7 artifact, so it stays in
    `MUTATION_OPERATORS` — but offering it to someone who just mistyped an
    operator name would hand them a second refusal. The suggestion list and
    the membership check are deliberately different sets now."""
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\noperators = ["typo-swap"]\n'
    )
    with pytest.raises(LaneConfigError) as exc:
        load_lane_file(project.write(lane))
    assert "typo-swap" in str(exc.value)
    # (A-331) Every catalogue operator is offered, with no exceptions to carve
    # out any more: the suggestion list and the membership check are the SAME
    # set again now that the withdrawn names have left the catalogue. The
    # branch that used to assert a withdrawn name was absent from the message
    # is deleted rather than left unreachable.
    for operator in sorted(MUTATION_OPERATORS):
        assert operator in str(exc.value)
    # ...and the withdrawn names are still never offered -- now because they
    # are not in the catalogue at all, which is what makes the loop above safe.
    for withdrawn in sorted(WITHDRAWN_MUTATION_OPERATORS):
        assert withdrawn not in str(exc.value)


def test_operators_is_order_preserving(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:falsy-swap", "python:compare-swap", "python:boolop-swap"]\n'
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.operators == ("python:falsy-swap", "python:compare-swap", "python:boolop-swap")


def test_an_empty_operators_list_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\noperators = []\n"
    )
    with pytest.raises(LaneConfigError, match="operators' is empty"):
        load_lane_file(project.write(lane))


def test_a_duplicate_operator_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:compare-swap", "python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="operators' contains a duplicate"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("bad_jobs", ["0", "-1", "true", '"2"', "1.5"])
def test_jobs_must_be_a_positive_integer(bad_jobs: str, project: Project):
    lane = _lane_with_mutation(
        f'\n[lanes.package.judge.mutation]\njobs = {bad_jobs}\nmax_mutants = 50\noperators = ["python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="judge.mutation.jobs"):
        load_lane_file(project.write(lane))


def test_a_missing_jobs_field_is_rejected(project: Project):
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\noperators = ["python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="missing required field 'judge.mutation.jobs'"):
        load_lane_file(project.write(lane))


def test_a_missing_operators_field_is_rejected(project: Project):
    lane = _lane_with_mutation("\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n")
    with pytest.raises(
        LaneConfigError, match="missing required field 'judge.mutation.operators'"
    ):
        load_lane_file(project.write(lane))


def test_an_unknown_mutation_key_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:compare-swap"]\ntimeout = 5\n'
    )
    with pytest.raises(LaneConfigError, match="unknown judge.mutation key"):
        load_lane_file(project.write(lane))


def test_mutation_as_declared_round_trips_exactly(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 3\nmax_mutants = 50\n"
        'operators = ["python:compare-swap", "python:falsy-swap"]\n'
    )
    loaded = load_lane_file(project.write(lane)).lane("package")

    assert loaded.as_declared()["judge"]["mutation"] == {
        "jobs": 3,
        "max_mutants": 50,
        "operators": ["python:compare-swap", "python:falsy-swap"],
    }


# ---------------------------------------------------------------------------
# P34/W4 -- the two v6 artifact fields, unreserved ONLY for a sql lane
# (A-227/A-230b's own refusal must survive for every other language).
# ---------------------------------------------------------------------------


def _sql_mutation_table(*, bad_field: str | None = None, bad_value: str = "") -> str:
    """A SQL R2 mutation table declaring BOTH artifact keys at a valid
    default, with exactly one optionally overridden to *bad_value* -- so a
    rejection test exercises exactly one defect at a time, never two at
    once (the other field, if itself required, stays valid so it cannot be
    the thing that actually raised)."""
    fields = {
        "equivalence_artifact": '".assay/schema-dump.sql"',
        "kill_signal_artifact": '".assay/kill-signal.txt"',
    }
    if bad_field is not None:
        fields[bad_field] = bad_value
    body = "".join(f"{name} = {value}\n" for name, value in fields.items())
    return (
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["sql:drop-check"]\n' + body
    )


def test_a_sql_lane_accepts_both_artifact_keys(project: Project):
    lane = _lane_with_mutation(_sql_mutation_table(), language="sql")
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.equivalence_artifact == ".assay/schema-dump.sql"
    assert judge.mutation.kill_signal_artifact == ".assay/kill-signal.txt"


def test_a_sql_lane_accepts_equivalence_artifact_alone(project: Project):
    """`kill_signal_artifact` stays OPTIONAL even on a sql lane (A-223b
    derives `kill_attribution` from its presence) -- only
    `equivalence_artifact` is required."""
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["sql:drop-check"]\n'
        'equivalence_artifact = ".assay/schema-dump.sql"\n',
        language="sql",
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.equivalence_artifact == ".assay/schema-dump.sql"
    assert judge.mutation.kill_signal_artifact is None


def test_a_sql_lane_without_equivalence_artifact_is_rejected(project: Project):
    """The carve's single most consequential config decision (§4.3):
    without it, a mutant that never actually mutated is recorded
    'survived', a false statement about the consumer's tests."""
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["sql:drop-check"]\n',
        language="sql",
    )
    with pytest.raises(
        LaneConfigError, match="equivalence_artifact' is required on a sql lane"
    ):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("language", ["python", "go"])
@pytest.mark.parametrize("field", ["kill_signal_artifact", "equivalence_artifact"])
def test_a_non_sql_lane_still_refuses_each_artifact_key(
    field: str, language: str, project: Project
):
    operator = {"python": "python:compare-swap", "go": "go:compare-swap"}[language]
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        f'operators = ["{operator}"]\n'
        f'{field} = ".assay/x.txt"\n',
        language=language,
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(project.write(lane))

    message = str(excinfo.value)
    assert field in message, "the refusal must name the offending field"
    assert "sql" in message, "the refusal must say WHICH language may declare it"
    assert f"{language!r}" in message, "the refusal must name the lane's own language"
    assert "unknown" not in message.lower(), (
        "a language-scoped refusal must not read like a plain typo"
    )


@pytest.mark.parametrize("field", ["equivalence_artifact", "kill_signal_artifact"])
def test_an_absolute_sql_artifact_path_is_rejected(
    field: str, project: Project, tmp_path
):
    absolute = str(tmp_path / "outside.txt")
    lane = _lane_with_mutation(
        _sql_mutation_table(bad_field=field, bad_value=f'"{absolute}"'),
        language="sql",
    )
    with pytest.raises(LaneConfigError, match="is absolute"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("field", ["equivalence_artifact", "kill_signal_artifact"])
def test_a_sql_artifact_path_escaping_the_project_root_via_dotdot_is_rejected(
    field: str, project: Project
):
    lane = _lane_with_mutation(
        _sql_mutation_table(bad_field=field, bad_value='"../outside/x.txt"'),
        language="sql",
    )
    with pytest.raises(LaneConfigError, match="not contained beneath the project root"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("field", ["equivalence_artifact", "kill_signal_artifact"])
def test_an_empty_sql_artifact_path_is_rejected(field: str, project: Project):
    lane = _lane_with_mutation(
        _sql_mutation_table(bad_field=field, bad_value='""'), language="sql"
    )
    with pytest.raises(LaneConfigError, match=f"{field}' is empty"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("field", ["equivalence_artifact", "kill_signal_artifact"])
def test_a_symlinked_project_root_ancestor_escaping_via_the_sql_artifact_is_rejected(
    field: str, project: Project, tmp_path
):
    # The identical escape `_resolve_source_root`/`judge.coverage.artifact`
    # already guard for: a symlink INSIDE the project resolving outside it.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project.root / "linked"
    link.symlink_to(outside)
    lane = _lane_with_mutation(
        _sql_mutation_table(bad_field=field, bad_value='"linked/x.txt"'),
        language="sql",
    )
    with pytest.raises(LaneConfigError, match="not contained beneath the project root"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("field", ["equivalence_artifact", "kill_signal_artifact"])
def test_a_relative_sql_artifact_path_inside_the_project_loads(
    field: str, project: Project
):
    lane = _lane_with_mutation(
        _sql_mutation_table(bad_field=field, bad_value='"build/x.txt"'),
        language="sql",
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert getattr(judge.mutation, field) == "build/x.txt"


def test_the_sql_artifact_need_not_exist_yet_at_load_time(project: Project):
    # A-048's own timing: both artifacts are OUTPUTS of the lane's command.
    assert not (project.root / ".assay").exists()
    lane = _lane_with_mutation(_sql_mutation_table(), language="sql")
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.equivalence_artifact == ".assay/schema-dump.sql"


def test_sql_mutation_as_declared_round_trips_both_artifact_keys(project: Project):
    lane = _lane_with_mutation(_sql_mutation_table(), language="sql")
    loaded = load_lane_file(project.write(lane)).lane("package")

    assert loaded.as_declared()["judge"]["mutation"] == {
        "jobs": 1,
        "max_mutants": 50,
        "operators": ["sql:drop-check"],
        "equivalence_artifact": ".assay/schema-dump.sql",
        "kill_signal_artifact": ".assay/kill-signal.txt",
    }


def test_config_and_verdict_shard_count_bounds_stay_equal():
    """(B026 N-5 round-3/round-2 note) `MAX_SHARD_COUNT` is deliberately
    duplicated between `config.py` (bounds a DECLARED `judge.mutation.
    shard_count`) and `verdict.py` (bounds an EXECUTED `judgment.r2.
    shard_count`) rather than imported -- the two modules import each other
    in the direction that makes a real import circular (`verdict.py`
    already imports FROM `config.py`). A hand-duplicated pair with no
    equality guard is exactly the drift risk this constant exists to close;
    this is that guard."""
    assert config_module.MAX_SHARD_COUNT == verdict_module.MAX_SHARD_COUNT
